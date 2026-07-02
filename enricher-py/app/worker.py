from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import app
from app.discover import TabDir, find_audio_file, read_song_meta
from app.errors import PermanentEnrichError, TransientEnrichError
from app.models import JobStatus
from app.output import commit_audio, write_no_match
from app.query import build_query, resolve_artist_song
from app.select import SelectConfig, choose
from app.sources.base import Downloader, Prober, Searcher


@dataclass
class EnrichDeps:
    searcher: Searcher
    downloader: Downloader
    prober: Prober
    settings: object
    clock: callable = time.time
    version: str = field(default_factory=lambda: app.__version__)
    yt_dlp_version: str = "unknown"


def _select_config(settings) -> SelectConfig:
    return SelectConfig(
        min_duration_s=settings.min_duration_s,
        reject_keywords=settings.reject_keyword_list(),
        confidence_threshold=settings.confidence_threshold,
    )


async def enrich_tab(tab: TabDir, deps: EnrichDeps) -> JobStatus:
    # Filesystem is the source of truth for completion: if audio already
    # exists (e.g. a prior run committed it then crashed before mark_done),
    # skip the expensive search/download and report done.
    if find_audio_file(tab.path) is not None:
        return JobStatus.DONE

    s = deps.settings
    now_iso = datetime.fromtimestamp(deps.clock()).isoformat(timespec="seconds")

    song_meta = read_song_meta(tab.path)
    try:
        artist, song = resolve_artist_song(tab.route, song_meta)
    except ValueError as e:
        raise PermanentEnrichError(str(e)) from e
    query = f"{artist} {song}"

    try:
        candidates = await deps.searcher.search(query, s.search_results)
    except Exception as e:  # network/tooling failure -> retryable
        raise TransientEnrichError(f"search failed: {e}") from e

    chosen = choose(candidates, artist, song, _select_config(s))
    if chosen is None:
        write_no_match(
            tab_dir=tab.path, query=query, reason="no_confident_candidate",
            candidates_considered=len(candidates), runners_up=[],
            enricher_version=deps.version, now_iso=now_iso,
        )
        return JobStatus.NO_MATCH

    with tempfile.TemporaryDirectory(dir=tab.path) as tmp:
        try:
            dl = await deps.downloader.download(
                chosen.candidate.video_id, Path(tmp), s.ytdlp_format
            )
            probe = await deps.prober.probe(dl.path)
            commit_audio(
                tab_dir=tab.path, query=query, chosen=chosen, audio_tmp=dl.path,
                ext=dl.ext, probe=probe, enricher_version=deps.version,
                yt_dlp_version=deps.yt_dlp_version, now_iso=now_iso,
            )
        except Exception as e:
            # Covers transient OS-level failures too (e.g. os.replace() hitting
            # WinError 32 when AV/indexing briefly locks a just-downloaded
            # file), not just search/tooling errors.
            raise TransientEnrichError(f"download/probe/commit failed: {e}") from e
    return JobStatus.DONE


async def _worker_loop(
    *, name: str, repo, deps: EnrichDeps, output_root: Path,
    stop_event: "asyncio.Event", budget: list[int], summary: dict,
) -> None:
    while True:
        if stop_event.is_set():
            return
        if budget[0] is not None and budget[0] <= 0:
            return
        job = await repo.claim_next(name)
        if job is None:
            return
        if budget[0] is not None:
            budget[0] -= 1
        tab = TabDir(job.tab_id, job.route, output_root / job.tab_id)
        try:
            status = await enrich_tab(tab, deps)
            # Recompute the query the same way enrich_tab did, so the recorded
            # query matches the one actually searched (song block > slug).
            query = build_query(job.route, read_song_meta(tab.path))
            if status == JobStatus.DONE:
                await repo.mark_done(job.tab_id, "", query)
                summary["done"] += 1
            else:
                await repo.mark_no_match(job.tab_id, query)
                summary["no_match"] += 1
        except PermanentEnrichError as e:
            await repo.mark_failed(job.tab_id, str(e))
            summary["failed"] += 1
        except TransientEnrichError as e:
            result = await repo.record_transient_failure(
                job.tab_id, str(e), deps.settings.backoff_base_seconds,
                deps.settings.max_attempts,
            )
            summary["failed" if result == "failed" else "retried"] += 1


async def run_pool(
    *, repo, deps: EnrichDeps, output_root: Path, concurrency: int,
    stop_event: "asyncio.Event | None" = None, limit: int | None = None,
) -> dict:
    output_root = Path(output_root)
    stop_event = stop_event or asyncio.Event()
    summary = {"done": 0, "no_match": 0, "failed": 0, "retried": 0}
    budget = [limit]  # shared mutable cell across workers
    workers = [
        _worker_loop(name=f"w{i}", repo=repo, deps=deps,
                     output_root=output_root, stop_event=stop_event,
                     budget=budget, summary=summary)
        for i in range(max(1, concurrency))
    ]
    await asyncio.gather(*workers)
    return summary
