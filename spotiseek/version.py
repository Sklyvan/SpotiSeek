"""Version-qualifier intelligence: classify a track title's version qualifier.

A track title carries version qualifiers in parentheses, brackets, or after a
dash — "(Radio Edit)", "[Extended Mix]", "- Original Mix", "(Uptempo Edit)",
"(Darren Styles Remix)". These qualifiers answer two *independent* questions:

  * **identity** — which recording? (the original, a remix, a VIP, an acoustic,
    a genre "edit", …)
  * **length** — which cut of it? (a short/radio edit, an extended/12" cut, or
    no length signal at all)

They are separate axes: "X (Darren Styles Remix) (Radio Edit)" is a remix
*identity* with a short *length*; "Extended Remix" is a remix that is already
long; "Original Mix" is the original at full length. A single flat enum cannot
represent those combinations, so :class:`VersionInfo` keeps them apart.

This module is the **single source of truth** for version vocabulary — the
matcher and downloader both consume it, so the meaning of a word like "edit"
is decided here, once. It is a *leaf* module: it operates on plain strings and
must never import :mod:`spotiseek.models` (which imports it), to avoid a cycle.

Decision table (case-insensitive; a genre word before "Edit" overrides SHORT):

    SHORT  (length-limited): Radio Edit, bare Edit, Single/Video/7" Edit,
           Mix Cut, Album Edit, Airplay/TV/Promo Edit  -> strippable, shorter
    LONG   (already full):   Extended Mix, Original Mix, Club Mix, Full Mix,
           12" Mix, Album Version, Continuous Mix       -> never re-append
    DERIVATIVE (a different recording): Remix/RMX, VIP, Bootleg, Flip, Refix,
           Rework, Mashup, Live, Acoustic, Instrumental, Acapella, Nightcore,
           Sped Up, Slowed+Reverb, Demo, Reprise, Cover, Remaster,
           "<Artist> Edit"                              -> preserve verbatim
    STYLE-EDIT ("<genre> Edit/Mix/Version"): Uptempo/Festival/Hardstyle/
           Rawstyle/Big Room/Club Edit, …               -> preserve verbatim

When several qualifiers coexist, identity precedence is
DERIVATIVE > STYLE-EDIT > LONG > SHORT.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- #
# Public types
# --------------------------------------------------------------------------- #
class Identity(str, Enum):
    """Which *recording* a qualifier denotes (independent of its length)."""

    ORIGINAL = "original"
    REMIX = "remix"
    VIP = "vip"
    BOOTLEG = "bootleg"
    MASHUP = "mashup"
    LIVE = "live"
    ACOUSTIC = "acoustic"
    INSTRUMENTAL = "instrumental"
    ACAPELLA = "acapella"
    NIGHTCORE = "nightcore"
    SPEEDMOD = "speedmod"  # sped up / slowed + reverb / chopped & screwed
    REMASTER = "remaster"
    STYLE_EDIT = "style_edit"  # "<genre> Edit" — a stylistic re-version
    OTHER = "other"  # recognized-but-unmapped derivative (cover, demo, dub, …)


class Length(str, Enum):
    """Which *cut* a qualifier denotes."""

    NEUTRAL = "neutral"
    SHORT = "short"
    LONG = "long"


class VersionKind(str, Enum):
    """Coarse label derived from (identity, length) — for readability/tests."""

    STANDARD = "standard"
    SHORT = "short"
    LONG = "long"
    DERIVATIVE = "derivative"
    STYLE_EDIT = "style_edit"


#: Identities the matcher treats as "a specific alternate recording" — a
#: candidate must carry these tokens to match, and a plain/original track must
#: reject them. ORIGINAL/REMASTER/OTHER are deliberately excluded (they are not
#: reliably labelled in peer filenames, so requiring/forbidding them over-rejects).
RESTRICTIVE_IDENTITIES = frozenset(
    {
        Identity.REMIX,
        Identity.VIP,
        Identity.BOOTLEG,
        Identity.MASHUP,
        Identity.LIVE,
        Identity.ACOUSTIC,
        Identity.INSTRUMENTAL,
        Identity.ACAPELLA,
        Identity.NIGHTCORE,
        Identity.SPEEDMOD,
        Identity.STYLE_EDIT,
    }
)


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """The classification of a single title's version qualifier(s)."""

    identity: Identity
    length: Length
    base_title: str  # the title with every classified qualifier removed
    qualifiers: tuple[str, ...] = ()  # raw qualifier segments, in title order
    tokens: frozenset[str] = frozenset()  # normalized identity/style words

    @property
    def kind(self) -> VersionKind:
        """A coarse single-axis label (STANDARD == original/other + neutral)."""
        if self.identity is Identity.STYLE_EDIT:
            return VersionKind.STYLE_EDIT
        if self.identity in RESTRICTIVE_IDENTITIES or self.identity in (
            Identity.REMASTER,
        ):
            return VersionKind.DERIVATIVE
        if self.length is Length.LONG:
            return VersionKind.LONG
        if self.length is Length.SHORT:
            return VersionKind.SHORT
        return VersionKind.STANDARD

    @property
    def is_restrictive(self) -> bool:
        """True if a matched candidate must carry this recording's tokens."""
        return self.identity in RESTRICTIVE_IDENTITIES

    @property
    def rejects_alternates(self) -> bool:
        """True if candidates that are a *different* specific recording (remix,
        VIP, …) should be rejected — i.e. we asked for the plain/original."""
        return self.identity in (Identity.ORIGINAL, Identity.REMASTER)


@dataclass(frozen=True, slots=True)
class ExtendedPlan:
    """How to pursue an Extended Mix for a given title (see :func:`plan_extended`)."""

    search_titles: tuple[str, ...]  # transformed first, then base fallback
    output_suffix: str | None  # e.g. "(Extended Mix)", or None to leave the name
    skip_note: str | None = None  # a log note when extended search is skipped


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _clean(text: str) -> str:
    """Collapse whitespace and strip a string safely."""
    return re.sub(r"\s+", " ", text or "").strip()


_QUOTE_MAP = {
    "“": '"', "”": '"', "″": '"',
    "‘": "'", "’": "'", "′": "'",
}
_DASH_RE = re.compile(r"\s[–—]\s")


def _normalize_title(title: str) -> str:
    """NFC-normalize, standardize quotes, and normalize spaced en/em dashes.

    Uses NFC (not NFKD) so real characters (``Alpha²``, ``½``) survive into
    the persisted base title / filename; NFKD folding is reserved for the
    matcher's *comparison* path only.
    """
    text = unicodedata.normalize("NFC", title or "")
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    text = _DASH_RE.sub(" - ", text)
    return _clean(text)


def _words(segment: str) -> list[str]:
    """Lowercased alphanumeric words of a segment (accents folded for matching)."""
    folded = unicodedata.normalize("NFKD", segment.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", folded)


# --------------------------------------------------------------------------- #
# Lexicons (the single source of truth)
# --------------------------------------------------------------------------- #
# SHORT: broadcast/format words that mark a length-limited cut. Bare "edit" is
# SHORT (a genre word before it flips it to STYLE-EDIT — handled separately).
_SHORT_HEADS = frozenset({"edit", "version", "mix", "cut", "size"})
_SHORT_PREFIXES = frozenset(
    {"radio", "short", "single", "video", "airplay", "promo", "tv", "album",
     "clean", "dirty", "cut", "mix", "pa"}
)
_SHORT_WHOLE = frozenset({"edit", "radio", "cut"})

# LONG: already the full/extended cut.
_LONG_PHRASES = (
    "extended mix", "extended version", "extended edit", "extended",
    "original mix", "original version", "original", "club mix", "club version",
    "full length", "full version", "full mix", "long version", "long edit",
    "maxi version", "maxi mix", "album version", "12 mix", "12 version",
    "continuous mix", "continuous dj mix",
)

# DERIVATIVE family -> identity. Presence of any of these keywords makes the
# segment a derivative (a different recording).
_DERIVATIVE_KEYWORDS: dict[str, Identity] = {
    "remix": Identity.REMIX, "rmx": Identity.REMIX, "remixed": Identity.REMIX,
    "rework": Identity.REMIX, "reworked": Identity.REMIX, "refix": Identity.REMIX,
    "rerub": Identity.REMIX, "redo": Identity.REMIX, "flip": Identity.REMIX,
    "vip": Identity.VIP,
    "bootleg": Identity.BOOTLEG, "boot": Identity.BOOTLEG, "bootie": Identity.BOOTLEG,
    "mashup": Identity.MASHUP, "blend": Identity.MASHUP,
    "live": Identity.LIVE,
    "acoustic": Identity.ACOUSTIC, "unplugged": Identity.ACOUSTIC,
    "stripped": Identity.ACOUSTIC, "orchestral": Identity.ACOUSTIC,
    "instrumental": Identity.INSTRUMENTAL, "inst": Identity.INSTRUMENTAL,
    "acapella": Identity.ACAPELLA, "acappella": Identity.ACAPELLA,
    "nightcore": Identity.NIGHTCORE,
    "slowed": Identity.SPEEDMOD, "reverb": Identity.SPEEDMOD,
    "sped": Identity.SPEEDMOD, "screwed": Identity.SPEEDMOD,
    "chopped": Identity.SPEEDMOD,
    "remaster": Identity.REMASTER, "remastered": Identity.REMASTER,
    "dub": Identity.OTHER, "dubplate": Identity.OTHER,
    "demo": Identity.OTHER, "reprise": Identity.OTHER, "interlude": Identity.OTHER,
    "cover": Identity.OTHER, "tribute": Identity.OTHER,
}

# STYLE-EDIT trigger words: a genre/style/set-context word before Edit/Mix/Version.
_GENRE_WORDS = frozenset(
    {
        # hard dance
        "uptempo", "rawstyle", "raw", "hardstyle", "frenchcore", "hardcore",
        "terror", "terrorcore", "speedcore", "gabber", "gabba", "freeform",
        "jumpstyle", "hardtek", "tekstyle", "rawphoric", "euphoric", "kick",
        "crossbreed",
        # set / stage context
        "festival", "mainstage", "anthem", "warmup", "closing", "peaktime",
        "mainfloor", "dancefloor", "floor", "set", "afterparty",
        # broader EDM
        "tech", "techno", "trance", "house", "deep", "future", "bigroom",
        "progressive", "prog", "electro", "melbourne", "bass", "bassline",
        "dubstep", "brostep", "riddim", "trap", "dnb", "jungle", "breakbeat",
        "breaks", "garage", "amapiano", "afro", "tribal", "minimal", "acid",
        "psytrance", "psy", "goa", "melodic", "organic", "hardtechno", "club",
    }
)
# Multi-word genre phrases, matched greedily before single words.
_GENRE_PHRASES = (
    "big room", "tech house", "deep house", "future house", "future bass",
    "drum and bass", "drum n bass", "hard techno", "hard trance",
    "uk garage", "speed garage", "melodic techno", "afro house", "peak time",
    "main stage", "warm up",
)

_FEAT_GUARD = frozenset({"feat", "ft", "featuring", "with", "prod", "presents",
                         "pres", "vs", "versus", "x"})
_HEAD_NOUNS = frozenset({"edit", "mix", "version"})


# --------------------------------------------------------------------------- #
# strip_for_search — behavior-preserving port of the former models._search_title
# --------------------------------------------------------------------------- #
_FEAT_RE = re.compile(
    r"\s*[\(\[][^)\]]*\b(?:feat|ft|featuring)\b\.?[^)\]]*[\)\]]", re.IGNORECASE
)
_REMASTER_RE = re.compile(
    r"\s*[-(\[]\s*(?:\d{4}\s*)?re-?master(?:ed)?"
    r"(?:\s*version)?(?:\s*\d{4})?\s*[)\]]?\s*$",
    re.IGNORECASE,
)
_VERSION_WORDS = frozenset({
    "radio", "edit", "edits", "edited", "extended", "original", "album",
    "single", "club", "version", "cut", "mix", "mixed",
})
_TRAIL_PAREN_RE = re.compile(r"\s*[\(\[]([^)\]]*)[\)\]]\s*$")
_TRAIL_DASH_RE = re.compile(r"\s[-–—]\s+([^-–—]+?)\s*$")


def _is_version_segment(segment: str) -> bool:
    words = re.findall(r"[a-z0-9]+", segment.lower())
    return bool(words) and all(w in _VERSION_WORDS or w.isdigit() for w in words)


def _strip_version_qualifiers(title: str) -> str:
    changed = True
    while changed:
        changed = False
        for pattern in (_TRAIL_PAREN_RE, _TRAIL_DASH_RE):
            match = pattern.search(title)
            if match and _is_version_segment(match.group(1)):
                title = title[: match.start()].rstrip()
                changed = True
    return title


def strip_for_search(title: str, known_artists: frozenset[str] = frozenset()) -> str:
    """Strip featured-artist, remaster and version noise to improve recall.

    Byte-identical to the former ``models._search_title`` for existing cases.
    ``known_artists`` is accepted for interface symmetry with :func:`classify`
    but does not affect search stripping.
    """
    cleaned = _FEAT_RE.sub("", title or "")
    cleaned = _REMASTER_RE.sub("", cleaned)
    cleaned = _strip_version_qualifiers(cleaned)
    cleaned = _clean(cleaned)
    return cleaned or _clean(title)


# --------------------------------------------------------------------------- #
# Segment extraction + classification
# --------------------------------------------------------------------------- #
_BRACKET_RE = re.compile(r"[(\[]([^()\[\]]*)[)\]]")


def _classify_segment(
    segment: str, known_artists: frozenset[str]
) -> tuple[Identity | None, Length | None, frozenset[str]]:
    """Classify one qualifier segment into (identity?, length?, tokens).

    Returns (None, None, frozenset()) when the segment is not a recognized
    version qualifier (a feat/prod credit, or unknown text) — the caller then
    leaves it attached to the base title.
    """
    words = _words(segment)
    if not words:
        return None, None, frozenset()

    # feat/with/prod/vs credit -> not a version qualifier
    if words[0] in _FEAT_GUARD:
        return None, None, frozenset()

    # 1) DERIVATIVE family (identity-bearing).
    for kw, identity in _DERIVATIVE_KEYWORDS.items():
        if kw in words:
            # also a LONG cut? ("Extended Remix")
            length = Length.LONG if "extended" in words else Length.NEUTRAL
            return identity, length, frozenset(words)

    # 2) LONG (before STYLE so "Club Mix" is LONG while "Club Edit" is STYLE).
    if " ".join(words) in _LONG_PHRASES or _matches_long(words):
        return Identity.ORIGINAL, Length.LONG, frozenset(words)

    # 3) Head-noun qualifiers: "<lead> <edit|mix|version>".
    if words[-1] in _HEAD_NOUNS:
        head = words[-1]
        lead = words[:-1]
        lead_str = " ".join(lead)
        is_genre = bool(lead) and (
            lead_str in _GENRE_PHRASES
            or any(g in _GENRE_WORDS for g in lead)
            or any(p in lead_str for p in _GENRE_PHRASES)
        )
        if is_genre:
            return Identity.STYLE_EDIT, Length.NEUTRAL, frozenset(words)
        if head == "edit":
            if not lead:  # bare "(Edit)" — low-confidence short cut
                return Identity.OTHER, Length.SHORT, frozenset(words)
            if any(a and (a.lower() in lead_str or lead_str in a.lower())
                   for a in known_artists):
                return Identity.REMIX, Length.NEUTRAL, frozenset(words)
            if any(p in _SHORT_PREFIXES for p in lead):
                return Identity.ORIGINAL, Length.SHORT, frozenset(words)
            # Unknown "<word> Edit": most often an artist re-edit — preserve it.
            return Identity.OTHER, Length.NEUTRAL, frozenset(words)
        # head is mix/version: a broadcast-prefix cut is SHORT ("Radio Version").
        if any(p in _SHORT_PREFIXES for p in lead):
            return Identity.ORIGINAL, Length.SHORT, frozenset(words)
        return None, None, frozenset()

    # 4) SHORT whole-words / "Mix Cut".
    if _is_short(words):
        ident = Identity.OTHER if words == ["edit"] else Identity.ORIGINAL
        return ident, Length.SHORT, frozenset(words)

    # Unknown -> leave attached to the base.
    return None, None, frozenset()


def _matches_long(words: list[str]) -> bool:
    if "extended" in words:
        return True
    if "original" in words and words[-1] in {"mix", "version"}:
        return True
    if words[-1] in {"mix", "version"} and (
        "club" in words or "full" in words or "continuous" in words
        or "maxi" in words or "12" in words
    ):
        return True
    if words == ["album", "version"] or words == ["long", "version"]:
        return True
    return False


def _is_short(words: list[str]) -> bool:
    if words in (["edit"], ["radio"], ["cut"]):
        return True
    head = words[-1]
    if head not in _SHORT_HEADS:
        return False
    lead = words[:-1]
    if not lead:
        # bare "edit"/"cut" handled above; a lone head noun like "mix" is not short
        return head in {"edit", "cut"}
    # "<prefix> edit/version/cut" where prefix is a broadcast/format word
    if head == "mix" and "cut" in lead:  # "Mix Cut" is reversed; handle below
        return True
    if any(p in _SHORT_PREFIXES for p in lead):
        return True
    # "Mix Cut": head "cut" preceded by "mix"
    if head == "cut" and "mix" in lead:
        return True
    return False


def classify(title: str, known_artists: frozenset[str] = frozenset()) -> VersionInfo:
    """Classify ``title``'s version qualifier(s) into a :class:`VersionInfo`.

    Pure and deterministic. ``known_artists`` (the track's own artists) lets an
    "<Artist> Edit" be told apart from a "<Genre> Edit".
    """
    norm = _normalize_title(title)
    if not norm:
        return VersionInfo(Identity.ORIGINAL, Length.NEUTRAL, "", (), frozenset())

    identities: list[Identity] = []
    lengths: list[Length] = []
    qualifiers: list[str] = []
    tokens: set[str] = set()

    # --- trailing dash suffixes (right to left), admitted only if recognized --
    base = norm
    dash_quals: list[str] = []
    while True:
        m = re.search(r"\s-\s+([^-]+?)\s*$", base)
        if not m:
            break
        seg = m.group(1).strip()
        ident, length, toks = _classify_segment(seg, known_artists)
        if ident is None and length is None:
            break  # first non-qualifier tail stops the walk (it's a subtitle)
        if ident is not None:
            identities.append(ident)
        if length is not None:
            lengths.append(length)
        tokens |= _identity_tokens(toks, ident)
        dash_quals.append(seg)
        base = base[: m.start()].rstrip()
    dash_quals.reverse()

    # --- bracket groups (left to right) -------------------------------------
    bracket_quals: list[str] = []
    kept_spans: list[str] = []

    def _sub(match: re.Match) -> str:
        seg = match.group(1).strip()
        ident, length, toks = _classify_segment(seg, known_artists)
        if ident is None and length is None:
            return match.group(0)  # keep unclassified/feat segment on the base
        if ident is not None:
            identities.append(ident)
        if length is not None:
            lengths.append(length)
        nonlocal tokens
        tokens |= _identity_tokens(toks, ident)
        bracket_quals.append(seg)
        return " "

    base = _BRACKET_RE.sub(_sub, base)
    base_title = _clean(base)
    qualifiers = bracket_quals + dash_quals

    if not identities and not lengths:
        # Nothing classified -> STANDARD, title untouched.
        return VersionInfo(Identity.ORIGINAL, Length.NEUTRAL, norm, (), frozenset())

    # Guard: never strip away the entire title (mirrors search_query_never_empty).
    if not base_title:
        return VersionInfo(Identity.ORIGINAL, Length.NEUTRAL, norm, (), frozenset())

    identity = _resolve_identity(identities)
    length = (
        Length.LONG if Length.LONG in lengths
        else Length.SHORT if Length.SHORT in lengths
        else Length.NEUTRAL
    )
    return VersionInfo(
        identity=identity,
        length=length,
        base_title=base_title,
        qualifiers=tuple(qualifiers),
        tokens=frozenset(tokens),
    )


def _identity_tokens(toks: frozenset[str], ident: Identity | None) -> set[str]:
    """Tokens the matcher should key on — identity/style words, never length."""
    if ident is None or ident is Identity.ORIGINAL:
        return set()
    length_words = {"extended", "original", "full", "long", "maxi", "continuous",
                    "radio", "short", "single", "video", "album", "12", "mix",
                    "version", "edit", "cut"}
    return {t for t in toks if t not in length_words}


_IDENTITY_PRECEDENCE = [
    Identity.REMIX, Identity.VIP, Identity.BOOTLEG, Identity.MASHUP,
    Identity.LIVE, Identity.ACOUSTIC, Identity.INSTRUMENTAL, Identity.ACAPELLA,
    Identity.NIGHTCORE, Identity.SPEEDMOD, Identity.REMASTER, Identity.OTHER,
    Identity.STYLE_EDIT, Identity.ORIGINAL,
]


def _resolve_identity(identities: list[Identity]) -> Identity:
    """Pick the winning identity: any real derivative beats STYLE-EDIT beats
    ORIGINAL. (DERIVATIVE > STYLE-EDIT > ORIGINAL, ties by precedence order.)"""
    for candidate in _IDENTITY_PRECEDENCE:
        if candidate in identities:
            return candidate
    return Identity.ORIGINAL


# --------------------------------------------------------------------------- #
# Extended-mix planning + qualifier application
# --------------------------------------------------------------------------- #
_EXTENDED_LABEL = "(Extended Mix)"


def apply_qualifier(title: str, label: str = _EXTENDED_LABEL,
                    known_artists: frozenset[str] = frozenset()) -> str:
    """Append ``label`` to ``title`` unless it is already a long/extended cut.

    Token-based (not kind-literal): "Song (Extended Remix)" already carries
    "extended", so it is left unchanged rather than becoming a double-suffix.
    A SHORT qualifier is stripped first, so "Oxygen - Radio Edit" becomes
    "Oxygen (Extended Mix)", never "Oxygen - Radio Edit (Extended Mix)".
    """
    info = classify(title, known_artists)
    if info.length is Length.LONG or "extended" in _words(title):
        return _normalize_title(title)
    if info.identity is not Identity.ORIGINAL:
        # A specific recording (remix / VIP / style-edit / …): preserve it
        # verbatim rather than fabricating an extended mix that may not exist.
        return _normalize_title(title)
    base = (
        info.base_title
        if (info.length is Length.SHORT or info.qualifiers)
        else _normalize_title(title)
    )
    return _clean(f"{base} {label}")


def plan_extended(info: VersionInfo) -> ExtendedPlan:
    """Decide how to pursue an Extended Mix given a title's classification.

    * already LONG                -> no-op (skip note; search the title as-is)
    * DERIVATIVE / STYLE-EDIT     -> preserve the qualifier, no append; strip
                                     only a co-occurring SHORT; search base too
    * SHORT                       -> strip it, append (Extended Mix), search base
    * plain / neutral             -> append (Extended Mix), fall back to base
    """
    base = info.base_title
    # Reconstruct the identity-preserving title (base + non-length qualifiers).
    preserved = base
    for q in info.qualifiers:
        # keep identity/style qualifiers; drop pure length words
        if _classify_pure_length(q):
            continue
        preserved = f"{preserved} ({q})"
    preserved = _clean(preserved)

    if info.length is Length.LONG:
        return ExtendedPlan(
            search_titles=(preserved or base,),
            output_suffix=None,
            skip_note="already an extended/long version",
        )
    if info.identity in RESTRICTIVE_IDENTITIES or info.identity in (
        Identity.REMASTER, Identity.OTHER,
    ):
        # A specific recording: don't fabricate an Extended Mix; preserve it,
        # and also try the bare base so a miss degrades to the original.
        titles = (preserved,) if preserved == base else (preserved, base)
        return ExtendedPlan(search_titles=titles, output_suffix=None, skip_note=None)
    # ORIGINAL identity (plain / short): pursue the extended cut, base fallback.
    return ExtendedPlan(
        search_titles=(_clean(f"{base} {_EXTENDED_LABEL}"), base),
        output_suffix=_EXTENDED_LABEL,
        skip_note=None,
    )


def _classify_pure_length(qualifier: str) -> bool:
    """True if a qualifier segment is only a length word (no identity)."""
    ident, length, _ = _classify_segment(qualifier, frozenset())
    return ident in (None, Identity.ORIGINAL) and length is not None
