"""Core data models shared across SpotiSeek."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

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


def _clean(text: str) -> str:
    """Collapse whitespace and strip a string safely."""
    return re.sub(r"\s+", " ", text or "").strip()


# Featured-artist segments (Soulseek filenames rarely list them, so they hurt
# search recall): "(feat. X)", "[featuring X]", " - with X".
_FEAT_RE = re.compile(
    r"\s*[\(\[][^)\]]*\b(?:feat|ft|featuring|with)\b\.?[^)\]]*[\)\]]", re.IGNORECASE
)
# Remaster / mono / stereo qualifiers appended by Spotify, e.g.
# "Song - Remastered 2011", "Song - 2011 Remaster", "Song - Mono Version".
_REMASTER_RE = re.compile(
    r"\s*[-(\[]\s*(?:\d{4}\s*)?(?:re-?master(?:ed)?|mono|stereo)"
    r"(?:\s*version)?(?:\s*\d{4})?\s*[)\]]?\s*$",
    re.IGNORECASE,
)


def _search_title(title: str) -> str:
    """Strip featured-artist and remaster noise to improve Soulseek recall."""
    cleaned = _FEAT_RE.sub("", title or "")
    cleaned = _REMASTER_RE.sub("", cleaned)
    cleaned = _clean(cleaned)
    return cleaned or _clean(title)


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
    def search_query(self) -> str:
        """Query used to search Soulseek: 'primary artist title'.

        The title is cleaned of featured-artist and remaster qualifiers, which
        Soulseek filenames rarely include; the matcher still validates against
        the full metadata, so recall improves without hurting precision.
        """
        return _clean(f"{self.primary_artist} {_search_title(self.title)}")

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

    @property
    def ok(self) -> bool:
        return self.status in (DownloadStatus.DOWNLOADED, DownloadStatus.DRY_RUN)
