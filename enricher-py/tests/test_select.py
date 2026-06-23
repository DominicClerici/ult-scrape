import json
from pathlib import Path

from app.select import ChosenCandidate, SelectConfig, choose
from app.sources.base import Candidate

CFG = SelectConfig(
    min_duration_s=60,
    reject_keywords=("lesson", "tutorial", "cover", "karaoke", "live", "remix"),
    confidence_threshold=0.5,
)


def _load():
    p = Path(__file__).parent / "fixtures" / "candidates_hotel_california.json"
    return [Candidate(**c) for c in json.loads(p.read_text())]


def test_topic_channel_wins():
    chosen = choose(_load(), "eagles", "hotel california", CFG)
    assert isinstance(chosen, ChosenCandidate)
    assert chosen.candidate.video_id == "topic1"
    assert chosen.reason == "topic_channel"
    assert chosen.confidence >= 0.9


def test_rejects_lesson_live_and_short():
    # Without the topic track, the remaining are all junk -> no_match.
    cands = [c for c in _load() if c.video_id != "topic1"]
    assert choose(cands, "eagles", "hotel california", CFG) is None


def test_title_match_when_no_topic_or_official():
    cands = [Candidate(video_id="ok", title="Eagles - Hotel California (Audio)",
                       channel="SomeUploader", duration_s=390,
                       view_count=1234, url="u")]
    chosen = choose(cands, "eagles", "hotel california", CFG)
    assert chosen is not None
    assert chosen.candidate.video_id == "ok"
    assert chosen.reason == "title_match"


def test_runners_up_recorded():
    cands = [
        Candidate(video_id="topic1", title="Hotel California",
                  channel="Eagles - Topic", duration_s=391,
                  view_count=80000000, url="u2"),
        Candidate(video_id="ok", title="Eagles - Hotel California",
                  channel="SomeUploader", duration_s=390,
                  view_count=1234, url="u"),
    ]
    chosen = choose(cands, "eagles", "hotel california", CFG)
    assert chosen.candidate.video_id == "topic1"
    assert any(r["video_id"] == "ok" for r in chosen.runners_up)
