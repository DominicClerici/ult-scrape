# Scraper-PY Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a long-running local Python service (`scraper-py/`) that logs into Ultimate Guitar once, works a persistent SQLite queue of tab-scrape jobs with a single async worker, captures raw encrypted XTZ bytes to a self-describing per-job output directory, and exposes a FastAPI control surface.

**Architecture:** One asyncio process. FastAPI serves the control API; on lifespan startup it launches a single Camoufox (async) browser, confirms login, and starts a background worker coroutine. The worker owns the browser and processes the queue sequentially. The API and worker coordinate only through SQLite (queue + state) and asyncio events. No decryption happens here — that is a separate Rust project that globs the output directory.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, Camoufox (`camoufox.async_api`) + async Playwright, aiosqlite, pydantic / pydantic-settings, pytest + pytest-asyncio + httpx.

## Global Constraints

- Python 3.13; async-first (worker is an asyncio task in FastAPI's event loop — no threads).
- Runtime deps: `fastapi`, `uvicorn[standard]`, `camoufox[geoip]==0.4.11`, `aiosqlite`, `pydantic-settings`, `python-dotenv`.
- Dev deps: `pytest`, `pytest-asyncio`, `httpx`. `asyncio_mode = "auto"` in pytest config.
- The scraper NEVER decrypts. It captures raw XTZ bytes only.
- Jobs are exact tab routes/URLs — no search-by-title.
- Single sequential worker; at most one `running` job.
- API is localhost-only (`127.0.0.1`); optional `API_KEY` header, off by default.
- Secrets (`UG_EMAIL`, `UG_PASSWORD`) are never written to logs.
- Output contract is the frozen interface to the Rust decoder: `OUTPUT_DIR/<tab_id>/` with raw `.xtz` files plus `metadata.json` written last; the whole directory is committed via an atomic rename.
- `scraper_version` is sourced from `app.__version__` (`"0.1.0"`).
- All `repo`/`worker`/`output`/`api` tests are async and run under `pytest-asyncio` auto mode. Browser modules (`app/browser/*`) are verified by a marker-gated integration test, not unit tests.

---

### Task 1: Project scaffold, config, version, errors

**Files:**
- Create: `scraper-py/pyproject.toml`
- Create: `scraper-py/.env.example`
- Create: `scraper-py/.gitignore`
- Create: `scraper-py/app/__init__.py`
- Create: `scraper-py/app/config.py`
- Create: `scraper-py/app/errors.py`
- Test: `scraper-py/tests/test_config.py`

**Interfaces:**
- Produces: `app.__version__: str`; `Settings` (pydantic-settings) with fields `ug_email, ug_password, ug_proxy: str`, `output_dir, db_path, profile_dir: Path`, `headless: bool`, `max_attempts: int`, `backoff_base_seconds, inter_job_delay_min, inter_job_delay_max: float`, `cloudflare_timeout_ms, capture_window_ms, api_port: int`, `api_host, api_key: str`, `poll_interval_seconds: float`; `get_settings() -> Settings`.
- Produces error hierarchy: `ScrapeError(Exception)`, `TransientScrapeError`, `PermanentScrapeError`, `SessionExpiredError`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "ult-scraper"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "camoufox[geoip]==0.4.11",
    "aiosqlite",
    "pydantic-settings",
    "python-dotenv",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "httpx"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-m 'not integration'"
markers = ["integration: requires a live browser + UG credentials + network"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 2: Create `.gitignore` and `.env.example`**

`scraper-py/.gitignore`:
```
.env
__pycache__/
.venv/
*.db
*.db-wal
*.db-shm
camoufox-profile/
output/
```

`scraper-py/.env.example`:
```
UG_EMAIL=
UG_PASSWORD=
UG_PROXY=
OUTPUT_DIR=./output
DB_PATH=./scraper.db
PROFILE_DIR=./camoufox-profile
HEADLESS=false
MAX_ATTEMPTS=3
BACKOFF_BASE_SECONDS=30
INTER_JOB_DELAY_MIN=5
INTER_JOB_DELAY_MAX=20
CLOUDFLARE_TIMEOUT_MS=120000
CAPTURE_WINDOW_MS=10000
API_HOST=127.0.0.1
API_PORT=8000
API_KEY=
POLL_INTERVAL_SECONDS=5
```

- [ ] **Step 3: Create `app/__init__.py` and `app/errors.py`**

`app/__init__.py`:
```python
__version__ = "0.1.0"
```

`app/errors.py`:
```python
class ScrapeError(Exception):
    """Base class for scrape failures."""


class TransientScrapeError(ScrapeError):
    """Retryable failure (cloudflare/navigation timeout, no XTZ captured)."""


class PermanentScrapeError(ScrapeError):
    """Non-retryable failure (tab 404 / invalid route)."""


class SessionExpiredError(ScrapeError):
    """Logged-out state detected; re-login and resume without consuming a retry."""
```

- [ ] **Step 4: Write the failing test**

`scraper-py/tests/test_config.py`:
```python
from pathlib import Path

from app import __version__
from app.config import get_settings


def test_version_is_string():
    assert __version__ == "0.1.0"


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("UG_EMAIL", "a@b.com")
    monkeypatch.setenv("UG_PASSWORD", "secret")
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("OUTPUT_DIR", "/tmp/out")
    s = get_settings()
    assert s.ug_email == "a@b.com"
    assert s.ug_password == "secret"
    assert s.headless is False
    assert s.max_attempts == 5
    assert s.output_dir == Path("/tmp/out")


def test_defaults():
    s = get_settings()
    assert s.api_host == "127.0.0.1"
    assert s.max_attempts == 3
    assert s.headless is False
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 6: Implement `app/config.py`**

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ug_email: str = ""
    ug_password: str = ""
    ug_proxy: str = ""

    output_dir: Path = Path("./output")
    db_path: Path = Path("./scraper.db")
    profile_dir: Path = Path("./camoufox-profile")

    headless: bool = False
    max_attempts: int = 3
    backoff_base_seconds: float = 30.0
    inter_job_delay_min: float = 5.0
    inter_job_delay_max: float = 20.0
    cloudflare_timeout_ms: int = 120_000
    capture_window_ms: int = 10_000
    poll_interval_seconds: float = 5.0

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_key: str = ""


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): scaffold project, config, errors"
```

---

### Task 2: Tab normalization

**Files:**
- Create: `scraper-py/app/normalize.py`
- Test: `scraper-py/tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize_tab(url_or_route: str) -> tuple[str, str]` returning `(tab_id, tab_url)`. Raises `ValueError` on invalid input. `TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"`.

- [ ] **Step 1: Write the failing test**

`scraper-py/tests/test_normalize.py`:
```python
import pytest

from app.normalize import normalize_tab

ROUTE = "eagles/hotel-california-official-1910943"
URL = f"https://tabs.ultimate-guitar.com/tab/{ROUTE}"


def test_bare_route():
    assert normalize_tab(ROUTE) == (ROUTE, URL)


def test_full_tabs_url():
    assert normalize_tab(URL) == (ROUTE, URL)


def test_www_url_with_tab_path():
    assert normalize_tab(f"https://www.ultimate-guitar.com/tab/{ROUTE}") == (ROUTE, URL)


def test_trailing_slash_trimmed():
    assert normalize_tab(f"/{ROUTE}/") == (ROUTE, URL)


def test_url_without_tab_segment_raises():
    with pytest.raises(ValueError):
        normalize_tab("https://example.com/foo/bar")


def test_empty_raises():
    with pytest.raises(ValueError):
        normalize_tab("   ")


def test_single_segment_raises():
    with pytest.raises(ValueError):
        normalize_tab("just-one-segment")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.normalize'`

- [ ] **Step 3: Implement `app/normalize.py`**

```python
from urllib.parse import urlparse

TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"


def normalize_tab(url_or_route: str) -> tuple[str, str]:
    raw = (url_or_route or "").strip()
    if not raw:
        raise ValueError("empty tab reference")

    if raw.startswith(("http://", "https://")):
        path = urlparse(raw).path
        marker = "/tab/"
        idx = path.find(marker)
        if idx == -1:
            raise ValueError(f"not a UG tab URL: {url_or_route!r}")
        route = path[idx + len(marker):]
    else:
        route = raw

    tab_id = route.strip("/")
    if not tab_id or "/" not in tab_id:
        raise ValueError(f"unrecognized tab route: {url_or_route!r}")

    return tab_id, f"{TAB_BASE_URL}/{tab_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_normalize.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): tab route/URL normalization"
```

---

### Task 3: Domain models

**Files:**
- Create: `scraper-py/app/models.py`
- Test: `scraper-py/tests/test_models.py`

**Interfaces:**
- Produces: `JobStatus(str, Enum)` = `QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELED`; `ServiceState(str, Enum)` = `STARTING, LOGGING_IN, IDLE, RUNNING, PAUSED, ERROR`; `Job(BaseModel)` with fields `id, tab_id, url: str`, `status: JobStatus`, `priority, attempts, max_attempts: int`, `next_attempt_at: float`, `force: bool`, `created_at, updated_at: float`, `started_at, finished_at: float | None`, `error, output_dir: str | None`; `EnqueueRequest(BaseModel)` = `url_or_route: str`, `priority: int = 0`, `force: bool = False`; `BulkEnqueueRequest(BaseModel)` = `items: list[EnqueueRequest]`; `StatusResponse(BaseModel)` = `state: ServiceState`, `current_job_id: str | None`, `queue_depth: int`, `counts: dict[str, int]`, `paused: bool`, `logged_in: bool`.

- [ ] **Step 1: Write the failing test**

`scraper-py/tests/test_models.py`:
```python
from app.models import (
    EnqueueRequest,
    Job,
    JobStatus,
    ServiceState,
    StatusResponse,
)


def test_job_status_values():
    assert JobStatus.QUEUED == "queued"
    assert JobStatus.SUCCEEDED == "succeeded"
    assert ServiceState.RUNNING == "running"


def test_enqueue_request_defaults():
    req = EnqueueRequest(url_or_route="a/b-1")
    assert req.priority == 0
    assert req.force is False


def test_job_round_trip():
    job = Job(
        id="x",
        tab_id="a/b-1",
        url="https://tabs.ultimate-guitar.com/tab/a/b-1",
        status=JobStatus.QUEUED,
        priority=0,
        attempts=0,
        max_attempts=3,
        next_attempt_at=0.0,
        force=False,
        created_at=1.0,
        updated_at=1.0,
    )
    assert job.started_at is None
    assert job.status is JobStatus.QUEUED


def test_status_response():
    resp = StatusResponse(
        state=ServiceState.IDLE,
        current_job_id=None,
        queue_depth=2,
        counts={"queued": 2},
        paused=False,
        logged_in=True,
    )
    assert resp.queue_depth == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Implement `app/models.py`**

```python
from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ServiceState(str, Enum):
    STARTING = "starting"
    LOGGING_IN = "logging_in"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class Job(BaseModel):
    id: str
    tab_id: str
    url: str
    status: JobStatus
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    next_attempt_at: float = 0.0
    force: bool = False
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    output_dir: str | None = None


class EnqueueRequest(BaseModel):
    url_or_route: str
    priority: int = 0
    force: bool = False


class BulkEnqueueRequest(BaseModel):
    items: list[EnqueueRequest]


class StatusResponse(BaseModel):
    state: ServiceState
    current_job_id: str | None
    queue_depth: int
    counts: dict[str, int]
    paused: bool
    logged_in: bool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): domain models"
```

---

### Task 4: SQLite connection + schema

**Files:**
- Create: `scraper-py/app/db.py`
- Test: `scraper-py/tests/test_db.py`

**Interfaces:**
- Produces: `async def connect(db_path: str | Path) -> aiosqlite.Connection` (sets `row_factory = aiosqlite.Row`, WAL mode); `async def init_schema(conn: aiosqlite.Connection) -> None` (creates `jobs` and `app_state` tables, idempotent).

- [ ] **Step 1: Write the failing test**

`scraper-py/tests/test_db.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement `app/db.py`**

```python
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): sqlite connection + schema"
```

---

### Task 5: Job repository — enqueue, reads, dedup, pause flag

**Files:**
- Create: `scraper-py/app/repo.py`
- Test: `scraper-py/tests/conftest.py`
- Test: `scraper-py/tests/test_repo_basic.py`

**Interfaces:**
- Produces: `class JobRepo` constructed as `JobRepo(conn, now_fn=time.time)`. This task adds:
  `async def enqueue(self, *, tab_id, url, priority=0, force=False, max_attempts) -> Job`,
  `async def get(self, job_id) -> Job | None`,
  `async def list(self, status=None, limit=50, offset=0) -> list[Job]`,
  `async def counts(self) -> dict[str, int]`,
  `async def queue_depth(self) -> int`,
  `async def set_paused(self, paused: bool) -> None`,
  `async def is_paused(self) -> bool`,
  and an internal `_row_to_job(row) -> Job`.
- Consumes: `app.db.connect/init_schema`, `app.models.Job/JobStatus`.

- [ ] **Step 1: Create shared test fixture**

`scraper-py/tests/conftest.py`:
```python
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
```

- [ ] **Step 2: Write the failing test**

`scraper-py/tests/test_repo_basic.py`:
```python
from app.models import JobStatus


async def test_enqueue_creates_queued_job(repo):
    job = await repo.enqueue(
        tab_id="a/b-1", url="u", max_attempts=3
    )
    assert job.status is JobStatus.QUEUED
    assert job.tab_id == "a/b-1"
    assert job.attempts == 0
    assert (await repo.get(job.id)).id == job.id


async def test_queue_depth_and_counts(repo):
    await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.enqueue(tab_id="a/b-2", url="u", max_attempts=3)
    assert await repo.queue_depth() == 2
    assert (await repo.counts())["queued"] == 2


async def test_dedup_returns_existing_succeeded(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    # Manually mark succeeded for the dedup path.
    await repo.conn.execute(
        "UPDATE jobs SET status='succeeded', output_dir='/out/a/b-1', finished_at=1 WHERE id=?",
        (first.id,),
    )
    await repo.conn.commit()
    again = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert again.id == first.id  # no new job created
    assert again.status is JobStatus.SUCCEEDED


async def test_force_bypasses_dedup(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.conn.execute(
        "UPDATE jobs SET status='succeeded' WHERE id=?", (first.id,)
    )
    await repo.conn.commit()
    forced = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    assert forced.id != first.id
    assert forced.status is JobStatus.QUEUED


async def test_pause_flag(repo):
    assert await repo.is_paused() is False
    await repo.set_paused(True)
    assert await repo.is_paused() is True
    await repo.set_paused(False)
    assert await repo.is_paused() is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_repo_basic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repo'`

- [ ] **Step 4: Implement `app/repo.py` (this task's methods)**

```python
import time
from uuid import uuid4

import aiosqlite

from app.models import Job, JobStatus


def backoff(attempts: int, base: float) -> float:
    return base * (2 ** max(0, attempts - 1))


class JobRepo:
    def __init__(self, conn: aiosqlite.Connection, now_fn=time.time):
        self.conn = conn
        self._now = now_fn

    @staticmethod
    def _row_to_job(row) -> Job:
        return Job(
            id=row["id"],
            tab_id=row["tab_id"],
            url=row["url"],
            status=JobStatus(row["status"]),
            priority=row["priority"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            next_attempt_at=row["next_attempt_at"],
            force=bool(row["force"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            output_dir=row["output_dir"],
        )

    async def get(self, job_id: str) -> Job | None:
        cur = await self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def _latest_succeeded(self, tab_id: str) -> Job | None:
        cur = await self.conn.execute(
            "SELECT * FROM jobs WHERE tab_id=? AND status='succeeded' "
            "ORDER BY finished_at DESC LIMIT 1",
            (tab_id,),
        )
        row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def enqueue(
        self, *, tab_id: str, url: str, priority: int = 0,
        force: bool = False, max_attempts: int,
    ) -> Job:
        if not force:
            existing = await self._latest_succeeded(tab_id)
            if existing is not None:
                return existing
        job_id = str(uuid4())
        now = self._now()
        await self.conn.execute(
            "INSERT INTO jobs (id, tab_id, url, status, priority, attempts, "
            "max_attempts, next_attempt_at, force, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, tab_id, url, "queued", priority, 0, max_attempts, 0,
             int(force), now, now),
        )
        await self.conn.commit()
        return await self.get(job_id)

    async def list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Job]:
        if status:
            cur = await self.conn.execute(
                "SELECT * FROM jobs WHERE status=? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_job(r) for r in await cur.fetchall()]

    async def counts(self) -> dict[str, int]:
        cur = await self.conn.execute(
            "SELECT status, COUNT(*) c FROM jobs GROUP BY status"
        )
        return {r["status"]: r["c"] for r in await cur.fetchall()}

    async def queue_depth(self) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status='queued'"
        )
        return (await cur.fetchone())["c"]

    async def set_paused(self, paused: bool) -> None:
        await self.conn.execute(
            "INSERT INTO app_state(key, value) VALUES('paused', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if paused else "0",),
        )
        await self.conn.commit()

    async def is_paused(self) -> bool:
        cur = await self.conn.execute(
            "SELECT value FROM app_state WHERE key='paused'"
        )
        row = await cur.fetchone()
        return bool(row and row["value"] == "1")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_repo_basic.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): job repo enqueue/reads/dedup/pause"
```

---

### Task 6: Job repository — claim + state transitions

**Files:**
- Modify: `scraper-py/app/repo.py`
- Test: `scraper-py/tests/test_repo_transitions.py`

**Interfaces:**
- Produces (added to `JobRepo`):
  `async def claim_next(self) -> Job | None` (atomic `queued`→`running`, eligible when `next_attempt_at <= now`, ordered by priority then created_at),
  `async def succeeded_output_for(self, tab_id, exclude_id) -> str | None`,
  `async def mark_succeeded(self, job_id, output_dir) -> None`,
  `async def mark_permanent_failure(self, job_id, error) -> None` (sets `failed`, `attempts++`),
  `async def record_transient_failure(self, job_id, error, base_backoff) -> str` (returns `"failed"` or `"queued"`),
  `async def requeue_unchanged(self, job_id) -> None` (session expiry: `running`→`queued`, attempts unchanged),
  `async def cancel(self, job_id) -> bool` (only `queued`→`canceled`),
  `async def retry(self, job_id) -> bool` (only `failed`→`queued`, attempts reset to 0).
- Consumes: module-level `backoff(attempts, base)` from Task 5.

- [ ] **Step 1: Write the failing test**

`scraper-py/tests/test_repo_transitions.py`:
```python
from app.models import JobStatus


async def test_claim_next_marks_running(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    claimed = await repo.claim_next()
    assert claimed.id == job.id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.started_at is not None
    # Nothing left to claim.
    assert await repo.claim_next() is None


async def test_claim_respects_priority_then_created(repo):
    low = await repo.enqueue(tab_id="a/b-1", url="u", priority=10, max_attempts=3)
    high = await repo.enqueue(tab_id="a/b-2", url="u", priority=0, max_attempts=3)
    first = await repo.claim_next()
    assert first.id == high.id


async def test_claim_skips_future_next_attempt(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.conn.execute(
        "UPDATE jobs SET next_attempt_at=99999 WHERE id=?", (job.id,)
    )
    await repo.conn.commit()
    assert await repo.claim_next() is None


async def test_mark_succeeded(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    await repo.mark_succeeded(job.id, "/out/a/b-1")
    got = await repo.get(job.id)
    assert got.status is JobStatus.SUCCEEDED
    assert got.output_dir == "/out/a/b-1"


async def test_succeeded_output_for_excludes_self(repo):
    a = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.mark_succeeded(a.id, "/out/a/b-1")
    b = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    found = await repo.succeeded_output_for("a/b-1", exclude_id=b.id)
    assert found == "/out/a/b-1"
    assert await repo.succeeded_output_for("a/b-1", exclude_id=a.id) is None


async def test_permanent_failure(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    await repo.mark_permanent_failure(job.id, "404")
    got = await repo.get(job.id)
    assert got.status is JobStatus.FAILED
    assert got.attempts == 1
    assert got.error == "404"


async def test_transient_failure_requeues_with_backoff(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    result = await repo.record_transient_failure(job.id, "timeout", base_backoff=10)
    assert result == "queued"
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 1
    assert got.next_attempt_at == 1000.0 + 10  # base * 2**0


async def test_transient_failure_exhausts_to_failed(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=2)
    await repo.claim_next()
    assert await repo.record_transient_failure(job.id, "t", 10) == "queued"
    await repo.claim_next()
    assert await repo.record_transient_failure(job.id, "t", 10) == "failed"
    assert (await repo.get(job.id)).status is JobStatus.FAILED


async def test_requeue_unchanged_keeps_attempts(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    await repo.requeue_unchanged(job.id)
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 0


async def test_cancel_only_queued(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert await repo.cancel(job.id) is True
    assert (await repo.get(job.id)).status is JobStatus.CANCELED
    # Cannot cancel a running job.
    job2 = await repo.enqueue(tab_id="a/b-2", url="u", max_attempts=3)
    await repo.claim_next()
    assert await repo.cancel(job2.id) is False


async def test_retry_only_failed_resets_attempts(repo):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=1)
    await repo.claim_next()
    await repo.record_transient_failure(job.id, "t", 10)  # -> failed (max=1)
    assert (await repo.get(job.id)).status is JobStatus.FAILED
    assert await repo.retry(job.id) is True
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 0
    assert got.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_repo_transitions.py -v`
Expected: FAIL — `AttributeError: 'JobRepo' object has no attribute 'claim_next'`

- [ ] **Step 3: Add transition methods to `app/repo.py`**

Append these methods inside the `JobRepo` class:
```python
    async def claim_next(self) -> Job | None:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='running', started_at=?, updated_at=? "
            "WHERE id = (SELECT id FROM jobs WHERE status='queued' "
            "AND next_attempt_at<=? ORDER BY priority ASC, created_at ASC LIMIT 1) "
            "RETURNING *",
            (now, now, now),
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return self._row_to_job(row) if row else None

    async def succeeded_output_for(self, tab_id: str, exclude_id: str) -> str | None:
        cur = await self.conn.execute(
            "SELECT output_dir FROM jobs WHERE tab_id=? AND status='succeeded' "
            "AND id!=? AND output_dir IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
            (tab_id, exclude_id),
        )
        row = await cur.fetchone()
        return row["output_dir"] if row else None

    async def mark_succeeded(self, job_id: str, output_dir: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='succeeded', output_dir=?, finished_at=?, "
            "updated_at=?, error=NULL WHERE id=?",
            (output_dir, now, now, job_id),
        )
        await self.conn.commit()

    async def mark_permanent_failure(self, job_id: str, error: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='failed', attempts=attempts+1, error=?, "
            "finished_at=?, updated_at=? WHERE id=?",
            (error, now, now, job_id),
        )
        await self.conn.commit()

    async def record_transient_failure(
        self, job_id: str, error: str, base_backoff: float
    ) -> str:
        now = self._now()
        job = await self.get(job_id)
        attempts = job.attempts + 1
        if attempts >= job.max_attempts:
            await self.conn.execute(
                "UPDATE jobs SET status='failed', attempts=?, error=?, "
                "finished_at=?, updated_at=? WHERE id=?",
                (attempts, error, now, now, job_id),
            )
            result = "failed"
        else:
            nxt = now + backoff(attempts, base_backoff)
            await self.conn.execute(
                "UPDATE jobs SET status='queued', attempts=?, error=?, "
                "next_attempt_at=?, started_at=NULL, updated_at=? WHERE id=?",
                (attempts, error, nxt, now, job_id),
            )
            result = "queued"
        await self.conn.commit()
        return result

    async def requeue_unchanged(self, job_id: str) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='queued', started_at=NULL, updated_at=? WHERE id=?",
            (now, job_id),
        )
        await self.conn.commit()

    async def cancel(self, job_id: str) -> bool:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='canceled', finished_at=?, updated_at=? "
            "WHERE id=? AND status='queued'",
            (now, now, job_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def retry(self, job_id: str) -> bool:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='queued', attempts=0, error=NULL, "
            "next_attempt_at=?, started_at=NULL, finished_at=NULL, updated_at=? "
            "WHERE id=? AND status='failed'",
            (now, now, job_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_repo_transitions.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): repo claim + state transitions"
```

---

### Task 7: Browser base types + output writer

**Files:**
- Create: `scraper-py/app/browser/__init__.py`
- Create: `scraper-py/app/browser/base.py`
- Create: `scraper-py/app/output.py`
- Test: `scraper-py/tests/test_output.py`

**Interfaces:**
- Produces: `@dataclass CapturedArtifact` with `filename: str`, `data: bytes`, `source_url: str`, `http_status: int`, `content_headers: dict[str, str]`; `class BrowserSession(Protocol)` with `async def ensure_logged_in(self) -> None`, `async def is_logged_in(self) -> bool`, `async def scrape(self, tab_url: str) -> list[CapturedArtifact]`, `async def close(self) -> None`.
- Produces: `def write_job_output(*, output_root, tab_id, url, route, scraper_version, http_status, artifacts, scraped_at) -> Path` — stages files in a temp dir, writes `metadata.json` LAST, atomically renames the dir into `output_root/<tab_id>`, returns the final path.

- [ ] **Step 1: Create `app/browser/__init__.py` (empty) and `app/browser/base.py`**

`app/browser/__init__.py`: (empty file)

`app/browser/base.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CapturedArtifact:
    filename: str
    data: bytes
    source_url: str
    http_status: int
    content_headers: dict[str, str] = field(default_factory=dict)


class BrowserSession(Protocol):
    async def ensure_logged_in(self) -> None: ...
    async def is_logged_in(self) -> bool: ...
    async def scrape(self, tab_url: str) -> list[CapturedArtifact]: ...
    async def close(self) -> None: ...
```

- [ ] **Step 2: Write the failing test**

`scraper-py/tests/test_output.py`:
```python
import json

from app.browser.base import CapturedArtifact
from app.output import write_job_output

XTZ = b"XTZ\x00" + b"payload-bytes"


def _artifact():
    return CapturedArtifact(
        filename="tab-download-ssid-1.xtz",
        data=XTZ,
        source_url="https://tabs.ultimate-guitar.com/tab/download/file?ssid=1",
        http_status=200,
        content_headers={"content-type": "application/octet-stream"},
    )


def test_writes_dir_with_raw_and_metadata(tmp_path):
    final = write_job_output(
        output_root=tmp_path,
        tab_id="a/b-1",
        url="https://tabs.ultimate-guitar.com/tab/a/b-1",
        route="a/b-1",
        scraper_version="0.1.0",
        http_status=200,
        artifacts=[_artifact()],
        scraped_at="2026-06-23T12:00:00",
    )
    assert final == tmp_path / "a/b-1"
    assert (final / "tab-download-ssid-1.xtz").read_bytes() == XTZ
    meta = json.loads((final / "metadata.json").read_text())
    assert meta["tab_id"] == "a/b-1"
    assert meta["scraper_version"] == "0.1.0"
    f = meta["files"][0]
    assert f["filename"] == "tab-download-ssid-1.xtz"
    assert f["byte_size"] == len(XTZ)
    assert f["xtz_magic_ok"] is True
    assert len(f["sha256"]) == 64


def test_no_staging_dir_left_behind(tmp_path):
    write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t",
    )
    assert not (tmp_path / ".tmp").exists() or not any((tmp_path / ".tmp").iterdir())


def test_rescrape_overwrites(tmp_path):
    write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t1",
    )
    final = write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t2",
    )
    meta = json.loads((final / "metadata.json").read_text())
    assert meta["scraped_at"] == "t2"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_output.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.output'`

- [ ] **Step 4: Implement `app/output.py`**

```python
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from app.browser.base import CapturedArtifact

XTZ_MAGIC = b"XTZ\x00"


def write_job_output(
    *,
    output_root: Path | str,
    tab_id: str,
    url: str,
    route: str,
    scraper_version: str,
    http_status: int,
    artifacts: list[CapturedArtifact],
    scraped_at: str,
) -> Path:
    output_root = Path(output_root)
    staging = output_root / ".tmp" / uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        files_meta = []
        for art in artifacts:
            (staging / art.filename).write_bytes(art.data)
            files_meta.append({
                "filename": art.filename,
                "sha256": hashlib.sha256(art.data).hexdigest(),
                "byte_size": len(art.data),
                "source_url": art.source_url,
                "content_headers": art.content_headers,
                "xtz_magic_ok": art.data[:4] == XTZ_MAGIC,
            })

        metadata = {
            "tab_id": tab_id,
            "url": url,
            "route": route,
            "scraped_at": scraped_at,
            "scraper_version": scraper_version,
            "http_status": http_status,
            "files": files_meta,
        }
        # metadata.json is written LAST — it is the commit marker.
        (staging / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True)
        )

        final = output_root / tab_id
        if final.exists():
            shutil.rmtree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)  # atomic dir rename within same filesystem
        return final
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_output.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): browser base types + atomic output writer"
```

---

### Task 8: Worker (agent runtime)

**Files:**
- Create: `scraper-py/app/worker.py`
- Test: `scraper-py/tests/test_worker.py`

**Interfaces:**
- Consumes: `JobRepo` (Tasks 5–6), `BrowserSession`/`CapturedArtifact` (Task 7), `write_job_output` (Task 7), error classes (Task 1), `Settings` (Task 1), `ServiceState` (Task 3), `app.__version__`.
- Produces: `class Worker(repo, browser, settings, now_fn=time.time)` with attributes `state: ServiceState`, `current_job_id: str | None`; methods `notify_enqueued()`, `request_resume()`, `stop()`, `async def run()`, `async def _process(self, job)`, `async def _delay_between_jobs()`.

- [ ] **Step 1: Write the failing test**

`scraper-py/tests/test_worker.py`:
```python
import pytest_asyncio

from app.browser.base import CapturedArtifact
from app.config import Settings
from app.errors import (
    PermanentScrapeError,
    SessionExpiredError,
    TransientScrapeError,
)
from app.models import JobStatus
from app.worker import Worker


class FakeBrowser:
    def __init__(self, artifacts=None, error=None):
        self.artifacts = artifacts or []
        self.error = error
        self.scrape_calls = 0
        self.login_calls = 0
        self._logged_in = True

    async def ensure_logged_in(self):
        self.login_calls += 1
        self._logged_in = True

    async def is_logged_in(self):
        return self._logged_in

    async def scrape(self, tab_url):
        self.scrape_calls += 1
        if self.error:
            raise self.error
        return list(self.artifacts)

    async def close(self):
        pass


def _settings(tmp_path):
    return Settings(
        output_dir=tmp_path / "out",
        inter_job_delay_min=0,
        inter_job_delay_max=0,
        backoff_base_seconds=10,
    )


def _artifact():
    return CapturedArtifact(
        filename="f.xtz", data=b"XTZ\x00data",
        source_url="u", http_status=200,
    )


@pytest_asyncio.fixture
async def worker_factory(repo, tmp_path):
    def make(browser):
        return Worker(repo, browser, _settings(tmp_path), now_fn=lambda: 1000.0)
    return make


async def test_process_success_writes_output(repo, worker_factory, tmp_path):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    browser = FakeBrowser(artifacts=[_artifact()])
    w = worker_factory(browser)
    await w._process(job)
    got = await repo.get(job.id)
    assert got.status is JobStatus.SUCCEEDED
    assert (tmp_path / "out" / "a/b-1" / "metadata.json").exists()


async def test_process_permanent_failure(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=PermanentScrapeError("404")))
    await w._process(job)
    assert (await repo.get(job.id)).status is JobStatus.FAILED


async def test_process_transient_requeues(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=TransientScrapeError("timeout")))
    await w._process(job)
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 1


async def test_process_session_expired_requeues_and_relogins(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    browser = FakeBrowser(error=SessionExpiredError("logged out"))
    w = worker_factory(browser)
    await w._process(job)
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 0  # no retry consumed
    assert browser.login_calls == 1


async def test_process_dedup_short_circuits(repo, worker_factory):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.mark_succeeded(first.id, "/out/a/b-1")
    second = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    # Force=True still creates the job; clear force to exercise dedup path.
    await repo.conn.execute("UPDATE jobs SET force=0 WHERE id=?", (second.id,))
    await repo.conn.commit()
    claimed = await repo.claim_next()
    browser = FakeBrowser(artifacts=[_artifact()])
    w = worker_factory(browser)
    await w._process(claimed)
    got = await repo.get(second.id)
    assert got.status is JobStatus.SUCCEEDED
    assert got.output_dir == "/out/a/b-1"
    assert browser.scrape_calls == 0


async def test_process_empty_artifacts_is_transient(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[]))
    await w._process(job)
    assert (await repo.get(job.id)).status is JobStatus.QUEUED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: Implement `app/worker.py`**

```python
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime

from app import __version__
from app.browser.base import BrowserSession
from app.config import Settings
from app.errors import (
    PermanentScrapeError,
    ScrapeError,
    SessionExpiredError,
)
from app.models import ServiceState
from app.output import write_job_output
from app.repo import JobRepo


class Worker:
    def __init__(
        self, repo: JobRepo, browser: BrowserSession, settings: Settings,
        now_fn=time.time,
    ):
        self.repo = repo
        self.browser = browser
        self.settings = settings
        self._now = now_fn
        self.state = ServiceState.STARTING
        self.current_job_id: str | None = None
        self._wakeup = asyncio.Event()
        self._resume = asyncio.Event()
        self._stop = False

    def notify_enqueued(self) -> None:
        self._wakeup.set()

    def request_resume(self) -> None:
        self._resume.set()

    def stop(self) -> None:
        self._stop = True
        self._wakeup.set()
        self._resume.set()

    async def run(self) -> None:
        self.state = ServiceState.IDLE
        while not self._stop:
            if await self.repo.is_paused():
                self.state = ServiceState.PAUSED
                self._resume.clear()
                await self._resume.wait()
                continue

            job = await self.repo.claim_next()
            if job is None:
                self.state = ServiceState.IDLE
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(
                        self._wakeup.wait(),
                        timeout=self.settings.poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            self.state = ServiceState.RUNNING
            self.current_job_id = job.id
            try:
                await self._process(job)
            finally:
                self.current_job_id = None
            await self._delay_between_jobs()

    async def _process(self, job) -> None:
        if not job.force:
            existing = await self.repo.succeeded_output_for(job.tab_id, job.id)
            if existing:
                await self.repo.mark_succeeded(job.id, existing)
                return

        try:
            artifacts = await self.browser.scrape(job.url)
        except SessionExpiredError:
            await self.repo.requeue_unchanged(job.id)
            self.state = ServiceState.LOGGING_IN
            await self.browser.ensure_logged_in()
            return
        except PermanentScrapeError as e:
            await self.repo.mark_permanent_failure(job.id, str(e))
            return
        except ScrapeError as e:
            await self.repo.record_transient_failure(
                job.id, str(e), self.settings.backoff_base_seconds
            )
            return
        except Exception as e:  # unexpected -> transient
            await self.repo.record_transient_failure(
                job.id, repr(e), self.settings.backoff_base_seconds
            )
            return

        if not artifacts:
            await self.repo.record_transient_failure(
                job.id, "no artifacts captured", self.settings.backoff_base_seconds
            )
            return

        final = write_job_output(
            output_root=self.settings.output_dir,
            tab_id=job.tab_id,
            url=job.url,
            route=job.tab_id,
            scraper_version=__version__,
            http_status=artifacts[0].http_status,
            artifacts=artifacts,
            scraped_at=datetime.now().isoformat(timespec="seconds"),
        )
        await self.repo.mark_succeeded(job.id, str(final))

    async def _delay_between_jobs(self) -> None:
        hi = self.settings.inter_job_delay_max
        if hi > 0:
            lo = self.settings.inter_job_delay_min
            await asyncio.sleep(random.uniform(lo, hi))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_worker.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): worker agent runtime + error taxonomy"
```

---

### Task 9: FastAPI surface + app wiring

**Files:**
- Create: `scraper-py/app/api/__init__.py`
- Create: `scraper-py/app/api/routes.py`
- Create: `scraper-py/app/main.py`
- Test: `scraper-py/tests/test_api.py`

**Interfaces:**
- Consumes: `JobRepo`, `Worker`, `Settings`, `normalize_tab`, models.
- Produces: `def create_app(repo=None, worker=None, settings=None) -> FastAPI`; an `APIRouter` with endpoints `GET /healthz`, `GET /status`, `GET /jobs`, `GET /jobs/{id}`, `POST /jobs`, `POST /jobs/bulk`, `DELETE /jobs/{id}`, `POST /jobs/{id}/retry`, `POST /pause`, `POST /resume`. `create_app()` with no args wires the production lifespan (browser + worker task).

- [ ] **Step 1: Create `app/api/__init__.py` (empty) and `app/api/routes.py`**

`app/api/__init__.py`: (empty file)

`app/api/routes.py`:
```python
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.models import (
    BulkEnqueueRequest,
    EnqueueRequest,
    Job,
    StatusResponse,
)
from app.normalize import normalize_tab

router = APIRouter()


def _repo(request: Request):
    return request.app.state.repo


def _worker(request: Request):
    return request.app.state.worker


def _settings(request: Request):
    return request.app.state.settings


async def require_api_key(
    request: Request, x_api_key: str | None = Header(default=None)
):
    key = request.app.state.settings.api_key
    if key and x_api_key != key:
        raise HTTPException(status_code=401, detail="invalid api key")


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/status", response_model=StatusResponse)
async def status(request: Request, _=Depends(require_api_key)):
    repo, worker = _repo(request), _worker(request)
    return StatusResponse(
        state=worker.state,
        current_job_id=worker.current_job_id,
        queue_depth=await repo.queue_depth(),
        counts=await repo.counts(),
        paused=await repo.is_paused(),
        logged_in=await worker.browser.is_logged_in(),
    )


@router.get("/jobs", response_model=list[Job])
async def list_jobs(
    request: Request, status: str | None = None,
    limit: int = 50, offset: int = 0, _=Depends(require_api_key),
):
    return await _repo(request).list(status=status, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, request: Request, _=Depends(require_api_key)):
    job = await _repo(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs", response_model=Job)
async def enqueue(
    req: EnqueueRequest, request: Request, _=Depends(require_api_key)
):
    try:
        tab_id, url = normalize_tab(req.url_or_route)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    job = await repo.enqueue(
        tab_id=tab_id, url=url, priority=req.priority,
        force=req.force, max_attempts=settings.max_attempts,
    )
    worker.notify_enqueued()
    return job


@router.post("/jobs/bulk", response_model=list[Job])
async def enqueue_bulk(
    req: BulkEnqueueRequest, request: Request, _=Depends(require_api_key)
):
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    out = []
    for item in req.items:
        try:
            tab_id, url = normalize_tab(item.url_or_route)
        except ValueError:
            continue
        out.append(await repo.enqueue(
            tab_id=tab_id, url=url, priority=item.priority,
            force=item.force, max_attempts=settings.max_attempts,
        ))
    worker.notify_enqueued()
    return out


@router.delete("/jobs/{job_id}")
async def dequeue(job_id: str, request: Request, _=Depends(require_api_key)):
    repo = _repo(request)
    if await repo.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not await repo.cancel(job_id):
        raise HTTPException(status_code=409, detail="job is not queued")
    return {"canceled": job_id}


@router.post("/jobs/{job_id}/retry", response_model=Job)
async def retry(job_id: str, request: Request, _=Depends(require_api_key)):
    repo, worker = _repo(request), _worker(request)
    if await repo.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not await repo.retry(job_id):
        raise HTTPException(status_code=409, detail="job is not failed")
    worker.notify_enqueued()
    return await repo.get(job_id)


@router.post("/pause")
async def pause(request: Request, _=Depends(require_api_key)):
    await _repo(request).set_paused(True)
    return {"paused": True}


@router.post("/resume")
async def resume(request: Request, _=Depends(require_api_key)):
    await _repo(request).set_paused(False)
    _worker(request).request_resume()
    return {"paused": False}
```

- [ ] **Step 2: Create `app/main.py`**

```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api.routes import router
from app.config import get_settings
from app.repo import JobRepo
from app.worker import Worker


def create_app(repo=None, worker=None, settings=None) -> FastAPI:
    app = FastAPI(title="ult-scraper")
    app.include_router(router)

    if repo is not None:
        app.state.repo = repo
        app.state.worker = worker
        app.state.settings = settings
        return app

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.browser.session import CamoufoxBrowserSession

        s = get_settings()
        conn = await db.connect(s.db_path)
        await db.init_schema(conn)
        repo_ = JobRepo(conn)
        browser = CamoufoxBrowserSession(s)
        await browser.start()
        await browser.ensure_logged_in()
        worker_ = Worker(repo_, browser, s)
        _app.state.repo = repo_
        _app.state.worker = worker_
        _app.state.settings = s
        task = asyncio.create_task(worker_.run())
        try:
            yield
        finally:
            worker_.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await browser.close()
            await conn.close()

    app.router.lifespan_context = lifespan
    return app


app = create_app()
```

- [ ] **Step 3: Write the failing test**

`scraper-py/tests/test_api.py`:
```python
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


async def test_pause_resume(client):
    assert (await client.post("/pause")).json()["paused"] is True
    r = await client.get("/status")
    assert r.json()["paused"] is True
    assert (await client.post("/resume")).json()["paused"] is False
    assert client.worker.resumed == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api'` (or `app.main`)

- [ ] **Step 5: Implement** — already written in Steps 1–2. Re-run.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_api.py -v`
Expected: PASS (6 tests)

Note: if the `app = create_app()` line at import time fails because `app.browser.session` does not yet exist, that import is inside the lifespan function and only runs at server startup, so test collection is unaffected. (Task 13 adds `session.py`.)

- [ ] **Step 7: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): fastapi surface + app wiring"
```

---

### Task 10: Async browser — humanize helpers (port of `common.py`)

**Files:**
- Create: `scraper-py/app/browser/humanize.py`
- Test: `scraper-py/tests/test_humanize_smoke.py`

**Note on testing:** These helpers drive a live Playwright `Page`; behavior is exercised by the marker-gated integration test in Task 13. This task's automated check is an import/smoke test plus the pure helper math. The code is a faithful async port of `PY/common.py`.

**Interfaces:**
- Produces: `human_pause(min_ms=120, max_ms=420)`, `wait_for_load_or_pause(page, ...)`, `is_cloudflare_wall(page) -> bool`, `wait_for_cloudflare_wall(page, timeout_ms=...)` (raises `CloudflareTimeout`), `human_click(page, locator, timeout=10_000)`, `human_type(page, locator, text)`, and pure helpers `_rand_int`, `_rand_float`. `class CloudflareTimeout(RuntimeError)`.

- [ ] **Step 1: Implement `app/browser/humanize.py`**

```python
from __future__ import annotations

import asyncio
import random

from playwright.async_api import TimeoutError as PWTimeout

CF_POLL_MS = 1000
CF_SELECTOR = ", ".join((
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "input[name='cf-turnstile-response']",
    ".cf-turnstile",
    "[id*='cf-chl']",
    "[class*='cf-chl']",
))
CF_TEXT = (
    "verify you are human",
    "checking if the site connection is secure",
    "cloudflare",
)


class CloudflareTimeout(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Timed out waiting for the Cloudflare challenge to clear.")


def _rand_int(a: int, b: int) -> int:
    return int(random.random() * (b + 1 - a) + a)


def _rand_float(a: float, b: float) -> float:
    return random.random() * (b - a) + a


async def human_pause(min_ms: int = 120, max_ms: int = 420) -> None:
    await asyncio.sleep(_rand_int(min_ms, max_ms) / 1000)


async def wait_for_load_or_pause(page, min_ms: int = 10_000, max_ms: int = 15_000) -> None:
    try:
        await page.wait_for_load_state("load", timeout=_rand_int(min_ms, max_ms))
    except PWTimeout:
        pass


async def is_cloudflare_wall(page) -> bool:
    url = page.url.lower()
    if "challenges.cloudflare.com" in url or "/cdn-cgi/challenge-platform/" in url:
        return True
    try:
        if await page.locator(CF_SELECTOR).count() > 0:
            return True
    except Exception:
        pass
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    try:
        body = (await page.locator("body").inner_text(timeout=1000)).lower()
    except Exception:
        body = ""
    return any(t in body for t in CF_TEXT)


async def wait_for_cloudflare_wall(page, timeout_ms: int = 120_000) -> None:
    if not await is_cloudflare_wall(page):
        return
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while loop.time() < deadline:
        await asyncio.sleep(CF_POLL_MS / 1000)
        if not await is_cloudflare_wall(page):
            return
    raise CloudflareTimeout()


async def human_click(page, locator, timeout: int = 10_000) -> None:
    await locator.wait_for(state="visible", timeout=timeout)
    box = await locator.bounding_box()
    if box:
        tx = box["x"] + box["width"] * _rand_float(0.35, 0.65)
        ty = box["y"] + box["height"] * _rand_float(0.35, 0.65)
        await page.mouse.move(tx + _rand_float(-180, 180), ty + _rand_float(-90, 90))
        await human_pause(80, 220)
        await page.mouse.move(tx, ty, steps=_rand_int(8, 18))
        await human_pause(90, 260)
        await page.mouse.down()
        await human_pause(45, 130)
        await page.mouse.up()
        return
    await locator.click()


async def human_type(page, locator, text: str) -> None:
    await locator.wait_for(state="visible")
    await human_click(page, locator)
    await human_pause(120, 300)
    for ch in text:
        await locator.type(ch, delay=_rand_int(45, 170))
        if random.random() < 0.08:
            await human_pause(180, 520)
```

- [ ] **Step 2: Write the smoke test**

`scraper-py/tests/test_humanize_smoke.py`:
```python
from app.browser import humanize


def test_rand_int_in_range():
    for _ in range(100):
        v = humanize._rand_int(5, 10)
        assert 5 <= v <= 10


def test_rand_float_in_range():
    for _ in range(100):
        v = humanize._rand_float(0.35, 0.65)
        assert 0.35 <= v <= 0.65


def test_cloudflare_timeout_message():
    assert "Cloudflare" in str(humanize.CloudflareTimeout())
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_humanize_smoke.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): async humanize/cloudflare helpers"
```

---

### Task 11: Async browser — login (port of `login.py`)

**Files:**
- Create: `scraper-py/app/browser/login.py`
- Test: `scraper-py/tests/test_login_smoke.py`

**Note on testing:** Real login is exercised by the integration test (Task 13). This task verifies the module imports and exposes the expected callables. Secrets are passed as args and never logged.

**Interfaces:**
- Consumes: humanize helpers (Task 10).
- Produces: `async def is_logged_in(page) -> bool`; `async def login(page, email: str, password: str, cf_timeout_ms: int) -> bool`. `PROFILE_HREF` / `PROFILE_SELECTOR` constants.

- [ ] **Step 1: Implement `app/browser/login.py`**

```python
from __future__ import annotations

import logging
import re

from playwright.async_api import TimeoutError as PWTimeout

from app.browser.humanize import (
    human_click,
    human_pause,
    human_type,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)

log = logging.getLogger(__name__)

PROFILE_HREF = "https://www.ultimate-guitar.com/u/tnd29hh6r4"
PROFILE_SELECTOR = f'[href="{PROFILE_HREF}"]'


async def is_logged_in(page) -> bool:
    try:
        return await page.locator(PROFILE_SELECTOR).count() > 0
    except Exception:
        return False


async def login(page, email: str, password: str, cf_timeout_ms: int) -> bool:
    if not email or not password:
        raise RuntimeError("UG_EMAIL and UG_PASSWORD must be set before logging in.")

    await page.goto(
        "https://www.ultimate-guitar.com/",
        wait_until="domcontentloaded", timeout=60_000,
    )
    await wait_for_load_or_pause(page)
    await wait_for_cloudflare_wall(page, cf_timeout_ms)
    await human_pause(700, 1600)

    if await is_logged_in(page):
        log.info("already logged in")
        return True

    login_button = page.locator(
        "button[type='button'][tabindex='0'][data-react-aria-pressable='true']",
        has=page.locator("span", has_text=re.compile(r"^Log in$", re.IGNORECASE)),
    )

    try:
        await human_click(page, login_button, timeout=20_000)
        await human_pause(800, 1400)

        email_input = page.locator(
            "input[name='username'][placeholder='Username or e-mail']"
        )
        password_input = page.locator(
            "input[name='password'][placeholder='Password']"
        )
        await email_input.wait_for(state="visible")
        await human_pause(250, 650)

        await human_type(page, email_input, email)
        await human_pause(250, 700)
        await human_type(page, password_input, password)
        await human_pause(2050, 4275)

        await human_click(
            page,
            page.locator(
                "button[type='submit']",
                has=page.locator(
                    "span", has_text=re.compile(r"^Log in$", re.IGNORECASE)
                ),
            ),
            timeout=20_000,
        )
        await human_pause(4050, 8275)
    except PWTimeout as e:
        log.warning("login timed out: %s", e)
        return False

    if await is_logged_in(page):
        log.info("logged in")
        return True

    try:
        await page.wait_for_selector(
            "input[name='username'][placeholder='Username or e-mail']",
            state="detached", timeout=30_000,
        )
        return True
    except Exception:
        return False
```

- [ ] **Step 2: Write the smoke test**

`scraper-py/tests/test_login_smoke.py`:
```python
import inspect

from app.browser import login


def test_callables_present():
    assert inspect.iscoroutinefunction(login.login)
    assert inspect.iscoroutinefunction(login.is_logged_in)
    assert login.PROFILE_SELECTOR.startswith("[href=")
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_login_smoke.py -v`
Expected: PASS (1 test)

- [ ] **Step 4: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): async UG login flow"
```

---

### Task 12: Async browser — scrape (port of `scrape.py`, capture-only)

**Files:**
- Create: `scraper-py/app/browser/scrape.py`
- Test: `scraper-py/tests/test_scrape_helpers.py`

**Note on testing:** The live navigation path is covered by the integration test (Task 13). The pure URL/header/filename helpers are unit-tested here. Decryption is intentionally absent — this captures raw XTZ only and classifies outcomes into the error taxonomy.

**Interfaces:**
- Consumes: `CapturedArtifact` (Task 7), humanize + login helpers, error classes.
- Produces: `async def scrape_tab(page, tab_url: str, capture_window_ms: int, cf_timeout_ms: int) -> list[CapturedArtifact]` (raises `SessionExpiredError`/`PermanentScrapeError`/`TransientScrapeError`); pure helpers `_should_capture(url) -> bool`, `_selected_headers(headers) -> dict`, `_filename(response_url, headers, body) -> str`.

- [ ] **Step 1: Write the failing test (pure helpers)**

`scraper-py/tests/test_scrape_helpers.py`:
```python
from app.browser.scrape import _filename, _selected_headers, _should_capture


def test_should_capture_matches_download_endpoints():
    assert _should_capture(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943"
    )
    assert _should_capture(
        "https://tabs.ultimate-guitar.com/download/public/abc"
    )
    assert not _should_capture("https://tabs.ultimate-guitar.com/tab/eagles/x-1")
    assert not _should_capture("https://example.com/tab/download/file")


def test_selected_headers_lowercases_and_filters():
    headers = {"Content-Type": "application/octet-stream", "Server": "cf", "X-Other": "z"}
    out = _selected_headers(headers)
    assert out["content-type"] == "application/octet-stream"
    assert "x-other" not in out


def test_filename_from_content_disposition():
    name = _filename(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943",
        {"content-disposition": 'attachment; filename="song.xtz"'},
        b"XTZ\x00data",
    )
    assert name == "song.xtz"


def test_filename_from_ssid_query_when_no_disposition():
    name = _filename(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943&m=1",
        {},
        b"XTZ\x00data",
    )
    assert name == "tab-download-ssid-1910943.xtz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper-py && python -m pytest tests/test_scrape_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.browser.scrape'`

- [ ] **Step 3: Implement `app/browser/scrape.py`**

```python
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.browser.base import CapturedArtifact
from app.browser.humanize import wait_for_cloudflare_wall, wait_for_load_or_pause
from app.browser.login import is_logged_in
from app.errors import (
    PermanentScrapeError,
    SessionExpiredError,
    TransientScrapeError,
)

CAPTURE_URL_PARTS = ("/download/public/", "/tab/download/file")
CAPTURE_HEADER_NAMES = (
    "content-disposition",
    "content-encoding",
    "content-length",
    "content-type",
    "location",
)
XTZ_MAGIC = b"XTZ\x00"


def _should_capture(url: str) -> bool:
    p = urlparse(url)
    if p.netloc != "tabs.ultimate-guitar.com":
        return False
    return any(part in p.path for part in CAPTURE_URL_PARTS)


def _selected_headers(headers: dict) -> dict:
    low = {k.lower(): v for k, v in headers.items()}
    return {n: low[n] for n in CAPTURE_HEADER_NAMES if n in low}


def _safe(value: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    return s[:120] or fallback


def _filename(response_url: str, headers: dict, body: bytes) -> str:
    low = {k.lower(): v for k, v in headers.items()}
    disp = low.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disp, re.IGNORECASE)
    if m:
        base = unquote(m.group(1))
    else:
        parsed = urlparse(response_url)
        base = Path(parsed.path).name or "response"
        if base == "file":
            ssid = parse_qs(parsed.query).get("ssid", [""])[0]
            base = f"tab-download-ssid-{ssid}" if ssid else base
    base = _safe(base, "response")
    if Path(base).suffix == "":
        base = f"{base}.xtz"
    return base


async def scrape_tab(
    page, tab_url: str, capture_window_ms: int, cf_timeout_ms: int
) -> list[CapturedArtifact]:
    captured = []

    def on_response(response):
        if _should_capture(response.url):
            captured.append(response)

    page.on("response", on_response)
    try:
        resp = await page.goto(
            tab_url, wait_until="domcontentloaded", timeout=60_000
        )
        await wait_for_load_or_pause(page)
        await wait_for_cloudflare_wall(page, cf_timeout_ms)

        if not await is_logged_in(page):
            raise SessionExpiredError(f"not logged in on {tab_url}")
        if resp is not None and resp.status == 404:
            raise PermanentScrapeError(f"tab not found (404): {tab_url}")

        await page.wait_for_timeout(capture_window_ms)

        artifacts: list[CapturedArtifact] = []
        for r in captured:
            if 300 <= r.status < 400:
                continue
            try:
                body = await r.body()
            except Exception:
                continue
            if not body.startswith(XTZ_MAGIC):
                continue
            artifacts.append(CapturedArtifact(
                filename=_filename(r.url, r.headers, body),
                data=body,
                source_url=r.url,
                http_status=r.status,
                content_headers=_selected_headers(r.headers),
            ))

        if not artifacts:
            raise TransientScrapeError(f"no XTZ download captured for {tab_url}")
        return artifacts
    finally:
        page.remove_listener("response", on_response)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper-py && python -m pytest tests/test_scrape_helpers.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): async capture-only scrape + helpers"
```

---

### Task 13: Camoufox session + integration test + README

**Files:**
- Create: `scraper-py/app/browser/session.py`
- Create: `scraper-py/tests/test_integration.py`
- Create: `scraper-py/README.md`
- Test: `scraper-py/tests/test_session_smoke.py`

**Note on testing:** `session.py` launches a real browser, so it is verified by the marker-gated integration test (skipped unless `UG_EMAIL`/`UG_PASSWORD` are set). A smoke test confirms the class implements the `BrowserSession` protocol shape.

**Interfaces:**
- Consumes: `Settings`, login/scrape helpers, `CapturedArtifact`.
- Produces: `class CamoufoxBrowserSession(settings)` implementing `BrowserSession`: `async def start()`, `async def ensure_logged_in()`, `async def is_logged_in() -> bool`, `async def scrape(tab_url) -> list[CapturedArtifact]`, `async def close()`.

- [ ] **Step 1: Implement `app/browser/session.py`**

```python
from __future__ import annotations

import logging

from camoufox.async_api import AsyncCamoufox

from app.browser.base import CapturedArtifact
from app.browser.login import is_logged_in, login
from app.browser.scrape import scrape_tab
from app.config import Settings

log = logging.getLogger(__name__)


class CamoufoxBrowserSession:
    def __init__(self, settings: Settings):
        self.s = settings
        self._cm = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        opts = dict(
            headless=self.s.headless,
            humanize=True,
            persistent_context=True,
            user_data_dir=str(self.s.profile_dir),
            os="windows",
            locale="en-US",
            block_webrtc=True,
        )
        if self.s.ug_proxy:
            opts["proxy"] = {"server": self.s.ug_proxy}
            opts["geoip"] = True
        self._cm = AsyncCamoufox(**opts)
        self._context = await self._cm.__aenter__()
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )

    async def ensure_logged_in(self) -> None:
        ok = await login(
            self._page, self.s.ug_email, self.s.ug_password,
            self.s.cloudflare_timeout_ms,
        )
        if not ok:
            raise RuntimeError("UG login failed")

    async def is_logged_in(self) -> bool:
        return await is_logged_in(self._page)

    async def scrape(self, tab_url: str) -> list[CapturedArtifact]:
        return await scrape_tab(
            self._page, tab_url, self.s.capture_window_ms,
            self.s.cloudflare_timeout_ms,
        )

    async def close(self) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
```

- [ ] **Step 2: Write the smoke test**

`scraper-py/tests/test_session_smoke.py`:
```python
import inspect

from app.browser.session import CamoufoxBrowserSession


def test_session_implements_protocol_shape():
    for name in ("start", "ensure_logged_in", "is_logged_in", "scrape", "close"):
        assert inspect.iscoroutinefunction(getattr(CamoufoxBrowserSession, name))
```

- [ ] **Step 3: Write the integration test (marker-gated)**

`scraper-py/tests/test_integration.py`:
```python
import os

import pytest

from app.browser.session import CamoufoxBrowserSession
from app.config import get_settings
from app.output import write_job_output

pytestmark = pytest.mark.integration

REASON = "set UG_EMAIL/UG_PASSWORD to run the live integration test"


@pytest.mark.skipif(
    not (os.getenv("UG_EMAIL") and os.getenv("UG_PASSWORD")), reason=REASON
)
async def test_live_scrape_hotel_california(tmp_path):
    settings = get_settings()
    settings.output_dir = tmp_path
    session = CamoufoxBrowserSession(settings)
    await session.start()
    try:
        await session.ensure_logged_in()
        assert await session.is_logged_in()
        url = (
            "https://tabs.ultimate-guitar.com/tab/"
            "eagles/hotel-california-official-1910943"
        )
        artifacts = await session.scrape(url)
        assert artifacts
        assert artifacts[0].data[:4] == b"XTZ\x00"
        final = write_job_output(
            output_root=tmp_path,
            tab_id="eagles/hotel-california-official-1910943",
            url=url,
            route="eagles/hotel-california-official-1910943",
            scraper_version="0.1.0",
            http_status=artifacts[0].http_status,
            artifacts=artifacts,
            scraped_at="live",
        )
        assert (final / "metadata.json").exists()
    finally:
        await session.close()
```

- [ ] **Step 4: Create `scraper-py/README.md`**

````markdown
# ult-scraper

FastAPI service that logs into Ultimate Guitar, works a SQLite queue of tab-scrape
jobs with a single async worker, and writes **raw encrypted XTZ** files to disk for a
separate Rust decoder. This service performs no decryption.

## Setup

```bash
cd scraper-py
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m camoufox fetch          # download the Camoufox browser
cp .env.example .env              # then fill in UG_EMAIL / UG_PASSWORD
```

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On startup the service launches Camoufox (headful), confirms login, and idles until
jobs are enqueued.

## API

| Method & path | Purpose |
|---|---|
| `GET /status` | Service state, queue depth, counts, login health |
| `GET /jobs?status=&limit=&offset=` | List/filter jobs |
| `GET /jobs/{id}` | Job detail |
| `POST /jobs` | Enqueue `{ "url_or_route": "...", "priority": 0, "force": false }` |
| `POST /jobs/bulk` | Enqueue `{ "items": [ ... ] }` |
| `DELETE /jobs/{id}` | Cancel a queued job (409 if running) |
| `POST /jobs/{id}/retry` | Re-queue a failed job |
| `POST /pause` / `POST /resume` | Pause/resume the worker |

## Output contract (for the Rust decoder)

Each successful job writes `OUTPUT_DIR/<tab_id>/`:
- `<filename>.xtz` — raw encrypted bytes, exactly as downloaded
- `metadata.json` — written last; its presence marks the directory as complete

## Tests

```bash
python -m pytest                  # unit tests (browser integration excluded by default)
python -m pytest -m integration   # live test; needs UG creds + network
```
````

- [ ] **Step 5: Run the smoke + full unit suite**

Run: `cd scraper-py && python -m pytest -v`
Expected: PASS — all unit tests green; the integration test is deselected by the
default `-m 'not integration'` addopts.

- [ ] **Step 6: Commit**

```bash
cd scraper-py && git add -A && git commit -m "feat(scraper-py): camoufox session, integration test, README"
```

---

### Task 14: Full-suite verification + manual smoke

**Files:**
- None (verification only)

- [ ] **Step 1: Run the entire unit suite**

Run: `cd scraper-py && python -m pytest -v`
Expected: PASS — every test from Tasks 1–13 green, integration deselected.

- [ ] **Step 2: Verify the app imports and the OpenAPI schema builds**

Run: `cd scraper-py && python -c "from app.main import create_app; a=create_app(); print(sorted({r.path for r in a.routes}))"`
Expected: prints the route paths including `/status`, `/jobs`, `/jobs/{job_id}`, `/pause`, `/resume`, `/healthz`.

- [ ] **Step 3: (Optional, manual) Live smoke with real credentials**

With `UG_EMAIL`/`UG_PASSWORD` set in `scraper-py/.env`:
```bash
cd scraper-py && python -m pytest -m integration -v
```
Expected: PASS — logs in, scrapes Hotel California, writes `metadata.json` + `.xtz`.

- [ ] **Step 4: Final commit (if any changes)**

```bash
cd scraper-py && git add -A && git commit -m "chore(scraper-py): full-suite verification" --allow-empty
```

---

## Self-Review Notes

- **Spec coverage:** project layout (Task 1, 7, 9–13); SQLite data model (Task 4); job lifecycle + dedup + session-expiry rule (Tasks 5, 6, 8); worker/agent runtime (Task 8); error taxonomy transient/permanent/session (Tasks 8, 12); output contract + atomic commit marker (Task 7, verified live Task 13); FastAPI endpoints incl. 409 on cancel-running and 422 on bad input (Task 9); config/env (Task 1); testing strategy with fakes + marker-gated integration (throughout, Task 13). Async-everything port of `login`/`scrape`/`common` (Tasks 10–12). Decryption intentionally dropped.
- **Type consistency:** `JobRepo` method names and signatures used by `Worker` (Task 8) and the API (Task 9) match those defined in Tasks 5–6 (`claim_next`, `succeeded_output_for`, `mark_succeeded`, `mark_permanent_failure`, `record_transient_failure`, `requeue_unchanged`, `cancel`, `retry`, `is_paused`, `set_paused`, `queue_depth`, `counts`, `list`, `get`, `enqueue`). `CapturedArtifact` fields are consistent across `base.py`, `output.py`, `scrape.py`, and the worker.
- **No placeholders:** every code step contains complete, runnable code.
