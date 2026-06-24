import logging

import pytest_asyncio

from app.browser.base import CapturedArtifact
from app.config import Settings
from app.errors import (
    PermanentScrapeError,
    RateLimitScrapeError,
    SessionExpiredError,
    TransientScrapeError,
)
from app.models import JobStatus, ServiceState
from app.worker import Outcome, Worker


class FakeBrowser:
    def __init__(self, artifacts=None, error=None, song=None):
        self.artifacts = artifacts or []
        self.error = error
        self.song = song
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
        return list(self.artifacts), self.song

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


async def test_process_output_write_failure_is_transient(repo, worker_factory, tmp_path):
    # Make output_dir unusable: a FILE where the output root should be a dir.
    bad_root = tmp_path / "out_is_a_file"
    bad_root.write_text("x")
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[_artifact()]))
    w.settings.output_dir = bad_root  # write_job_output will raise
    await w._process(job)  # must NOT raise
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 1


async def test_process_relogin_failure_does_not_escape(repo, worker_factory):
    from app.errors import SessionExpiredError
    from app.models import ServiceState

    class FailReloginBrowser(FakeBrowser):
        async def ensure_logged_in(self):
            self.login_calls += 1
            raise RuntimeError("re-login failed")

    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    browser = FailReloginBrowser(error=SessionExpiredError("logged out"))
    w = worker_factory(browser)
    await w._process(job)  # must NOT raise
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED  # requeued unchanged
    assert got.attempts == 0               # session expiry consumes no retry
    assert w.state is ServiceState.ERROR
    assert browser.login_calls == 1


async def test_process_logs_job_and_counts_success(repo, worker_factory, tmp_path, caplog):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[_artifact()]))
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await w._process(job)
    assert "[JOB] Scraping a/b-1" in caplog.text
    assert w._scraped_count == 1


async def test_process_failure_logs_error(repo, worker_factory, caplog):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=PermanentScrapeError("404")))
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        await w._process(job)
    assert "[ERROR] Failed to scrape a/b-1: 404" in caplog.text


async def test_dedup_short_circuit_does_not_count_or_log_job(repo, worker_factory, caplog):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.mark_succeeded(first.id, "/out/a/b-1")
    second = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    await repo.conn.execute("UPDATE jobs SET force=0 WHERE id=?", (second.id,))
    await repo.conn.commit()
    claimed = await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[_artifact()]))
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await w._process(claimed)
    assert "[JOB] Scraping" not in caplog.text
    assert w._scraped_count == 0


def test_log_batch_complete_logs_and_resets(repo, tmp_path, caplog):
    w = Worker(repo, FakeBrowser(), _settings(tmp_path), now_fn=lambda: 1000.0)
    w._scraped_count = 3
    with caplog.at_level(logging.INFO, logger="app.worker"):
        w._log_batch_complete()
    assert "[COMPLETE] Finished scraping 3 tab(s)" in caplog.text
    assert w._scraped_count == 0
    caplog.clear()
    # No tabs scraped -> nothing logged, no spurious [COMPLETE] on every idle tick.
    with caplog.at_level(logging.INFO, logger="app.worker"):
        w._log_batch_complete()
    assert "[COMPLETE]" not in caplog.text


async def test_process_returns_success_outcome(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[_artifact()]))
    assert await w._process(job) is Outcome.SUCCESS


async def test_process_returns_dedup_outcome(repo, worker_factory):
    first = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.mark_succeeded(first.id, "/out/a/b-1")
    second = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3, force=True)
    await repo.conn.execute("UPDATE jobs SET force=0 WHERE id=?", (second.id,))
    await repo.conn.commit()
    claimed = await repo.claim_next()
    w = worker_factory(FakeBrowser(artifacts=[_artifact()]))
    assert await w._process(claimed) is Outcome.DEDUP


async def test_process_returns_failure_outcome_on_permanent(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=PermanentScrapeError("404")))
    assert await w._process(job) is Outcome.FAILURE


async def test_process_returns_session_expired_outcome(repo, worker_factory):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=SessionExpiredError("logged out")))
    assert await w._process(job) is Outcome.SESSION_EXPIRED


async def test_process_session_expiry_applies_backoff_delay(repo, tmp_path):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        session_expiry_backoff_seconds=45,
    )
    w = Worker(repo, FakeBrowser(error=SessionExpiredError("logged out")), s,
               now_fn=lambda: 1000.0)
    await w._process(job)
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED
    assert got.attempts == 0
    assert got.next_attempt_at == 1000.0 + 45


async def test_process_rate_limit_is_transient_and_logged(repo, worker_factory, caplog):
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    w = worker_factory(FakeBrowser(error=RateLimitScrapeError("blocked")))
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        outcome = await w._process(job)
    assert outcome is Outcome.RATE_LIMITED
    got = await repo.get(job.id)
    assert got.status is JobStatus.QUEUED  # retryable
    assert got.attempts == 1               # consumes a retry, unlike session expiry
    assert "[RATE LIMIT]" in caplog.text


async def test_circuit_breaker_pauses_after_threshold(repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        circuit_breaker_threshold=3,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    await w._note_outcome(Outcome.FAILURE)
    await w._note_outcome(Outcome.FAILURE)
    assert not await repo.is_paused()
    await w._note_outcome(Outcome.FAILURE)
    assert await repo.is_paused()
    assert w.state is ServiceState.PAUSED


async def test_circuit_breaker_resets_on_success(repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        circuit_breaker_threshold=3,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    await w._note_outcome(Outcome.FAILURE)
    await w._note_outcome(Outcome.FAILURE)
    await w._note_outcome(Outcome.SUCCESS)
    await w._note_outcome(Outcome.FAILURE)
    await w._note_outcome(Outcome.FAILURE)
    assert not await repo.is_paused()  # counter was reset by the success


async def test_circuit_breaker_counts_session_expiry_and_rate_limit(repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        circuit_breaker_threshold=2,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    await w._note_outcome(Outcome.SESSION_EXPIRED)
    await w._note_outcome(Outcome.RATE_LIMITED)
    assert await repo.is_paused()


async def test_rate_limited_delay_uses_rate_limit_seconds(monkeypatch, repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        rate_limit_delay_seconds=123,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.worker.asyncio.sleep", fake_sleep)
    await w._delay_between_jobs(Outcome.RATE_LIMITED)
    assert slept == [123]


async def test_normal_delay_uses_inter_job_range(monkeypatch, repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=7, inter_job_delay_max=7,
        rate_limit_delay_seconds=123,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.worker.asyncio.sleep", fake_sleep)
    await w._delay_between_jobs(Outcome.SUCCESS)
    assert slept == [7]


async def test_rate_limit_cooloff_escalates_and_caps(monkeypatch, repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        rate_limit_delay_seconds=100, rate_limit_escalation_factor=2.0,
        rate_limit_max_delay_seconds=350, rate_limit_max_level=5,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.worker.asyncio.sleep", fake_sleep)
    for _ in range(3):
        w._record_pacing_outcome(Outcome.RATE_LIMITED)
        await w._delay_between_jobs(Outcome.RATE_LIMITED)
    # 100*2^0, 100*2^1, 100*2^2=400 -> capped at 350
    assert slept == [100, 200, 350]


async def test_rate_limit_widens_inter_job_gap(monkeypatch, repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=10, inter_job_delay_max=10,
        rate_limit_escalation_factor=2.0, rate_limit_recovery_successes=99,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.worker.asyncio.sleep", fake_sleep)
    w._record_pacing_outcome(Outcome.RATE_LIMITED)  # level 1
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 1, no reset (needs 99)
    await w._delay_between_jobs(Outcome.SUCCESS)     # 10 * 2^1
    assert slept == [20]


async def test_clean_streak_resets_escalation(monkeypatch, repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=10, inter_job_delay_max=10,
        rate_limit_escalation_factor=2.0, rate_limit_recovery_successes=2,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.worker.asyncio.sleep", fake_sleep)
    w._record_pacing_outcome(Outcome.RATE_LIMITED)  # level 1
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 1
    await w._delay_between_jobs(Outcome.SUCCESS)     # still level 1 -> 20
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 2 -> reset level
    await w._delay_between_jobs(Outcome.SUCCESS)     # level 0 -> 10
    assert slept == [20, 10]
    assert w._rate_limit_level == 0


async def test_non_rate_limit_failure_holds_level_but_breaks_streak(repo, tmp_path):
    s = Settings(
        output_dir=tmp_path / "out", inter_job_delay_min=0, inter_job_delay_max=0,
        rate_limit_recovery_successes=2,
    )
    w = Worker(repo, FakeBrowser(), s, now_fn=lambda: 1000.0)
    w._record_pacing_outcome(Outcome.RATE_LIMITED)  # level 1
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 1
    w._record_pacing_outcome(Outcome.FAILURE)       # breaks streak, holds level
    assert w._rate_limit_level == 1
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 1 again
    assert w._rate_limit_level == 1                  # not yet recovered
    w._record_pacing_outcome(Outcome.SUCCESS)       # streak 2 -> reset
    assert w._rate_limit_level == 0


async def test_process_writes_song_block(repo, worker_factory, tmp_path):
    import json
    job = await repo.enqueue(tab_id="a/b-1", url="u", max_attempts=3)
    await repo.claim_next()
    song = {"artist_name": "Eagles", "song_name": "Hotel California"}
    browser = FakeBrowser(artifacts=[_artifact()], song=song)
    w = worker_factory(browser)
    await w._process(job)
    meta = json.loads(
        (tmp_path / "out" / "a/b-1" / "metadata.json").read_text()
    )
    assert meta["song"] == song
