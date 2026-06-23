from urllib.parse import urlparse

TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"


def normalize_tab(url_or_route: str) -> tuple[str, str]:
    raw = (url_or_route or "").strip()
    if not raw:
        raise ValueError("empty tab reference")

    if raw.startswith(("http://", "https://")):
        path = urlparse(raw).path
        marker = "/tab/"
        idx = path.find(marker)
        if idx == -1:
            raise ValueError(f"not a UG tab URL: {url_or_route!r}")
        route = path[idx + len(marker):]
    else:
        route = raw

    tab_id = route.strip("/")
    if not tab_id or "/" not in tab_id:
        raise ValueError(f"unrecognized tab route: {url_or_route!r}")

    return tab_id, f"{TAB_BASE_URL}/{tab_id}"
