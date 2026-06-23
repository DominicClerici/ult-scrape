from urllib.parse import parse_qs

from app.discovery.facets import (
    FacetCatalog,
    FacetValue,
    SliceSpec,
    build_query,
    catalog_from_store,
)
from app.discovery.parser import ExploreStore


def _store():
    return ExploreStore(
        tabs=[], pages=1, per_page=50, current_page=0, total_results=0,
        filters=[
            {"param_name": "genres", "values": [
                {"name": "Rock", "url_name": 4, "count": 156741},
                {"name": "Metal", "url_name": 8, "count": 111476},
            ]},
            {"param_name": "decade", "values": [
                {"name": "2020s", "url_name": 2020, "count": 6078},
            ]},
        ],
        order={"all": [{"url_name": "date_desc"}, {"url_name": "rating_desc"}]},
    )


def test_catalog_from_store():
    cat = catalog_from_store(_store())
    assert isinstance(cat, FacetCatalog)
    genres = cat.values("genres")
    assert genres[0] == FacetValue(url_name="4", name="Rock", count=156741)
    assert [g.url_name for g in genres] == ["4", "8"]
    assert cat.values("decade")[0].url_name == "2020"
    assert cat.values("missing") == []
    assert cat.sorts == ["date_desc", "rating_desc"]


def test_build_query_encodes_type_filters_sort_page():
    spec = SliceSpec(filters={"type": "Pro", "genres": 4, "decade": 2020}, order="date_desc")
    q = build_query(spec, page=3)
    parsed = parse_qs(q)
    assert parsed["type[]"] == ["Pro"]
    assert parsed["genres[]"] == ["4"]
    assert parsed["decade[]"] == ["2020"]
    assert parsed["order"] == ["date_desc"]
    assert parsed["page"] == ["3"]


def test_build_query_omits_order_when_none():
    spec = SliceSpec(filters={"type": "Pro"})
    q = build_query(spec, page=1)
    assert "order=" not in q
    assert parse_qs(q)["page"] == ["1"]
