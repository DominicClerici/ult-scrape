from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

log = logging.getLogger(__name__)

from app import __version__
from app.browser.base import BrowserSession
from app.config import Settings
from app.discovery import runner as discovery_runner
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
        self._scraped_count = 0  # tabs scraped since the queue last drained
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

            run = await self.repo.claim_discovery()
            if run is not None:
                self.state = ServiceState.DISCOVERING
                try:
                    await discovery_runner.run(self.browser, self.repo, run, self.settings)
                except Exception:
                    log.exception("discovery run %s crashed", run.id)
                continue

            job = await self.repo.claim_next()
            if job is None:
                self._log_batch_complete()
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
            except Exception as e:
                log.exception("unexpected error processing job %s", job.id)
                try:
                    await self.repo.record_transient_failure(
                        job.id, f"worker error: {e!r}", self.settings.backoff_base_seconds
                    )
                except Exception:
                    pass
            finally:
                self.current_job_id = None
            await self._delay_between_jobs()

    def _log_batch_complete(self) -> None:
        if self._scraped_count > 0:
            log.info("[COMPLETE] Finished scraping %d tab(s)", self._scraped_count)
            self._scraped_count = 0

    async def _process(self, job) -> None:
        if not job.force:
            existing = await self.repo.succeeded_output_for(job.tab_id, job.id)
            if existing:
                await self.repo.mark_succeeded(job.id, existing)
                return

        log.info("[JOB] Scraping %s", job.tab_id)
        try:
            artifacts, song = await self.browser.scrape(job.url)
        except SessionExpiredError:
            await self.repo.requeue_unchanged(job.id)
            self.state = ServiceState.LOGGING_IN
            try:
                await self.browser.ensure_logged_in()
            except Exception as e:
                log.warning("re-login after session expiry failed: %r", e)
                self.state = ServiceState.ERROR
            return
        except PermanentScrapeError as e:
            log.error("[ERROR] Failed to scrape %s: %s", job.tab_id, e)
            await self.repo.mark_permanent_failure(job.id, str(e))
            return
        except ScrapeError as e:
            log.error("[ERROR] Failed to scrape %s: %s", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, str(e), self.settings.backoff_base_seconds
            )
            return
        except Exception as e:  # unexpected -> transient
            log.error("[ERROR] Failed to scrape %s: %r", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, repr(e), self.settings.backoff_base_seconds
            )
            return

        if not artifacts:
            log.error("[ERROR] Failed to scrape %s: no artifacts captured", job.tab_id)
            await self.repo.record_transient_failure(
                job.id, "no artifacts captured", self.settings.backoff_base_seconds
            )
            return

        try:
            final = write_job_output(
                output_root=self.settings.output_dir,
                tab_id=job.tab_id,
                url=job.url,
                route=job.tab_id,
                scraper_version=__version__,
                http_status=artifacts[0].http_status,
                artifacts=artifacts,
                scraped_at=datetime.now().isoformat(timespec="seconds"),
                song=song,
            )
        except Exception as e:
            log.error("[ERROR] Failed to scrape %s: output write failed: %r", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, f"output write failed: {e!r}", self.settings.backoff_base_seconds
            )
            return
        await self.repo.mark_succeeded(job.id, str(final))
        self._scraped_count += 1

    async def _delay_between_jobs(self) -> None:
        hi = self.settings.inter_job_delay_max
        if hi > 0:
            lo = self.settings.inter_job_delay_min
            await asyncio.sleep(random.uniform(lo, hi))
