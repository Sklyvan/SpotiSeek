"""Unit tests for configuration loading and precedence."""

from __future__ import annotations

from pathlib import Path

from spotiseek.config import (
    DEFAULT_SOULSEEK_PASSWORD,
    DEFAULT_SOULSEEK_USERNAME,
    Config,
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
    assert cfg.soulseek_username == DEFAULT_SOULSEEK_USERNAME
    assert cfg.soulseek_password == DEFAULT_SOULSEEK_PASSWORD
    assert cfg.parallel == 1
    assert cfg.match_strictness is MatchStrictness.BALANCED
    assert cfg.tag is True
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
        output_dir="/tmp/out",
    )
    assert cfg.soulseek_username == "cliuser"
    assert cfg.parallel == 4
    assert cfg.match_strictness is MatchStrictness.STRICT
    assert cfg.min_bitrate == 256
    assert cfg.tag is False
    assert str(cfg.output_dir) == "/tmp/out"


def test_parallel_floor(monkeypatch, tmp_path) -> None:
    _clear_env(monkeypatch)
    cfg = Config.load(env_file=_missing_env_file(tmp_path), parallel=0)
    assert cfg.parallel == 1
