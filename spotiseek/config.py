"""Configuration for SpotiSeek.

Resolution order (highest priority first): explicit constructor arguments
(from CLI flags) > environment variables / ``.env`` > built-in defaults.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv, set_key

from .models import MatchStrictness

DEFAULT_ENV_FILE = ".env"
DEFAULT_SEARCH_TIMEOUT = 15.0
DEFAULT_SOULSEEK_USERNAME = "Sklyvan"
DEFAULT_SOULSEEK_PASSWORD = "12345"


def default_download_dir() -> Path:
    """Return the operating system's Downloads folder.

    Works on macOS, Windows and Linux. On Linux the XDG user-dirs config is
    honored (so localized/relocated Downloads folders are respected); elsewhere,
    and as a fallback, ``~/Downloads`` is used.
    """
    home = Path.home()
    if sys.platform.startswith("linux"):
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
        dirs_file = Path(config_home) / "user-dirs.dirs"
        try:
            for line in dirs_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("XDG_DOWNLOAD_DIR"):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    raw = raw.replace("$HOME", str(home))
                    resolved = Path(os.path.expandvars(raw)).expanduser()
                    if str(resolved):
                        return resolved
        except OSError:
            pass
    return home / "Downloads"


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value else None


def save_env(
    values: dict[str, str],
    env_file: str | os.PathLike[str] = DEFAULT_ENV_FILE,
) -> Path:
    """Persist the given key/value pairs to a ``.env`` file.

    Existing keys are updated in place and other keys are preserved (via
    python-dotenv's ``set_key``); the file is created if it does not exist.
    Also updates ``os.environ`` so a subsequent :meth:`Config.load` in the same
    process sees the new values. Returns the path written.
    """
    path = Path(env_file)
    path.touch(exist_ok=True)
    for key, value in values.items():
        value = value or ""
        set_key(str(path), key, value)
        os.environ[key] = value
    return path


@dataclass(slots=True)
class Config:
    """Runtime configuration resolved from flags, env and defaults."""

    # Spotify
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None

    # Soulseek
    soulseek_username: str = DEFAULT_SOULSEEK_USERNAME
    soulseek_password: str = DEFAULT_SOULSEEK_PASSWORD

    # Behaviour
    output_dir: Path = field(default_factory=default_download_dir)
    parallel: int = 1
    match_strictness: MatchStrictness = MatchStrictness.BALANCED
    search_timeout: float = DEFAULT_SEARCH_TIMEOUT
    min_bitrate: int | None = None
    tag: bool = True
    dry_run: bool = False
    extended_mix: bool = False

    @property
    def has_spotify_credentials(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @classmethod
    def load(
        cls,
        *,
        output_dir: str | os.PathLike[str] | None = None,
        parallel: int | None = None,
        match_strictness: MatchStrictness | str | None = None,
        search_timeout: float | None = None,
        min_bitrate: int | None = None,
        tag: bool | None = None,
        dry_run: bool | None = None,
        extended_mix: bool | None = None,
        soulseek_username: str | None = None,
        soulseek_password: str | None = None,
        env_file: str | os.PathLike[str] | None = None,
    ) -> "Config":
        """Build a Config, layering CLI overrides over env/.env over defaults."""
        # ``load_dotenv`` never overrides variables already present in the
        # environment, which keeps real env vars authoritative over the file.
        load_dotenv(dotenv_path=env_file, override=False)

        cfg = cls(
            spotify_client_id=_env("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=_env("SPOTIFY_CLIENT_SECRET"),
            soulseek_username=(
                soulseek_username
                or _env("SOULSEEK_USERNAME")
                or DEFAULT_SOULSEEK_USERNAME
            ),
            soulseek_password=(
                soulseek_password
                or _env("SOULSEEK_PASSWORD")
                or DEFAULT_SOULSEEK_PASSWORD
            ),
        )

        if output_dir is not None:
            cfg.output_dir = Path(output_dir)
        if parallel is not None:
            cfg.parallel = max(1, parallel)
        if match_strictness is not None:
            cfg.match_strictness = MatchStrictness(match_strictness)
        if search_timeout is not None:
            cfg.search_timeout = search_timeout
        if min_bitrate is not None:
            cfg.min_bitrate = min_bitrate
        if tag is not None:
            cfg.tag = tag
        if dry_run is not None:
            cfg.dry_run = dry_run
        if extended_mix is not None:
            cfg.extended_mix = extended_mix

        return cfg
