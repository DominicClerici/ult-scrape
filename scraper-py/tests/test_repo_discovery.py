import pytest_asyncio

from app import db
from app.repo import JobRepo


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    clock = {"t": 1000.0}
    r = JobRepo(conn, now_fn=lambda: clock["t"])
    r.clock = clock
    yield r
    await conn.close()


async def test_request_and_claim_discovery(repo):
    run = await repo.request_discovery({"max_slices": 5})
    assert run is not None and run.state == "requested"
    assert await repo.has_active_discovery() is True
    # second request rejected while one is active
    assert await repo.request_discovery({}) is None

    claimed = await repo.claim_discovery()
    assert claimed.id == run.id and claimed.state == "running"
    assert claimed.started_at == 1000.0
    assert await repo.claim_discovery() is None  # nothing left to claim


async def test_progress_finish_and_cancel(repo):
    run = await repo.request_discovery({})
    await repo.claim_discovery()
    await repo.update_discovery_progress(run.id, slices_total=10, slices_done=3, tabs_found=120)
    got = await repo.get_discovery_run(run.id)
    assert (got.slices_total, got.slices_done, got.tabs_found) == (10, 3, 120)

    assert await repo.request_discovery_cancel(run.id) is True
    assert await repo.is_discovery_cancel_requested(run.id) is True

    await repo.finish_discovery(run.id, "canceled")
    done = await repo.get_discovery_run(run.id)
    assert done.state == "canceled" and done.finished_at == 1000.0
    assert await repo.has_active_discovery() is False


async def test_claim_skips_run_canceled_while_pending(repo):
    run = await repo.request_discovery({})
    assert await repo.request_discovery_cancel(run.id) is True

    repo.clock["t"] = 1500.0
    assert await repo.claim_discovery() is None  # never reaches 'running'
    got = await repo.get_discovery_run(run.id)
    assert got.state == "canceled"
    assert got.started_at is None
    assert got.finished_at == 1500.0
    assert await repo.has_active_discovery() is False


async def test_upsert_tab_metadata_and_discovered_routes(repo):
    rec = {"id": 111, "tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-a-official-111"}
    await repo.upsert_tab_metadata("run1", rec)
    routes = await repo.discovered_routes(exclude_succeeded=True)
    assert routes == [("band/song-a-official-111",
                       "https://tabs.ultimate-guitar.com/tab/band/song-a-official-111")]

    # re-upsert updates last_seen_at, no duplicate
    repo.clock["t"] = 2000.0
    await repo.upsert_tab_metadata("run2", rec)
    routes2 = await repo.discovered_routes()
    assert len(routes2) == 1

    # excluded once a succeeded job exists for that tab
    await repo.enqueue(tab_id="band/song-a-official-111",
                       url="https://tabs.ultimate-guitar.com/tab/band/song-a-official-111",
                       max_attempts=3)
    job = (await repo.list(status="queued"))[0]
    await repo.mark_succeeded(job.id, "/out/band/song-a-official-111")
    assert await repo.discovered_routes(exclude_succeeded=True) == []


async def test_fail_interrupted_discovery(repo):
    run = await repo.request_discovery({})
    await repo.claim_discovery()
    n = await repo.fail_interrupted_discovery()
    assert n == 1
    got = await repo.get_discovery_run(run.id)
    assert got.state == "failed" and got.error == "interrupted by restart"
