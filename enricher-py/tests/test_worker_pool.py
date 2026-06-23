import asyncio

from app.config import Settings
from app.db import connect, init_schema
from app.models import JobStatus
from app.repo import JobRepo
from app.worker import EnrichDeps, run_pool
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


class Clock:
    t = 1_750_000_000.0

    def __call__(self):
        return self.t


async def _repo(tmp_path):
    conn = await connect(tmp_path / "e.db")
    await init_schema(conn)
    return JobRepo(conn, now_fn=Clock())


def _make_tab(root, tab_id):
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")


def _deps(searcher, downloader=None):
    return EnrichDeps(searcher=searcher, downloader=downloader or FakeDownloader(),
                      prober=FakeProber(), settings=Settings(_env_file=None),
                      clock=Clock(), version="0.1.0", yt_dlp_version="test")


async def test_pool_drains_queue(tmp_path, fakes):
    out = tmp_path / "output"
    for i in range(3):
        _make_tab(out, f"eagles/hotel-california-guitar-pro-{i}")
    repo = await _repo(tmp_path)
    try:
        for i in range(3):
            await repo.upsert_pending(f"eagles/hotel-california-guitar-pro-{i}",
                                      f"eagles/hotel-california-guitar-pro-{i}")

        deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
        summary = await run_pool(repo=repo, deps=deps, output_root=out, concurrency=2)

        assert summary["done"] == 3
        counts = await repo.counts()
        assert counts.get("done") == 3
    finally:
        await repo.conn.close()


async def test_pool_records_transient(tmp_path, fakes):
    out = tmp_path / "output"
    _make_tab(out, "eagles/hc-1")
    repo = await _repo(tmp_path)
    try:
        await repo.upsert_pending("eagles/hc-1", "eagles/hc-1")

        deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]),
                     downloader=FakeDownloader(error=RuntimeError("net")))
        await run_pool(repo=repo, deps=deps, output_root=out, concurrency=1)

        job = await repo.get("eagles/hc-1")
        assert job.attempts == 1
        assert job.status == JobStatus.PENDING  # backed off, retryable
    finally:
        await repo.conn.close()


async def test_pool_stops_on_event(tmp_path, fakes):
    out = tmp_path / "output"
    for i in range(5):
        _make_tab(out, f"a/song-{i}")
    repo = await _repo(tmp_path)
    try:
        for i in range(5):
            await repo.upsert_pending(f"a/song-{i}", f"a/song-{i}")

        stop = asyncio.Event()
        stop.set()  # already set -> no new claims, drains immediately
        deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
        summary = await run_pool(repo=repo, deps=deps, output_root=out,
                                 concurrency=2, stop_event=stop)
        assert summary["done"] == 0
        assert (await repo.counts()).get("pending") == 5
    finally:
        await repo.conn.close()
