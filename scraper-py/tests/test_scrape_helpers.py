from app.browser.scrape import _filename, _selected_headers, _should_capture


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
