"""M13 tests: the bundled mpv pack (materialisation, overlay, modes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aniworld_cli import mpv_pack, shaders


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


def test_modernz_conf_is_seeded_once_and_never_clobbered(tmp_path: Path, monkeypatch):
    """Users edit modernz.conf; a pack update must not undo that."""
    dest = mpv_pack.materialize(tmp_path / "mpv")
    assert dest is not None
    conf = dest / "script-opts" / "modernz.conf"
    assert conf.is_file()
    assert "custom_button=yes" in conf.read_text(encoding="utf-8")

    conf.write_text("custom_button=no\n", encoding="utf-8")
    monkeypatch.setattr(mpv_pack, "PACK_VERSION", "999")
    mpv_pack.materialize(tmp_path / "mpv")
    assert conf.read_text(encoding="utf-8") == "custom_button=no\n"
    # ...while ordinary pack files are still refreshed.
    assert "custom_button" not in (dest / "mpv.conf").read_text(encoding="utf-8")


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
