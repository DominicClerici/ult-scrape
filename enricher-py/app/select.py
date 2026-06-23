from __future__ import annotations

import re
from dataclasses import dataclass

from app.sources.base import Candidate


@dataclass
class SelectConfig:
    min_duration_s: int
    reject_keywords: tuple[str, ...]
    confidence_threshold: float


@dataclass
class ChosenCandidate:
    candidate: Candidate
    reason: str
    confidence: float
    runners_up: list[dict]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t}


def _title_similarity(title: str, artist: str, song: str) -> float:
    want = _tokens(artist) | _tokens(song)
    if not want:
        return 0.0
    have = _tokens(title)
    return len(want & have) / len(want)


def _has_reject_kw(text: str, keywords: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in keywords)


def _score(c: Candidate, artist: str, song: str, cfg: SelectConfig) -> tuple[float, str]:
    """Return (confidence, reason). 0.0 means disqualified."""
    if c.duration_s is not None and c.duration_s < cfg.min_duration_s:
        return 0.0, "too_short"

    channel_low = (c.channel or "").lower()
    is_topic = channel_low == f"{artist.lower()} - topic"

    # Junk keywords disqualify non-topic uploads (topic art-tracks are trusted).
    if not is_topic and _has_reject_kw(c.title, cfg.reject_keywords):
        return 0.0, "rejected_keyword"

    sim = _title_similarity(c.title, artist, song)

    if is_topic:
        return max(0.95, sim), "topic_channel"
    if artist.lower() in channel_low and sim >= 0.75:
        return max(0.8, sim), "official_channel"
    if sim >= 0.75:
        return min(0.74, 0.4 + 0.34 * sim), "title_match"
    return 0.0, "low_similarity"


def choose(
    candidates: list[Candidate], artist: str, song: str, cfg: SelectConfig
) -> ChosenCandidate | None:
    scored = []
    for c in candidates:
        conf, reason = _score(c, artist, song, cfg)
        if conf > 0.0:
            scored.append((conf, c.view_count or 0, c, reason))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best_conf, _, best, best_reason = scored[0]
    if best_conf < cfg.confidence_threshold:
        return None
    runners_up = [
        {"video_id": c.video_id, "title": c.title, "score": round(conf, 3)}
        for conf, _, c, _ in scored[1:4]
    ]
    return ChosenCandidate(
        candidate=best, reason=best_reason,
        confidence=round(best_conf, 3), runners_up=runners_up,
    )
