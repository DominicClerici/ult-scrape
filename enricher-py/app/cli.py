from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

import app
from app.config import Settings, get_settings
from app.db import connect, init_schema
from app.discover import find_audio_file, iter_ready_tabs, read_status
from app.repo import JobRepo
from app.worker import EnrichDeps, run_pool


async def _open_repo(settings: Settings) -> JobRepo:
    settings.enricher_db.parent.mkdir(parents=True, exist_ok=True)
    conn = await connect(settings.enricher_db)
    await init_schema(conn)
    return JobRepo(conn)


async def cmd_scan(settings: Settings, *, retry_failed: bool = False) -> dict:
    repo = await _open_repo(settings)
    try:
        await repo.reset_working_to_pending()
        if retry_failed:
            await repo.retry_terminal()
        counts = {"enqueued": 0, "skipped_done": 0, "skipped_no_match": 0}
        for tab in iter_ready_tabs(settings.output_dir):
            if find_audio_file(tab.path) is not None:
                counts["skipped_done"] += 1
                continue
            if not retry_failed and read_status(tab.path) == "no_match":
                counts["skipped_no_match"] += 1
                continue
            await repo.upsert_pending(tab.tab_id, tab.route)
            counts["enqueued"] += 1
        return counts
    finally:
        await repo.conn.close()


def _build_deps(settings: Settings) -> EnrichDeps:
    from app.sources.probe import FfprobeProber
    from app.sources.youtube import YtDlpSource, yt_dlp_version

    src = YtDlpSource()
    return EnrichDeps(
        searcher=src, downloader=src, prober=FfprobeProber(),
        settings=settings, version=app.__version__,
        yt_dlp_version=yt_dlp_version(),
    )


async def cmd_run(
    settings: Settings, *, jobs: int, limit: int | None = None,
    retry_failed: bool = False, deps: EnrichDeps | None = None,
    stop_event: "asyncio.Event | None" = None,
) -> dict:
    await cmd_scan(settings, retry_failed=retry_failed)
    repo = await _open_repo(settings)
    try:
        await repo.reset_working_to_pending()  # recover any stale claims
        deps = deps or _build_deps(settings)
        return await run_pool(
            repo=repo, deps=deps, output_root=settings.output_dir,
            concurrency=jobs, stop_event=stop_event, limit=limit,
        )
    finally:
        await repo.conn.close()


async def cmd_status(settings: Settings) -> dict:
    repo = await _open_repo(settings)
    try:
        return await repo.counts()
    finally:
        await repo.conn.close()


def _apply_overrides(settings: Settings, args) -> Settings:
    if args.output_dir:
        settings.output_dir = Path(args.output_dir)
    if args.db:
        settings.enricher_db = Path(args.db)
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enricher")
    parser.add_argument("--output-dir")
    parser.add_argument("--db")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan")
    p_run = sub.add_parser("run")
    p_run.add_argument("--jobs", type=int, default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--retry-failed", action="store_true")
    sub.add_parser("status")

    args = parser.parse_args(argv)
    settings = _apply_overrides(get_settings(), args)

    if args.command == "scan":
        counts = asyncio.run(cmd_scan(settings))
        if not args.quiet:
            print(counts)
        return 0

    if args.command == "status":
        print(asyncio.run(cmd_status(settings)))
        return 0

    # run
    jobs = args.jobs or settings.max_concurrency

    async def _go() -> dict:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, stop.set)
        except NotImplementedError:  # e.g. Windows
            pass
        return await cmd_run(settings, jobs=jobs, limit=args.limit,
                             retry_failed=args.retry_failed, stop_event=stop)

    summary = asyncio.run(_go())
    if not args.quiet:
        print(summary)
    return 0
