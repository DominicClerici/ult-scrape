import pytest

from app.sources.probe import FfprobeProber
from app.sources.youtube import YtDlpSource, yt_dlp_version

pytestmark = pytest.mark.integration


async def test_search_returns_candidates():
    src = YtDlpSource()
    results = await src.search("eagles hotel california", 5)
    assert results
    assert any("topic" in c.channel.lower() for c in results) or len(results) >= 1


async def test_download_and_probe(tmp_path):
    src = YtDlpSource()
    results = await src.search("rick astley never gonna give you up", 3)
    dl = await src.download(results[0].video_id, tmp_path, "bestaudio")
    assert dl.path.exists() and dl.path.stat().st_size > 0
    probe = await FfprobeProber().probe(dl.path)
    assert probe.codec != "unknown"
    assert probe.duration_s and probe.duration_s > 30


def test_version_string():
    assert isinstance(yt_dlp_version(), str)
