"""Unit tests for the Soulseek candidate matcher."""

from __future__ import annotations

from spotiseek.models import MatchStrictness, Track
from spotiseek.soulseek.matcher import (
    has_ready_lossless_match,
    is_extended_mix,
    is_official_extended_mix,
    score_candidates,
)

from ..conftest import make_candidate


def test_ranks_lossless_first(sample_track) -> None:
    cands = [
        make_candidate(username="mp3", filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=320, duration=320),
        make_candidate(username="flac", filename="daft punk - one more time.flac",
                       extension="flac", duration=320),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED)
    assert [c.username for c in ranked] == ["flac", "mp3"]
    assert ranked[0].score > ranked[1].score


def test_higher_bitrate_wins_between_mp3(sample_track) -> None:
    cands = [
        make_candidate(username="low", filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=128, duration=320),
        make_candidate(username="high", filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=320, duration=320),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED)
    assert ranked[0].username == "high"


def test_rejects_non_audio(sample_track) -> None:
    cands = [make_candidate(filename="cover.jpg", extension="jpg", duration=None)]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED) == []


def test_rejects_wrong_title(sample_track) -> None:
    cands = [
        make_candidate(filename="Some Other Artist - Totally Different.mp3",
                       extension="mp3", bitrate=320, duration=320)
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED) == []


def test_balanced_rejects_wrong_duration(sample_track) -> None:
    cands = [
        make_candidate(filename="daft punk - one more time (radio edit).mp3",
                       extension="mp3", bitrate=320, duration=180)  # 140s off
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED) == []


def test_lenient_ignores_duration(sample_track) -> None:
    cands = [
        make_candidate(filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=320, duration=180)
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.LENIENT)


def test_min_bitrate_filters_lossy_but_keeps_lossless(sample_track) -> None:
    cands = [
        make_candidate(username="low", filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=128, duration=320),
        make_candidate(username="flac", filename="daft punk - one more time.flac",
                       extension="flac", duration=320),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                              min_bitrate=256)
    assert [c.username for c in ranked] == ["flac"]


def test_unknown_duration_not_rejected(sample_track) -> None:
    cands = [
        make_candidate(filename="daft punk - one more time.mp3",
                       extension="mp3", bitrate=320, duration=None)
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED)


def test_is_extended_mix() -> None:
    assert is_extended_mix("Daft Punk - One More Time (Extended Mix).flac")
    assert is_extended_mix("01 one_more_time_extended_mix.mp3")
    assert not is_extended_mix("Daft Punk - One More Time (Radio Mix).flac")
    assert not is_extended_mix("Daft Punk - One More Time (Extended Version).flac")
    assert not is_extended_mix("Daft Punk - One More Time.flac")


def test_require_extended_keeps_only_extended(sample_track) -> None:
    cands = [
        make_candidate(username="ext",
                       filename="Daft Punk - One More Time (Extended Mix).flac",
                       duration=480),
        make_candidate(username="std", filename="Daft Punk - One More Time.flac",
                       duration=320),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                              require_extended=True)
    assert [c.username for c in ranked] == ["ext"]


def test_require_extended_ignores_duration(sample_track) -> None:
    # Extended mix is much longer than the standard track; must NOT be rejected.
    cands = [
        make_candidate(username="ext",
                       filename="Daft Punk - One More Time (Extended Mix).flac",
                       duration=600)
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                            require_extended=True)


def test_require_extended_empty_when_no_extended(sample_track) -> None:
    cands = [
        make_candidate(username="std", filename="Daft Punk - One More Time.flac",
                       duration=320)
    ]
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                            require_extended=True) == []


def test_is_official_extended_mix(sample_track) -> None:
    official = make_candidate(filename="Daft Punk - One More Time (Extended Mix).flac")
    flip = make_candidate(
        filename="Daft Punk - One More Time (RetroVision Flip) [EXTENDED MIX].wav",
        extension="wav")
    remix = make_candidate(
        filename="Daft Punk - One More Time (Someone Remix) [Extended Mix].flac")
    assert is_official_extended_mix(sample_track, official) is True
    assert is_official_extended_mix(sample_track, flip) is False
    assert is_official_extended_mix(sample_track, remix) is False


def test_require_extended_rejects_remixes(sample_track) -> None:
    cands = [
        make_candidate(username="flip",
                       filename="Daft Punk - One More Time (RetroVision Flip) [EXTENDED MIX].wav",
                       extension="wav", duration=300),
        make_candidate(username="remix",
                       filename="Daft Punk - One More Time (Skrillex Remix) [Extended Mix].flac",
                       duration=360),
        make_candidate(username="official",
                       filename="Daft Punk - One More Time (Extended Mix).flac",
                       duration=480),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                              require_extended=True)
    assert [c.username for c in ranked] == ["official"]


def test_require_extended_falls_back_when_only_remixes(sample_track) -> None:
    cands = [
        make_candidate(username="flip",
                       filename="Daft Punk - One More Time (DJ X Flip) [Extended Mix].flac",
                       duration=300),
        make_candidate(username="bootleg",
                       filename="Daft Punk - One More Time (Bootleg) (Extended Mix).mp3",
                       extension="mp3", bitrate=320, duration=330),
    ]
    # No official extended mix -> nothing survives -> downloader falls back.
    assert score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                            require_extended=True) == []


def test_require_extended_prefers_cleaner_official(sample_track) -> None:
    cands = [
        make_candidate(username="verbose",
                       filename="Daft Punk - Discovery Deluxe Reissue - One More Time (Extended Mix).flac",
                       duration=480),
        make_candidate(username="clean",
                       filename="Daft Punk - One More Time (Extended Mix).flac",
                       duration=480),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED,
                              require_extended=True)
    assert ranked[0].username == "clean"


def test_has_ready_lossless_match(sample_track) -> None:
    # Lossless + free slot + matching -> ready.
    ready = [make_candidate(filename="Daft Punk - One More Time.flac",
                            extension="flac", duration=320, has_free_slots=True)]
    assert has_ready_lossless_match(sample_track, ready, MatchStrictness.BALANCED)

    # Lossless but no free slot -> not ready.
    queued = [make_candidate(filename="Daft Punk - One More Time.flac",
                             extension="flac", duration=320, has_free_slots=False)]
    assert not has_ready_lossless_match(sample_track, queued, MatchStrictness.BALANCED)

    # Free slot but lossy -> not "ready lossless".
    lossy = [make_candidate(filename="Daft Punk - One More Time.mp3",
                            extension="mp3", bitrate=320, duration=320,
                            has_free_slots=True)]
    assert not has_ready_lossless_match(sample_track, lossy, MatchStrictness.BALANCED)

    # Lossless + free slot but wrong song -> not ready.
    wrong = [make_candidate(filename="Other Artist - Other Song.flac",
                            extension="flac", duration=320, has_free_slots=True)]
    assert not has_ready_lossless_match(sample_track, wrong, MatchStrictness.BALANCED)


def test_free_slot_preferred_over_queued(sample_track) -> None:
    cands = [
        make_candidate(username="queued", filename="daft punk - one more time.flac",
                       extension="flac", duration=320, has_free_slots=False,
                       queue_size=40),
        make_candidate(username="free", filename="daft punk - one more time.flac",
                       extension="flac", duration=320, has_free_slots=True,
                       queue_size=0),
    ]
    ranked = score_candidates(sample_track, cands, MatchStrictness.BALANCED)
    assert ranked[0].username == "free"


# --------------------------------------------------------------------------- #
# (a) Folder-path-aware extended detection
# --------------------------------------------------------------------------- #
def test_extended_signal_from_folder(sample_track) -> None:
    # A plainly-named file inside an "(Extended Mixes)" folder still counts as
    # extended, even though the filename alone doesn't say so.
    cands = [
        make_candidate(
            username="folder-ext",
            filename=r"VA - Hardstyle (Extended Mixes)\01 - One More Time.flac",
            duration=380,
        ),
    ]
    ranked = score_candidates(sample_track, cands, require_extended=True)
    assert len(ranked) == 1
    assert is_official_extended_mix(sample_track, cands[0])


def test_extended_signal_filename_still_works(sample_track) -> None:
    cand = make_candidate(
        filename=r"Music\Daft Punk - One More Time (Extended Mix).flac", duration=380
    )
    assert is_official_extended_mix(sample_track, cand)


def test_plain_file_in_plain_folder_not_extended(sample_track) -> None:
    cand = make_candidate(
        filename=r"Music\Daft Punk\One More Time.flac", duration=320
    )
    assert not is_official_extended_mix(sample_track, cand)


# --------------------------------------------------------------------------- #
# (b) --prefer-longest
# --------------------------------------------------------------------------- #
def test_prefer_longest_ranks_longest_first(sample_track) -> None:
    # Same track, three lengths; all lossless. Longest should rank first.
    cands = [
        make_candidate(username="radio", filename="Daft Punk - One More Time.flac",
                       duration=320),
        make_candidate(username="extended", filename="Daft Punk - One More Time.flac",
                       duration=470),
        make_candidate(username="mid", filename="Daft Punk - One More Time.flac",
                       duration=400),
    ]
    ranked = score_candidates(sample_track, cands, prefer_longest=True)
    assert ranked[0].username == "extended"


def test_prefer_longest_allows_longer_than_spotify(sample_track) -> None:
    # A 470s full version is >15s longer than Spotify's 320s; normally the
    # balanced duration filter would reject it, but prefer_longest keeps it.
    cands = [
        make_candidate(filename="Daft Punk - One More Time.flac", duration=470),
    ]
    assert score_candidates(sample_track, cands) == []  # rejected normally
    assert len(score_candidates(sample_track, cands, prefer_longest=True)) == 1


def test_prefer_longest_still_rejects_short_preview(sample_track) -> None:
    cands = [
        make_candidate(filename="Daft Punk - One More Time.flac", duration=30),
    ]
    assert score_candidates(sample_track, cands, prefer_longest=True) == []


def test_prefer_longest_rejects_whole_album_mix(sample_track) -> None:
    # A 60-minute continuous mix is way beyond the sane ratio -> rejected.
    cands = [
        make_candidate(filename="Daft Punk - One More Time.flac", duration=3600),
    ]
    assert score_candidates(sample_track, cands, prefer_longest=True) == []


def test_prefer_longest_does_not_penalize_unknown_duration(sample_track) -> None:
    # A strong lossless match that simply doesn't report its duration must not
    # be demoted below a weaker match just because it has no length to blend in.
    cands = [
        make_candidate(username="no_duration",
                       filename="Daft Punk - One More Time.flac",
                       duration=None, bitrate=None),
        make_candidate(username="shorter_named_worse",
                       filename="Daft Punk - One More Time (radio).mp3",
                       extension="mp3", bitrate=320, duration=320),
    ]
    ranked = score_candidates(sample_track, cands, prefer_longest=True)
    # The lossless, name-clean candidate should still win despite no duration.
    assert ranked[0].username == "no_duration"
