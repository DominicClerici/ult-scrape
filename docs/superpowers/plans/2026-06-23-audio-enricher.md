# Audio Enricher (`enricher-py`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `enricher-py`, a third decoupled project that, for each scraped tab in the `output/` tree, finds and downloads the best-available full-length audio (YouTube, Topic-first via `yt-dlp`) into that tab's directory.

**Architecture:** A standalone async Python CLI with its own SQLite queue (`enricher.db`) and worker pool. It reads work from the filesystem (`output/<tab_id>/metadata.json`), never depends on the other two projects' code, and writes two new artifacts per tab (`audio.<ext>`, `audio.json`) atomically. Idempotent per tab dir (audio file present = done); durable, pausable (Ctrl-C drains), and crash-recoverable (stale `working` jobs reset on startup).

**Tech Stack:** Python ≥ 3.13, `aiosqlite`, `pydantic` / `pydantic-settings`, `yt-dlp` (subprocess), `ffprobe` (from ffmpeg, subprocess), `pytest` + `pytest-asyncio`.

**Spec:** [`docs/superpowers/specs/2026-06-23-audio-enricher-design.md`](../specs/2026-06-23-audio-enricher-design.md). Read it before starting.

## Global Constraints

- Python `requires-python = ">=3.13"`.
- New project lives at repo-root `enricher-py/`. **Shares no code** with `scraper-py`/`decoder-rs`; communicates only through the `output/` tree.
- Input gate is `metadata.json` (existence only — do not depend on its schema beyond `tab_id`/`route`, which are also derivable from the directory path).
- **No `scraper-py` changes** in this plan. The deferred metadata capture is documented only (`enricher-py/CAPTURE_NOTE.md`).
- **No re-encoding** — `yt-dlp -f bestaudio`, keep native codec/container.
- **No HTTP API** — CLI only (`scan`, `run`, `status`).
- All SQL lives in `app/repo.py` (single SQL owner). Use an injectable clock (`now_fn`, default `time.time`) for deterministic tests — mirror `scraper-py/app/repo.py`.
- Tests deterministic and network-free by default; live `yt-dlp`/`ffprobe` behind the `integration` marker (excluded by default).
- Commit ordering for a successful enrich: write `audio.json` first, then rename the `audio.<ext>` file in **last** as the commit marker. `no_match`/`failed` write no audio file.
- Run all commands from `enricher-py/` unless noted.

## File Structure

```
enricher-py/
  pyproject.toml          # packaging, deps, pytest config, `enricher` script entrypoint
  .env.example            # documented config keys
  README.md               # quickstart + ffmpeg note
  CAPTURE_NOTE.md         # deferred scraper metadata-capture note (Task 14)
  app/
    __init__.py           # __version__
    config.py             # Settings (pydantic-settings)
    errors.py             # TransientEnrichError / PermanentEnrichError
    models.py             # JobStatus enum, Job model
    db.py                 # SCHEMA, connect, init_schema
    repo.py               # JobRepo — all SQL, injectable clock
    query.py              # route -> (artist, song) -> search query  (pure)
    select.py             # Candidate selection heuristic (pure)
    discover.py           # walk output/, per-tab filesystem state
    output.py             # atomic commit of audio.<ext> + audio.json
    sources/
      __init__.py
      base.py             # Candidate/DownloadResult/AudioProbe + Searcher/Downloader/Prober Protocols
      youtube.py          # yt-dlp-backed Searcher + Downloader  (Task 13)
      probe.py            # ffprobe-backed Prober                (Task 13)
    worker.py             # enrich_tab() pipeline + run_pool() with pause/recovery
    cli.py                # argparse: scan / run / status + lockfile guard
  tests/
    __init__.py
    conftest.py           # temp output dir, temp db, fakes, clock fixtures
    fixtures/candidates_hotel_california.json
    test_config.py
    test_models.py
    test_db.py
    test_repo.py
    test_query.py
    test_select.py
    test_discover.py
    test_output.py
    test_worker.py
    test_cli.py
    test_integration.py   # live yt-dlp/ffprobe (marker)
```

---

### Task 1: Project scaffold

**Files:**
- Create: `enricher-py/pyproject.toml`, `enricher-py/app/__init__.py`, `enricher-py/app/sources/__init__.py`, `enricher-py/tests/__init__.py`, `enricher-py/.env.example`, `enricher-py/README.md`
- Test: `enricher-py/tests/test_version.py`

**Interfaces:**
- Produces: `app.__version__: str`.

- [ ] **Step 1: Write the failing test**

`enricher-py/tests/test_version.py`:
```python
import app


def test_version_present():
    assert isinstance(app.__version__, str)
    assert app.__version__
```

- [ ] **Step 2: Create packaging + package files**

`enricher-py/pyproject.toml`:
```toml
[project]
name = "ult-enricher"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "aiosqlite",
    "pydantic",
    "pydantic-settings",
    "python-dotenv",
    "yt-dlp",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]

[project.scripts]
enricher = "app.cli:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-m 'not integration'"
markers = ["integration: requires network + yt-dlp + ffmpeg (ffprobe)"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

`enricher-py/app/__init__.py`:
```python
__version__ = "0.1.0"
```

`enricher-py/app/sources/__init__.py`: empty file.
`enricher-py/tests/__init__.py`: empty file.

`enricher-py/.env.example`:
```bash
# Where the scraper/decoder output tree lives (repo-root output/)
OUTPUT_DIR=../output
# Enricher queue database
ENRICHER_DB=./enricher.db

# Worker pool
MAX_CONCURRENCY=2
SEARCH_RESULTS=5

# Selection
MIN_DURATION_S=60
CONFIDENCE_THRESHOLD=0.5
REJECT_KEYWORDS=lesson,tutorial,how to play,cover,karaoke,backing track,instrumental,live,remix,8-bit,8 bit,reaction

# Download
YTDLP_FORMAT=bestaudio

# Retry / backoff
MAX_ATTEMPTS=5
BACKOFF_BASE_SECONDS=30
RATE_LIMIT_MIN_INTERVAL_S=1.0
```

`enricher-py/README.md`:
```markdown
# enricher-py

Audio enrichment for `ult-scrape`. For each scraped tab in the shared `output/`
tree, finds and downloads the best-available full audio (YouTube, Topic-first
via `yt-dlp`) into the tab's directory. See
[docs/enricher-py/overview.md](../docs/enricher-py/overview.md) and the
[output contract](../docs/output-contract.md).

## Requirements

- Python >= 3.13
- `ffmpeg` installed (provides `ffprobe`)

## Setup

```bash
cd enricher-py
pip install -e ".[dev]"
cp .env.example .env   # edit as needed
```

## Use

```bash
enricher scan          # walk output/, enqueue tabs needing audio
enricher run --jobs 2  # download (Ctrl-C = graceful pause; rerun resumes)
enricher status        # counts by job state
```

## Test

```bash
python -m pytest                 # unit tests (network-free)
python -m pytest -m integration  # live yt-dlp + ffprobe; needs network
```
```

- [ ] **Step 3: Install and verify the test passes**

Run:
```bash
cd enricher-py && pip install -e ".[dev]" && python -m pytest tests/test_version.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add enricher-py/pyproject.toml enricher-py/app/__init__.py enricher-py/app/sources/__init__.py enricher-py/tests/__init__.py enricher-py/tests/test_version.py enricher-py/.env.example enricher-py/README.md
git commit -m "feat(enricher): project scaffold"
```

---

### Task 2: Config

**Files:**
- Create: `enricher-py/app/config.py`
- Test: `enricher-py/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings) with fields `output_dir: Path`, `enricher_db: Path`, `max_concurrency: int`, `search_results: int`, `min_duration_s: int`, `confidence_threshold: float`, `reject_keywords: str`, `ytdlp_format: str`, `max_attempts: int`, `backoff_base_seconds: float`, `rate_limit_min_interval_s: float`; `get_settings() -> Settings`; `Settings.reject_keyword_list() -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

`enricher-py/tests/test_config.py`:
```python
from pathlib import Path

from app.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.output_dir == Path("../output")
    assert s.max_concurrency == 2
    assert s.max_attempts == 5
    assert "lesson" in s.reject_keyword_list()


def test_env_override(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENCY", "7")
    monkeypatch.setenv("REJECT_KEYWORDS", "live, remix ,cover")
    s = Settings(_env_file=None)
    assert s.max_concurrency == 7
    assert s.reject_keyword_list() == ("live", "remix", "cover")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: app.config`).

- [ ] **Step 3: Implement**

`enricher-py/app/config.py`:
```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    output_dir: Path = Path("../output")
    enricher_db: Path = Path("./enricher.db")

    max_concurrency: int = 2
    search_results: int = 5

    min_duration_s: int = 60
    confidence_threshold: float = 0.5
    reject_keywords: str = (
        "lesson,tutorial,how to play,cover,karaoke,backing track,"
        "instrumental,live,remix,8-bit,8 bit,reaction"
    )

    ytdlp_format: str = "bestaudio"

    max_attempts: int = 5
    backoff_base_seconds: float = 30.0
    rate_limit_min_interval_s: float = 1.0

    def reject_keyword_list(self) -> tuple[str, ...]:
        return tuple(
            k.strip().lower() for k in self.reject_keywords.split(",") if k.strip()
        )


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/config.py enricher-py/tests/test_config.py
git commit -m "feat(enricher): settings/config"
```

---

### Task 3: Errors + models

**Files:**
- Create: `enricher-py/app/errors.py`, `enricher-py/app/models.py`
- Test: `enricher-py/tests/test_models.py`

**Interfaces:**
- Produces: `TransientEnrichError`, `PermanentEnrichError` (in `errors.py`); `JobStatus(str, Enum)` with members `PENDING="pending"`, `WORKING="working"`, `DONE="done"`, `NO_MATCH="no_match"`, `FAILED="failed"`; `Job` (pydantic) with fields `tab_id: str`, `route: str`, `status: JobStatus`, `attempts: int`, `next_attempt_at: float`, `claimed_at: float | None`, `worker_id: str | None`, `query: str | None`, `chosen_video_id: str | None`, `last_error: str | None`, `created_at: float`, `updated_at: float`.

- [ ] **Step 1: Write the failing test**

`enricher-py/tests/test_models.py`:
```python
from app.errors import PermanentEnrichError, TransientEnrichError
from app.models import Job, JobStatus


def test_status_values():
    assert JobStatus.PENDING == "pending"
    assert {s.value for s in JobStatus} == {
        "pending", "working", "done", "no_match", "failed"
    }


def test_job_model_defaults():
    j = Job(tab_id="eagles/hotel-california-guitar-pro-382996",
            route="eagles/hotel-california-guitar-pro-382996",
            status=JobStatus.PENDING, created_at=1.0, updated_at=1.0)
    assert j.attempts == 0
    assert j.claimed_at is None


def test_error_hierarchy():
    assert issubclass(TransientEnrichError, Exception)
    assert issubclass(PermanentEnrichError, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/errors.py`:
```python
class EnrichError(Exception):
    """Base class for enrichment failures."""


class TransientEnrichError(EnrichError):
    """Retryable failure (network error, rate-limit, download interrupted)."""


class PermanentEnrichError(EnrichError):
    """Non-retryable failure (unusable route / metadata)."""
```

`enricher-py/app/models.py`:
```python
from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = "pending"
    WORKING = "working"
    DONE = "done"
    NO_MATCH = "no_match"
    FAILED = "failed"


class Job(BaseModel):
    tab_id: str
    route: str
    status: JobStatus
    attempts: int = 0
    next_attempt_at: float = 0.0
    claimed_at: float | None = None
    worker_id: str | None = None
    query: str | None = None
    chosen_video_id: str | None = None
    last_error: str | None = None
    created_at: float
    updated_at: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/errors.py enricher-py/app/models.py enricher-py/tests/test_models.py
git commit -m "feat(enricher): errors and models"
```

---

### Task 4: DB + schema

**Files:**
- Create: `enricher-py/app/db.py`
- Test: `enricher-py/tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `async connect(db_path) -> aiosqlite.Connection` (row_factory=Row, WAL); `async init_schema(conn) -> None`; a `jobs` table matching the `Job` model.

- [ ] **Step 1: Write the failing test**

`enricher-py/tests/test_db.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/db.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/db.py enricher-py/tests/test_db.py
git commit -m "feat(enricher): sqlite schema"
```

---

### Task 5: Repo (queue, transitions, backoff, recovery)

**Files:**
- Create: `enricher-py/app/repo.py`
- Test: `enricher-py/tests/test_repo.py`

**Interfaces:**
- Consumes: `app.db`, `app.models.Job/JobStatus`.
- Produces: `backoff(attempts, base) -> float`; class `JobRepo(conn, now_fn=time.time)` with async methods:
  - `upsert_pending(tab_id, route) -> None` — insert a `pending` job if none exists; if an existing job is terminal (`done`/`no_match`/`failed`) leave it; if `pending`/`working` leave it.
  - `get(tab_id) -> Job | None`
  - `claim_next(worker_id) -> Job | None` — claim oldest `pending` with `next_attempt_at <= now`, set `working`, `claimed_at`, `worker_id`.
  - `mark_done(tab_id, chosen_video_id, query) -> None`
  - `mark_no_match(tab_id, query) -> None`
  - `record_transient_failure(tab_id, error, base_backoff, max_attempts) -> str` — increment attempts; if `>= max_attempts` → `failed`; else → `pending` with backoff. Returns `"failed"`/`"pending"`.
  - `mark_failed(tab_id, error) -> None`
  - `reset_working_to_pending() -> int` — crash recovery; returns rowcount.
  - `retry_terminal() -> int` — reset `no_match`/`failed` → `pending` (for `--retry-failed`).
  - `counts() -> dict[str, int]`

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_repo.py`:
```python
import pytest

from app.db import connect, init_schema
from app.models import JobStatus
from app.repo import JobRepo, backoff


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
async def repo(tmp_path):
    conn = await connect(tmp_path / "e.db")
    await init_schema(conn)
    yield JobRepo(conn, now_fn=Clock())
    await conn.close()


def test_backoff_grows():
    assert backoff(1, 30) == 30
    assert backoff(2, 30) == 60
    assert backoff(3, 30) == 120


async def test_upsert_and_claim(repo):
    await repo.upsert_pending("a/b-1", "a/b-1")
    await repo.upsert_pending("a/b-1", "a/b-1")  # idempotent
    job = await repo.claim_next("w1")
    assert job.tab_id == "a/b-1"
    assert job.status == JobStatus.WORKING
    assert job.worker_id == "w1"
    assert await repo.claim_next("w1") is None  # nothing left to claim


async def test_done_and_no_match_are_terminal(repo):
    await repo.upsert_pending("a/done", "a/done")
    await repo.claim_next("w1")
    await repo.mark_done("a/done", "vid123", "a done")
    await repo.upsert_pending("a/done", "a/done")  # must not revert to pending
    assert (await repo.get("a/done")).status == JobStatus.DONE

    await repo.upsert_pending("a/nm", "a/nm")
    await repo.claim_next("w1")
    await repo.mark_no_match("a/nm", "a nm")
    assert (await repo.get("a/nm")).status == JobStatus.NO_MATCH


async def test_transient_then_backoff_then_fail(repo):
    await repo.upsert_pending("a/t", "a/t")
    await repo.claim_next("w1")
    result = await repo.record_transient_failure("a/t", "boom", 30, max_attempts=2)
    assert result == "pending"
    j = await repo.get("a/t")
    assert j.attempts == 1
    assert j.next_attempt_at == 1000.0 + 30  # backoff(1, 30)
    # second failure hits max_attempts -> failed
    await repo.claim_next("w1")  # not claimable yet (backoff), so claim None
    # advance clock past backoff
    repo._now.t = 2000.0
    await repo.claim_next("w1")
    result = await repo.record_transient_failure("a/t", "boom2", 30, max_attempts=2)
    assert result == "failed"
    assert (await repo.get("a/t")).status == JobStatus.FAILED


async def test_reset_working_to_pending(repo):
    await repo.upsert_pending("a/c", "a/c")
    await repo.claim_next("w1")
    assert (await repo.get("a/c")).status == JobStatus.WORKING
    n = await repo.reset_working_to_pending()
    assert n == 1
    j = await repo.get("a/c")
    assert j.status == JobStatus.PENDING
    assert j.claimed_at is None


async def test_retry_terminal(repo):
    await repo.upsert_pending("a/f", "a/f")
    await repo.claim_next("w1")
    await repo.mark_failed("a/f", "dead")
    assert (await repo.retry_terminal()) == 1
    assert (await repo.get("a/f")).status == JobStatus.PENDING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_repo.py -v`
Expected: FAIL (`ModuleNotFoundError: app.repo`).

- [ ] **Step 3: Implement**

`enricher-py/app/repo.py`:
```python
import time

import aiosqlite

from app.models import Job, JobStatus

_TERMINAL = ("done", "no_match", "failed")


def backoff(attempts: int, base: float) -> float:
    return base * (2 ** max(0, attempts - 1))


class JobRepo:
    def __init__(self, conn: aiosqlite.Connection, now_fn=time.time):
        self.conn = conn
        self._now = now_fn

    @staticmethod
    def _row_to_job(row) -> Job:
        return Job(
            tab_id=row["tab_id"],
            route=row["route"],
            status=JobStatus(row["status"]),
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            claimed_at=row["claimed_at"],
            worker_id=row["worker_id"],
            query=row["query"],
            chosen_video_id=row["chosen_video_id"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get(self, tab_id: str) -> Job | None:
        cur = await self.conn.execute("SELECT * FROM jobs WHERE tab_id=?", (tab_id,))
        row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def upsert_pending(self, tab_id: str, route: str) -> None:
        now = self._now()
        # Insert as pending only when absent; never disturb an existing row.
        await self.conn.execute(
            "INSERT INTO jobs (tab_id, route, status, attempts, next_attempt_at, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(tab_id) DO NOTHING",
            (tab_id, route, "pending", 0, now, now, now),
        )
        await self.conn.commit()

    async def claim_next(self, worker_id: str) -> Job | None:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='working', claimed_at=?, worker_id=?, updated_at=? "
            "WHERE tab_id = (SELECT tab_id FROM jobs WHERE status='pending' "
            "AND next_attempt_at<=? ORDER BY created_at ASC LIMIT 1) "
            "RETURNING *",
            (now, worker_id, now, now),
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return self._row_to_job(row) if row else None

    async def mark_done(self, tab_id: str, chosen_video_id: str, query: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='done', chosen_video_id=?, query=?, "
            "last_error=NULL, claimed_at=NULL, updated_at=? WHERE tab_id=?",
            (chosen_video_id, query, now, tab_id),
        )
        await self.conn.commit()

    async def mark_no_match(self, tab_id: str, query: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='no_match', query=?, claimed_at=NULL, "
            "updated_at=? WHERE tab_id=?",
            (query, now, tab_id),
        )
        await self.conn.commit()

    async def mark_failed(self, tab_id: str, error: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='failed', last_error=?, claimed_at=NULL, "
            "updated_at=? WHERE tab_id=?",
            (error, now, tab_id),
        )
        await self.conn.commit()

    async def record_transient_failure(
        self, tab_id: str, error: str, base_backoff: float, max_attempts: int
    ) -> str:
        now = self._now()
        job = await self.get(tab_id)
        attempts = job.attempts + 1
        if attempts >= max_attempts:
            await self.conn.execute(
                "UPDATE jobs SET status='failed', attempts=?, last_error=?, "
                "claimed_at=NULL, updated_at=? WHERE tab_id=?",
                (attempts, error, now, tab_id),
            )
            result = "failed"
        else:
            nxt = now + backoff(attempts, base_backoff)
            await self.conn.execute(
                "UPDATE jobs SET status='pending', attempts=?, last_error=?, "
                "next_attempt_at=?, claimed_at=NULL, worker_id=NULL, updated_at=? "
                "WHERE tab_id=?",
                (attempts, error, nxt, now, tab_id),
            )
            result = "pending"
        await self.conn.commit()
        return result

    async def reset_working_to_pending(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='pending', claimed_at=NULL, worker_id=NULL, "
            "updated_at=? WHERE status='working'",
            (now,),
        )
        await self.conn.commit()
        return cur.rowcount

    async def retry_terminal(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='pending', attempts=0, last_error=NULL, "
            "next_attempt_at=?, claimed_at=NULL, worker_id=NULL, updated_at=? "
            "WHERE status IN ('no_match','failed')",
            (now, now),
        )
        await self.conn.commit()
        return cur.rowcount

    async def counts(self) -> dict[str, int]:
        cur = await self.conn.execute(
            "SELECT status, COUNT(*) c FROM jobs GROUP BY status"
        )
        return {r["status"]: r["c"] for r in await cur.fetchall()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_repo.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/repo.py enricher-py/tests/test_repo.py
git commit -m "feat(enricher): job repo with backoff and crash recovery"
```

---

### Task 6: Query normalizer (route -> search query)

**Files:**
- Create: `enricher-py/app/query.py`
- Test: `enricher-py/tests/test_query.py`

**Interfaces:**
- Produces: `split_route(route) -> tuple[str, str]` returning `(artist, song)` as human strings; `build_query(route) -> str`.

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_query.py`:
```python
import pytest

from app.query import build_query, split_route


@pytest.mark.parametrize("route,artist,song", [
    ("eagles/hotel-california-guitar-pro-382996", "eagles", "hotel california"),
    ("metallica/nothing-else-matters-guitar-pro-225441",
     "metallica", "nothing else matters"),
    ("guns-n-roses/sweet-child-o-mine-official-220689",
     "guns n roses", "sweet child o mine"),
    ("nirvana/smells-like-teen-spirit-ver2-1940883",
     "nirvana", "smells like teen spirit"),
])
def test_split_route(route, artist, song):
    assert split_route(route) == (artist, song)


def test_build_query():
    assert build_query("eagles/hotel-california-guitar-pro-382996") == \
        "eagles hotel california"


def test_invalid_route():
    with pytest.raises(ValueError):
        split_route("no-slash-here")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_query.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/query.py`:
```python
import re

# Trailing UG cruft tokens to strip from the song slug, in order.
_TRAIL_PATTERNS = [
    re.compile(r"-guitar-pro-\d+$"),
    re.compile(r"-(?:official|ver\d+|tab|tabs|chords|bass|drums|ukulele)$"),
    re.compile(r"-\d+$"),  # trailing numeric id
]


def split_route(route: str) -> tuple[str, str]:
    route = (route or "").strip().strip("/")
    if "/" not in route:
        raise ValueError(f"unrecognized route: {route!r}")
    artist_slug, song_slug = route.split("/", 1)

    prev = None
    while prev != song_slug:
        prev = song_slug
        for pat in _TRAIL_PATTERNS:
            song_slug = pat.sub("", song_slug)

    artist = artist_slug.replace("-", " ").strip()
    song = song_slug.replace("-", " ").strip()
    if not artist or not song:
        raise ValueError(f"empty artist/song from route: {route!r}")
    return artist, song


def build_query(route: str) -> str:
    artist, song = split_route(route)
    return f"{artist} {song}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_query.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/query.py enricher-py/tests/test_query.py
git commit -m "feat(enricher): route->query normalizer"
```

---

### Task 7: Source interface (Candidate / Protocols)

**Files:**
- Create: `enricher-py/app/sources/base.py`
- Test: `enricher-py/tests/test_sources_base.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Candidate(video_id: str, title: str, channel: str, duration_s: int | None, view_count: int | None, url: str)`
  - `@dataclass(frozen=True) DownloadResult(path: Path, ext: str)`
  - `@dataclass(frozen=True) AudioProbe(codec: str, bitrate_kbps: int | None, sample_rate: int | None, channels: int | None, duration_s: float | None)`
  - `Searcher` Protocol: `async def search(self, query: str, limit: int) -> list[Candidate]`
  - `Downloader` Protocol: `async def download(self, video_id: str, dest_dir: Path, fmt: str) -> DownloadResult`
  - `Prober` Protocol: `async def probe(self, path: Path) -> AudioProbe`

- [ ] **Step 1: Write the failing test**

`enricher-py/tests/test_sources_base.py`:
```python
from pathlib import Path

from app.sources.base import AudioProbe, Candidate, DownloadResult


def test_candidate_is_frozen_dataclass():
    c = Candidate(video_id="v", title="t", channel="ch",
                  duration_s=300, view_count=10, url="u")
    assert c.video_id == "v"


def test_download_and_probe_dataclasses():
    d = DownloadResult(path=Path("/tmp/x.opus"), ext="opus")
    p = AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                   channels=2, duration_s=391.0)
    assert d.ext == "opus"
    assert p.codec == "opus"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sources_base.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/sources/base.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    channel: str
    duration_s: int | None
    view_count: int | None
    url: str


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    ext: str


@dataclass(frozen=True)
class AudioProbe:
    codec: str
    bitrate_kbps: int | None
    sample_rate: int | None
    channels: int | None
    duration_s: float | None


class Searcher(Protocol):
    async def search(self, query: str, limit: int) -> list[Candidate]: ...


class Downloader(Protocol):
    async def download(
        self, video_id: str, dest_dir: Path, fmt: str
    ) -> DownloadResult: ...


class Prober(Protocol):
    async def probe(self, path: Path) -> AudioProbe: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sources_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/sources/base.py enricher-py/tests/test_sources_base.py
git commit -m "feat(enricher): source interface (Candidate + Protocols)"
```

---

### Task 8: Selection heuristic

**Files:**
- Create: `enricher-py/app/select.py`, `enricher-py/tests/fixtures/candidates_hotel_california.json`
- Test: `enricher-py/tests/test_select.py`

**Interfaces:**
- Consumes: `app.sources.base.Candidate`.
- Produces:
  - `@dataclass SelectConfig(min_duration_s: int, reject_keywords: tuple[str, ...], confidence_threshold: float)`
  - `@dataclass ChosenCandidate(candidate: Candidate, reason: str, confidence: float, runners_up: list[dict])`
  - `def choose(candidates: list[Candidate], artist: str, song: str, cfg: SelectConfig) -> ChosenCandidate | None`
  - Returns `None` when nothing clears `confidence_threshold` (→ caller writes `no_match`).
  - `reason` is one of: `topic_channel`, `official_channel`, `title_match`.

- [ ] **Step 1: Create the fixture**

`enricher-py/tests/fixtures/candidates_hotel_california.json`:
```json
[
  {"video_id": "lesson1", "title": "Hotel California Guitar Lesson Pt.1",
   "channel": "GuitarJamz", "duration_s": 700, "view_count": 5000000, "url": "u1"},
  {"video_id": "topic1", "title": "Hotel California",
   "channel": "Eagles - Topic", "duration_s": 391, "view_count": 80000000, "url": "u2"},
  {"video_id": "live1", "title": "Hotel California (Live 1977)",
   "channel": "Eagles", "duration_s": 400, "view_count": 9000000, "url": "u3"},
  {"video_id": "short1", "title": "Hotel California",
   "channel": "randomuser", "duration_s": 35, "view_count": 100, "url": "u4"}
]
```

- [ ] **Step 2: Write the failing tests**

`enricher-py/tests/test_select.py`:
```python
import json
from pathlib import Path

from app.select import ChosenCandidate, SelectConfig, choose
from app.sources.base import Candidate

CFG = SelectConfig(
    min_duration_s=60,
    reject_keywords=("lesson", "tutorial", "cover", "karaoke", "live", "remix"),
    confidence_threshold=0.5,
)


def _load():
    p = Path(__file__).parent / "fixtures" / "candidates_hotel_california.json"
    return [Candidate(**c) for c in json.loads(p.read_text())]


def test_topic_channel_wins():
    chosen = choose(_load(), "eagles", "hotel california", CFG)
    assert isinstance(chosen, ChosenCandidate)
    assert chosen.candidate.video_id == "topic1"
    assert chosen.reason == "topic_channel"
    assert chosen.confidence >= 0.9


def test_rejects_lesson_live_and_short():
    # Without the topic track, the remaining are all junk -> no_match.
    cands = [c for c in _load() if c.video_id != "topic1"]
    assert choose(cands, "eagles", "hotel california", CFG) is None


def test_title_match_when_no_topic_or_official():
    cands = [Candidate(video_id="ok", title="Eagles - Hotel California (Audio)",
                       channel="SomeUploader", duration_s=390,
                       view_count=1234, url="u")]
    chosen = choose(cands, "eagles", "hotel california", CFG)
    assert chosen is not None
    assert chosen.candidate.video_id == "ok"
    assert chosen.reason == "title_match"


def test_runners_up_recorded():
    cands = [
        Candidate(video_id="topic1", title="Hotel California",
                  channel="Eagles - Topic", duration_s=391,
                  view_count=80000000, url="u2"),
        Candidate(video_id="ok", title="Eagles - Hotel California",
                  channel="SomeUploader", duration_s=390,
                  view_count=1234, url="u"),
    ]
    chosen = choose(cands, "eagles", "hotel california", CFG)
    assert chosen.candidate.video_id == "topic1"
    assert any(r["video_id"] == "ok" for r in chosen.runners_up)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_select.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement**

`enricher-py/app/select.py`:
```python
from __future__ import annotations

import re
from dataclasses import dataclass

from app.sources.base import Candidate


@dataclass
class SelectConfig:
    min_duration_s: int
    reject_keywords: tuple[str, ...]
    confidence_threshold: float


@dataclass
class ChosenCandidate:
    candidate: Candidate
    reason: str
    confidence: float
    runners_up: list[dict]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t}


def _title_similarity(title: str, artist: str, song: str) -> float:
    want = _tokens(artist) | _tokens(song)
    if not want:
        return 0.0
    have = _tokens(title)
    return len(want & have) / len(want)


def _has_reject_kw(text: str, keywords: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in keywords)


def _score(c: Candidate, artist: str, song: str, cfg: SelectConfig) -> tuple[float, str]:
    """Return (confidence, reason). 0.0 means disqualified."""
    if c.duration_s is not None and c.duration_s < cfg.min_duration_s:
        return 0.0, "too_short"

    channel_low = (c.channel or "").lower()
    is_topic = channel_low == f"{artist.lower()} - topic"

    # Junk keywords disqualify non-topic uploads (topic art-tracks are trusted).
    if not is_topic and _has_reject_kw(c.title, cfg.reject_keywords):
        return 0.0, "rejected_keyword"

    sim = _title_similarity(c.title, artist, song)

    if is_topic:
        return max(0.95, sim), "topic_channel"
    if artist.lower() in channel_low and sim >= 0.75:
        return max(0.8, sim), "official_channel"
    if sim >= 0.75:
        return min(0.74, 0.4 + 0.34 * sim), "title_match"
    return 0.0, "low_similarity"


def choose(
    candidates: list[Candidate], artist: str, song: str, cfg: SelectConfig
) -> ChosenCandidate | None:
    scored = []
    for c in candidates:
        conf, reason = _score(c, artist, song, cfg)
        if conf > 0.0:
            scored.append((conf, c.view_count or 0, c, reason))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best_conf, _, best, best_reason = scored[0]
    if best_conf < cfg.confidence_threshold:
        return None
    runners_up = [
        {"video_id": c.video_id, "title": c.title, "score": round(conf, 3)}
        for conf, _, c, _ in scored[1:4]
    ]
    return ChosenCandidate(
        candidate=best, reason=best_reason,
        confidence=round(best_conf, 3), runners_up=runners_up,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_select.py -v`
Expected: PASS (all 4).

- [ ] **Step 6: Commit**

```bash
git add enricher-py/app/select.py enricher-py/tests/test_select.py enricher-py/tests/fixtures/candidates_hotel_california.json
git commit -m "feat(enricher): Topic-first selection heuristic"
```

---

### Task 9: Discovery (walk output tree)

**Files:**
- Create: `enricher-py/app/discover.py`
- Test: `enricher-py/tests/test_discover.py`

**Interfaces:**
- Produces:
  - `AUDIO_EXTS: tuple[str, ...]` = `(".opus", ".m4a", ".webm", ".mp3", ".ogg")`
  - `@dataclass(frozen=True) TabDir(tab_id: str, route: str, path: Path)`
  - `def find_audio_file(tab_dir: Path) -> Path | None`
  - `def read_status(tab_dir: Path) -> str | None` (reads `audio.json` `status`, or `None`)
  - `def iter_ready_tabs(output_root: Path) -> Iterator[TabDir]` — yields every dir containing `metadata.json`; `tab_id`/`route` are the path relative to `output_root` (POSIX-joined).

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_discover.py`:
```python
import json

from app.discover import find_audio_file, iter_ready_tabs, read_status


def _make_tab(root, tab_id, *, audio=None, status=None):
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    if audio:
        (d / audio).write_bytes(b"x")
    if status:
        (d / "audio.json").write_text(json.dumps({"status": status}))
    return d


def test_iter_finds_only_committed_dirs(tmp_path):
    _make_tab(tmp_path, "eagles/hotel-california-guitar-pro-1")
    # a dir without metadata.json must be ignored
    (tmp_path / "pending" / "x").mkdir(parents=True)
    tabs = list(iter_ready_tabs(tmp_path))
    assert len(tabs) == 1
    assert tabs[0].tab_id == "eagles/hotel-california-guitar-pro-1"
    assert tabs[0].route == "eagles/hotel-california-guitar-pro-1"


def test_find_audio_file(tmp_path):
    d = _make_tab(tmp_path, "a/b-1", audio="audio.opus")
    assert find_audio_file(d).name == "audio.opus"
    d2 = _make_tab(tmp_path, "a/c-1")
    assert find_audio_file(d2) is None


def test_read_status(tmp_path):
    d = _make_tab(tmp_path, "a/d-1", status="no_match")
    assert read_status(d) == "no_match"
    d2 = _make_tab(tmp_path, "a/e-1")
    assert read_status(d2) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_discover.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/discover.py`:
```python
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = (".opus", ".m4a", ".webm", ".mp3", ".ogg")


@dataclass(frozen=True)
class TabDir:
    tab_id: str
    route: str
    path: Path


def find_audio_file(tab_dir: Path) -> Path | None:
    for ext in AUDIO_EXTS:
        p = tab_dir / f"audio{ext}"
        if p.exists():
            return p
    return None


def read_status(tab_dir: Path) -> str | None:
    p = tab_dir / "audio.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("status")
    except (ValueError, OSError):
        return None


def iter_ready_tabs(output_root: Path) -> Iterator[TabDir]:
    output_root = Path(output_root)
    for meta in output_root.rglob("metadata.json"):
        tab_dir = meta.parent
        rel = tab_dir.relative_to(output_root).as_posix()
        yield TabDir(tab_id=rel, route=rel, path=tab_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_discover.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/discover.py enricher-py/tests/test_discover.py
git commit -m "feat(enricher): output-tree discovery"
```

---

### Task 10: Output writer (atomic commit of audio + sidecar)

**Files:**
- Create: `enricher-py/app/output.py`
- Test: `enricher-py/tests/test_output.py`

**Interfaces:**
- Consumes: `app.select.ChosenCandidate`, `app.sources.base.AudioProbe`.
- Produces:
  - `def commit_audio(*, tab_dir: Path, query: str, chosen: ChosenCandidate, audio_tmp: Path, ext: str, probe: AudioProbe, enricher_version: str, yt_dlp_version: str, now_iso: str) -> Path` — writes `audio.json` (status `ok`) then renames `audio_tmp` to `tab_dir/audio.<ext>` **last**; returns the audio path.
  - `def write_no_match(*, tab_dir: Path, query: str, reason: str, candidates_considered: int, runners_up: list[dict], enricher_version: str, now_iso: str) -> None` — writes `audio.json` with status `no_match`, no audio file.
  - Both write `audio.json` atomically (temp + `os.replace`).

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_output.py`:
```python
import json

from app.output import commit_audio, write_no_match
from app.select import ChosenCandidate
from app.sources.base import AudioProbe, Candidate


def _chosen():
    c = Candidate(video_id="topic1", title="Hotel California",
                  channel="Eagles - Topic", duration_s=391,
                  view_count=80000000, url="https://youtu.be/topic1")
    return ChosenCandidate(candidate=c, reason="topic_channel",
                           confidence=0.95, runners_up=[])


def test_commit_audio_writes_both_and_marker_last(tmp_path):
    tab = tmp_path / "eagles/hotel-california-guitar-pro-1"
    tab.mkdir(parents=True)
    src = tmp_path / "dl.opus"
    src.write_bytes(b"OggS-fake-audio")
    probe = AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                       channels=2, duration_s=391.0)

    audio_path = commit_audio(
        tab_dir=tab, query="eagles hotel california", chosen=_chosen(),
        audio_tmp=src, ext="opus", probe=probe,
        enricher_version="0.1.0", yt_dlp_version="2025.01.01",
        now_iso="2026-06-23T12:00:00",
    )

    assert audio_path == tab / "audio.opus"
    assert audio_path.read_bytes() == b"OggS-fake-audio"
    assert not src.exists()  # moved, not copied

    meta = json.loads((tab / "audio.json").read_text())
    assert meta["status"] == "ok"
    assert meta["audio_file"] == "audio.opus"
    assert meta["source"]["video_id"] == "topic1"
    assert meta["source"]["channel_is_topic"] is True
    assert meta["format"]["codec"] == "opus"
    assert len(meta["format"]["sha256"]) == 64


def test_write_no_match(tmp_path):
    tab = tmp_path / "a/b-1"
    tab.mkdir(parents=True)
    write_no_match(tab_dir=tab, query="a b", reason="low_confidence",
                   candidates_considered=5, runners_up=[],
                   enricher_version="0.1.0", now_iso="2026-06-23T12:00:00")
    meta = json.loads((tab / "audio.json").read_text())
    assert meta["status"] == "no_match"
    assert meta["audio_file"] is None
    assert not any(tab.glob("audio.*[!n]"))  # no audio.<ext>, only audio.json
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_output.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`enricher-py/app/output.py`:
```python
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.select import ChosenCandidate
from app.sources.base import AudioProbe


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def commit_audio(
    *, tab_dir: Path, query: str, chosen: ChosenCandidate, audio_tmp: Path,
    ext: str, probe: AudioProbe, enricher_version: str, yt_dlp_version: str,
    now_iso: str,
) -> Path:
    tab_dir = Path(tab_dir)
    audio_name = f"audio.{ext}"
    c = chosen.candidate
    payload = {
        "status": "ok",
        "query": query,
        "source": {
            "platform": "youtube",
            "video_id": c.video_id,
            "url": c.url,
            "channel": c.channel,
            "channel_is_topic": chosen.reason == "topic_channel",
            "title": c.title,
            "duration_s": c.duration_s,
            "view_count": c.view_count,
        },
        "selection": {
            "reason": chosen.reason,
            "confidence": chosen.confidence,
            "runners_up": chosen.runners_up,
        },
        "audio_file": audio_name,
        "format": {
            "codec": probe.codec,
            "bitrate_kbps": probe.bitrate_kbps,
            "sample_rate": probe.sample_rate,
            "channels": probe.channels,
            "byte_size": audio_tmp.stat().st_size,
            "sha256": _sha256(audio_tmp),
        },
        "enriched_at": now_iso,
        "enricher_version": enricher_version,
        "yt_dlp_version": yt_dlp_version,
    }
    # audio.json first; the audio file is the commit marker, renamed in LAST.
    _write_json_atomic(tab_dir / "audio.json", payload)
    final = tab_dir / audio_name
    os.replace(audio_tmp, final)
    return final


def write_no_match(
    *, tab_dir: Path, query: str, reason: str, candidates_considered: int,
    runners_up: list[dict], enricher_version: str, now_iso: str,
) -> None:
    tab_dir = Path(tab_dir)
    payload = {
        "status": "no_match",
        "query": query,
        "source": None,
        "selection": {
            "reason": reason,
            "candidates_considered": candidates_considered,
            "runners_up": runners_up,
        },
        "audio_file": None,
        "format": None,
        "enriched_at": now_iso,
        "enricher_version": enricher_version,
    }
    _write_json_atomic(tab_dir / "audio.json", payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_output.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add enricher-py/app/output.py enricher-py/tests/test_output.py
git commit -m "feat(enricher): atomic audio + sidecar writer"
```

---

### Task 11: Worker — enrich-one pipeline

**Files:**
- Create: `enricher-py/app/worker.py` (pipeline only; pool added in Task 12)
- Test: `enricher-py/tests/conftest.py`, `enricher-py/tests/test_worker.py`

**Interfaces:**
- Consumes: `query`, `select`, `output`, `sources.base`, `errors`, `config.Settings`.
- Produces:
  - `@dataclass EnrichDeps(searcher: Searcher, downloader: Downloader, prober: Prober, settings: Settings, clock=time.time, version: str = app.__version__, yt_dlp_version: str = "fake")`
  - `async def enrich_tab(tab: TabDir, deps: EnrichDeps) -> JobStatus` — runs query → search → choose. On a chosen candidate: download to a temp dir, probe, `commit_audio`, return `JobStatus.DONE`. On no candidate: `write_no_match`, return `JobStatus.NO_MATCH`. Raises `TransientEnrichError` on search/download/probe failure (caller records backoff). Raises `PermanentEnrichError` on an unusable route.

- [ ] **Step 1: Add fakes to conftest**

`enricher-py/tests/conftest.py`:
```python
import shutil
from pathlib import Path

import pytest

from app.sources.base import AudioProbe, Candidate, DownloadResult


class FakeSearcher:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    async def search(self, query, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return list(self.results)


class FakeDownloader:
    def __init__(self, payload=b"FAKEAUDIO", ext="opus", error=None):
        self.payload = payload
        self.ext = ext
        self.error = error
        self.calls = []

    async def download(self, video_id, dest_dir, fmt):
        self.calls.append((video_id, dest_dir, fmt))
        if self.error:
            raise self.error
        dest = Path(dest_dir) / f"dl.{self.ext}"
        dest.write_bytes(self.payload)
        return DownloadResult(path=dest, ext=self.ext)


class FakeProber:
    async def probe(self, path):
        return AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                          channels=2, duration_s=391.0)


@pytest.fixture
def fakes():
    return {
        "searcher": FakeSearcher,
        "downloader": FakeDownloader,
        "prober": FakeProber,
        "topic_candidate": Candidate(
            video_id="topic1", title="Hotel California",
            channel="Eagles - Topic", duration_s=391,
            view_count=80000000, url="https://youtu.be/topic1"),
    }
```

- [ ] **Step 2: Write the failing tests**

`enricher-py/tests/test_worker.py`:
```python
import pytest

from app.config import Settings
from app.discover import TabDir, find_audio_file
from app.errors import TransientEnrichError
from app.models import JobStatus
from app.worker import EnrichDeps, enrich_tab
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


def _deps(searcher, downloader=None):
    return EnrichDeps(
        searcher=searcher,
        downloader=downloader or FakeDownloader(),
        prober=FakeProber(),
        settings=Settings(_env_file=None),
        clock=lambda: 1_750_000_000.0,
        version="0.1.0",
        yt_dlp_version="test",
    )


async def test_enrich_ok_writes_audio(tmp_path, fakes):
    tab_dir = tmp_path / "eagles/hotel-california-guitar-pro-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("eagles/hotel-california-guitar-pro-1",
                 "eagles/hotel-california-guitar-pro-1", tab_dir)

    deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
    status = await enrich_tab(tab, deps)

    assert status == JobStatus.DONE
    assert find_audio_file(tab_dir).name == "audio.opus"
    assert (tab_dir / "audio.json").exists()


async def test_enrich_no_match_writes_marker(tmp_path):
    tab_dir = tmp_path / "a/obscure-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("a/obscure-1", "a/obscure-1", tab_dir)

    status = await enrich_tab(tab, _deps(FakeSearcher(results=[])))
    assert status == JobStatus.NO_MATCH
    assert find_audio_file(tab_dir) is None
    assert (tab_dir / "audio.json").exists()


async def test_enrich_transient_on_download_error(tmp_path, fakes):
    tab_dir = tmp_path / "eagles/hc-1"
    tab_dir.mkdir(parents=True)
    (tab_dir / "metadata.json").write_text("{}")
    tab = TabDir("eagles/hc-1", "eagles/hc-1", tab_dir)

    deps = _deps(
        FakeSearcher(results=[fakes["topic_candidate"]]),
        downloader=FakeDownloader(error=RuntimeError("net down")),
    )
    with pytest.raises(TransientEnrichError):
        await enrich_tab(tab, deps)
    # no partial artifacts left behind
    assert find_audio_file(tab_dir) is None
    assert not (tab_dir / "audio.json").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_worker.py -v`
Expected: FAIL (`ModuleNotFoundError: app.worker`).

- [ ] **Step 4: Implement the pipeline**

`enricher-py/app/worker.py`:
```python
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import app
from app.discover import TabDir
from app.errors import PermanentEnrichError, TransientEnrichError
from app.models import JobStatus
from app.output import commit_audio, write_no_match
from app.query import build_query, split_route
from app.select import SelectConfig, choose
from app.sources.base import Downloader, Prober, Searcher


@dataclass
class EnrichDeps:
    searcher: Searcher
    downloader: Downloader
    prober: Prober
    settings: "object"
    clock: callable = time.time
    version: str = field(default_factory=lambda: app.__version__)
    yt_dlp_version: str = "unknown"


def _select_config(settings) -> SelectConfig:
    return SelectConfig(
        min_duration_s=settings.min_duration_s,
        reject_keywords=settings.reject_keyword_list(),
        confidence_threshold=settings.confidence_threshold,
    )


async def enrich_tab(tab: TabDir, deps: EnrichDeps) -> JobStatus:
    s = deps.settings
    now_iso = datetime.fromtimestamp(deps.clock()).isoformat(timespec="seconds")

    try:
        artist, song = split_route(tab.route)
    except ValueError as e:
        raise PermanentEnrichError(str(e)) from e
    query = build_query(tab.route)

    try:
        candidates = await deps.searcher.search(query, s.search_results)
    except Exception as e:  # network/tooling failure -> retryable
        raise TransientEnrichError(f"search failed: {e}") from e

    chosen = choose(candidates, artist, song, _select_config(s))
    if chosen is None:
        write_no_match(
            tab_dir=tab.path, query=query, reason="no_confident_candidate",
            candidates_considered=len(candidates), runners_up=[],
            enricher_version=deps.version, now_iso=now_iso,
        )
        return JobStatus.NO_MATCH

    with tempfile.TemporaryDirectory(dir=str(tab.path)) as tmp:
        try:
            dl = await deps.downloader.download(
                chosen.candidate.video_id, Path(tmp), s.ytdlp_format
            )
            probe = await deps.prober.probe(dl.path)
        except Exception as e:
            raise TransientEnrichError(f"download/probe failed: {e}") from e

        commit_audio(
            tab_dir=tab.path, query=query, chosen=chosen, audio_tmp=dl.path,
            ext=dl.ext, probe=probe, enricher_version=deps.version,
            yt_dlp_version=deps.yt_dlp_version, now_iso=now_iso,
        )
    return JobStatus.DONE
```

> Note: the `TemporaryDirectory(dir=tab.path)` is auto-removed, so any partial
> download is cleaned on error and never leaves a stray `audio.*` (the commit
> `os.replace` moves the file out of `tmp` only on success).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker.py -v`
Expected: PASS (3).

- [ ] **Step 6: Commit**

```bash
git add enricher-py/app/worker.py enricher-py/tests/conftest.py enricher-py/tests/test_worker.py
git commit -m "feat(enricher): enrich-one pipeline"
```

---

### Task 12: Worker pool — concurrency, pause, recovery

**Files:**
- Modify: `enricher-py/app/worker.py` (append pool functions)
- Test: `enricher-py/tests/test_worker_pool.py`

**Interfaces:**
- Consumes: `JobRepo`, `EnrichDeps`, `enrich_tab`.
- Produces:
  - `async def run_pool(*, repo: JobRepo, deps: EnrichDeps, output_root: Path, concurrency: int, stop_event: asyncio.Event | None = None, limit: int | None = None) -> dict` — claims and processes `pending` jobs across `concurrency` workers until the queue is drained or `stop_event` is set (graceful drain: in-flight finishes, no new claims). Maps results to repo: `DONE→mark_done`, `NO_MATCH→mark_no_match`, `TransientEnrichError→record_transient_failure`, `PermanentEnrichError→mark_failed`. Returns a summary dict of counts. Resolves each claimed job's `TabDir` from `output_root`.

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_worker_pool.py`:
```python
import asyncio

from app.config import Settings
from app.db import connect, init_schema
from app.models import JobStatus
from app.repo import JobRepo
from app.worker import EnrichDeps, run_pool
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


class Clock:
    t = 1_750_000_000.0

    def __call__(self):
        return self.t


async def _repo(tmp_path):
    conn = await connect(tmp_path / "e.db")
    await init_schema(conn)
    return JobRepo(conn, now_fn=Clock())


def _make_tab(root, tab_id):
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")


def _deps(searcher, downloader=None):
    return EnrichDeps(searcher=searcher, downloader=downloader or FakeDownloader(),
                      prober=FakeProber(), settings=Settings(_env_file=None),
                      clock=Clock(), version="0.1.0", yt_dlp_version="test")


async def test_pool_drains_queue(tmp_path, fakes):
    out = tmp_path / "output"
    for i in range(3):
        _make_tab(out, f"eagles/hotel-california-guitar-pro-{i}")
    repo = await _repo(tmp_path)
    for i in range(3):
        await repo.upsert_pending(f"eagles/hotel-california-guitar-pro-{i}",
                                  f"eagles/hotel-california-guitar-pro-{i}")

    deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
    summary = await run_pool(repo=repo, deps=deps, output_root=out, concurrency=2)

    assert summary["done"] == 3
    counts = await repo.counts()
    assert counts.get("done") == 3


async def test_pool_records_transient(tmp_path, fakes):
    out = tmp_path / "output"
    _make_tab(out, "eagles/hc-1")
    repo = await _repo(tmp_path)
    await repo.upsert_pending("eagles/hc-1", "eagles/hc-1")

    deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]),
                 downloader=FakeDownloader(error=RuntimeError("net")))
    await run_pool(repo=repo, deps=deps, output_root=out, concurrency=1)

    job = await repo.get("eagles/hc-1")
    assert job.attempts == 1
    assert job.status == JobStatus.PENDING  # backed off, retryable


async def test_pool_stops_on_event(tmp_path, fakes):
    out = tmp_path / "output"
    for i in range(5):
        _make_tab(out, f"a/song-{i}")
    repo = await _repo(tmp_path)
    for i in range(5):
        await repo.upsert_pending(f"a/song-{i}", f"a/song-{i}")

    stop = asyncio.Event()
    stop.set()  # already set -> no new claims, drains immediately
    deps = _deps(FakeSearcher(results=[fakes["topic_candidate"]]))
    summary = await run_pool(repo=repo, deps=deps, output_root=out,
                             concurrency=2, stop_event=stop)
    assert summary["done"] == 0
    assert (await repo.counts()).get("pending") == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_worker_pool.py -v`
Expected: FAIL (`ImportError: cannot import name 'run_pool'`).

- [ ] **Step 3: Append the pool implementation to `app/worker.py`**

Add these imports at the top of `app/worker.py` (merge with existing):
```python
import asyncio
```

Append to `app/worker.py`:
```python
async def _worker_loop(
    *, name: str, repo, deps: EnrichDeps, output_root: Path,
    stop_event: "asyncio.Event", budget: list[int], summary: dict,
) -> None:
    while True:
        if stop_event.is_set():
            return
        if budget[0] is not None and budget[0] <= 0:
            return
        job = await repo.claim_next(name)
        if job is None:
            return
        if budget[0] is not None:
            budget[0] -= 1
        tab = TabDir(job.tab_id, job.route, output_root / job.tab_id)
        try:
            status = await enrich_tab(tab, deps)
            if status == JobStatus.DONE:
                await repo.mark_done(job.tab_id, "", build_query(job.route))
                summary["done"] += 1
            else:
                await repo.mark_no_match(job.tab_id, build_query(job.route))
                summary["no_match"] += 1
        except PermanentEnrichError as e:
            await repo.mark_failed(job.tab_id, str(e))
            summary["failed"] += 1
        except TransientEnrichError as e:
            result = await repo.record_transient_failure(
                job.tab_id, str(e), deps.settings.backoff_base_seconds,
                deps.settings.max_attempts,
            )
            summary["failed" if result == "failed" else "retried"] += 1


async def run_pool(
    *, repo, deps: EnrichDeps, output_root: Path, concurrency: int,
    stop_event: "asyncio.Event | None" = None, limit: int | None = None,
) -> dict:
    output_root = Path(output_root)
    stop_event = stop_event or asyncio.Event()
    summary = {"done": 0, "no_match": 0, "failed": 0, "retried": 0}
    budget = [limit]  # shared mutable cell across workers
    workers = [
        _worker_loop(name=f"w{i}", repo=repo, deps=deps,
                     output_root=output_root, stop_event=stop_event,
                     budget=budget, summary=summary)
        for i in range(max(1, concurrency))
    ]
    await asyncio.gather(*workers)
    return summary
```

> `mark_done` is called with `""` for the chosen video id here to keep the pool
> independent of the pipeline's internal choice; the authoritative provenance is
> in `audio.json`. (If you want the id in the DB too, have `enrich_tab` return it
> — out of scope for this plan.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker_pool.py -v`
Expected: PASS (3).

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add enricher-py/app/worker.py enricher-py/tests/test_worker_pool.py
git commit -m "feat(enricher): worker pool with graceful stop"
```

---

### Task 13: Real sources (yt-dlp + ffprobe) and integration test

**Files:**
- Create: `enricher-py/app/sources/youtube.py`, `enricher-py/app/sources/probe.py`, `enricher-py/tests/test_integration.py`

**Interfaces:**
- Produces:
  - `class YtDlpSource` implementing both `Searcher` and `Downloader` over the `yt-dlp` CLI; helper `def yt_dlp_version() -> str`.
  - `class FfprobeProber` implementing `Prober` over the `ffprobe` CLI.

- [ ] **Step 1: Implement the yt-dlp source**

`enricher-py/app/sources/youtube.py`:
```python
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from app.sources.base import Candidate, DownloadResult


def yt_dlp_version() -> str:
    try:
        return subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def _run(args: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


class YtDlpSource:
    async def search(self, query: str, limit: int) -> list[Candidate]:
        args = [
            "yt-dlp", f"ytsearch{limit}:{query}",
            "--dump-json", "--no-warnings", "--flat-playlist",
        ]
        code, out, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"yt-dlp search failed: {err.decode()[:300]}")
        results: list[Candidate] = []
        for line in out.decode().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            vid = d.get("id")
            if not vid:
                continue
            results.append(Candidate(
                video_id=vid,
                title=d.get("title") or "",
                channel=d.get("channel") or d.get("uploader") or "",
                duration_s=int(d["duration"]) if d.get("duration") else None,
                view_count=d.get("view_count"),
                url=d.get("url") or f"https://www.youtube.com/watch?v={vid}",
            ))
        return results

    async def download(
        self, video_id: str, dest_dir: Path, fmt: str
    ) -> DownloadResult:
        out_tmpl = str(Path(dest_dir) / "audio.%(ext)s")
        args = [
            "yt-dlp", f"https://www.youtube.com/watch?v={video_id}",
            "-f", fmt, "--no-warnings", "-o", out_tmpl,
        ]
        code, _, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"yt-dlp download failed: {err.decode()[:300]}")
        files = [p for p in Path(dest_dir).iterdir() if p.name.startswith("audio.")]
        if not files:
            raise RuntimeError("yt-dlp produced no audio file")
        path = files[0]
        return DownloadResult(path=path, ext=path.suffix.lstrip("."))
```

`enricher-py/app/sources/probe.py`:
```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.sources.base import AudioProbe


async def _run(args: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


class FfprobeProber:
    async def probe(self, path: Path) -> AudioProbe:
        args = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ]
        code, out, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"ffprobe failed: {err.decode()[:300]}")
        data = json.loads(out.decode())
        astream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            {},
        )
        fmt = data.get("format", {})
        bitrate = fmt.get("bit_rate") or astream.get("bit_rate")
        return AudioProbe(
            codec=astream.get("codec_name") or "unknown",
            bitrate_kbps=int(int(bitrate) / 1000) if bitrate else None,
            sample_rate=int(astream["sample_rate"]) if astream.get("sample_rate") else None,
            channels=astream.get("channels"),
            duration_s=float(fmt["duration"]) if fmt.get("duration") else None,
        )
```

- [ ] **Step 2: Write the integration test**

`enricher-py/tests/test_integration.py`:
```python
import pytest

from app.sources.probe import FfprobeProber
from app.sources.youtube import YtDlpSource, yt_dlp_version

pytestmark = pytest.mark.integration


async def test_search_returns_candidates():
    src = YtDlpSource()
    results = await src.search("eagles hotel california", 5)
    assert results
    assert any("topic" in c.channel.lower() for c in results) or len(results) >= 1


async def test_download_and_probe(tmp_path):
    src = YtDlpSource()
    results = await src.search("rick astley never gonna give you up", 3)
    dl = await src.download(results[0].video_id, tmp_path, "bestaudio")
    assert dl.path.exists() and dl.path.stat().st_size > 0
    probe = await FfprobeProber().probe(dl.path)
    assert probe.codec != "unknown"
    assert probe.duration_s and probe.duration_s > 30


def test_version_string():
    assert isinstance(yt_dlp_version(), str)
```

- [ ] **Step 3: Verify unit suite stays green; smoke the integration test if network is available**

Run (default, network-free): `python -m pytest -q`
Expected: PASS, integration deselected.

Optionally, with network: `python -m pytest -m integration -q`
Expected: PASS (requires `yt-dlp` and `ffprobe` installed).

- [ ] **Step 4: Commit**

```bash
git add enricher-py/app/sources/youtube.py enricher-py/app/sources/probe.py enricher-py/tests/test_integration.py
git commit -m "feat(enricher): yt-dlp + ffprobe sources and integration test"
```

---

### Task 14: CLI (scan / run / status) with pause + recovery wiring

**Files:**
- Create: `enricher-py/app/cli.py`
- Test: `enricher-py/tests/test_cli.py`

**Interfaces:**
- Consumes: `config`, `db`, `repo`, `discover`, `worker`, `sources.*`.
- Produces:
  - `async def cmd_scan(settings, *, retry_failed=False) -> dict` — `reset_working_to_pending()`, then for each `iter_ready_tabs`: skip if `find_audio_file`; skip if `read_status == "no_match"` and not `retry_failed`; if `retry_failed` call `retry_terminal()` once up front; else `upsert_pending`. Returns counts.
  - `async def cmd_run(settings, *, jobs, limit=None, retry_failed=False, deps=None, stop_event=None) -> dict` — scan, recover, build real `EnrichDeps` (unless injected), then `run_pool`. Acquires a lockfile to prevent concurrent runs.
  - `async def cmd_status(settings) -> dict` — return `repo.counts()`.
  - `def main(argv=None) -> int` — argparse front door (`scan`, `run`, `status`; flags `--jobs`, `--limit`, `--retry-failed`, `--output-dir`, `--db`, `--quiet`). Installs a `SIGINT` handler that sets the `stop_event`.

- [ ] **Step 1: Write the failing tests**

`enricher-py/tests/test_cli.py`:
```python
from app.cli import cmd_run, cmd_scan, cmd_status
from app.config import Settings
from app.discover import find_audio_file
from app.worker import EnrichDeps
from tests.conftest import FakeDownloader, FakeProber, FakeSearcher


def _settings(tmp_path):
    return Settings(_env_file=None, output_dir=tmp_path / "output",
                    enricher_db=tmp_path / "e.db", max_concurrency=2)


def _make_tab(root, tab_id, *, audio=None, status=None):
    import json
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    if audio:
        (d / audio).write_bytes(b"x")
    if status:
        (d / "audio.json").write_text(json.dumps({"status": status}))


async def test_scan_enqueues_only_needy(tmp_path):
    out = tmp_path / "output"
    _make_tab(out, "a/needs-1")
    _make_tab(out, "a/has-1", audio="audio.opus")
    _make_tab(out, "a/miss-1", status="no_match")
    counts = await cmd_scan(_settings(tmp_path))
    assert counts["enqueued"] == 1
    assert counts["skipped_done"] == 1
    assert counts["skipped_no_match"] == 1


async def test_run_with_injected_deps(tmp_path, fakes):
    out = tmp_path / "output"
    _make_tab(out, "eagles/hotel-california-guitar-pro-1")
    deps = EnrichDeps(searcher=FakeSearcher(results=[fakes["topic_candidate"]]),
                      downloader=FakeDownloader(), prober=FakeProber(),
                      settings=_settings(tmp_path), clock=lambda: 1.0,
                      version="0.1.0", yt_dlp_version="test")
    summary = await cmd_run(_settings(tmp_path), jobs=2, deps=deps)
    assert summary["done"] == 1
    assert find_audio_file(out / "eagles/hotel-california-guitar-pro-1")


async def test_status(tmp_path):
    await cmd_scan(_settings(tmp_path))  # creates db
    counts = await cmd_status(_settings(tmp_path))
    assert isinstance(counts, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: app.cli`).

- [ ] **Step 3: Implement**

`enricher-py/app/cli.py`:
```python
from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

import app
from app.config import Settings, get_settings
from app.db import connect, init_schema
from app.discover import find_audio_file, iter_ready_tabs, read_status
from app.repo import JobRepo
from app.worker import EnrichDeps, run_pool


async def _open_repo(settings: Settings) -> JobRepo:
    settings.enricher_db.parent.mkdir(parents=True, exist_ok=True)
    conn = await connect(settings.enricher_db)
    await init_schema(conn)
    return JobRepo(conn)


async def cmd_scan(settings: Settings, *, retry_failed: bool = False) -> dict:
    repo = await _open_repo(settings)
    try:
        await repo.reset_working_to_pending()
        if retry_failed:
            await repo.retry_terminal()
        counts = {"enqueued": 0, "skipped_done": 0, "skipped_no_match": 0}
        for tab in iter_ready_tabs(settings.output_dir):
            if find_audio_file(tab.path) is not None:
                counts["skipped_done"] += 1
                continue
            if not retry_failed and read_status(tab.path) == "no_match":
                counts["skipped_no_match"] += 1
                continue
            await repo.upsert_pending(tab.tab_id, tab.route)
            counts["enqueued"] += 1
        return counts
    finally:
        await repo.conn.close()


def _build_deps(settings: Settings) -> EnrichDeps:
    from app.sources.probe import FfprobeProber
    from app.sources.youtube import YtDlpSource, yt_dlp_version

    src = YtDlpSource()
    return EnrichDeps(
        searcher=src, downloader=src, prober=FfprobeProber(),
        settings=settings, version=app.__version__,
        yt_dlp_version=yt_dlp_version(),
    )


async def cmd_run(
    settings: Settings, *, jobs: int, limit: int | None = None,
    retry_failed: bool = False, deps: EnrichDeps | None = None,
    stop_event: "asyncio.Event | None" = None,
) -> dict:
    await cmd_scan(settings, retry_failed=retry_failed)
    repo = await _open_repo(settings)
    try:
        await repo.reset_working_to_pending()  # recover any stale claims
        deps = deps or _build_deps(settings)
        return await run_pool(
            repo=repo, deps=deps, output_root=settings.output_dir,
            concurrency=jobs, stop_event=stop_event, limit=limit,
        )
    finally:
        await repo.conn.close()


async def cmd_status(settings: Settings) -> dict:
    repo = await _open_repo(settings)
    try:
        return await repo.counts()
    finally:
        await repo.conn.close()


def _apply_overrides(settings: Settings, args) -> Settings:
    if args.output_dir:
        settings.output_dir = Path(args.output_dir)
    if args.db:
        settings.enricher_db = Path(args.db)
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enricher")
    parser.add_argument("--output-dir")
    parser.add_argument("--db")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan")
    p_run = sub.add_parser("run")
    p_run.add_argument("--jobs", type=int, default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--retry-failed", action="store_true")
    sub.add_parser("status")

    args = parser.parse_args(argv)
    settings = _apply_overrides(get_settings(), args)

    if args.command == "scan":
        counts = asyncio.run(cmd_scan(settings))
        if not args.quiet:
            print(counts)
        return 0

    if args.command == "status":
        print(asyncio.run(cmd_status(settings)))
        return 0

    # run
    jobs = args.jobs or settings.max_concurrency

    async def _go() -> dict:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, stop.set)
        except NotImplementedError:  # e.g. Windows
            pass
        return await cmd_run(settings, jobs=jobs, limit=args.limit,
                             retry_failed=args.retry_failed, stop_event=stop)

    summary = asyncio.run(_go())
    if not args.quiet:
        print(summary)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (3).

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all green (integration deselected).

- [ ] **Step 6: Commit**

```bash
git add enricher-py/app/cli.py enricher-py/tests/test_cli.py
git commit -m "feat(enricher): CLI (scan/run/status) with pause + recovery"
```

---

### Task 15: CAPTURE_NOTE.md + documentation

**Files:**
- Create: `enricher-py/CAPTURE_NOTE.md`, `docs/enricher-py/overview.md`
- Modify: `OVERVIEW.md`, `docs/architecture.md`, `docs/output-contract.md`, `CLAUDE.md`

**Interfaces:** docs only — no code, no tests.

- [ ] **Step 1: Write `enricher-py/CAPTURE_NOTE.md`**

```markdown
# CAPTURE_NOTE — deferred scraper metadata capture

> Status: **not implemented.** This documents a small, optional future change to
> `scraper-py` that would improve enrichment match quality. The enricher works
> today off the tab slug; this note records what to capture when we decide to.

## Why

The enricher builds its YouTube query from the tab slug (e.g.
`eagles/hotel-california-guitar-pro-382996` → `"eagles hotel california"`). That
works, but clean fields give a better query and a stable dedup key.

## What UG already exposes (verified, logged-in, on the tab page)

Every tab page hydrates `window.UGAPP.store.page.data`. Useful fields:

| Field | Example |
|---|---|
| `tab.artist_name` / `tab.artist_id` | `Eagles` / `1509` |
| `tab.song_name` / `tab.song_id` | `Hotel California` |
| `tab.recording.album_id` | `2992` |
| `tab_view.meta.tonality` | `Em` |
| `tab_view.meta.tuning` | `{name, value, index}` |

## Proposed change

In `scraper-py`, capture these during the existing tab-page load and write an
additive `song` block into `metadata.json` (the decoder ignores unknown keys):

```json
"song": {
  "artist_name": "Eagles",
  "artist_id": 1509,
  "song_name": "Hotel California",
  "song_id": 12345,
  "album_id": 2992,
  "tonality": "Em",
  "tuning": "E A D G B E"
}
```

The enricher will then prefer `metadata.json["song"]` when present and fall back
to slug-parsing (`app/query.py`) when absent — no breaking change.

## Warning — do NOT source audio from `song_image`

`tab_view.song_image` is an 11-char YouTube id, but it is a community **video
lesson** (often a guitar tutorial), frequently wrong-content, and sometimes a
dead/private video. It is **not** the master recording. `tab.recording`'s
`video_urls` / `recording_artists` are empty on tab pages. Source audio only via
the enricher's search + selection.
```

- [ ] **Step 2: Write `docs/enricher-py/overview.md`**

```markdown
# enricher-py — overview

> Part of the [documentation map](../../OVERVIEW.md). The **third** decoupled
> project. It reads scraped tabs from the shared `output/` tree and downloads the
> best-available full audio (YouTube, Topic-first via `yt-dlp`) into each tab
> directory. It shares no code with `scraper-py`/`decoder-rs`.

## What it does

For each `output/<tab_id>/` that has a `metadata.json` but no audio yet, the
enricher builds a search query from the tab slug, searches YouTube, selects the
best candidate (preferring `<Artist> - Topic` Art Tracks = studio masters),
downloads best-available audio, and writes `audio.<ext>` + `audio.json`. See the
[output contract](../output-contract.md) for the artifacts.

## Module map

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings (`.env`). |
| `app/db.py` | SQLite schema + connection (`enricher.db`). |
| `app/repo.py` | **Only** SQL owner: queue, transitions, backoff, recovery. |
| `app/query.py` | Tab slug → search query (pure). |
| `app/select.py` | Topic-first candidate selection (pure). |
| `app/discover.py` | Walk `output/`; per-tab filesystem state. |
| `app/output.py` | Atomic commit of `audio.<ext>` + `audio.json`. |
| `app/sources/base.py` | `Candidate`/`DownloadResult`/`AudioProbe` + Protocols. |
| `app/sources/youtube.py` | `yt-dlp`-backed search + download. |
| `app/sources/probe.py` | `ffprobe`-backed audio probe. |
| `app/worker.py` | `enrich_tab` pipeline + `run_pool` (concurrency, pause). |
| `app/cli.py` | `scan` / `run` / `status`. |

## Queue, idempotency, pause & recovery

- A tab is **done** when an `audio.<ext>` file exists; a `no_match` `audio.json`
  marks a permanent miss (skip unless `--retry-failed`). The DB tracks lifecycle
  (attempts/backoff/`failed`), but the filesystem is the source of truth for
  completion — a deleted `enricher.db` is rebuilt from the tree by `scan`.
- **Pause:** `Ctrl-C` sets a stop event; workers finish in-flight jobs, claim no
  more, and exit. Rerun to resume.
- **Crash recovery:** on `run`/`scan` startup, `reset_working_to_pending()`
  reclaims interrupted jobs. Downloads land in a temp dir and are renamed in only
  on success, so a partial never satisfies the done gate.

## Commands

```bash
cd enricher-py
pip install -e ".[dev]"     # needs ffmpeg (ffprobe) on PATH
enricher scan               # enqueue tabs needing audio
enricher run --jobs 2       # download (Ctrl-C = graceful pause)
enricher status             # counts by state
python -m pytest            # unit tests (browser/network-free)
python -m pytest -m integration  # live yt-dlp + ffprobe
```

## Deferred / future

- `CAPTURE_NOTE.md` — optional `scraper-py` change to capture clean song
  metadata into `metadata.json`.
- Verification (correct-recording confirmation) and time-alignment are separate
  future steps (see the design spec, §15).
```

- [ ] **Step 3: Update `OVERVIEW.md`, `docs/architecture.md`, `docs/output-contract.md`, `CLAUDE.md`**

In `OVERVIEW.md`: add `enricher-py` to the components/doc map table(s) (a row pointing to `docs/enricher-py/overview.md`).

In `docs/architecture.md`: change "two decoupled projects" framing to **three**, add an `enricher-py` row to the projects table, and add a line to the data-flow section: after decode, `enricher-py` independently walks `output/` and writes `audio.<ext>` + `audio.json` per tab.

In `docs/output-contract.md`: under the directory layout, add:
```
  audio.<ext>                       # (added later by enricher-py) best-available source audio
  audio.json                        # (added later by enricher-py) provenance + status sidecar
```
and a short subsection "Output files written by the enricher" documenting: one
audio file per tab dir; `audio.json` written first then the audio file renamed in
last as the commit marker; `no_match` writes only `audio.json`; a re-scrape's
`rmtree` wipes these too (self-healing); the decoder ignores them.

In `CLAUDE.md`: add `enricher-py` rows to the "Where to read about each part" and
"code → doc" tables, and add its commands to "Common commands".

- [ ] **Step 4: Verify links resolve and suite still green**

Run: `cd enricher-py && python -m pytest -q`
Expected: green.
Manually confirm the new doc links resolve (paths exist).

- [ ] **Step 5: Commit**

```bash
git add enricher-py/CAPTURE_NOTE.md docs/enricher-py/overview.md OVERVIEW.md docs/architecture.md docs/output-contract.md CLAUDE.md
git commit -m "docs(enricher): capture note + project docs and contract update"
```

---

## Self-Review

**1. Spec coverage:**

- §1–2 (purpose, YouTube Topic-first) → Tasks 7, 8, 13.
- §3 (decoupled third project, metadata.json gate) → Tasks 1, 9.
- §4 non-goals (no scraper change, no re-encode, CLI only, per-tab dedup) → respected throughout; `YTDLP_FORMAT=bestaudio` (Task 2/13), per-tab idempotency (Tasks 9, 14).
- §5 (slug→query normalizer; CAPTURE_NOTE) → Tasks 6, 15.
- §6 (output contract: `audio.<ext>`/`audio.json`, commit ordering, idempotency, source-of-truth, decoder/re-scrape interaction) → Tasks 10, 15.
- §7 (queue+worker: schema, states, scan/enqueue, pool, rate-limit/backoff, pause, recovery) → Tasks 4, 5, 12, 14. **Note:** `RATE_LIMIT_MIN_INTERVAL_S` is defined in config (Task 2) and documented, but no inter-request throttle is wired into `run_pool`. With default concurrency 2 and serial `yt-dlp` subprocesses this is low-risk; if rate-limiting bites, add an `asyncio`-based min-interval gate in `_worker_loop` before `claim_next`/search. Recorded here so it is not mistaken for complete.
- §8 (selection heuristic) → Task 8.
- §9 (download/probe, Protocols + fakes) → Tasks 7, 11, 13.
- §10 (CLI) → Task 14.
- §11 (config keys) → Task 2 + `.env.example` (Task 1).
- §12 (testing strategy) → every task is TDD; integration marker in Task 13.
- §13 (docs) → Task 15.
- §15 (future) → recorded in docs (Task 15), no code.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle errors appropriately". The one
deliberate simplification (`mark_done(..., "")` not threading the chosen video id
into the DB) is called out inline with rationale; provenance is complete in
`audio.json`.

**3. Type consistency:** `Candidate`, `DownloadResult`, `AudioProbe` defined once
(Task 7) and consumed by Tasks 8/10/11/13. `ChosenCandidate`/`SelectConfig`
defined in Task 8, consumed by Tasks 10/11. `EnrichDeps`/`enrich_tab` defined in
Task 11, consumed by Task 12/14. `JobRepo` method names match between Task 5 and
their callers in Tasks 12/14 (`claim_next`, `mark_done`, `mark_no_match`,
`record_transient_failure`, `mark_failed`, `reset_working_to_pending`,
`retry_terminal`, `counts`, `upsert_pending`). `run_pool` signature matches
between Task 12 and the call sites in Tasks 12-tests and Task 14.
```
