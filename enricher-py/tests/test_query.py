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


def test_resolve_prefers_song_meta():
    from app.query import resolve_artist_song
    song = {"artist_name": "Eagles", "song_name": "Hotel California"}
    assert resolve_artist_song("x/y-1", song) == ("Eagles", "Hotel California")


def test_resolve_falls_back_to_slug_when_no_meta():
    from app.query import resolve_artist_song
    assert resolve_artist_song(
        "eagles/hotel-california-guitar-pro-382996", None
    ) == ("eagles", "hotel california")


def test_resolve_falls_back_when_meta_incomplete():
    from app.query import resolve_artist_song
    # missing song_name -> not usable, fall back to slug
    song = {"artist_name": "Eagles"}
    assert resolve_artist_song(
        "eagles/hotel-california-guitar-pro-382996", song
    ) == ("eagles", "hotel california")


def test_build_query_uses_song_meta():
    song = {"artist_name": "AC/DC", "song_name": "Back in Black"}
    assert build_query("x/y-1", song) == "AC/DC Back in Black"
