from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.select import ChosenCandidate
from app.sources.base import AudioProbe


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def commit_audio(
    *, tab_dir: Path, query: str, chosen: ChosenCandidate, audio_tmp: Path,
    ext: str, probe: AudioProbe, enricher_version: str, yt_dlp_version: str,
    now_iso: str,
) -> Path:
    tab_dir = Path(tab_dir)
    audio_name = f"audio.{ext}"
    c = chosen.candidate
    payload = {
        "status": "ok",
        "query": query,
        "source": {
            "platform": "youtube",
            "video_id": c.video_id,
            "url": c.url,
            "channel": c.channel,
            "channel_is_topic": chosen.reason == "topic_channel",
            "title": c.title,
            "duration_s": c.duration_s,
            "view_count": c.view_count,
        },
        "selection": {
            "reason": chosen.reason,
            "confidence": chosen.confidence,
            "runners_up": chosen.runners_up,
        },
        "audio_file": audio_name,
        "format": {
            "codec": probe.codec,
            "bitrate_kbps": probe.bitrate_kbps,
            "sample_rate": probe.sample_rate,
            "channels": probe.channels,
            "byte_size": audio_tmp.stat().st_size,
            "sha256": _sha256(audio_tmp),
        },
        "enriched_at": now_iso,
        "enricher_version": enricher_version,
        "yt_dlp_version": yt_dlp_version,
    }
    # audio.json first; the audio file is the commit marker, renamed in LAST.
    _write_json_atomic(tab_dir / "audio.json", payload)
    final = tab_dir / audio_name
    os.replace(audio_tmp, final)
    return final


def write_no_match(
    *, tab_dir: Path, query: str, reason: str, candidates_considered: int,
    runners_up: list[dict], enricher_version: str, now_iso: str,
) -> None:
    tab_dir = Path(tab_dir)
    payload = {
        "status": "no_match",
        "query": query,
        "source": None,
        "selection": {
            "reason": reason,
            "candidates_considered": candidates_considered,
            "runners_up": runners_up,
        },
        "audio_file": None,
        "format": None,
        "enriched_at": now_iso,
        "enricher_version": enricher_version,
    }
    _write_json_atomic(tab_dir / "audio.json", payload)
