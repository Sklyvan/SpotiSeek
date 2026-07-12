"""Unit tests for the Soulseek candidate matcher."""

from __future__ import annotations

from spotiseek.models import MatchStrictness
from spotiseek.soulseek.matcher import is_extended_mix, score_candidates

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
