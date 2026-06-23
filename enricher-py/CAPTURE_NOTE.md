# CAPTURE_NOTE — deferred scraper metadata capture

> Status: **not implemented.** This documents a small, optional future change to
> `scraper-py` that would improve enrichment match quality. The enricher works
> today off the tab slug; this note records what to capture when we decide to.

## Why

The enricher builds its YouTube query from the tab slug (e.g.
`eagles/hotel-california-guitar-pro-382996` → `"eagles hotel california"`). That
works, but clean fields give a better query and a stable dedup key.

## What UG already exposes (verified, logged-in, on the tab page)

Every tab page hydrates `window.UGAPP.store.page.data`. Useful fields:

| Field | Example |
|---|---|
| `tab.artist_name` / `tab.artist_id` | `Eagles` / `1509` |
| `tab.song_name` / `tab.song_id` | `Hotel California` |
| `tab.recording.album_id` | `2992` |
| `tab_view.meta.tonality` | `Em` |
| `tab_view.meta.tuning` | `{name, value, index}` |

## Proposed change

In `scraper-py`, capture these during the existing tab-page load and write an
additive `song` block into `metadata.json` (the decoder ignores unknown keys):

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

The enricher will then prefer `metadata.json["song"]` when present and fall back
to slug-parsing (`app/query.py`) when absent — no breaking change.

## Warning — do NOT source audio from `song_image`

`tab_view.song_image` is an 11-char YouTube id, but it is a community **video
lesson** (often a guitar tutorial), frequently wrong-content, and sometimes a
dead/private video. It is **not** the master recording. `tab.recording`'s
`video_urls` / `recording_artists` are empty on tab pages. Source audio only via
the enricher's search + selection.
