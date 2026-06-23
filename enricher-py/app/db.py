from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    tab_id          TEXT PRIMARY KEY,
    route           TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    claimed_at      REAL,
    worker_id       TEXT,
    query           TEXT,
    chosen_video_id TEXT,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs(status, next_attempt_at, created_at);
"""


async def connect(db_path: str | Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    return conn


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()
