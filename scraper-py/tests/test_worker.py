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
