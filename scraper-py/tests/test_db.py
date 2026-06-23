from app import db


async def test_connect_and_schema_idempotent():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    await db.init_schema(conn)  # idempotent

    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cur.fetchall()
    names = {r["name"] for r in rows}
    assert "jobs" in names
    assert "app_state" in names
    await conn.close()


async def test_jobs_columns_present():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    cur = await conn.execute("PRAGMA table_info(jobs)")
    cols = {r["name"] for r in await cur.fetchall()}
    expected = {
        "id", "tab_id", "url", "status", "priority", "attempts",
        "max_attempts", "next_attempt_at", "force", "created_at",
        "updated_at", "started_at", "finished_at", "error", "output_dir",
    }
    assert expected <= cols
    await conn.close()
