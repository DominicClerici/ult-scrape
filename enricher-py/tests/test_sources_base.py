from pathlib import Path

from app.sources.base import AudioProbe, Candidate, DownloadResult


def test_candidate_is_frozen_dataclass():
    c = Candidate(video_id="v", title="t", channel="ch",
                  duration_s=300, view_count=10, url="u")
    assert c.video_id == "v"


def test_download_and_probe_dataclasses():
    d = DownloadResult(path=Path("/tmp/x.opus"), ext="opus")
    p = AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                   channels=2, duration_s=391.0)
    assert d.ext == "opus"
    assert p.codec == "opus"
