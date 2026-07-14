"""Per-provider lossless downloaders backed by third-party proxy APIs.

Each provider talks to a self-hosted / reverse-engineered proxy whose base URL
is supplied by the caller (from config/env). Because those proxies rotate and
their response shapes drift, the stream-URL extraction is deliberately
defensive: we look under a set of well-known keys, then fall back to scanning
the whole JSON payload for a plausible audio URL.

A provider never raises — ``fetch`` returns ``(temp_path, extension)`` on
success or ``None`` on any failure, so one dead provider can't abort a run.
"""

from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; SpotiSeek)"
_RESOLVE_TIMEOUT = 30.0
_STREAM_TIMEOUT = 300.0
_CHUNK = 1 << 16
# Anything smaller than this is almost certainly an error page / JSON blob that
# a proxy returned with a 200, not real audio.
_MIN_AUDIO_BYTES = 64 * 1024

# JSON keys under which proxies commonly expose the direct media URL.
_URL_KEYS = (
    "OriginalTrackUrl", "originalTrackUrl", "url", "URL", "link",
    "downloadUrl", "download_url", "streamUrl", "stream_url", "manifest",
)
_MEDIA_HINTS = (".flac", ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".mp4")
_CT_TO_EXT = {
    "audio/flac": "flac", "audio/x-flac": "flac",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/ogg": "ogg", "audio/opus": "opus",
}


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _ext_from(url: str, content_type: str | None) -> str | None:
    """Best-effort file extension from a URL path, then a Content-Type."""
    path = urlsplit(url).path.lower()
    for hint in _MEDIA_HINTS:
        if path.endswith(hint):
            return hint.lstrip(".").replace("mp4", "m4a")
    if content_type:
        return _CT_TO_EXT.get(content_type.split(";", 1)[0].strip().lower())
    return None


def _find_stream_url(payload: object) -> str | None:
    """Scan a decoded JSON payload for a playable audio URL."""

    def is_http(value: object) -> bool:
        return isinstance(value, str) and value.startswith(("http://", "https://"))

    # 1) Prefer values under known keys (recursing into nested containers).
    if isinstance(payload, dict):
        for key in _URL_KEYS:
            if key in payload:
                if is_http(payload[key]):
                    return payload[key]
                nested = _find_stream_url(payload[key])
                if nested:
                    return nested

    # 2) Otherwise walk everything, preferring URLs that look like media files.
    fallback: str | None = None
    stack: list[object] = [payload]
    while stack:
        item = stack.pop()
        if is_http(item):
            if any(h in item.lower() for h in _MEDIA_HINTS):
                return item  # type: ignore[return-value]
            fallback = fallback or item  # type: ignore[assignment]
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return fallback


class BaseProvider:
    """Common resolve → download machinery. Subclasses build the resolve URL."""

    name = "base"
    #: Default file extension to assume when the stream URL / headers are mute.
    default_ext = "flac"

    def __init__(self, base_url: str, session: requests.Session | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.session = session or requests.Session()

    # -- subclass hook ----------------------------------------------------- #
    def _resolve_stream(self, identifier: str) -> str | None:
        """Return a direct media URL for ``identifier`` (native id or ISRC)."""
        raise NotImplementedError

    def _get_json(self, url: str, params: dict) -> object:
        resp = self.session.get(
            url, params=params, timeout=_RESOLVE_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        return resp.json()

    # -- public API -------------------------------------------------------- #
    def fetch(self, identifier: str, dest_dir: str) -> tuple[str, str] | None:
        if not self.base_url:
            logger.info(
                "Fallback: %s has no proxy URL configured "
                "(set SPOTISEEK_%s_API_URL); skipping.",
                self.name, self.name.upper(),
            )
            return None
        try:
            stream_url = self._resolve_stream(identifier)
        except (requests.RequestException, ValueError) as exc:
            logger.debug("%s: could not resolve stream for %s: %s",
                         self.name, identifier, exc)
            return None
        if not stream_url:
            logger.debug("%s: no stream URL for %s", self.name, identifier)
            return None
        return self._download(stream_url, dest_dir)

    def _download(self, url: str, dest_dir: str) -> tuple[str, str] | None:
        os.makedirs(dest_dir, exist_ok=True)
        ext = _ext_from(url, None) or self.default_ext
        tmp = os.path.join(dest_dir, f"fallback-{self.name}-{uuid.uuid4().hex}.{ext}")
        try:
            with self.session.get(
                url, stream=True, timeout=_STREAM_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            ) as resp:
                resp.raise_for_status()
                real_ext = _ext_from(url, resp.headers.get("Content-Type"))
                if real_ext and real_ext != ext:
                    ext = real_ext
                    tmp = f"{tmp.rsplit('.', 1)[0]}.{ext}"
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(_CHUNK):
                        if chunk:
                            fh.write(chunk)
        except (requests.RequestException, OSError) as exc:
            logger.debug("%s: download failed: %s", self.name, exc)
            _safe_remove(tmp)
            return None

        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if size < _MIN_AUDIO_BYTES:
            logger.debug("%s: file too small (%d bytes), discarding.", self.name, size)
            _safe_remove(tmp)
            return None
        return tmp, ext


class TidalProvider(BaseProvider):
    """Tidal via a ``hifi-api``-style proxy: ``{base}/track/?id=..&quality=..``."""

    name = "tidal"

    def __init__(self, base_url: str, session=None, quality: str = "LOSSLESS") -> None:
        super().__init__(base_url, session)
        self.quality = quality

    def _resolve_stream(self, identifier: str) -> str | None:
        data = self._get_json(
            f"{self.base_url}/track/",
            {"id": identifier, "quality": self.quality},
        )
        return _find_stream_url(data)


class QobuzProvider(BaseProvider):
    """Qobuz via a ``qobuz-rest``-style proxy. Resolved by ISRC (Odesli has no
    Qobuz), so ``identifier`` here is an ISRC when one is available."""

    name = "qobuz"

    def __init__(self, base_url: str, session=None, quality: str = "27") -> None:
        super().__init__(base_url, session)
        self.quality = quality

    def _resolve_stream(self, identifier: str) -> str | None:
        data = self._get_json(
            f"{self.base_url}/api/download-music",
            {"isrc": identifier, "quality": self.quality},
        )
        return _find_stream_url(data)


class AmazonProvider(BaseProvider):
    """Amazon Music via a proxy: ``{base}/track/?id=..``."""

    name = "amazon"

    def _resolve_stream(self, identifier: str) -> str | None:
        data = self._get_json(f"{self.base_url}/track/", {"id": identifier})
        return _find_stream_url(data)


class DeezerProvider(BaseProvider):
    """Deezer via a proxy: ``{base}/track/?id=..``."""

    name = "deezer"

    def _resolve_stream(self, identifier: str) -> str | None:
        data = self._get_json(f"{self.base_url}/track/", {"id": identifier})
        return _find_stream_url(data)


#: Provider key -> (class, config attribute holding its base URL).
PROVIDER_REGISTRY = {
    "tidal": (TidalProvider, "tidal_api_url"),
    "qobuz": (QobuzProvider, "qobuz_api_url"),
    "amazon": (AmazonProvider, "amazon_api_url"),
    "deezer": (DeezerProvider, "deezer_api_url"),
}
