import json

from app.discover import find_audio_file, iter_ready_tabs, read_status


def _make_tab(root, tab_id, *, audio=None, status=None):
    d = root / tab_id
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    if audio:
        (d / audio).write_bytes(b"x")
    if status:
        (d / "audio.json").write_text(json.dumps({"status": status}))
    return d


def test_iter_finds_only_committed_dirs(tmp_path):
    _make_tab(tmp_path, "eagles/hotel-california-guitar-pro-1")
    # a dir without metadata.json must be ignored
    (tmp_path / "pending" / "x").mkdir(parents=True)
    tabs = list(iter_ready_tabs(tmp_path))
    assert len(tabs) == 1
    assert tabs[0].tab_id == "eagles/hotel-california-guitar-pro-1"
    assert tabs[0].route == "eagles/hotel-california-guitar-pro-1"


def test_find_audio_file(tmp_path):
    d = _make_tab(tmp_path, "a/b-1", audio="audio.opus")
    assert find_audio_file(d).name == "audio.opus"
    d2 = _make_tab(tmp_path, "a/c-1")
    assert find_audio_file(d2) is None


def test_read_status(tmp_path):
    d = _make_tab(tmp_path, "a/d-1", status="no_match")
    assert read_status(d) == "no_match"
    d2 = _make_tab(tmp_path, "a/e-1")
    assert read_status(d2) is None
