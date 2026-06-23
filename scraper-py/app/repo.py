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
