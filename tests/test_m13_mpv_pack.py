"""M13 tests: the bundled mpv pack (materialisation, overlay, modes)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from aniworld_cli import mpv_pack, shaders


def test_every_button_target_actually_exists():
    """Every script-binding the shipped configs use must exist in the script.

    A missing binding is silent: the button fires, mpv answers "can't find
    script", and nothing happens. A Lua error is worse -- mpv terminates the
    script, after which every button stops working until the player restarts.
    """
    pack_root = mpv_pack.pack_source()
    lua = (pack_root / "scripts" / "aniworld.lua").read_text(encoding="utf-8")
    bound = set(re.findall(r'mp\.add_key_binding\(\s*nil\s*,\s*"([\w-]+)"', lua))
    assert "toggle-shaders" in bound and "menu-shaders" in bound

    used = set()
    for name in ("script-opts/modernz.conf", "input.conf"):
        text = (pack_root / name).read_text(encoding="utf-8")
        used |= set(re.findall(r"script-binding\s+aniworld/([\w-]+)", text))
    assert used, "no aniworld bindings referenced at all?"
    missing = used - bound
    assert not missing, f"config references bindings the script does not define: {missing}"


def test_materialize_copies_the_pack_and_creates_the_overlay(tmp_path: Path):
    dest = mpv_pack.materialize(tmp_path / "mpv")
    assert dest is not None
    assert (dest / "mpv.conf").is_file()
    assert (dest / "input.conf").is_file()
    assert (dest / "scripts" / "aniworld.lua").is_file()
    assert (dest / "shaders").is_dir()
    assert (dest / mpv_pack.USER_OVERLAY).is_file()


def test_materialize_is_idempotent_and_keeps_user_edits(tmp_path: Path):
    dest = mpv_pack.materialize(tmp_path / "mpv")
    assert dest is not None
    overlay = dest / mpv_pack.USER_OVERLAY
    overlay.write_text("volume=40\n", encoding="utf-8")
    again = mpv_pack.materialize(tmp_path / "mpv")
    assert again == dest
    assert overlay.read_text(encoding="utf-8") == "volume=40\n"


def test_materialize_refreshes_when_the_pack_version_changes(tmp_path: Path, monkeypatch):
    dest = mpv_pack.materialize(tmp_path / "mpv")
    assert dest is not None
    (dest / "mpv.conf").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(mpv_pack, "PACK_VERSION", "999")
    mpv_pack.materialize(tmp_path / "mpv")
    assert "stale" not in (dest / "mpv.conf").read_text(encoding="utf-8")


def test_modernz_conf_edit_is_never_clobbered(tmp_path: Path, monkeypatch):
    """Users edit modernz.conf; a pack update must not undo that."""
    dest = mpv_pack.materialize(tmp_path / "mpv")
    assert dest is not None
    conf = dest / "script-opts" / "modernz.conf"
    assert conf.is_file()
    text = conf.read_text(encoding="utf-8")
    assert "custom_button1=yes" in text        # A4K: Anime4K menu
    assert "custom_button2=yes" in text        # K: key bindings
    assert "custom_button3=yes" in text        # Lang: language menu
    # Switching audio tracks cannot work here (one track, languages are separate
    # encodes), so the track button is off and Lang does the job instead.
    assert "audio_tracks_button=no" in text

    conf.write_text("custom_button1=no\n", encoding="utf-8")
    monkeypatch.setattr(mpv_pack, "PACK_VERSION", "999")
    mpv_pack.materialize(tmp_path / "mpv")
    assert conf.read_text(encoding="utf-8") == "custom_button1=no\n"
    # ...while ordinary pack files are still refreshed.
    assert "custom_button" not in (dest / "mpv.conf").read_text(encoding="utf-8")


def test_untouched_modernz_conf_gets_new_defaults(tmp_path: Path, monkeypatch):
    """A user who never edited the file must still receive pack updates.

    Seeding a file once used to freeze it at its first version, so a new default
    -- a new button, a renamed option -- could never reach an existing install.
    The shipped content is hashed instead, and only an edit blocks the refresh.
    """
    pack = tmp_path / "pack"
    shutil.copytree(mpv_pack.pack_source(), pack)
    monkeypatch.setattr(mpv_pack, "pack_source", lambda: pack)
    dest = mpv_pack.materialize(tmp_path / "mpv")
    conf = dest / "script-opts" / "modernz.conf"

    def ship(text: str) -> None:
        (pack / "script-opts" / "modernz.conf").write_text(text, encoding="utf-8")
        monkeypatch.setattr(mpv_pack, "PACK_VERSION", mpv_pack.PACK_VERSION + text)

    ship("custom_button1=yes  # v2\n")
    mpv_pack.materialize(tmp_path / "mpv")
    assert conf.read_text(encoding="utf-8") == "custom_button1=yes  # v2\n"

    # Now the user edits it: from here on the file is theirs alone.
    conf.write_text("custom_button1=no\n", encoding="utf-8")
    ship("custom_button1=yes  # v3\n")
    mpv_pack.materialize(tmp_path / "mpv")
    assert conf.read_text(encoding="utf-8") == "custom_button1=no\n"


def test_materialize_returns_none_without_a_source(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mpv_pack, "pack_source", lambda: tmp_path / "missing")
    assert mpv_pack.materialize(tmp_path / "mpv") is None


def test_user_mode_disables_the_bundled_config():
    assert mpv_pack.resolve("user", "mpv") is None


def test_path_mode_is_used_verbatim(tmp_path: Path):
    assert mpv_pack.resolve(str(tmp_path), "mpv") == tmp_path.resolve()


def test_bundled_mode_points_at_a_usable_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mpv_pack, "app_data_dir", lambda: tmp_path)
    resolved = mpv_pack.resolve("bundled", "mpv")
    assert resolved is not None
    assert (resolved / "mpv.conf").is_file()


@pytest.mark.skipif(
    not __import__("sys").platform.startswith("win"),
    reason="portable_config is a Windows-only mpv feature",
)
def test_windows_portable_config_wins(tmp_path: Path):  # pragma: no cover
    (tmp_path / "mpv").mkdir()
    (tmp_path / "mpv" / "portable_config").mkdir()
    (tmp_path / "mpv" / "portable_config" / "mpv.conf").write_text("", encoding="utf-8")
    assert mpv_pack.resolve("bundled", str(tmp_path / "mpv" / "mpv.exe")) == (
        tmp_path / "mpv" / "portable_config"
    )


def test_shader_dir_prefers_the_config_dir(tmp_path: Path):
    (tmp_path / "shaders").mkdir()
    assert mpv_pack.shader_dir(tmp_path) == tmp_path / "shaders"


def test_shader_dir_falls_back_to_the_pack(tmp_path: Path):
    """Windows portable_config has no shaders; the bundled chain must still load."""
    (tmp_path / "mpv.conf").write_text("", encoding="utf-8")
    resolved = mpv_pack.shader_dir(tmp_path)
    assert resolved is not None
    assert (resolved / "Anime4K_Clamp_Highlights.glsl").is_file()


def test_shader_dir_is_none_without_a_pack(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mpv_pack, "pack_source", lambda: tmp_path / "missing")
    assert mpv_pack.shader_dir(tmp_path) is None


def test_every_preset_has_a_menu_hint():
    """The shader menu explains each mode; a missing hint is a silent gap."""
    assert set(shaders.HINTS) == set(shaders.PRESETS)
    for token, label in shaders.LABELS:
        hint = shaders.HINTS[token]
        assert hint, token
        # Drawn at fs19 inside an 860 px panel: keep it on one line.
        assert len(hint) <= 56, (token, len(hint))
