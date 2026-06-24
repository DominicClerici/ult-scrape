from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from enum import Enum

log = logging.getLogger(__name__)

from app import __version__
from app.browser.base import BrowserSession
from app.config import Settings
from app.discovery import runner as discovery_runner
from app.errors import (
    PermanentScrapeError,
    RateLimitScrapeError,
    ScrapeError,
    SessionExpiredError,
)
from app.models import ServiceState
from app.output import write_job_output
from app.repo import JobRepo


class Outcome(Enum):
    """Result of processing one job, used to drive the circuit breaker and the
    inter-job delay. SUCCESS/DEDUP reset the breaker; everything else trips it."""

    SUCCESS = "success"
    DEDUP = "dedup"
    FAILURE = "failure"
    RATE_LIMITED = "rate_limited"
    SESSION_EXPIRED = "session_expired"


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
        self._consecutive_failures = 0  # for the circuit breaker
        self._rate_limit_level = 0  # escalating 403/429 pacing (0 = baseline)
        self._clean_streak = 0  # successes in a row, for clearing the escalation
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
                outcome = await self._process(job)
            except Exception as e:
                log.exception("unexpected error processing job %s", job.id)
                outcome = Outcome.FAILURE
                try:
                    await self.repo.record_transient_failure(
                        job.id, f"worker error: {e!r}", self.settings.backoff_base_seconds
                    )
                except Exception:
                    pass
            finally:
                self.current_job_id = None
            await self._note_outcome(outcome)
            self._record_pacing_outcome(outcome)
            await self._delay_between_jobs(outcome)

    def _log_batch_complete(self) -> None:
        if self._scraped_count > 0:
            log.info("[COMPLETE] Finished scraping %d tab(s)", self._scraped_count)
            self._scraped_count = 0

    async def _process(self, job) -> Outcome:
        if not job.force:
            existing = await self.repo.succeeded_output_for(job.tab_id, job.id)
            if existing:
                await self.repo.mark_succeeded(job.id, existing)
                return Outcome.DEDUP

        log.info("[JOB] Scraping %s", job.tab_id)
        try:
            artifacts, song = await self.browser.scrape(job.url)
        except SessionExpiredError:
            await self.repo.requeue_unchanged(
                job.id, delay=self.settings.session_expiry_backoff_seconds
            )
            self.state = ServiceState.LOGGING_IN
            try:
                await self.browser.ensure_logged_in()
            except Exception as e:
                log.warning("re-login after session expiry failed: %r", e)
                self.state = ServiceState.ERROR
            return Outcome.SESSION_EXPIRED
        except RateLimitScrapeError as e:
            log.warning("[RATE LIMIT] %s: %s; backing off", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, str(e), self.settings.backoff_base_seconds
            )
            return Outcome.RATE_LIMITED
        except PermanentScrapeError as e:
            log.error("[ERROR] Failed to scrape %s: %s", job.tab_id, e)
            await self.repo.mark_permanent_failure(job.id, str(e))
            return Outcome.FAILURE
        except ScrapeError as e:
            log.error("[ERROR] Failed to scrape %s: %s", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, str(e), self.settings.backoff_base_seconds
            )
            return Outcome.FAILURE
        except Exception as e:  # unexpected -> transient
            log.error("[ERROR] Failed to scrape %s: %r", job.tab_id, e)
            await self.repo.record_transient_failure(
                job.id, repr(e), self.settings.backoff_base_seconds
            )
            return Outcome.FAILURE

        if not artifacts:
            log.error("[ERROR] Failed to scrape %s: no artifacts captured", job.tab_id)
            await self.repo.record_transient_failure(
                job.id, "no artifacts captured", self.settings.backoff_base_seconds
            )
            return Outcome.FAILURE

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
            return Outcome.FAILURE
        await self.repo.mark_succeeded(job.id, str(final))
        self._scraped_count += 1
        return Outcome.SUCCESS

    async def _note_outcome(self, outcome: Outcome) -> None:
        """Drive the circuit breaker: reset on real progress, otherwise count up
        and auto-pause the worker once too many jobs fail in a row. A persisted
        pause survives restart, so an operator must investigate and POST /resume."""
        if outcome in (Outcome.SUCCESS, Outcome.DEDUP):
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.settings.circuit_breaker_threshold:
            log.error(
                "[CIRCUIT BREAKER] %d consecutive failures; pausing worker. "
                "Investigate, then POST /resume.",
                self._consecutive_failures,
            )
            await self.repo.set_paused(True)
            self.state = ServiceState.PAUSED
            self._consecutive_failures = 0

    def _record_pacing_outcome(self, outcome: Outcome) -> None:
        """Escalate or relax the 403/429 pacing. Each rate-limit bumps the
        escalation level (capped), widening both the cool-off and the inter-job
        gap; a clean streak of successes clears it. Other failures hold the level
        but break the recovery streak so it can't be cleared by stray progress."""
        if outcome is Outcome.RATE_LIMITED:
            self._rate_limit_level = min(
                self._rate_limit_level + 1, self.settings.rate_limit_max_level
            )
            self._clean_streak = 0
            return
        if outcome in (Outcome.SUCCESS, Outcome.DEDUP):
            self._clean_streak += 1
            if (
                self._rate_limit_level > 0
                and self._clean_streak >= self.settings.rate_limit_recovery_successes
            ):
                self._rate_limit_level = 0
                self._clean_streak = 0
            return
        self._clean_streak = 0

    async def _delay_between_jobs(self, outcome: Outcome | None = None) -> None:
        factor = self.settings.rate_limit_escalation_factor
        if outcome is Outcome.RATE_LIMITED:
            # Escalating global cool-off: level 1 = base, then *factor per strike.
            level = max(self._rate_limit_level, 1)
            delay = self.settings.rate_limit_delay_seconds * factor ** (level - 1)
            delay = min(delay, self.settings.rate_limit_max_delay_seconds)
            if delay > 0:
                await asyncio.sleep(delay)
            return
        hi = self.settings.inter_job_delay_max
        if hi > 0:
            lo = self.settings.inter_job_delay_min
            # Widen the gap while we're in an escalated (post 403/429) state.
            await asyncio.sleep(random.uniform(lo, hi) * factor ** self._rate_limit_level)
