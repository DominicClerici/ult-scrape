from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import app
from app.discover import TabDir
from app.errors import PermanentEnrichError, TransientEnrichError
from app.models import JobStatus
from app.output import commit_audio, write_no_match
from app.query import build_query, split_route
from app.select import SelectConfig, choose
from app.sources.base import Downloader, Prober, Searcher


@dataclass
class EnrichDeps:
    searcher: Searcher
    downloader: Downloader
    prober: Prober
    settings: "object"
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
    s = deps.settings
    now_iso = datetime.fromtimestamp(deps.clock()).isoformat(timespec="seconds")

    try:
        artist, song = split_route(tab.route)
    except ValueError as e:
        raise PermanentEnrichError(str(e)) from e
    query = build_query(tab.route)

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

    with tempfile.TemporaryDirectory(dir=str(tab.path)) as tmp:
        try:
            dl = await deps.downloader.download(
                chosen.candidate.video_id, Path(tmp), s.ytdlp_format
            )
            probe = await deps.prober.probe(dl.path)
        except Exception as e:
            raise TransientEnrichError(f"download/probe failed: {e}") from e

        commit_audio(
            tab_dir=tab.path, query=query, chosen=chosen, audio_tmp=dl.path,
            ext=dl.ext, probe=probe, enricher_version=deps.version,
            yt_dlp_version=deps.yt_dlp_version, now_iso=now_iso,
        )
    return JobStatus.DONE
