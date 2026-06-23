import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api.routes import router
from app.config import get_settings
from app.models import ServiceState
from app.repo import JobRepo
from app.worker import Worker

log = logging.getLogger(__name__)


def create_app(repo=None, worker=None, settings=None) -> FastAPI:
    app = FastAPI(title="ult-scraper")
    app.include_router(router)

    if repo is not None:
        app.state.repo = repo
        app.state.worker = worker
        app.state.settings = settings
        return app

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.browser.session import CamoufoxBrowserSession

        s = get_settings()
        conn = await db.connect(s.db_path)
        await db.init_schema(conn)
        repo_ = JobRepo(conn)
        await repo_.reset_running_to_queued()
        await repo_.fail_interrupted_discovery()
        browser = CamoufoxBrowserSession(s)
        await browser.start()
        await browser.ensure_logged_in()
        worker_ = Worker(repo_, browser, s)
        _app.state.repo = repo_
        _app.state.worker = worker_
        _app.state.settings = s
        task = asyncio.create_task(worker_.run())

        def _on_worker_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error("worker task died: %r", exc)
                worker_.state = ServiceState.ERROR

        task.add_done_callback(_on_worker_done)
        try:
            yield
        finally:
            worker_.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await browser.close()
            await conn.close()

    app.router.lifespan_context = lifespan
    return app


app = create_app()
