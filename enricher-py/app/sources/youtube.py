from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from app.sources.base import Candidate, DownloadResult


def yt_dlp_version() -> str:
    try:
        return subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def _run(args: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


class YtDlpSource:
    async def search(self, query: str, limit: int) -> list[Candidate]:
        args = [
            "yt-dlp", f"ytsearch{limit}:{query}",
            "--dump-json", "--no-warnings", "--flat-playlist",
        ]
        code, out, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"yt-dlp search failed: {err.decode()[:300]}")
        results: list[Candidate] = []
        for line in out.decode().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            vid = d.get("id")
            if not vid:
                continue
            results.append(Candidate(
                video_id=vid,
                title=d.get("title") or "",
                channel=d.get("channel") or d.get("uploader") or "",
                duration_s=int(d["duration"]) if d.get("duration") else None,
                view_count=d.get("view_count"),
                url=d.get("url") or f"https://www.youtube.com/watch?v={vid}",
            ))
        return results

    async def download(
        self, video_id: str, dest_dir: Path, fmt: str
    ) -> DownloadResult:
        out_tmpl = str(Path(dest_dir) / "audio.%(ext)s")
        args = [
            "yt-dlp", f"https://www.youtube.com/watch?v={video_id}",
            "-f", fmt, "--no-warnings", "-o", out_tmpl,
        ]
        code, _, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"yt-dlp download failed: {err.decode()[:300]}")
        files = [p for p in Path(dest_dir).iterdir() if p.name.startswith("audio.")]
        if not files:
            raise RuntimeError("yt-dlp produced no audio file")
        path = files[0]
        return DownloadResult(path=path, ext=path.suffix.lstrip("."))
