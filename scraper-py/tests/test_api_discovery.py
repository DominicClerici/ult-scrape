import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.repo import JobRepo


class FakeWorker:
    def __init__(self):
        self.notified = False
        from app.models import ServiceState
        self.state = ServiceState.IDLE
        self.current_job_id = None

    def notify_enqueued(self):
        self.notified = True

    class _B:
        async def is_logged_in(self):
            return True

    browser = _B()


@pytest_asyncio.fixture
async def client():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    repo = JobRepo(conn)
    app = create_app(repo=repo, worker=FakeWorker(), settings=Settings(_env_file=None))
    yield TestClient(app), repo
    await conn.close()


async def test_discover_start_and_get(client):
    c, repo = client
    r = c.post("/discover", json={"max_slices": 3, "genres": [4]})
    assert r.status_code == 200
    run_id = r.json()["id"]
    assert r.json()["state"] == "requested"
    assert r.json()["params"]["genres"] == [4]

    # 409 while one is active
    assert c.post("/discover", json={}).status_code == 409

    got = c.get(f"/discover/{run_id}")
    assert got.status_code == 200 and got.json()["id"] == run_id
    assert c.get("/discover").json()[0]["id"] == run_id


async def test_discover_accepted_when_jobs_active(client):
    c, repo = client
    await repo.enqueue(tab_id="a/b-official-1", url="https://tabs.ultimate-guitar.com/tab/a/b-official-1", max_attempts=3)
    r = c.post("/discover", json={})
    assert r.status_code == 200
    assert r.json()["state"] == "requested"  # waits for the worker; no browser use yet


async def test_discover_cancel(client):
    c, repo = client
    run_id = c.post("/discover", json={}).json()["id"]
    assert c.post(f"/discover/{run_id}/cancel").status_code == 200
    assert c.post("/discover/does-not-exist/cancel").status_code == 404


async def test_discover_enqueue(client):
    c, repo = client
    await repo.upsert_tab_metadata("run1", {
        "id": 5, "tab_url": "https://tabs.ultimate-guitar.com/tab/a/b-official-5"})
    r = c.post("/discover/enqueue")
    assert r.status_code == 200
    assert [j["tab_id"] for j in r.json()] == ["a/b-official-5"]
    assert (await repo.queue_depth()) == 1

    # Idempotent: re-running returns the existing queued job, no duplicate.
    r2 = c.post("/discover/enqueue")
    assert [j["id"] for j in r2.json()] == [r.json()[0]["id"]]
    assert (await repo.queue_depth()) == 1
