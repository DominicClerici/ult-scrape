# CAPTURE_NOTE — scraper song-metadata capture

> Status: **implemented.** The optional `scraper-py` change this note proposed
> (capture clean song fields into `metadata.json`, prefer them in the enricher)
> now ships. This file is kept as a pointer to the canonical docs.

## What shipped

- **scraper-py** reads UG's hydrated `window.UGAPP.store.page.data` during the
  existing tab-page load (`app/browser/scrape.py` → `extract_song_meta` /
  `_song_block`) and writes an additive `song` block into `metadata.json`:

  ```json
  "song": {
    "artist_name": "Eagles",
    "artist_id": 1509,
    "song_name": "Hotel California",
    "song_id": 12345,
    "album_id": 2992,
    "tonality": "Em",
    "tuning": "E A D G B E"
  }
  ```

  The block is best-effort: it appears only when both `artist_name` and
  `song_name` were captured, individual null/blank sub-fields are dropped, and any
  failure to read the store leaves the block off entirely (the `.xtz` capture is
  never jeopardized).

- **enricher-py** prefers `song.artist_name` + `song.song_name` for its YouTube
  query (`app/query.py` `resolve_artist_song`, `app/discover.py`
  `read_song_meta`) and falls back to slug-parsing the route when the block is
  absent or incomplete — no breaking change.

## Canonical docs

- Contract: [`docs/output-contract.md` → "The additive `song` block"](../docs/output-contract.md#the-additive-song-block)
- Scraper capture: [`docs/scraper-py/browser.md`](../docs/scraper-py/browser.md#song-metadata-capture-extract_song_meta--_song_block)
- Enricher consumption: [`docs/enricher-py/overview.md`](../docs/enricher-py/overview.md)

## Warning — do NOT source audio from `song_image`

`tab_view.song_image` is an 11-char YouTube id, but it is a community **video
lesson** (often a guitar tutorial), frequently wrong-content, and sometimes a
dead/private video. It is **not** the master recording. `tab.recording`'s
`video_urls` / `recording_artists` are empty on tab pages. The scraper does not
capture it; audio is sourced only via the enricher's search + selection.
