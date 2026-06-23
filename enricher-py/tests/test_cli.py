from app.cli import cmd_run, cmd_scan, cmd_status
from app.config import Settings
from app.discover import find_audio_file
from app.worker import EnrichDeps
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


def _settings(tmp_path):
    return Settings(_env_file=None, output_dir=tmp_path / "output",
                    enricher_db=tmp_path / "e.db", max_concurrency=2)


def _make_tab(root, tab_id, *, audio=None, status=None):
    import json
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    if audio:
        (d / audio).write_bytes(b"x")
    if status:
        (d / "audio.json").write_text(json.dumps({"status": status}))


async def test_scan_enqueues_only_needy(tmp_path):
    out = tmp_path / "output"
    _make_tab(out, "a/needs-1")
    _make_tab(out, "a/has-1", audio="audio.opus")
    _make_tab(out, "a/miss-1", status="no_match")
    counts = await cmd_scan(_settings(tmp_path))
    assert counts["enqueued"] == 1
    assert counts["skipped_done"] == 1
    assert counts["skipped_no_match"] == 1


async def test_run_with_injected_deps(tmp_path, fakes):
    out = tmp_path / "output"
    _make_tab(out, "eagles/hotel-california-guitar-pro-1")
    deps = EnrichDeps(searcher=FakeSearcher(results=[fakes["topic_candidate"]]),
                      downloader=FakeDownloader(), prober=FakeProber(),
                      settings=_settings(tmp_path), clock=lambda: 1.0,
                      version="0.1.0", yt_dlp_version="test")
    summary = await cmd_run(_settings(tmp_path), jobs=2, deps=deps)
    assert summary["done"] == 1
    assert find_audio_file(out / "eagles/hotel-california-guitar-pro-1")


async def test_status(tmp_path):
    await cmd_scan(_settings(tmp_path))  # creates db
    counts = await cmd_status(_settings(tmp_path))
    assert isinstance(counts, dict)
