import asyncio
import time

import aiosqlite

from app.models import Job, JobStatus


def backoff(attempts: int, base: float) -> float:
    return base * (2 ** max(0, attempts - 1))


class JobRepo:
    # aiosqlite serializes calls onto one background thread, but a single
    # connection is shared across all worker coroutines; without this lock,
    # one coroutine's execute() can interleave between another's execute()
    # and commit(), which sqlite3 rejects ("SQL statements in progress").
    def __init__(self, conn: aiosqlite.Connection, now_fn=time.time):
        self.conn = conn
        self._now = now_fn
        self._lock = asyncio.Lock()

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

    async def _get(self, tab_id: str) -> Job | None:
        cur = await self.conn.execute("SELECT * FROM jobs WHERE tab_id=?", (tab_id,))
        row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def get(self, tab_id: str) -> Job | None:
        async with self._lock:
            return await self._get(tab_id)

    async def upsert_pending(self, tab_id: str, route: str) -> None:
        now = self._now()
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO jobs (tab_id, route, status, attempts, next_attempt_at, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(tab_id) DO NOTHING",
                (tab_id, route, "pending", 0, now, now, now),
            )
            await self.conn.commit()

    async def claim_next(self, worker_id: str) -> Job | None:
        now = self._now()
        async with self._lock:
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
        async with self._lock:
            await self.conn.execute(
                "UPDATE jobs SET status='done', chosen_video_id=?, query=?, "
                "last_error=NULL, claimed_at=NULL, updated_at=? WHERE tab_id=?",
                (chosen_video_id, query, now, tab_id),
            )
            await self.conn.commit()

    async def mark_no_match(self, tab_id: str, query: str) -> None:
        now = self._now()
        async with self._lock:
            await self.conn.execute(
                "UPDATE jobs SET status='no_match', query=?, claimed_at=NULL, "
                "updated_at=? WHERE tab_id=?",
                (query, now, tab_id),
            )
            await self.conn.commit()

    async def mark_failed(self, tab_id: str, error: str) -> None:
        now = self._now()
        async with self._lock:
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
        async with self._lock:
            job = await self._get(tab_id)
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
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE jobs SET status='pending', claimed_at=NULL, worker_id=NULL, "
                "updated_at=? WHERE status='working'",
                (now,),
            )
            await self.conn.commit()
            return cur.rowcount

    async def retry_terminal(self) -> int:
        now = self._now()
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE jobs SET status='pending', attempts=0, last_error=NULL, "
                "next_attempt_at=?, claimed_at=NULL, worker_id=NULL, updated_at=? "
                "WHERE status IN ('no_match','failed')",
                (now, now),
            )
            await self.conn.commit()
            return cur.rowcount

    async def counts(self) -> dict[str, int]:
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT status, COUNT(*) c FROM jobs GROUP BY status"
            )
            return {r["status"]: r["c"] for r in await cur.fetchall()}
