import pytest

from app.browser.discover import explore_url, fetch_explore_html
from app.errors import DiscoveryFetchError


def test_explore_url_builds_absolute_explore_path():
    assert explore_url("type%5B%5D=Pro&page=2") == (
        "https://www.ultimate-guitar.com/explore?type%5B%5D=Pro&page=2"
    )


class _FakeLocator:
    async def count(self):
        return 0

    async def inner_text(self, timeout=None):
        return "explore page body"


class _FakePage:
    """Fake Playwright page driving fetch_explore_html.

    `xhr` is a list of results returned by successive in-page fetch evaluations
    (a dict, or an Exception to raise). The Cloudflare-wall probe is stubbed to
    look clear so wait_for_cloudflare_wall returns immediately.
    """

    def __init__(self, xhr):
        self._xhr = list(xhr)
        self.goto_calls = []
        self.url = "https://www.ultimate-guitar.com/explore"

    async def evaluate(self, script, *args):
        r = self._xhr.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def goto(self, url, **kw):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, *a, **k):
        pass

    async def title(self):
        return "Explore | Ultimate Guitar"

    def locator(self, *a, **k):
        return _FakeLocator()


async def test_returns_body_on_ok_without_navigating():
    page = _FakePage([{"ok": True, "status": 200, "body": "<server html>"}])
    out = await fetch_explore_html(page, "type%5B%5D=Official&page=1", 30_000, 1_000)
    assert out == "<server html>"
    assert page.goto_calls == []


async def test_falls_back_to_navigation_when_xhr_is_challenged():
    # First XHR is the Cloudflare 403 challenge; after a real navigation the
    # retried XHR carries the clearance cookie and returns the real HTML.
    page = _FakePage([
        {"ok": False, "status": 403, "body": "<html><title>Just a moment...</title></html>"},
        {"ok": True, "status": 200, "body": "<server html with js-store>"},
    ])
    out = await fetch_explore_html(page, "type%5B%5D=Official&page=1", 30_000, 1_000)
    assert out == "<server html with js-store>"
    assert page.goto_calls == ["https://www.ultimate-guitar.com/explore?type%5B%5D=Official&page=1"]


async def test_falls_back_when_xhr_throws():
    page = _FakePage([
        RuntimeError("fetch boom"),
        {"ok": True, "status": 200, "body": "<server html>"},
    ])
    out = await fetch_explore_html(page, "type%5B%5D=Official&page=1", 30_000, 1_000)
    assert out == "<server html>"
    assert page.goto_calls  # navigation happened


async def test_raises_when_still_blocked_after_fallback():
    page = _FakePage([
        {"ok": False, "status": 403, "body": "blocked"},
        {"ok": False, "status": 403, "body": "still blocked"},
    ])
    with pytest.raises(DiscoveryFetchError):
        await fetch_explore_html(page, "type%5B%5D=Official&page=1", 30_000, 1_000)
