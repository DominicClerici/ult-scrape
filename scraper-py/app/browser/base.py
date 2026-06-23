from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CapturedArtifact:
    filename: str
    data: bytes
    source_url: str
    http_status: int
    content_headers: dict[str, str] = field(default_factory=dict)


class BrowserSession(Protocol):
    async def ensure_logged_in(self) -> None: ...
    async def is_logged_in(self) -> bool: ...
    async def scrape(self, tab_url: str) -> list[CapturedArtifact]: ...
    async def fetch_explore(self, query: str) -> str: ...
    async def close(self) -> None: ...
