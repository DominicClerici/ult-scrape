import json

from app.browser.base import CapturedArtifact
from app.output import write_job_output

XTZ = b"XTZ\x00" + b"payload-bytes"


def _artifact():
    return CapturedArtifact(
        filename="tab-download-ssid-1.xtz",
        data=XTZ,
        source_url="https://tabs.ultimate-guitar.com/tab/download/file?ssid=1",
        http_status=200,
        content_headers={"content-type": "application/octet-stream"},
    )


def test_writes_dir_with_raw_and_metadata(tmp_path):
    final = write_job_output(
        output_root=tmp_path,
        tab_id="a/b-1",
        url="https://tabs.ultimate-guitar.com/tab/a/b-1",
        route="a/b-1",
        scraper_version="0.1.0",
        http_status=200,
        artifacts=[_artifact()],
        scraped_at="2026-06-23T12:00:00",
    )
    assert final == tmp_path / "a/b-1"
    assert (final / "tab-download-ssid-1.xtz").read_bytes() == XTZ
    meta = json.loads((final / "metadata.json").read_text())
    assert meta["tab_id"] == "a/b-1"
    assert meta["scraper_version"] == "0.1.0"
    f = meta["files"][0]
    assert f["filename"] == "tab-download-ssid-1.xtz"
    assert f["byte_size"] == len(XTZ)
    assert f["xtz_magic_ok"] is True
    assert len(f["sha256"]) == 64


def test_no_staging_dir_left_behind(tmp_path):
    write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t",
    )
    assert not (tmp_path / ".tmp").exists() or not any((tmp_path / ".tmp").iterdir())


def test_rescrape_overwrites(tmp_path):
    write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t1",
    )
    final = write_job_output(
        output_root=tmp_path, tab_id="a/b-1", url="u", route="a/b-1",
        scraper_version="0.1.0", http_status=200,
        artifacts=[_artifact()], scraped_at="t2",
    )
    meta = json.loads((final / "metadata.json").read_text())
    assert meta["scraped_at"] == "t2"
