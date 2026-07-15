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

# Order in which the lossless fallback tries streaming-service proxies.
DEFAULT_FALLBACK_PROVIDERS = ["tidal", "deezer", "amazon", "qobuz"]
# Proxy base URLs are intentionally empty: these third-party services rotate and
# go offline constantly, so there is no reliable default. Point the matching
# SPOTISEEK_<PROVIDER>_API_URL env var at a currently-working instance to enable
# a provider; unconfigured providers are skipped.
DEFAULT_TIDAL_API_URL = ""
DEFAULT_QOBUZ_API_URL = ""
DEFAULT_AMAZON_API_URL = ""
DEFAULT_DEEZER_API_URL = ""


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
                    if raw:  # an empty value must not resolve to "." (cwd)
                        return Path(os.path.expandvars(raw)).expanduser()
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
    # The file holds the Spotify secret and the Soulseek password in plaintext —
    # make it owner-only so other local users can't read it. Best-effort: some
    # filesystems / platforms (e.g. Windows) don't honor POSIX modes.
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
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

    # Soulseek (no built-in default — the user supplies their own login)
    soulseek_username: str = ""
    soulseek_password: str = ""

    # Behaviour
    output_dir: Path = field(default_factory=default_download_dir)
    parallel: int = 3
    match_strictness: MatchStrictness = MatchStrictness.BALANCED
    search_timeout: float = DEFAULT_SEARCH_TIMEOUT
    min_bitrate: int | None = None
    tag: bool = True
    dry_run: bool = False
    extended_mix: bool = False
    prefer_longest: bool = False

    # Lossless fallback (opt-in): fetch from streaming-service proxies via Odesli
    # when Soulseek can't deliver a track.
    fallback: bool = False
    fallback_providers: list[str] = field(
        default_factory=lambda: list(DEFAULT_FALLBACK_PROVIDERS)
    )
    tidal_api_url: str = DEFAULT_TIDAL_API_URL
    qobuz_api_url: str = DEFAULT_QOBUZ_API_URL
    amazon_api_url: str = DEFAULT_AMAZON_API_URL
    deezer_api_url: str = DEFAULT_DEEZER_API_URL

    @property
    def has_spotify_credentials(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @property
    def has_soulseek_credentials(self) -> bool:
        return bool(self.soulseek_username and self.soulseek_password)

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
        prefer_longest: bool | None = None,
        fallback: bool | None = None,
        fallback_providers: list[str] | None = None,
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
                soulseek_username or _env("SOULSEEK_USERNAME") or ""
            ),
            soulseek_password=(
                soulseek_password or _env("SOULSEEK_PASSWORD") or ""
            ),
            tidal_api_url=_env("SPOTISEEK_TIDAL_API_URL") or DEFAULT_TIDAL_API_URL,
            qobuz_api_url=_env("SPOTISEEK_QOBUZ_API_URL") or DEFAULT_QOBUZ_API_URL,
            amazon_api_url=_env("SPOTISEEK_AMAZON_API_URL") or DEFAULT_AMAZON_API_URL,
            deezer_api_url=_env("SPOTISEEK_DEEZER_API_URL") or DEFAULT_DEEZER_API_URL,
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
        if prefer_longest is not None:
            cfg.prefer_longest = prefer_longest
        if fallback is not None:
            cfg.fallback = fallback
        if fallback_providers is not None:
            cfg.fallback_providers = fallback_providers

        return cfg
