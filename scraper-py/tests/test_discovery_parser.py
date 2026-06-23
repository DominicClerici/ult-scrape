from pathlib import Path

import pytest

from app.discovery.parser import ExploreStore, parse_explore_html
from app.errors import DiscoveryParseError

FIXTURE = Path(__file__).parent / "fixtures" / "explore_min.html"


def test_parse_extracts_tabs_and_pagination():
    store = parse_explore_html(FIXTURE.read_text())
    assert isinstance(store, ExploreStore)
    assert store.pages == 2
    assert store.per_page == 50
    assert store.current_page == 0
    assert store.total_results == 73
    assert [t["id"] for t in store.tabs] == [111, 222]
    assert store.tabs[0]["tab_url"].endswith("song-a-official-111")
    assert {f["param_name"] for f in store.filters} == {"genres", "decade"}
    assert store.order["all"][0]["url_name"] == "date_desc"


def test_parse_missing_store_raises():
    with pytest.raises(DiscoveryParseError):
        parse_explore_html("<html><body>no store here</body></html>")
