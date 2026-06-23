from __future__ import annotations

import json
import logging
from dataclasses import fields, is_dataclass
from pathlib import Path

from browserforge.fingerprints import (
    Fingerprint,
    NavigatorFingerprint,
    ScreenFingerprint,
    VideoCard,
)

from camoufox.fingerprints import generate_fingerprint

log = logging.getLogger(__name__)


def _build(cls, data):
    """Reconstruct a dataclass from a plain dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def _fingerprint_from_dict(d: dict) -> Fingerprint:
    fp = _build(Fingerprint, d)
    # Re-hydrate the nested dataclasses that asdict() flattened into plain dicts.
    fp.screen = _build(ScreenFingerprint, d["screen"])
    fp.navigator = _build(NavigatorFingerprint, d["navigator"])
    fp.videoCard = _build(VideoCard, d["videoCard"]) if d.get("videoCard") else None
    return fp


def load_or_create_fingerprint(path: Path, os_name: str) -> Fingerprint:
    """
    Return a stable Camoufox fingerprint, generating and persisting one on first
    use so that every later launch presents the *same* device.

    The fingerprint is the device identity (user-agent, screen, fonts, GPU,
    codecs, hardwareConcurrency, ...). Pinning it so it matches the persistent
    cookie jar is what makes the saved session look like one consistent browser
    across runs instead of a new device on every launch.
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fp = _fingerprint_from_dict(data)
            log.info("loaded persisted fingerprint from %s", path)
            return fp
        except Exception as e:  # corrupt/incompatible file -> regenerate
            log.warning("could not load fingerprint %s (%s); regenerating", path, e)

    fp = generate_fingerprint(os=os_name)
    assert is_dataclass(fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(fp.dumps(), encoding="utf-8")
    tmp.replace(path)  # atomic
    log.info("generated and persisted new fingerprint to %s", path)
    return fp
