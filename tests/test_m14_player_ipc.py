"""M14 tests: mpv JSON IPC and the in-player switch logic (offline).

The IPC client is exercised against a fake mpv server that speaks the same
newline-delimited JSON, so the protocol (request ids, client-message events and
the ``loadfile`` argument layout) is covered without a display or a network.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from aniworld_cli import mpvctl, playback, resolve, shaders
from aniworld_cli.models import Episode, Hoster, Series, Stream

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="unix-socket fake server, POSIX only"
)


class FakeMpv:
    """Minimal mpv: replies to requests and can push a client-message."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.requests: list[list] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(1)
        self._conn: socket.socket | None = None
        self._accepted = threading.Event()

    def serve(self) -> threading.Thread:
        def run():
            conn, _ = self._sock.accept()
            self._conn = conn
            self._accepted.set()
            buf = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    message = json.loads(line)
                    self.requests.append(message["command"])
                    reply = {
                        "error": "success",
                        "data": 137.5 if message["command"][0] == "get_property" else None,
                        "request_id": message["request_id"],
                    }
                    conn.sendall((json.dumps(reply) + "\n").encode())

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def push_client_message(self, *args: str) -> None:
        assert self._accepted.wait(3), "client never connected"
        assert self._conn is not None
        payload = {"event": "client-message", "args": list(args)}
        self._conn.sendall((json.dumps(payload) + "\n").encode())

    def close(self) -> None:
        for obj in (self._conn, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass


def _client(tmp_path: Path) -> tuple[mpvctl.Mpv, FakeMpv]:
    path = str(tmp_path / "mpv.sock")
    server = FakeMpv(path)
    server.serve()
    client = mpvctl.Mpv(path)
    client.connect(timeout=5)
    return client, server


def test_loadfile_uses_the_index_slot_and_start_option(tmp_path: Path):
    client, server = _client(tmp_path)
    try:
        client.loadfile("https://cdn/x.m3u8", start=137.4)
        time.sleep(0.2)
        assert server.requests[-1] == [
            "loadfile",
            "https://cdn/x.m3u8",
            "replace",
            -1,
            "start=137.400",
        ]
    finally:
        client.close()
        server.close()


def test_get_property_round_trip(tmp_path: Path):
    client, server = _client(tmp_path)
    try:
        assert client.get("time-pos") == 137.5
    finally:
        client.close()
        server.close()


def test_client_message_reaches_the_callback(tmp_path: Path):
    received: list[list[str]] = []
    client, server = _client(tmp_path)
    client.on_client_message = received.append
    try:
        server.push_client_message("aniworld-cmd", "language", "eng-sub")
        deadline = time.time() + 3
        while not received and time.time() < deadline:
            time.sleep(0.02)
        assert received == [["aniworld-cmd", "language", "eng-sub"]]
    finally:
        client.close()
        server.close()


def test_socket_path_is_short_enough_for_af_unix():
    path = mpvctl.default_socket_path()
    assert len(path) < 100, path


# -- preset selection ---------------------------------------------------


def test_auto_preset_matches_the_source():
    assert shaders.auto_preset(480) == "a"
    assert shaders.auto_preset(720) == "a"
    assert shaders.auto_preset(1080) == "restore"
    assert shaders.auto_preset(None) == shaders.DEFAULT


def test_every_preset_only_references_shipped_files():
    shipped = {p.name for p in (Path(mpvctl.__file__).parent / "mpv" / "shaders").iterdir()}
    for _name, files in shaders.PRESETS.items():
        missing = set(files) - shipped
        assert not missing, missing


def test_glsl_value_is_absolute_and_colon_separated(tmp_path: Path):
    value = shaders.glsl_value("restore", tmp_path)
    assert value.split(":")[0] == str(tmp_path / "Anime4K_Clamp_Highlights.glsl")


# -- switch logic -------------------------------------------------------


class _StubMpv:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.time = 420.0

    def get(self, prop, timeout=3.0):
        return self.time

    def set(self, prop, value):
        self.calls.append(("set", prop, value))

    def loadfile(self, url, start=None, options=None):
        self.calls.append(("loadfile", url, start))

    def script_message(self, script, *args):
        self.calls.append(("osd", args))


def _session(tmp_path: Path) -> playback.Session:
    args = type("A", (), {"anime4k": "restore", "mpv_args": None, "mpv_config": "user"})()
    session = playback.Session(
        series=Series(title="Frieren", slug="frieren", url=""),
        episode=Episode(series_slug="frieren", url="", season=1, number=1),
        hosters=[Hoster("VOE", 1, "ger-dub", "u1"), Hoster("VOE", 3, "eng-sub", "u3")],
        stream=Stream(url="https://cdn/a.m3u8", headers={"Referer": "https://ref-a"}),
        hoster=Hoster("VOE", 1, "ger-dub", "u1"),
        language="ger-dub",
        args=args,
        mpv_path="mpv",
    )
    session.mpv = _StubMpv()  # type: ignore[assignment]
    session._fetch_variants = lambda stream: []  # type: ignore[assignment]
    return session


def test_language_switch_reloads_at_the_same_position(tmp_path: Path, monkeypatch):
    session = _session(tmp_path)
    monkeypatch.setattr(
        resolve,
        "resolve_stream",
        lambda hosters, language=None, report=None: (
            Stream(url="https://cdn/b.m3u8", headers={"Referer": "https://ref-b"}),
            Hoster("VOE", 3, "eng-sub", "u3"),
        ),
    )
    session._switch("language", "eng-sub")

    stub = session.mpv
    assert ("set", "http-header-fields", "Referer: https://ref-b") in stub.calls
    assert ("loadfile", "https://cdn/b.m3u8", 420.0) in stub.calls
    assert session.language == "eng-sub"
    assert session.stream.url == "https://cdn/b.m3u8"


def test_failed_switch_keeps_the_current_stream(tmp_path: Path, monkeypatch):
    session = _session(tmp_path)
    monkeypatch.setattr(
        resolve, "resolve_stream", lambda *a, **kw: (None, None)
    )
    session._switch("language", "eng-sub")

    stub = session.mpv
    assert not [c for c in stub.calls if c[0] == "loadfile"]
    assert session.stream.url == "https://cdn/a.m3u8"
    assert session.busy is False


def test_shader_switch_is_instant_and_needs_no_reload(tmp_path: Path):
    session = _session(tmp_path)
    session.shader_dir = tmp_path
    session._switch("shader", "off")

    stub = session.mpv
    assert ("set", "glsl-shaders", "") in stub.calls
    assert not [c for c in stub.calls if c[0] == "loadfile"]
    assert session.preset == "off"


def test_switch_updates_the_user_agent_property_too(tmp_path: Path, monkeypatch):
    """A reload cannot reuse argv, so the UA has to move into a property."""
    session = _session(tmp_path)
    monkeypatch.setattr(
        resolve,
        "resolve_stream",
        lambda hosters, language=None, report=None: (
            Stream(
                url="https://cdn/b.m3u8",
                headers={"Referer": "https://ref-b", "User-Agent": "UA-b"},
            ),
            Hoster("VOE", 3, "eng-sub", "u3"),
        ),
    )
    session._switch("language", "eng-sub")

    stub = session.mpv
    assert ("set", "user-agent", "UA-b") in stub.calls
    # ...and never duplicated into the header list.
    fields = [c for c in stub.calls if c[0] == "set" and c[1] == "http-header-fields"]
    assert fields == [("set", "http-header-fields", "Referer: https://ref-b")]


def test_handle_strips_the_message_name_from_client_message_args(tmp_path: Path, monkeypatch):
    """mpv's client-message args are ['aniworld-cmd', <action>, <value>]."""
    session = _session(tmp_path)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(session, "_switch", lambda kind, token: seen.append((kind, token)))

    session._handle(["aniworld-cmd", "language", "eng-sub"])
    assert seen == [("language", "eng-sub")]

    session._handle(["aniworld-cmd", "shader"])  # too short: ignored
    assert seen == [("language", "eng-sub")]


def test_explicit_preset_overrides_the_source(tmp_path: Path):
    """--anime4k fast must win over the height-based choice; junk falls back."""
    session = _session(tmp_path)
    session.shader_dir = tmp_path
    session.args.anime4k = "fast"
    assert session._initial_preset() == "fast"

    session.args.anime4k = "no-such-preset"
    assert session._initial_preset() == shaders.DEFAULT

    session.args.anime4k = "auto"
    assert session._initial_preset() == shaders.auto_preset(None)
