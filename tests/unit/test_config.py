"""Unit tests for configuration loading and precedence."""

from __future__ import annotations

from pathlib import Path

import os

from spotiseek.config import (
    Config,
    default_download_dir,
    save_env,
)
from spotiseek.models import MatchStrictness

_ENV_VARS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SOULSEEK_USERNAME",
    "SOULSEEK_PASSWORD",
]


def _clear_env(monkeypatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _missing_env_file(tmp_path: Path) -> str:
    # Point python-dotenv at a nonexistent file so it never reads the repo .env.
    return str(tmp_path / "nonexistent.env")


def test_defaults(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    cfg = Config.load(env_file=_missing_env_file(tmp_path))
    # No credentials are baked in — the user supplies their own Soulseek login.
    assert cfg.soulseek_username == ""
    assert cfg.soulseek_password == ""
    assert cfg.has_soulseek_credentials is False
    assert cfg.parallel == 1
    assert cfg.match_strictness is MatchStrictness.BALANCED
    assert cfg.tag is True
    assert cfg.extended_mix is False
    assert cfg.has_spotify_credentials is False


def test_env_populates_credentials(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SOULSEEK_USERNAME", "envuser")
    cfg = Config.load(env_file=_missing_env_file(tmp_path))
    assert cfg.has_spotify_credentials is True
    assert cfg.spotify_client_id == "cid"
    assert cfg.soulseek_username == "envuser"


def test_cli_overrides_env(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("SOULSEEK_USERNAME", "envuser")
    cfg = Config.load(
        env_file=_missing_env_file(tmp_path),
        soulseek_username="cliuser",
        parallel=4,
        match_strictness="strict",
        min_bitrate=256,
        tag=False,
        extended_mix=True,
        output_dir="/tmp/out",
    )
    assert cfg.soulseek_username == "cliuser"
    assert cfg.parallel == 4
    assert cfg.match_strictness is MatchStrictness.STRICT
    assert cfg.min_bitrate == 256
    assert cfg.tag is False
    assert cfg.extended_mix is True
    assert str(cfg.output_dir) == "/tmp/out"


def test_parallel_floor(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    cfg = Config.load(env_file=_missing_env_file(tmp_path), parallel=0)
    assert cfg.parallel == 1


def test_save_env_writes_and_preserves(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING_KEY=keepme\n")
    try:
        path = save_env(
            {"SPOTIFY_CLIENT_ID": "cid", "SOULSEEK_USERNAME": "user"},
            env_file=env_file,
        )
        content = env_file.read_text()
        assert path == env_file
        assert "EXISTING_KEY" in content  # unrelated keys are preserved
        assert "cid" in content and "SPOTIFY_CLIENT_ID" in content
        # Loading from that file reflects the saved values.
        cfg = Config.load(env_file=str(env_file))
        assert cfg.spotify_client_id == "cid"
        # os.environ is updated so a same-process reload sees it.
        assert os.environ["SPOTIFY_CLIENT_ID"] == "cid"
    finally:
        for key in ("SPOTIFY_CLIENT_ID", "SOULSEEK_USERNAME"):
            os.environ.pop(key, None)


def test_default_download_dir_uses_home_downloads(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert default_download_dir() == Path.home() / "Downloads"


def test_default_output_dir_is_downloads(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    cfg = Config.load(env_file=_missing_env_file(tmp_path))
    assert cfg.output_dir == default_download_dir()


def test_default_download_dir_linux_honors_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Media/DL"\n')
    assert default_download_dir() == Path.home() / "Media" / "DL"


def test_default_download_dir_linux_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no user-dirs.dirs present
    assert default_download_dir() == Path.home() / "Downloads"


def test_save_env_creates_missing_file(tmp_path) -> None:
    env_file = tmp_path / "sub" / ".env"
    env_file.parent.mkdir()
    try:
        save_env({"SOULSEEK_PASSWORD": "secret"}, env_file=env_file)
        assert env_file.exists()
        assert "SOULSEEK_PASSWORD" in env_file.read_text()
    finally:
        os.environ.pop("SOULSEEK_PASSWORD", None)
