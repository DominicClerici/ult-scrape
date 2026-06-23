from app.browser.fingerprint import load_or_create_fingerprint


def test_generates_and_persists_then_reloads_identically(tmp_path):
    path = tmp_path / "fp.json"
    assert not path.exists()

    first = load_or_create_fingerprint(path, "windows")
    assert path.exists()
    # Generated a Windows Firefox device.
    assert "Firefox" in first.navigator.userAgent
    assert "Win" in first.navigator.platform

    # Second call must reload the *same* device, not generate a new one.
    second = load_or_create_fingerprint(path, "windows")
    assert second.navigator.userAgent == first.navigator.userAgent
    assert second.screen.width == first.screen.width
    assert second.screen.height == first.screen.height
    assert second.fonts == first.fonts


def test_corrupt_file_is_regenerated(tmp_path):
    path = tmp_path / "fp.json"
    path.write_text("{not valid json", encoding="utf-8")

    fp = load_or_create_fingerprint(path, "windows")
    assert "Firefox" in fp.navigator.userAgent
    # File was rewritten with valid content that round-trips.
    again = load_or_create_fingerprint(path, "windows")
    assert again.navigator.userAgent == fp.navigator.userAgent
