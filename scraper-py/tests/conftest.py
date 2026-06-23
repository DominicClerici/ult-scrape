import pytest_asyncio

from app import db
from app.repo import JobRepo


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    clock = {"t": 1000.0}

    def now():
        return clock["t"]

    r = JobRepo(conn, now_fn=now)
    r.clock = clock  # tests advance time via repo.clock["t"]
    yield r
    await conn.close()
