"""Shared pytest fixtures and configuration.

Integration tests (marked ``@pytest.mark.integration``) hit the live Spotify
and Soulseek networks. They are skipped by default; pass ``--run-integration``
to enable them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spotiseek.models import Candidate, Track

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO_FIXTURES = FIXTURES / "audio"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require the live network.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def sample_track() -> Track:
    return Track(
        title="One More Time",
        artists=["Daft Punk"],
        album="Discovery",
        track_number=1,
        duration_ms=320357,
        release_date="2001-03-12",
        cover_url=None,
        spotify_id="0DiWol3AO6WpXZgp0goxAV",
    )


def make_candidate(**kwargs) -> Candidate:
    defaults = dict(
        username="peer",
        filename=r"Music\Daft Punk\Discovery\01 One More Time.flac",
        filesize=40_000_000,
        extension="flac",
        duration=320,
        has_free_slots=True,
        avg_speed=500_000,
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def audio_dir() -> Path:
    return AUDIO_FIXTURES
