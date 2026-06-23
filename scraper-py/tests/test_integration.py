import os

import pytest

from app.browser.session import CamoufoxBrowserSession
from app.config import get_settings
from app.output import write_job_output

pytestmark = pytest.mark.integration

REASON = "set UG_EMAIL/UG_PASSWORD to run the live integration test"


@pytest.mark.skipif(
    not (os.getenv("UG_EMAIL") and os.getenv("UG_PASSWORD")), reason=REASON
)
async def test_live_scrape_hotel_california(tmp_path):
    settings = get_settings()
    settings.output_dir = tmp_path
    session = CamoufoxBrowserSession(settings)
    await session.start()
    try:
        await session.ensure_logged_in()
        assert await session.is_logged_in()
        url = (
            "https://tabs.ultimate-guitar.com/tab/"
            "eagles/hotel-california-official-1910943"
        )
        artifacts = await session.scrape(url)
        assert artifacts
        assert artifacts[0].data[:4] == b"XTZ\x00"
        final = write_job_output(
            output_root=tmp_path,
            tab_id="eagles/hotel-california-official-1910943",
            url=url,
            route="eagles/hotel-california-official-1910943",
            scraper_version="0.1.0",
            http_status=artifacts[0].http_status,
            artifacts=artifacts,
            scraped_at="live",
        )
        assert (final / "metadata.json").exists()
    finally:
        await session.close()
