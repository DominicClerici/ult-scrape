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
