from app.db import connect, init_schema


async def test_schema_creates_jobs_table(tmp_path):
    conn = await connect(tmp_path / "e.db")
    await init_schema(conn)
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    )
    assert await cur.fetchone() is not None
    cur = await conn.execute("PRAGMA table_info(jobs)")
    cols = {r["name"] for r in await cur.fetchall()}
    assert {"tab_id", "status", "attempts", "next_attempt_at",
            "claimed_at", "worker_id"} <= cols
    await conn.close()
