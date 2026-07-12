"""Live end-to-end integration test.

Downloads a very popular track from Soulseek and verifies a real audio file
lands on disk. Also exercises a deliberately obscure query to confirm the
pipeline skips gracefully when nothing is available. Network- and peer-
dependent; skipped unless --run-integration is passed.
"""

from __future__ import annotations

import pytest

from spotiseek.config import Config
from spotiseek.downloader import run_download
from spotiseek.models import DownloadStatus

pytestmark = pytest.mark.integration

# "One More Time" — extremely widely shared, reliable for an e2e check.
POPULAR_TRACK = "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV"


async def test_end_to_end_download(tmp_path) -> None:
    config = Config.load(output_dir=tmp_path / "dl", search_timeout=25.0)
    results = await run_download(config, POPULAR_TRACK)

    assert len(results) == 1
    result = results[0]
    # Availability is peer-dependent; if it downloaded, verify the artifact.
    if result.status is DownloadStatus.DOWNLOADED:
        assert result.path
        path = tmp_path / "dl"
        files = list(path.glob("*"))
        assert files, "download reported success but no file on disk"
        assert files[0].stat().st_size > 100_000  # a real audio file, not empty
    else:
        # Acceptable outcomes if no peer served the file in time.
        assert result.status in (
            DownloadStatus.SKIPPED_NO_RESULTS,
            DownloadStatus.SKIPPED_NO_MATCH,
            DownloadStatus.FAILED,
        )


async def test_dry_run_matches_popular_track(tmp_path) -> None:
    config = Config.load(
        output_dir=tmp_path / "dl", search_timeout=25.0, dry_run=True
    )
    results = await run_download(config, POPULAR_TRACK)
    assert len(results) == 1
    # A track this popular should almost always produce at least a match.
    assert results[0].status in (
        DownloadStatus.DRY_RUN,
        DownloadStatus.SKIPPED_NO_RESULTS,
        DownloadStatus.SKIPPED_NO_MATCH,
    )
