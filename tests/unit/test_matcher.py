"""Unit tests for the Soulseek candidate matcher."""

from __future__ import annotations

from spotiseek.models import MatchStrictness
from spotiseek.soulseek.matcher import score_candidates

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
