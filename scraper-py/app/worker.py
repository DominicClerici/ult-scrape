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
