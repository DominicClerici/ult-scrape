from __future__ import annotations

import json
import time
from uuid import uuid4

import aiosqlite

from app.models import DiscoveryRun, Job, JobStatus
from app.normalize import normalize_tab


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

    async def requeue_unchanged(self, job_id: str, delay: float = 0.0) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE jobs SET status='queued', started_at=NULL, "
            "next_attempt_at=?, updated_at=? WHERE id=?",
            (now + delay, now, job_id),
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

    async def cancel_all_queued(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='canceled', finished_at=?, updated_at=? "
            "WHERE status='queued'",
            (now, now),
        )
        await self.conn.commit()
        return cur.rowcount

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

    async def reset_running_to_queued(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE jobs SET status='queued', started_at=NULL, updated_at=? "
            "WHERE status='running'",
            (now,),
        )
        await self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _row_to_discovery(row) -> DiscoveryRun:
        return DiscoveryRun(
            id=row["id"],
            params=json.loads(row["params_json"]),
            state=row["state"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            slices_total=row["slices_total"],
            slices_done=row["slices_done"],
            tabs_found=row["tabs_found"],
            cancel_requested=bool(row["cancel_requested"]),
            error=row["error"],
        )

    async def count_active_jobs(self) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','running')"
        )
        return (await cur.fetchone())["c"]

    async def has_active_discovery(self) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) c FROM discovery_runs WHERE state IN ('requested','running')"
        )
        return (await cur.fetchone())["c"] > 0

    async def request_discovery(self, params: dict) -> DiscoveryRun | None:
        if await self.has_active_discovery():
            return None
        run_id = str(uuid4())
        now = self._now()
        await self.conn.execute(
            "INSERT INTO discovery_runs (id, params_json, state, created_at) "
            "VALUES (?,?, 'requested', ?)",
            (run_id, json.dumps(params), now),
        )
        await self.conn.commit()
        return await self.get_discovery_run(run_id)

    async def claim_discovery(self) -> DiscoveryRun | None:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET state='running', started_at=? "
            "WHERE id = (SELECT id FROM discovery_runs WHERE state='requested' "
            "ORDER BY created_at ASC LIMIT 1) RETURNING *",
            (now,),
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return self._row_to_discovery(row) if row else None

    async def get_discovery_run(self, run_id: str) -> DiscoveryRun | None:
        cur = await self.conn.execute(
            "SELECT * FROM discovery_runs WHERE id=?", (run_id,)
        )
        row = await cur.fetchone()
        return self._row_to_discovery(row) if row else None

    async def list_discovery_runs(self, limit: int = 20) -> list[DiscoveryRun]:
        cur = await self.conn.execute(
            "SELECT * FROM discovery_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self._row_to_discovery(r) for r in await cur.fetchall()]

    async def update_discovery_progress(
        self, run_id: str, slices_total: int, slices_done: int, tabs_found: int
    ) -> None:
        await self.conn.execute(
            "UPDATE discovery_runs SET slices_total=?, slices_done=?, tabs_found=? WHERE id=?",
            (slices_total, slices_done, tabs_found, run_id),
        )
        await self.conn.commit()

    async def finish_discovery(
        self, run_id: str, state: str, error: str | None = None
    ) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE discovery_runs SET state=?, error=?, finished_at=? WHERE id=?",
            (state, error, now, run_id),
        )
        await self.conn.commit()

    async def request_discovery_cancel(self, run_id: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET cancel_requested=1 "
            "WHERE id=? AND state IN ('requested','running')",
            (run_id,),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def is_discovery_cancel_requested(self, run_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT cancel_requested FROM discovery_runs WHERE id=?", (run_id,)
        )
        row = await cur.fetchone()
        return bool(row and row["cancel_requested"])

    async def fail_interrupted_discovery(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET state='failed', error='interrupted by restart', "
            "finished_at=? WHERE state='running'",
            (now,),
        )
        await self.conn.commit()
        return cur.rowcount

    async def upsert_tab_metadata(self, run_id: str, record: dict) -> None:
        tab_id, url = normalize_tab(record["tab_url"])
        now = self._now()
        await self.conn.execute(
            "INSERT INTO tab_metadata "
            "(tab_id, numeric_id, route, url, explore_json, first_seen_at, last_seen_at, discovery_run_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tab_id) DO UPDATE SET "
            "numeric_id=excluded.numeric_id, url=excluded.url, "
            "explore_json=excluded.explore_json, last_seen_at=excluded.last_seen_at, "
            "discovery_run_id=excluded.discovery_run_id",
            (tab_id, record.get("id"), tab_id, url, json.dumps(record), now, now, run_id),
        )
        await self.conn.commit()

    async def discovered_routes(
        self, exclude_succeeded: bool = True
    ) -> list[tuple[str, str]]:
        if exclude_succeeded:
            cur = await self.conn.execute(
                "SELECT m.tab_id, m.url FROM tab_metadata m "
                "WHERE NOT EXISTS (SELECT 1 FROM jobs j "
                "WHERE j.tab_id=m.tab_id AND j.status='succeeded') "
                "ORDER BY m.first_seen_at ASC"
            )
        else:
            cur = await self.conn.execute(
                "SELECT tab_id, url FROM tab_metadata ORDER BY first_seen_at ASC"
            )
        return [(r["tab_id"], r["url"]) for r in await cur.fetchall()]
