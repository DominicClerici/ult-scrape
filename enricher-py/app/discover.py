from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = (".opus", ".m4a", ".webm", ".mp3", ".ogg")


@dataclass(frozen=True)
class TabDir:
    tab_id: str
    route: str
    path: Path


def find_audio_file(tab_dir: Path) -> Path | None:
    for ext in AUDIO_EXTS:
        p = tab_dir / f"audio{ext}"
        if p.exists():
            return p
    return None


def read_status(tab_dir: Path) -> str | None:
    p = tab_dir / "audio.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("status")
    except (ValueError, OSError):
        return None


def iter_ready_tabs(output_root: Path) -> Iterator[TabDir]:
    output_root = Path(output_root)
    for meta in output_root.rglob("metadata.json"):
        tab_dir = meta.parent
        rel = tab_dir.relative_to(output_root).as_posix()
        yield TabDir(tab_id=rel, route=rel, path=tab_dir)
