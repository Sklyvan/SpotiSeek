"""Live integration tests for the lossless fallback source.

The Odesli resolve step is a free public API and should work whenever the
network is up. The actual proxy download depends on third-party services whose
URLs the user must configure (SPOTISEEK_<PROVIDER>_API_URL) and which go offline
frequently — so that leg is xfailed/skipped rather than asserted, and only runs
when at least one proxy URL is configured.

Skipped unless --run-integration is passed.
"""

from __future__ import annotations

import pytest

from spotiseek.config import Config
from spotiseek.fallback import FallbackSource, odesli
from spotiseek.models import Track

pytestmark = pytest.mark.integration

# "Never Gonna Give You Up" — resolves on every platform Odesli knows.
SPOTIFY_ID = "4cOdK2wGLETKBW3PvgPWqT"


def test_odesli_resolve_live() -> None:
    result = odesli.resolve(SPOTIFY_ID)
    assert result is not None
    # Odesli should map this ubiquitous track onto Tidal and Deezer at least.
    assert result.id_for("tidal")
    assert result.id_for("deezer")


def test_fallback_download_live(tmp_path) -> None:
    config = Config.load()
    if not any(
        getattr(config, attr)
        for attr in ("tidal_api_url", "qobuz_api_url", "amazon_api_url", "deezer_api_url")
    ):
        pytest.skip("No SPOTISEEK_<PROVIDER>_API_URL configured; nothing to download.")

    track = Track(title="Never Gonna Give You Up", artists=["Rick Astley"],
                  spotify_id=SPOTIFY_ID)
    outcome = FallbackSource(config).download(track, str(tmp_path))
    if outcome is None:
        pytest.xfail("Configured proxy did not deliver (services rotate/go offline).")

    from pathlib import Path

    saved = Path(outcome.path)
    assert saved.exists()
    assert saved.stat().st_size > 64 * 1024
    assert outcome.extension in {"flac", "m4a", "mp3", "wav"}
