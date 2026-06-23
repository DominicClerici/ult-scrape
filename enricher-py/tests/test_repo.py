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
