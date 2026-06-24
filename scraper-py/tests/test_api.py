import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import db
from app.config import Settings
from app.main import create_app
from app.repo import JobRepo


class FakeWorker:
    def __init__(self, browser):
        from app.models import ServiceState
        self.browser = browser
        self.state = ServiceState.IDLE
        self.current_job_id = None
        self.notified = 0
        self.resumed = 0

    def notify_enqueued(self):
        self.notified += 1

    def request_resume(self):
        self.resumed += 1


class FakeBrowser:
    async def is_logged_in(self):
        return True


@pytest_asyncio.fixture
async def client():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    repo = JobRepo(conn)
    worker = FakeWorker(FakeBrowser())
    settings = Settings(max_attempts=3)
    app = create_app(repo=repo, worker=worker, settings=settings)
    app.state._worker = worker  # keep handle for assertions
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.worker = worker
        yield c
    await conn.close()


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200


async def test_enqueue_and_get(client):
    r = await client.post("/jobs", json={"url_or_route": "eagles/hotel-1"})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "queued"
    assert job["tab_id"] == "eagles/hotel-1"
    assert client.worker.notified == 1
    r2 = await client.get(f"/jobs/{job['id']}")
    assert r2.status_code == 200


async def test_enqueue_invalid_is_422(client):
    r = await client.post("/jobs", json={"url_or_route": "no-slash"})
    assert r.status_code == 422


async def test_status(client):
    await client.post("/jobs", json={"url_or_route": "a/b-1"})
    r = await client.get("/status")
    body = r.json()
    assert body["queue_depth"] == 1
    assert body["logged_in"] is True


async def test_delete_queued_then_running(client):
    r = await client.post("/jobs", json={"url_or_route": "a/b-1"})
    jid = r.json()["id"]
    assert (await client.delete(f"/jobs/{jid}")).status_code == 200
    # Second delete now 409 (already canceled, not queued).
    assert (await client.delete(f"/jobs/{jid}")).status_code == 409


async def test_clear_queue(client):
    await client.post("/jobs", json={"url_or_route": "a/b-1"})
    await client.post("/jobs", json={"url_or_route": "a/b-2"})
    r = await client.delete("/jobs")
    assert r.status_code == 200
    assert r.json()["canceled"] == 2
    assert (await client.get("/status")).json()["queue_depth"] == 0
    # Idempotent: clearing an empty queue cancels nothing.
    assert (await client.delete("/jobs")).json()["canceled"] == 0


async def test_pause_resume(client):
    assert (await client.post("/pause")).json()["paused"] is True
    r = await client.get("/status")
    assert r.json()["paused"] is True
    assert (await client.post("/resume")).json()["paused"] is False
    assert client.worker.resumed == 1
