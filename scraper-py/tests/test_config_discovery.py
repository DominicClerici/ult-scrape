from app.config import Settings


def test_discovery_defaults():
    s = Settings(_env_file=None)
    assert s.discovery_sort_orders == "date_desc,artistname_asc,artistname_desc,songname_asc"
    assert s.discovery_facet_ladder == "genres,decade,tonality"
    assert s.discovery_page_delay_min == 2.0
    assert s.discovery_page_delay_max == 6.0
    assert s.discovery_max_slices == 0
    assert s.discovery_target_cap == 0
    assert s.discovery_request_timeout_ms == 30_000
    assert s.discovery_untagged_sweep is True
