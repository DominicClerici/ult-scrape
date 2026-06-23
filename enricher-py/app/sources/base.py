from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    channel: str
    duration_s: int | None
    view_count: int | None
    url: str


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    ext: str


@dataclass(frozen=True)
class AudioProbe:
    codec: str
    bitrate_kbps: int | None
    sample_rate: int | None
    channels: int | None
    duration_s: float | None


class Searcher(Protocol):
    async def search(self, query: str, limit: int) -> list[Candidate]: ...


class Downloader(Protocol):
    async def download(
        self, video_id: str, dest_dir: Path, fmt: str
    ) -> DownloadResult: ...


class Prober(Protocol):
    async def probe(self, path: Path) -> AudioProbe: ...
