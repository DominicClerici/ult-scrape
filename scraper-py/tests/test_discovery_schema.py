import pytest_asyncio

from app import db
from app.models import DiscoveryRun, DiscoveryStartRequest, ServiceState


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.init_schema(c)
    yield c
    await c.close()


async def test_discovery_tables_exist(conn):
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {r["name"] for r in await cur.fetchall()}
    assert {"tab_metadata", "discovery_runs"} <= names


async def test_discovering_state_and_models():
    assert ServiceState.DISCOVERING.value == "discovering"
    run = DiscoveryRun(
        id="r1", params={}, state="requested", created_at=1.0,
        started_at=None, finished_at=None, slices_total=0, slices_done=0,
        tabs_found=0, cancel_requested=False, error=None,
    )
    assert run.state == "requested"
    req = DiscoveryStartRequest(genres=[4, 8])
    assert req.genres == [4, 8]
    assert req.sorts is None
