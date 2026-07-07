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


async def test_enqueue_returns_existing_queued_job(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    again = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert again.id == first.id  # no duplicate row
    assert again.status is JobStatus.QUEUED
    assert await repo.queue_depth() == 1


async def test_enqueue_returns_existing_running_job(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    claimed = await repo.claim_next()
    assert claimed.id == first.id
    again = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert again.id == first.id
    assert again.status is JobStatus.RUNNING


async def test_enqueue_after_failed_creates_new_job(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    await repo.mark_permanent_failure(first.id, "404")
    again = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert again.id != first.id  # deliberate retry semantics
    assert again.status is JobStatus.QUEUED


async def test_enqueue_after_canceled_creates_new_job(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert await repo.cancel(first.id)
    again = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    assert again.id != first.id
    assert again.status is JobStatus.QUEUED


async def test_force_bypasses_active_dedup(repo):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    forced = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    assert forced.id != first.id
    assert await repo.queue_depth() == 2


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
