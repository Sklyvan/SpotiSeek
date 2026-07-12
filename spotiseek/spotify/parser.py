"""Parse Spotify URLs and URIs into a (kind, id) pair.

Handles the common forms:
  - https://open.spotify.com/track/<id>
  - https://open.spotify.com/intl-es/album/<id>?si=...   (locale prefix + query)
  - http://open.spotify.com/playlist/<id>
  - spotify:track:<id>                                    (URI form)
  - a bare 22-char base-62 id is rejected (kind is ambiguous)
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..errors import SpotifyError
from ..models import SpotifyKind

# Spotify IDs are base-62, historically 22 characters, but we stay lenient.
_ID_RE = r"(?P<id>[A-Za-z0-9]{16,})"
_KINDS = "|".join(k.value for k in SpotifyKind)

# spotify:track:ID  (optionally spotify:user:...:playlist:ID)
_URI_RE = re.compile(rf"^spotify:(?:.+:)?(?P<kind>{_KINDS}):{_ID_RE}$")
# .../track/ID  (optionally with an /intl-xx/ prefix already stripped by urlparse path)
_PATH_RE = re.compile(rf"/(?P<kind>{_KINDS})/{_ID_RE}")


def parse_spotify_url(url: str) -> tuple[SpotifyKind, str]:
    """Return the (kind, id) for a Spotify URL or URI.

    Raises :class:`SpotifyError` for anything unrecognized or unsupported.
    """
    if not url or not url.strip():
        raise SpotifyError("Empty Spotify URL.")

    text = url.strip()

    uri_match = _URI_RE.match(text)
    if uri_match:
        return SpotifyKind(uri_match.group("kind")), uri_match.group("id")

    parsed = urlparse(text)
    host = parsed.netloc.lower()
    if host and "spotify.com" not in host:
        raise SpotifyError(f"Not a Spotify URL: {url!r}")

    path_match = _PATH_RE.search(parsed.path or text)
    if path_match:
        return SpotifyKind(path_match.group("kind")), path_match.group("id")

    supported = ", ".join(k.value for k in SpotifyKind)
    raise SpotifyError(
        f"Unsupported or malformed Spotify URL: {url!r}. "
        f"Expected a {supported} URL or URI."
    )
