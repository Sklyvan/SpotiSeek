"""Configuration for SpotiSeek.

Resolution order (highest priority first): explicit constructor arguments
(from CLI flags) > environment variables / ``.env`` > built-in defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .models import MatchStrictness

DEFAULT_OUTPUT_DIR = "downloads"
DEFAULT_SEARCH_TIMEOUT = 15.0
DEFAULT_SOULSEEK_USERNAME = "Sklyvan"
DEFAULT_SOULSEEK_PASSWORD = "12345"


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value else None


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
    output_dir: Path = field(default_factory=lambda: Path(DEFAULT_OUTPUT_DIR))
    parallel: int = 1
    match_strictness: MatchStrictness = MatchStrictness.BALANCED
    search_timeout: float = DEFAULT_SEARCH_TIMEOUT
    min_bitrate: int | None = None
    tag: bool = True
    dry_run: bool = False

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

        return cfg
