"""Live Soulseek integration test: log in and search.

Requires network access and Soulseek credentials configured in the environment
/ .env (SOULSEEK_USERNAME + SOULSEEK_PASSWORD); the test is skipped if they are
absent. Search results are peer-dependent, so we only assert that login
succeeds and the search returns without error; a popular query is very likely
(but not guaranteed) to return candidates.
"""

from __future__ import annotations

import pytest

from spotiseek.config import Config
from spotiseek.soulseek.client import SoulseekClient

pytestmark = pytest.mark.integration


async def test_login_and_search(tmp_path) -> None:
    config = Config.load()
    if not config.has_soulseek_credentials:
        pytest.skip("Soulseek credentials not configured (set SOULSEEK_USERNAME/PASSWORD)")
    async with SoulseekClient(
        config.soulseek_username,
        config.soulseek_password,
        tmp_path / "incoming",
    ) as client:
        candidates = await client.search("daft punk one more time", timeout=20)
        # Login worked if we got here; results are best-effort.
        assert isinstance(candidates, list)
        if candidates:
            assert candidates[0].filename
            assert candidates[0].username
