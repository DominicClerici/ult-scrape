from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.sources.base import AudioProbe


async def _run(args: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


class FfprobeProber:
    async def probe(self, path: Path) -> AudioProbe:
        args = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ]
        code, out, err = await _run(args)
        if code != 0:
            raise RuntimeError(f"ffprobe failed: {err.decode()[:300]}")
        data = json.loads(out.decode())
        astream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            {},
        )
        fmt = data.get("format", {})
        bitrate = fmt.get("bit_rate") or astream.get("bit_rate")
        return AudioProbe(
            codec=astream.get("codec_name") or "unknown",
            bitrate_kbps=int(int(bitrate) / 1000) if bitrate else None,
            sample_rate=int(astream["sample_rate"]) if astream.get("sample_rate") else None,
            channels=astream.get("channels"),
            duration_s=float(fmt["duration"]) if fmt.get("duration") else None,
        )
