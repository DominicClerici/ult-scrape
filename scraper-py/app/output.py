from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from app.browser.base import CapturedArtifact

XTZ_MAGIC = b"XTZ\x00"


def write_job_output(
    *,
    output_root: Path | str,
    tab_id: str,
    url: str,
    route: str,
    scraper_version: str,
    http_status: int,
    artifacts: list[CapturedArtifact],
    scraped_at: str,
    song: dict | None = None,
) -> Path:
    output_root = Path(output_root)
    staging = output_root / ".tmp" / uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        files_meta = []
        for art in artifacts:
            (staging / art.filename).write_bytes(art.data)
            files_meta.append({
                "filename": art.filename,
                "sha256": hashlib.sha256(art.data).hexdigest(),
                "byte_size": len(art.data),
                "source_url": art.source_url,
                "content_headers": art.content_headers,
                "xtz_magic_ok": art.data[:4] == XTZ_MAGIC,
            })

        metadata = {
            "tab_id": tab_id,
            "url": url,
            "route": route,
            "scraped_at": scraped_at,
            "scraper_version": scraper_version,
            "http_status": http_status,
            "files": files_meta,
        }
        if song:
            metadata["song"] = song
        # metadata.json is written LAST — it is the commit marker.
        (staging / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
        )

        final = output_root / tab_id
        if final.exists():
            shutil.rmtree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)  # atomic dir rename within same filesystem
        return final
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
