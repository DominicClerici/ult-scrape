import re

# Trailing UG cruft tokens to strip from the song slug, in order.
_TRAIL_PATTERNS = [
    re.compile(r"-guitar-pro-\d+$"),
    re.compile(r"-(?:official|ver\d+|tab|tabs|chords|bass|drums|ukulele)$"),
    re.compile(r"-\d+$"),  # trailing numeric id
]


def split_route(route: str) -> tuple[str, str]:
    route = (route or "").strip().strip("/")
    if "/" not in route:
        raise ValueError(f"unrecognized route: {route!r}")
    artist_slug, song_slug = route.split("/", 1)

    prev = None
    while prev != song_slug:
        prev = song_slug
        for pat in _TRAIL_PATTERNS:
            song_slug = pat.sub("", song_slug)

    artist = artist_slug.replace("-", " ").strip()
    song = song_slug.replace("-", " ").strip()
    if not artist or not song:
        raise ValueError(f"empty artist/song from route: {route!r}")
    return artist, song


def build_query(route: str) -> str:
    artist, song = split_route(route)
    return f"{artist} {song}"
