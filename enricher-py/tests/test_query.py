import pytest

from app.query import build_query, split_route


@pytest.mark.parametrize("route,artist,song", [
    ("eagles/hotel-california-guitar-pro-382996", "eagles", "hotel california"),
    ("metallica/nothing-else-matters-guitar-pro-225441",
     "metallica", "nothing else matters"),
    ("guns-n-roses/sweet-child-o-mine-official-220689",
     "guns n roses", "sweet child o mine"),
    ("nirvana/smells-like-teen-spirit-ver2-1940883",
     "nirvana", "smells like teen spirit"),
])
def test_split_route(route, artist, song):
    assert split_route(route) == (artist, song)


def test_build_query():
    assert build_query("eagles/hotel-california-guitar-pro-382996") == \
        "eagles hotel california"


def test_invalid_route():
    with pytest.raises(ValueError):
        split_route("no-slash-here")
