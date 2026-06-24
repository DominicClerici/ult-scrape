import pytest

from app.browser.scrape import (
    _filename,
    _raise_for_rate_limit,
    _selected_headers,
    _should_capture,
    _song_block,
    extract_song_meta,
)
from app.errors import RateLimitScrapeError, TransientScrapeError


def test_rate_limit_error_is_transient_subclass():
    assert issubclass(RateLimitScrapeError, TransientScrapeError)


@pytest.mark.parametrize("status", [403, 429])
def test_raise_for_rate_limit_raises_on_block_statuses(status):
    with pytest.raises(RateLimitScrapeError):
        _raise_for_rate_limit(status, "https://tabs.ultimate-guitar.com/tab/a/b-1")


@pytest.mark.parametrize("status", [None, 200, 404, 500])
def test_raise_for_rate_limit_passes_other_statuses(status):
    _raise_for_rate_limit(status, "u")  # must not raise


def test_should_capture_matches_download_endpoints():
    assert _should_capture(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943"
    )
    assert _should_capture(
        "https://tabs.ultimate-guitar.com/download/public/abc"
    )
    assert not _should_capture("https://tabs.ultimate-guitar.com/tab/eagles/x-1")
    assert not _should_capture("https://example.com/tab/download/file")


def test_selected_headers_lowercases_and_filters():
    headers = {"Content-Type": "application/octet-stream", "Server": "cf", "X-Other": "z"}
    out = _selected_headers(headers)
    assert out["content-type"] == "application/octet-stream"
    assert "x-other" not in out


def test_filename_from_content_disposition():
    name = _filename(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943",
        {"content-disposition": 'attachment; filename="song.xtz"'},
        b"XTZ\x00data",
    )
    assert name == "song.xtz"


def test_filename_from_ssid_query_when_no_disposition():
    name = _filename(
        "https://tabs.ultimate-guitar.com/tab/download/file?ssid=1910943&m=1",
        {},
        b"XTZ\x00data",
    )
    assert name == "tab-download-ssid-1910943.xtz"


def test_song_block_full_record():
    raw = {
        "artist_name": "Eagles", "artist_id": 1509,
        "song_name": "Hotel California", "song_id": 12345,
        "album_id": 2992, "tonality": "Em",
        "tuning": {"name": "Standard", "value": "E A D G B E", "index": 0},
    }
    assert _song_block(raw) == {
        "artist_name": "Eagles", "artist_id": 1509,
        "song_name": "Hotel California", "song_id": 12345,
        "album_id": 2992, "tonality": "Em",
        "tuning": "E A D G B E",
    }


def test_song_block_drops_nulls_and_blanks():
    raw = {
        "artist_name": "Eagles", "song_name": "Hotel California",
        "artist_id": None, "song_id": None, "album_id": None,
        "tonality": "", "tuning": None,
    }
    assert _song_block(raw) == {
        "artist_name": "Eagles", "song_name": "Hotel California",
    }


def test_song_block_tuning_plain_string():
    raw = {"artist_name": "A", "song_name": "B", "tuning": "D A D G B E"}
    assert _song_block(raw)["tuning"] == "D A D G B E"


def test_song_block_requires_artist_and_song():
    assert _song_block({"artist_name": "Eagles", "tonality": "Em"}) is None
    assert _song_block({"song_name": "Hotel California"}) is None


def test_song_block_non_dict_input():
    assert _song_block(None) is None
    assert _song_block("nope") is None


class _FakePage:
    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises

    async def evaluate(self, script):
        if self._raises:
            raise RuntimeError("evaluate boom")
        return self._result


async def test_extract_song_meta_returns_block():
    page = _FakePage(result={
        "artist_name": "Eagles", "song_name": "Hotel California",
    })
    assert await extract_song_meta(page) == {
        "artist_name": "Eagles", "song_name": "Hotel California",
    }


async def test_extract_song_meta_swallows_eval_errors():
    assert await extract_song_meta(_FakePage(raises=True)) is None


async def test_extract_song_meta_none_when_no_ugapp():
    assert await extract_song_meta(_FakePage(result=None)) is None
