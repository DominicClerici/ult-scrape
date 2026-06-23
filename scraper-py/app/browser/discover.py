from __future__ import annotations

EXPLORE_BASE = "https://www.ultimate-guitar.com/explore"


def explore_url(query: str) -> str:
    return f"{EXPLORE_BASE}?{query}"


async def fetch_explore_html(page, query: str, timeout_ms: int) -> str:
    url = explore_url(query)
    try:
        return await page.evaluate(
            """async (u) => {
                const r = await fetch(u, { credentials: 'include' });
                return await r.text();
            }""",
            url,
        )
    except Exception:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return await page.content()
