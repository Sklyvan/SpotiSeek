"""Write audio tags and embed cover art using mutagen.

Tagging is best-effort: a failure here never fails the download, it is logged
as a warning. MP3 (ID3), FLAC/OGG/OPUS (Vorbis comments), MP4/M4A and WAV/AIFF
(ID3) get full tags plus embedded cover art. **Any other format** mutagen can
open still receives the Spotify text metadata (title/artist/album/track/date)
via a generic fallback, so downloads always end up tagged. Cover art is fetched
from the track's Spotify cover URL and cached per URL for the run.
"""

from __future__ import annotations

import logging

import requests

from .models import Track

logger = logging.getLogger(__name__)

# Successful cover-art downloads cached by URL, so an album/playlist that shares
# one cover downloads it once. Only *successes* are cached — a transient failure
# must not permanently disable artwork for the run.
_cover_cache: dict[str, tuple[bytes, str]] = {}


def _download_cover(url: str, timeout: float = 20.0) -> tuple[bytes, str] | None:
    """Download cover art, returning (bytes, mime) or None on failure."""
    cached = _cover_cache.get(url)
    if cached is not None:
        return cached
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Could not download cover art from %s: %s", url, exc)
        return None  # not cached: a later track may retry successfully
    data = resp.content
    mime = resp.headers.get("Content-Type", "")
    if not mime.startswith("image/"):
        # Sniff the magic bytes if the server did not send a useful type.
        mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    result = (data, mime)
    _cover_cache[url] = result
    return result


def _year(release_date: str | None) -> str | None:
    return release_date.split("-")[0] if release_date else None


def _tag_mp3(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    from mutagen.id3 import (
        APIC,
        ID3,
        ID3NoHeaderError,
        TALB,
        TDRC,
        TIT2,
        TPE1,
        TRCK,
    )

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.setall("TIT2", [TIT2(encoding=3, text=track.title)])
    if track.artist_string:
        tags.setall("TPE1", [TPE1(encoding=3, text=track.artist_string)])
    if track.album:
        tags.setall("TALB", [TALB(encoding=3, text=track.album)])
    if track.track_number:
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track.track_number))])
    if _year(track.release_date):
        tags.setall("TDRC", [TDRC(encoding=3, text=_year(track.release_date))])
    if cover:
        data, mime = cover
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    tags.save(path)


def _tag_vorbis(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    """FLAC and Ogg/Opus (Vorbis comments)."""
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import PictureType

    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Unsupported Vorbis-style file")

    audio["title"] = track.title
    if track.artist_string:
        audio["artist"] = track.artist_string
    if track.album:
        audio["album"] = track.album
    if track.track_number:
        audio["tracknumber"] = str(track.track_number)
    if _year(track.release_date):
        audio["date"] = _year(track.release_date)

    if cover:
        data, mime = cover
        pic = Picture()
        pic.data = data
        pic.type = PictureType.COVER_FRONT
        pic.mime = mime
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            audio.add_picture(pic)
        else:  # Ogg/Opus: base64-encoded metadata block
            import base64

            audio["metadata_block_picture"] = [
                base64.b64encode(pic.write()).decode("ascii")
            ]
    audio.save()


def _tag_mp4(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    audio["\xa9nam"] = track.title
    if track.artist_string:
        audio["\xa9ART"] = track.artist_string
    if track.album:
        audio["\xa9alb"] = track.album
    if track.track_number:
        audio["trkn"] = [(track.track_number, 0)]
    if _year(track.release_date):
        audio["\xa9day"] = _year(track.release_date)
    if cover:
        data, mime = cover
        fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(data, imageformat=fmt)]
    audio.save()


def _tag_id3_container(audio, track: Track, cover: tuple[bytes, str] | None) -> None:
    """Write ID3 tags into an already-opened container (WAV / AIFF)."""
    from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TRCK

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.setall("TIT2", [TIT2(encoding=3, text=track.title)])
    if track.artist_string:
        tags.setall("TPE1", [TPE1(encoding=3, text=track.artist_string)])
    if track.album:
        tags.setall("TALB", [TALB(encoding=3, text=track.album)])
    if track.track_number:
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track.track_number))])
    if _year(track.release_date):
        tags.setall("TDRC", [TDRC(encoding=3, text=_year(track.release_date))])
    if cover:
        data, mime = cover
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    audio.save()


def _tag_wav(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    from mutagen.wave import WAVE

    _tag_id3_container(WAVE(path), track, cover)


def _tag_aiff(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    from mutagen.aiff import AIFF

    _tag_id3_container(AIFF(path), track, cover)


def _tag_generic(path: str, track: Track, cover: tuple[bytes, str] | None) -> None:
    """Fallback for any other format mutagen can open.

    Writes the Spotify text metadata using the standardized "easy" keys so that
    files with an unusual extension (or no tags at all) still get title/artist/
    album/track/date. Cover art is not embedded here (container support varies);
    the common art-capable formats are handled by the dedicated taggers above.
    """
    import mutagen

    audio = mutagen.File(path, easy=True)
    if audio is None:
        raise ValueError("mutagen could not recognize the audio format")
    if audio.tags is None:
        audio.add_tags()

    audio["title"] = track.title
    if track.artist_string:
        audio["artist"] = track.artist_string
    if track.album:
        audio["album"] = track.album
    if track.track_number:
        audio["tracknumber"] = str(track.track_number)
    if _year(track.release_date):
        audio["date"] = _year(track.release_date)
    audio.save()


_TAGGERS = {
    "mp3": _tag_mp3,
    "flac": _tag_vorbis,
    "ogg": _tag_vorbis,
    "opus": _tag_vorbis,
    "oga": _tag_vorbis,
    "m4a": _tag_mp4,
    "mp4": _tag_mp4,
    "aac": _tag_mp4,
    "wav": _tag_wav,
    "wave": _tag_wav,
    "aiff": _tag_aiff,
    "aif": _tag_aiff,
    "aifc": _tag_aiff,
}


def tag_file(
    path: str,
    track: Track,
    embed_art: bool = True,
    cover: tuple[bytes, str] | None = None,
) -> bool:
    """Tag ``path`` with ``track`` metadata. Returns True on success.

    ``cover`` may be supplied pre-fetched (bytes, mime); otherwise it is
    downloaded from ``track.cover_url`` when ``embed_art`` is set. Never raises.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    # Any unknown extension still gets Spotify text metadata via _tag_generic.
    tagger = _TAGGERS.get(ext, _tag_generic)

    if embed_art and cover is None and track.cover_url:
        cover = _download_cover(track.cover_url)

    try:
        tagger(path, track, cover)
        logger.debug("Tagged %s", path)
        return True
    except Exception as exc:  # tagging must never abort a successful download
        logger.warning("Failed to tag %s: %s", path, exc)
        return False
