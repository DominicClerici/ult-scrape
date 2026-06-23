import json

from app.output import commit_audio, write_no_match
from app.select import ChosenCandidate
from app.sources.base import AudioProbe, Candidate


def _chosen():
    c = Candidate(video_id="topic1", title="Hotel California",
                  channel="Eagles - Topic", duration_s=391,
                  view_count=80000000, url="https://youtu.be/topic1")
    return ChosenCandidate(candidate=c, reason="topic_channel",
                           confidence=0.95, runners_up=[])


def test_commit_audio_writes_both_and_marker_last(tmp_path):
    tab = tmp_path / "eagles/hotel-california-guitar-pro-1"
    tab.mkdir(parents=True)
    src = tmp_path / "dl.opus"
    src.write_bytes(b"OggS-fake-audio")
    probe = AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                       channels=2, duration_s=391.0)

    audio_path = commit_audio(
        tab_dir=tab, query="eagles hotel california", chosen=_chosen(),
        audio_tmp=src, ext="opus", probe=probe,
        enricher_version="0.1.0", yt_dlp_version="2025.01.01",
        now_iso="2026-06-23T12:00:00",
    )

    assert audio_path == tab / "audio.opus"
    assert audio_path.read_bytes() == b"OggS-fake-audio"
    assert not src.exists()  # moved, not copied

    meta = json.loads((tab / "audio.json").read_text())
    assert meta["status"] == "ok"
    assert meta["audio_file"] == "audio.opus"
    assert meta["source"]["video_id"] == "topic1"
    assert meta["source"]["channel_is_topic"] is True
    assert meta["format"]["codec"] == "opus"
    assert len(meta["format"]["sha256"]) == 64


def test_write_no_match(tmp_path):
    tab = tmp_path / "a/b-1"
    tab.mkdir(parents=True)
    write_no_match(tab_dir=tab, query="a b", reason="low_confidence",
                   candidates_considered=5, runners_up=[],
                   enricher_version="0.1.0", now_iso="2026-06-23T12:00:00")
    meta = json.loads((tab / "audio.json").read_text())
    assert meta["status"] == "no_match"
    assert meta["audio_file"] is None
    assert not any(tab.glob("audio.*[!n]"))  # no audio.<ext>, only audio.json
