import pytest

from app.normalize import normalize_tab

ROUTE = "eagles/hotel-california-official-1910943"
URL = f"https://tabs.ultimate-guitar.com/tab/{ROUTE}"


def test_bare_route():
    assert normalize_tab(ROUTE) == (ROUTE, URL)


def test_full_tabs_url():
    assert normalize_tab(URL) == (ROUTE, URL)


def test_www_url_with_tab_path():
    assert normalize_tab(f"https://www.ultimate-guitar.com/tab/{ROUTE}") == (ROUTE, URL)


def test_trailing_slash_trimmed():
    assert normalize_tab(f"/{ROUTE}/") == (ROUTE, URL)


def test_url_without_tab_segment_raises():
    with pytest.raises(ValueError):
        normalize_tab("https://example.com/foo/bar")


def test_empty_raises():
    with pytest.raises(ValueError):
        normalize_tab("   ")


def test_single_segment_raises():
    with pytest.raises(ValueError):
        normalize_tab("just-one-segment")
