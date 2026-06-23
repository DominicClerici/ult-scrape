from app.discovery.facets import FacetCatalog, FacetValue, SliceSpec
from app.discovery.planner import initial_slices, sort_windows, subdivide


def _catalog():
    return FacetCatalog(
        facets={
            "genres": [FacetValue("4", "Rock", 156741), FacetValue("8", "Metal", 111476)],
            "decade": [FacetValue("2020", "2020s", 6078), FacetValue("2010", "2010s", 9000)],
            "tonality": [FacetValue("1", "C", 50), FacetValue("2", "G", 40)],
        },
        sorts=["date_desc", "rating_desc"],
    )


def test_initial_slices_one_per_genre_plus_untagged():
    slices = initial_slices(_catalog(), untagged_sweep=True)
    genre_vals = sorted(s.filters.get("genres") for s in slices if "genres" in s.filters)
    assert genre_vals == ["4", "8"]
    untagged = [s for s in slices if s.filters == {"type": "Pro"}]
    assert len(untagged) == 1
    assert all(s.depth == 0 for s in slices)


def test_initial_slices_respects_genre_subset_and_no_sweep():
    slices = initial_slices(_catalog(), untagged_sweep=False, genres=[8])
    assert [s.filters["genres"] for s in slices] == ["8"]


def test_subdivide_adds_next_ladder_facet():
    parent = SliceSpec(filters={"type": "Pro", "genres": "4"}, depth=0)
    children = subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"])
    assert children is not None
    assert all(c.filters["genres"] == "4" for c in children)
    assert sorted(c.filters["decade"] for c in children) == ["2010", "2020"]
    assert all(c.depth == 1 for c in children)


def test_subdivide_descends_to_third_facet():
    parent = SliceSpec(filters={"type": "Pro", "genres": "4", "decade": "2020"}, depth=1)
    children = subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"])
    assert sorted(c.filters["tonality"] for c in children) == ["1", "2"]


def test_subdivide_returns_none_when_ladder_exhausted():
    parent = SliceSpec(
        filters={"type": "Pro", "genres": "4", "decade": "2020", "tonality": "1"}, depth=2
    )
    assert subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"]) is None


def test_sort_windows_one_per_sort():
    spec = SliceSpec(filters={"type": "Pro", "genres": "4"})
    windows = sort_windows(spec, ["date_desc", "rating_desc"])
    assert [w.order for w in windows] == ["date_desc", "rating_desc"]
    assert all(w.filters == spec.filters for w in windows)
