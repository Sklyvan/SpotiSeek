"""Core data models shared across SpotiSeek."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from . import version
from .version import _clean

# Extensions we consider lossless for scoring purposes.
LOSSLESS_EXTENSIONS = frozenset({"flac", "wav", "aiff", "aif", "ape", "alac"})
# Extensions we accept as audio at all.
AUDIO_EXTENSIONS = frozenset(
    LOSSLESS_EXTENSIONS | {"mp3", "m4a", "aac", "ogg", "opus", "wma"}
)


class SpotifyKind(str, Enum):
    """The type of entity a Spotify URL points to."""

    TRACK = "track"
    ALBUM = "album"
    PLAYLIST = "playlist"


class MatchStrictness(str, Enum):
    """How aggressively the matcher accepts Soulseek candidates."""

    STRICT = "strict"
    BALANCED = "balanced"
    LENIENT = "lenient"


class MetadataSource(str, Enum):
    """Which provider produced the metadata."""

    WEB_API = "web_api"
    EMBED = "embed"


class DownloadStatus(str, Enum):
    """Outcome of processing a single track."""

    DOWNLOADED = "downloaded"
    SKIPPED_NO_RESULTS = "skipped_no_results"
    SKIPPED_NO_MATCH = "skipped_no_match"
    FAILED = "failed"
    DRY_RUN = "dry_run"


# Version-qualifier parsing (search-query cleanup, title classification) now
# lives in :mod:`spotiseek.version`, the single source of truth for version
# vocabulary. ``Track`` delegates to it below.


@dataclass(slots=True)
class Track:
    """A normalized track from any metadata provider."""

    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    cover_url: str | None = None
    isrc: str | None = None
    spotify_id: str | None = None
    # Lazily-computed version classification; not part of identity/repr so every
    # existing ``Track(...)`` construction and equality is unaffected.
    _version: "version.VersionInfo | None" = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def artist_string(self) -> str:
        return ", ".join(a for a in self.artists if a)

    @property
    def duration_s(self) -> float | None:
        return self.duration_ms / 1000.0 if self.duration_ms else None

    @property
    def version(self) -> "version.VersionInfo":
        """The parsed version qualifier of this track's title (cached)."""
        if self._version is None:
            self._version = version.classify(self.title, frozenset(self.artists))
        return self._version

    @property
    def search_query(self) -> str:
        """Query used to search Soulseek: 'primary artist title'.

        The title is cleaned of featured-artist and remaster qualifiers, which
        Soulseek filenames rarely include; the matcher still validates against
        the full metadata, so recall improves without hurting precision.
        """
        return _clean(
            f"{self.primary_artist} "
            f"{version.strip_for_search(self.title, frozenset(self.artists))}"
        )

    @property
    def display(self) -> str:
        return _clean(f"{self.artist_string} - {self.title}")


@dataclass(slots=True)
class Candidate:
    """A single downloadable file offered by a Soulseek peer."""

    username: str
    filename: str  # remote path as shared by the peer
    filesize: int = 0
    extension: str = ""  # lowercase, no leading dot
    bitrate: int | None = None  # kbps
    duration: int | None = None  # seconds
    sample_rate: int | None = None  # Hz
    bit_depth: int | None = None  # bits
    vbr: bool | None = None
    has_free_slots: bool = False
    avg_speed: int = 0  # bytes/sec advertised by the peer
    queue_size: int = 0
    score: float = 0.0  # filled in by the matcher

    @property
    def basename(self) -> str:
        # Soulseek paths use backslashes (Windows peers) or forward slashes.
        return re.split(r"[\\/]", self.filename)[-1]

    @property
    def folder(self) -> str:
        """The containing folder name (last path segment before the file)."""
        parts = [p for p in re.split(r"[\\/]", self.filename) if p]
        return parts[-2] if len(parts) >= 2 else ""

    @property
    def is_lossless(self) -> bool:
        return self.extension in LOSSLESS_EXTENSIONS

    @property
    def is_audio(self) -> bool:
        return self.extension in AUDIO_EXTENSIONS


@dataclass(slots=True)
class DownloadResult:
    """The outcome of attempting to obtain one track."""

    track: Track
    status: DownloadStatus
    candidate: Candidate | None = None
    path: str | None = None
    error: str | None = None
    extended: bool = False  # True when an Extended Mix was chosen/downloaded
    source: str | None = None  # fallback provider name (e.g. "tidal"), if used

    @property
    def ok(self) -> bool:
        return self.status in (DownloadStatus.DOWNLOADED, DownloadStatus.DRY_RUN)
