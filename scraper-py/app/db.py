from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tab_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    force INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    output_dir TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs(status, next_attempt_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_tab ON jobs(tab_id, status);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tab_metadata (
    tab_id TEXT PRIMARY KEY,
    numeric_id INTEGER,
    route TEXT NOT NULL,
    url TEXT NOT NULL,
    explore_json TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    discovery_run_id TEXT
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    id TEXT PRIMARY KEY,
    params_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    slices_total INTEGER NOT NULL DEFAULT 0,
    slices_done INTEGER NOT NULL DEFAULT 0,
    tabs_found INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_discovery_state ON discovery_runs(state);
"""


async def connect(db_path: str | Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    return conn


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()
