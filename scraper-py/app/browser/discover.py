from __future__ import annotations

from app.browser.humanize import wait_for_cloudflare_wall, wait_for_load_or_pause
from app.errors import DiscoveryFetchError

EXPLORE_BASE = "https://www.ultimate-guitar.com/explore"

# In-page XHR that reuses the logged-in session cookies. Returns the response
# status alongside the body so the caller can tell a real page (200) from a
# Cloudflare interstitial (403) — a bare `await r.text()` would hand the
# challenge HTML back as if it were the page.
_XHR_JS = """async (u) => {
    try {
        const r = await fetch(u, { credentials: 'include' });
        return { ok: r.ok, status: r.status, body: await r.text() };
    } catch (e) {
        return { ok: false, status: 0, body: '', error: String(e) };
    }
}"""


def explore_url(query: str) -> str:
    return f"{EXPLORE_BASE}?{query}"


async def _xhr_fetch(page, url: str) -> dict:
    try:
        res = await page.evaluate(_XHR_JS, url)
    except Exception as e:  # evaluate itself failing (page detached, navigation)
        return {"ok": False, "status": 0, "body": "", "error": repr(e)}
    return res if isinstance(res, dict) else {"ok": False, "status": 0, "body": ""}


async def fetch_explore_html(
    page, query: str, timeout_ms: int, cf_timeout_ms: int
) -> str:
    """Fetch one explore page's server HTML from the logged-in session.

    Fast path is an in-page XHR that reuses the session cookies without a
    navigation. Cloudflare periodically challenges that XHR with a 403 "Just a
    moment" body an XHR can't solve; when the response is not OK we fall back to
    a real navigation — which survives the challenge and refreshes the
    cf_clearance cookie (the same pattern scrape.py uses) — wait the wall out,
    then retry the XHR, which now carries the cookie and returns the real server
    HTML with `.js-store` intact.
    """
    url = explore_url(query)

    res = await _xhr_fetch(page, url)
    if res.get("ok"):
        return res["body"]

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await wait_for_load_or_pause(page)
    await wait_for_cloudflare_wall(page, cf_timeout_ms)

    res = await _xhr_fetch(page, url)
    if res.get("ok"):
        return res["body"]

    raise DiscoveryFetchError(
        f"explore fetch blocked after Cloudflare fallback "
        f"(HTTP {res.get('status')}, {len(res.get('body') or '')} bytes); "
        f"query={query!r}"
    )
