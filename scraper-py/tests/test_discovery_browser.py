from app.browser.discover import explore_url


def test_explore_url_builds_absolute_explore_path():
    assert explore_url("type%5B%5D=Pro&page=2") == (
        "https://www.ultimate-guitar.com/explore?type%5B%5D=Pro&page=2"
    )
