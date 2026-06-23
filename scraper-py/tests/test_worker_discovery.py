import asyncio

import pytest
import pytest_asyncio

from app import db
from app.config import Settings
from app.models import ServiceState
from app.repo import JobRepo
from app.worker import Worker


class FakeBrowser:
    async def ensure_logged_in(self): ...
    async def is_logged_in(self): return True
    async def scrape(self, url): return []
    async def fetch_explore(self, query): return ""
    async def close(self): ...


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    r = JobRepo(conn)
    yield r
    await conn.close()


async def test_worker_runs_pending_discovery(repo, monkeypatch):
    ran = {}

    async def fake_run(browser, repo_, run, settings, **kw):
        ran["id"] = run.id
        await repo_.finish_discovery(run.id, "done")

    monkeypatch.setattr("app.discovery.runner.run", fake_run)

    await repo.request_discovery({})
    settings = Settings(_env_file=None, poll_interval_seconds=0.01)
    worker = Worker(repo, FakeBrowser(), settings)

    task = asyncio.create_task(worker.run())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if "id" in ran:
            break
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert "id" in ran
    assert await repo.has_active_discovery() is False
