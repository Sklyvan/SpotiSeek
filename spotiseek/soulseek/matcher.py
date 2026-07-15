"""Score and rank Soulseek candidates against a Spotify track.

The matcher is pure and deterministic (no I/O), which keeps it easy to unit
test. It filters out unusable candidates and returns the survivors ranked best
first, with ``Candidate.score`` populated.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from rapidfuzz import fuzz

from ..models import Candidate, MatchStrictness, Track

# Minimum normalized name-similarity (0..1) required to accept a candidate.
_NAME_THRESHOLD = {
    MatchStrictness.STRICT: 0.80,
    MatchStrictness.BALANCED: 0.58,
    MatchStrictness.LENIENT: 0.42,
}
# Duration tolerance in seconds (None = do not filter on duration).
_DURATION_TOLERANCE_S = {
    MatchStrictness.STRICT: 7,
    MatchStrictness.BALANCED: 15,
    MatchStrictness.LENIENT: None,
}

# Relative weights of the score components (sum to 1.0).
_W_NAME = 0.50
_W_FORMAT = 0.30
_W_AVAIL = 0.20

# --prefer-longest: how much a candidate's length sways ranking (the rest stays
# name/format/availability), and the longest sane duration accepted relative to
# the Spotify track (guards against whole-album "megamix" files matching loosely).
_W_LENGTH = 0.30
_PREFER_LONGEST_MAX_RATIO = 3.0

# Tokens that mark an *alternate/derivative* version — i.e. NOT the official
# extended mix. A file labelled "(Extended Mix)" that also carries any of these
# (e.g. "RetroVision Flip [Extended Mix]") is a remix/edit, not the canonical
# extended mix, so it is rejected in --extended-mix mode.
_ALT_VERSION_KEYWORDS = frozenset({
    "remix", "remixed", "rmx", "rework", "reworked", "flip", "bootleg", "boot",
    "vip", "mashup", "refix", "reedit", "dub", "instrumental", "inst",
    "acapella", "acappella", "karaoke", "live", "cover", "tribute", "nightcore",
    "slowed", "sped", "edit", "edits", "remake", "reprise", "demo",
})

# Non-descriptive tokens ignored when judging how "clean" an extended-mix name
# is (so track numbers, formats and quality markers don't count as extras).
_NOISE_TOKENS = frozenset({
    "extended", "mix", "the", "a", "an", "flac", "wav", "wave", "mp3", "m4a",
    "aac", "ogg", "opus", "aiff", "aif", "alac", "kbps", "kbit", "khz", "hz",
    "bit", "cd", "web", "vinyl", "hd", "hq", "lossless", "stereo", "remaster",
    "remastered", "original", "feat", "ft", "featuring", "prod",
})


@lru_cache(maxsize=4096)
def _normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace.

    Cached: scoring normalizes the same target string once per candidate and the
    early-stop check re-normalizes the same candidate names on every poll, so a
    single search re-derives the same values many times.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_extended_mix(name: str) -> bool:
    """True if a filename looks like an Extended Mix (has both 'extended' and 'mix')."""
    normalized = _normalize(name)
    return "extended" in normalized and "mix" in normalized


def _extended_signal(candidate: Candidate) -> bool:
    """True if the candidate looks like an extended/full version.

    Strict on the filename ('extended' + 'mix'), looser on the containing folder
    ('extended' alone): batches are often foldered as "… (Extended Mixes)" or
    "… (Extended Edition)" while the individual files are named plainly
    (e.g. "11 - The Black Demon.flac").
    """
    if is_extended_mix(candidate.basename):
        return True
    return "extended" in _normalize(candidate.folder)


def _extra_tokens(track: Track, candidate: Candidate) -> set[str]:
    """Tokens in the filename that aren't the artist, title, or common noise.

    These are the "descriptive extras" — a remixer name, an alternate-version
    label, etc. The canonical extended mix has (almost) none of them.
    """
    base = set(_normalize(f"{track.artist_string} {track.title}").split())
    extras: set[str] = set()
    for token in _normalize(candidate.basename).split():
        if token in base or token in _NOISE_TOKENS or token.isdigit():
            continue
        extras.add(token)
    return extras


def is_official_extended_mix(track: Track, candidate: Candidate) -> bool:
    """True if the candidate is an Extended Mix with no alternate-version markers."""
    if not _extended_signal(candidate):
        return False
    return not (_extra_tokens(track, candidate) & _ALT_VERSION_KEYWORDS)


def _name_score(track: Track, candidate: Candidate) -> float:
    """Fuzzy similarity (0..1) of 'artist title' vs the candidate filename.

    Uses the full remote path (folder names often carry the artist/album) but
    weights the basename higher, and requires the title itself to be present.
    """
    target = _normalize(f"{track.artist_string} {track.title}")
    base = _normalize(candidate.basename)
    full = _normalize(candidate.filename.replace("\\", " ").replace("/", " "))

    base_score = fuzz.token_set_ratio(target, base) / 100.0
    full_score = fuzz.token_set_ratio(target, full) / 100.0

    # Guard against matching the artist alone (e.g. a whole-discography folder)
    # by also checking the title tokens specifically against the basename.
    title_score = fuzz.token_set_ratio(_normalize(track.title), base) / 100.0

    return max(base_score, full_score) * 0.6 + title_score * 0.4


def _format_score(candidate: Candidate) -> float:
    """Rank by format/quality: lossless > high-bitrate MP3 > lower > other."""
    if candidate.is_lossless:
        return 1.0
    bitrate = candidate.bitrate or 0
    if bitrate >= 320:
        return 0.75
    if bitrate >= 256:
        return 0.60
    if bitrate >= 192:
        return 0.45
    if bitrate >= 128:
        return 0.30
    return 0.20 if candidate.is_audio else 0.0


def _availability_score(candidate: Candidate) -> float:
    """Prefer peers with a free slot, short queue and decent speed."""
    score = 0.6 if candidate.has_free_slots else 0.0
    score -= min(candidate.queue_size, 50) / 50.0 * 0.3
    score += min(candidate.avg_speed / 1_000_000.0, 1.0) * 0.4  # cap at ~1 MB/s
    return max(0.0, min(score, 1.0))


def _passes_duration(
    track: Track,
    candidate: Candidate,
    tolerance: int | None,
    prefer_longest: bool = False,
) -> bool:
    if tolerance is None:
        return True
    if not track.duration_s or not candidate.duration:
        return True  # cannot compare -> do not reject
    if prefer_longest:
        # Keep out shorter previews/radio edits, but allow longer full/extended
        # versions — up to a sane multiple so a whole-album mix isn't accepted.
        return (
            track.duration_s - tolerance
            <= candidate.duration
            <= track.duration_s * _PREFER_LONGEST_MAX_RATIO
        )
    return abs(candidate.duration - track.duration_s) <= tolerance


def has_ready_lossless_match(
    track: Track,
    candidates: list[Candidate],
    strictness: MatchStrictness = MatchStrictness.BALANCED,
    require_extended: bool = False,
    prefer_longest: bool = False,
) -> bool:
    """Cheap early-stop check for searching: is a lossless candidate with a free
    upload slot already available and matching?

    Short-circuits on the first qualifier and only fuzzy-matches lossless,
    free-slot candidates, so it is far cheaper than a full ``score_candidates``
    pass to run repeatedly while a search is still collecting results.
    """
    name_threshold = _NAME_THRESHOLD[strictness]
    tolerance = None if require_extended else _DURATION_TOLERANCE_S[strictness]
    for candidate in candidates:
        if not (candidate.is_lossless and candidate.has_free_slots):
            continue
        if require_extended and not is_official_extended_mix(track, candidate):
            continue
        if _name_score(track, candidate) < name_threshold:
            continue
        if _passes_duration(track, candidate, tolerance, prefer_longest):
            return True
    return False


def score_candidates(
    track: Track,
    candidates: list[Candidate],
    strictness: MatchStrictness = MatchStrictness.BALANCED,
    min_bitrate: int | None = None,
    require_extended: bool = False,
    prefer_longest: bool = False,
) -> list[Candidate]:
    """Return acceptable candidates ranked best-first, scores populated.

    When ``require_extended`` is set, only files that look like an Extended Mix
    are accepted, and the duration filter is skipped (extended mixes are longer
    than the Spotify-reported duration of the standard track).

    When ``prefer_longest`` is set, longer versions are accepted (only clearly
    shorter previews/edits are rejected) and a length term sways the ranking
    toward the longest good match — useful for grabbing full/extended versions
    that peers don't explicitly label "Extended Mix".
    """
    name_threshold = _NAME_THRESHOLD[strictness]
    tolerance = None if require_extended else _DURATION_TOLERANCE_S[strictness]

    ranked: list[Candidate] = []
    for candidate in candidates:
        if not candidate.is_audio:
            continue

        extras: set[str] = set()
        if require_extended:
            if not _extended_signal(candidate):
                continue
            extras = _extra_tokens(track, candidate)
            # Reject remixes/edits/etc. — favour the official extended mix.
            if extras & _ALT_VERSION_KEYWORDS:
                continue

        # Enforce a minimum bitrate for lossy files (lossless always passes).
        if (
            min_bitrate
            and not candidate.is_lossless
            and (candidate.bitrate or 0) < min_bitrate
        ):
            continue

        name = _name_score(track, candidate)
        if name < name_threshold:
            continue
        if not _passes_duration(track, candidate, tolerance, prefer_longest):
            continue

        fmt = _format_score(candidate)
        avail = _availability_score(candidate)
        combined = _W_NAME * name + _W_FORMAT * fmt + _W_AVAIL * avail
        if require_extended:
            # Tiebreak toward the cleanest official name (fewest descriptive
            # extras like an album/edition label sitting next to it).
            combined *= 0.85 + 0.15 / (1.0 + len(extras))
        candidate.score = round(combined * 100, 3)
        ranked.append(candidate)

    if prefer_longest:
        _apply_length_preference(ranked)

    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


def _apply_length_preference(ranked: list[Candidate]) -> None:
    """Blend a length term into each candidate's score (longest = biggest boost)."""
    max_dur = max((c.duration or 0) for c in ranked) if ranked else 0
    if max_dur <= 0:
        return  # no usable durations -> leave scores untouched
    for candidate in ranked:
        if not candidate.duration:
            # Length unknown for this peer: don't reward or penalize it. Blending
            # in a 0 length term would demote an otherwise-strong match purely
            # for not reporting its duration, so leave its base score intact.
            continue
        length_score = candidate.duration / max_dur
        base = candidate.score / 100.0
        blended = (1.0 - _W_LENGTH) * base + _W_LENGTH * length_score
        candidate.score = round(blended * 100, 3)
