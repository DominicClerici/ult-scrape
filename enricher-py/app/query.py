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


def resolve_artist_song(
    route: str, song_meta: dict | None = None
) -> tuple[str, str]:
    """Prefer the scraper's clean `song` block; fall back to slug parsing.

    The block is only trusted when it carries both `artist_name` and `song_name`
    (the two fields the query is built from); anything less falls back to
    `split_route`, which raises ValueError on an unusable route.
    """
    if song_meta:
        artist = (song_meta.get("artist_name") or "").strip()
        song = (song_meta.get("song_name") or "").strip()
        if artist and song:
            return artist, song
    return split_route(route)


def build_query(route: str, song_meta: dict | None = None) -> str:
    artist, song = resolve_artist_song(route, song_meta)
    return f"{artist} {song}"
