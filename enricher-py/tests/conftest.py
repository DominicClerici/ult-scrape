import shutil
from pathlib import Path

import pytest

from app.sources.base import AudioProbe, Candidate, DownloadResult


class FakeSearcher:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    async def search(self, query, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return list(self.results)


class FakeDownloader:
    def __init__(self, payload=b"FAKEAUDIO", ext="opus", error=None):
        self.payload = payload
        self.ext = ext
        self.error = error
        self.calls = []

    async def download(self, video_id, dest_dir, fmt):
        self.calls.append((video_id, dest_dir, fmt))
        if self.error:
            raise self.error
        dest = Path(dest_dir) / f"dl.{self.ext}"
        dest.write_bytes(self.payload)
        return DownloadResult(path=dest, ext=self.ext)


class FakeProber:
    async def probe(self, path):
        return AudioProbe(codec="opus", bitrate_kbps=160, sample_rate=48000,
                          channels=2, duration_s=391.0)


@pytest.fixture
def fakes():
    return {
        "searcher": FakeSearcher,
        "downloader": FakeDownloader,
        "prober": FakeProber,
        "topic_candidate": Candidate(
            video_id="topic1", title="Hotel California",
            channel="Eagles - Topic", duration_s=391,
            view_count=80000000, url="https://youtu.be/topic1"),
    }
