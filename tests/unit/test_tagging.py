"""Unit tests for tagging (uses generated audio fixtures, no network)."""

from __future__ import annotations

import shutil
from pathlib import Path

import mutagen
import pytest

from spotiseek import tagging
from spotiseek.models import Track

# 1x1 PNG, supplied directly so the test never touches the network.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@pytest.fixture
def track() -> Track:
    return Track(
        title="One More Time",
        artists=["Daft Punk", "Guest"],
        album="Discovery",
        track_number=3,
        release_date="2001-03-12",
    )


def _copy_fixture(audio_dir: Path, tmp_path: Path, ext: str) -> Path | None:
    src = audio_dir / f"sample.{ext}"
    if not src.exists():
        return None
    dst = tmp_path / f"track.{ext}"
    shutil.copy(src, dst)
    return dst


def _read(path: Path, keys: list[str]):
    f = mutagen.File(path)
    for k in keys:
        if f.tags and k in f.tags:
            v = f.tags[k]
            return v[0] if isinstance(v, list) else v
    return None


@pytest.mark.parametrize("ext", ["mp3", "flac", "wav", "m4a", "aiff"])
def test_tag_round_trip(ext, track, audio_dir, tmp_path) -> None:
    path = _copy_fixture(audio_dir, tmp_path, ext)
    if path is None:
        pytest.skip(f"missing audio fixture sample.{ext}")

    ok = tagging.tag_file(str(path), track, embed_art=True, cover=(_PNG, "image/png"))
    assert ok is True

    title = _read(path, ["title", "TIT2", "\xa9nam"])
    assert "One More Time" in str(title)

    # Cover art should be present in whatever the format's native container is.
    f = mutagen.File(path)
    has_cover = (
        bool(getattr(f, "pictures", None))
        or "covr" in getattr(f, "tags", {})
        or any(str(k).startswith("APIC") for k in (f.tags or {}))
        or "metadata_block_picture" in (f.tags or {})
    )
    assert has_cover, f"no embedded cover for {ext}"


def test_generic_tagger_handles_unknown_extension(track, audio_dir, tmp_path) -> None:
    # A real audio file with an unusual extension: mutagen sniffs the content,
    # so the generic fallback still writes the Spotify text metadata.
    src = audio_dir / "sample.flac"
    if not src.exists():
        pytest.skip("missing flac fixture")
    dst = tmp_path / "track.weirdext"
    shutil.copy(src, dst)
    assert tagging.tag_file(str(dst), track, embed_art=False) is True
    assert "One More Time" in str(mutagen.File(dst)["title"])


def test_tagger_returns_false_for_non_audio(track, tmp_path) -> None:
    path = tmp_path / "track.xyz"
    path.write_bytes(b"not audio at all")
    # Not recognizable audio -> generic tagger fails gracefully, returns False.
    assert tagging.tag_file(str(path), track, embed_art=False) is False


def test_tagging_never_raises_on_corrupt_file(track, tmp_path) -> None:
    path = tmp_path / "corrupt.mp3"
    path.write_bytes(b"this is not a real mp3 file")
    # Should log a warning and return False, not raise.
    result = tagging.tag_file(str(path), track, embed_art=False)
    assert result in (True, False)


def test_no_art_when_disabled(track, audio_dir, tmp_path) -> None:
    path = _copy_fixture(audio_dir, tmp_path, "flac")
    if path is None:
        pytest.skip("missing flac fixture")
    tagging.tag_file(str(path), track, embed_art=False)
    from mutagen.flac import FLAC

    assert len(FLAC(path).pictures) == 0


@pytest.mark.parametrize("ext", ["wav", "aiff", "mp3"])
def test_retagging_does_not_stack_cover_art(ext, track, audio_dir, tmp_path) -> None:
    # Tagging the same file twice must replace the artwork, not append a second
    # APIC frame (ID3 containers: WAV/AIFF/MP3).
    path = _copy_fixture(audio_dir, tmp_path, ext)
    if path is None:
        pytest.skip(f"missing audio fixture sample.{ext}")

    cover = (_PNG, "image/png")
    assert tagging.tag_file(str(path), track, embed_art=True, cover=cover) is True
    assert tagging.tag_file(str(path), track, embed_art=True, cover=cover) is True

    f = mutagen.File(path)
    apics = [k for k in (f.tags or {}) if str(k).startswith("APIC")]
    assert len(apics) == 1, f"expected one cover frame for {ext}, got {apics}"
