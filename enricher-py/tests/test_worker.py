import pytest

from app.config import Settings
from app.discover import TabDir, find_audio_file
from app.errors import TransientEnrichError
from app.models import JobStatus
from app.worker import EnrichDeps, enrich_tab
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


def _deps(searcher, downloader=None):
    return EnrichDeps(
        searcher=searcher,
        downloader=downloader or FakeDownloader(),
        prober=FakeProber(),
        settings=Settings(_env_file=None),
        clock=lambda: 1_750_000_000.0,
        version="0.1.0",
        yt_dlp_version="test",
    )


async def test_enrich_ok_writes_audio(tmp_path, fakes):
    tab_dir = tmp_path / "eagles/hotel-california-guitar-pro-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("eagles/hotel-california-guitar-pro-1",
                 "eagles/hotel-california-guitar-pro-1", tab_dir)

    deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
    status = await enrich_tab(tab, deps)

    assert status == JobStatus.DONE
    assert find_audio_file(tab_dir).name == "audio.opus"
    assert (tab_dir / "audio.json").exists()


async def test_enrich_no_match_writes_marker(tmp_path):
    tab_dir = tmp_path / "a/obscure-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("a/obscure-1", "a/obscure-1", tab_dir)

    status = await enrich_tab(tab, _deps(FakeSearcher(results=[])))
    assert status == JobStatus.NO_MATCH
    assert find_audio_file(tab_dir) is None
    assert (tab_dir / "audio.json").exists()


async def test_enrich_transient_on_download_error(tmp_path, fakes):
    tab_dir = tmp_path / "eagles/hc-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("eagles/hc-1", "eagles/hc-1", tab_dir)

    deps = _deps(
        FakeSearcher(results=[fakes["topic_candidate"]]),
        downloader=FakeDownloader(error=RuntimeError("net down")),
    )
    with pytest.raises(TransientEnrichError):
        await enrich_tab(tab, deps)
    # no partial artifacts left behind
    assert find_audio_file(tab_dir) is None
    assert not (tab_dir / "audio.json").exists()
