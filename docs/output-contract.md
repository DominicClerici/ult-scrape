# The Output Contract (the seam between scraper and decoder)

> Part of the [documentation map](../OVERVIEW.md). This is the **shared
> interface** between all three projects: [`scraper-py`](./scraper-py/overview.md)
> (producer), [`decoder-rs`](./decoder-rs/overview.md) (decoder consumer), and
> [`enricher-py`](./enricher-py/overview.md) (audio consumer/writer). All three
> share no code — only this filesystem layout. Treat it as **frozen**: a change
> here must be reflected in every affected project at once.

## Directory layout

`OUTPUT_DIR` lives at the **repo root** (`output/`). Both projects default to it
without configuration: the scraper resolves `../output` from its working dir
(`scraper-py/`), and the decoder walks up from wherever it is launched to find the
repo root. Either can be pointed elsewhere via the `OUTPUT_DIR` env var / CLI arg.

Each successfully scraped tab is committed as one directory under `OUTPUT_DIR`:

```
OUTPUT_DIR/<tab_id>/                # <tab_id> contains a slash, e.g. eagles/hotel-california-official-1910943
  <name>.xtz                        # one or more raw encrypted blobs (magic "XTZ\0")
  metadata.json                     # written LAST — its presence marks the dir complete
  <name>.gp | <name>.gpx            # (added later by decoder-rs) decrypted Guitar Pro container: .gp (GP7/8 ZIP) or .gpx (GP6 BCFZ)
  <name>.gpif                       # (added later by decoder-rs) extracted score.gpif XML
  audio.<ext>                       # (added later by enricher-py) best-available source audio
  audio.json                        # (added later by enricher-py) provenance + status sidecar
```

- `<tab_id>` is the canonical route, e.g. `eagles/hotel-california-official-1910943`.
  Because it contains a `/`, each tab directory is nested one level under
  `OUTPUT_DIR` (`OUTPUT_DIR/eagles/hotel-california-official-1910943/`).
- A tab may yield **more than one** `.xtz` artifact; each gets its own
  container (`.gp` or `.gpx`) plus `.gpif`.

## The commit marker: `metadata.json`

`metadata.json` is the **commit marker**. The scraper stages the whole directory
in a temp location and moves it into place with a single atomic `os.replace`
(rename), writing `metadata.json` last. Therefore:

- A consumer must treat a tab directory as **ready only if `metadata.json` exists**.
- The directory is never observed half-written — the rename is atomic.

**Producer:** [`scraper-py/app/output.py`](./scraper-py/configuration.md) →
`write_job_output()`.
**Consumer:** [`decoder-rs/src/discover.rs`](./decoder-rs/pipeline.md#discovery)
→ a directory is *eligible* iff it directly contains `metadata.json`.

> The decoder uses `metadata.json` purely as an **existence gate** — it does not
> parse it. So `metadata.json`'s schema can evolve without touching the decoder,
> as long as the file is still present and written last.

## `metadata.json` schema

Written by `write_job_output()` with `indent=2, sort_keys=True`:

```json
{
  "tab_id": "eagles/hotel-california-official-1910943",
  "url": "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943",
  "route": "eagles/hotel-california-official-1910943",
  "scraped_at": "2026-06-23T12:00:00",
  "scraper_version": "0.1.0",
  "http_status": 200,
  "files": [
    {
      "filename": "tab-download-ssid-1910943.xtz",
      "sha256": "…",
      "byte_size": 89306,
      "source_url": "https://tabs.ultimate-guitar.com/tab/download/file?…",
      "content_headers": { "content-type": "…", "content-disposition": "…" },
      "xtz_magic_ok": true
    }
  ],
  "song": {
    "artist_name": "Eagles",
    "artist_id": 1509,
    "song_name": "Hotel California",
    "song_id": 12345,
    "album_id": 2992,
    "tonality": "Em",
    "tuning": "E A D G B E"
  }
}
```

| Field | Meaning |
|---|---|
| `tab_id` / `route` | Canonical UG route (currently identical). The dedup key. |
| `url` | Full UG tab URL the worker navigated to. |
| `scraped_at` | ISO-8601 local timestamp, seconds precision. |
| `scraper_version` | `app.__version__` at scrape time. |
| `http_status` | HTTP status of the first captured artifact. |
| `files[]` | One entry per captured `.xtz`: name, sha256, size, source URL, selected response headers, and whether the bytes start with the `XTZ\0` magic. |
| `song` | **Optional, additive.** Clean song fields read from UG's hydrated page store at scrape time. Present only when both `artist_name` and `song_name` were captured; individual null/blank sub-fields are omitted. Consumed by the enricher; ignored by the decoder. |

### The additive `song` block

`song` is written by `scraper-py` (`extract_song_meta` in
[`browser/scrape.py`](./scraper-py/browser.md#song-metadata-capture-extract_song_meta--_song_block))
from UG's page store (the `.js-store` JSON blob in the server HTML; see
[browser.md](./scraper-py/browser.md#song-metadata-capture-extract_song_meta--_song_block)).
It is **best-effort and optional** — a
tab scraped before this field existed, or one whose store could not be read,
simply has no `song` key. Sub-fields (`artist_id`, `song_id`, `album_id`,
`tonality`, `tuning`) are each omitted when absent.

**Consumer:** `enricher-py` prefers `song.artist_name` + `song.song_name` for its
YouTube query and falls back to slug-parsing the route (`app/query.py`
`resolve_artist_song`) when the block is missing or lacks either field — so adding
or omitting `song` never changes which tabs are enrichable. The decoder ignores
it entirely (it uses `metadata.json` only as an existence gate).

> **Never source audio from `song_image`.** UG's `tab_view.song_image` is a
> YouTube id for a community video *lesson*, not the master recording — the
> scraper does not capture it, and the enricher selects audio via search.

## `.xtz` file format (summary)

The `.xtz` bytes are stored **exactly as downloaded** — no transformation. The
binary format (20-byte header + ChaCha8 payload) is owned by the decoder and
documented in [XTZ format & cipher](./decoder-rs/xtz-format-and-cipher.md).

## Re-scrape & idempotency

On a re-scrape, the scraper does `rmtree(<tab_id>)` then re-commits the directory.
This deletes any `.gp`/`.gpif` the decoder previously wrote and any
`audio.*`/`audio.json` the enricher wrote — the system is self-healing with no
extra state. See [decoder idempotency](./decoder-rs/pipeline.md#idempotency).

## Output files written by the decoder

For each pending `<stem>.xtz`, the decoder writes (atomically, temp + rename) the
decrypted container plus its extracted `score.gpif`. The container's extension
depends on the Guitar Pro version (switched on the decrypted payload's magic):

- `<stem>.gp` — **GP7/8**: the decrypted payload is a ZIP (`PK\x03\x04`);
  `score.gpif` comes from its `Content/score.gpif` entry.
- `<stem>.gpx` — **GP6**: the decrypted payload is a `BCFZ` blob; `score.gpif`
  comes from the BCFS filesystem inside it (see
  [GPX/BCFZ format](./decoder-rs/gpx-bcfz-format.md)).
- `<stem>.gpif` — the extracted `score.gpif` XML, for convenience (uniform across
  both versions).

Either container file is byte-for-byte the decrypted payload and is directly
openable in Guitar Pro. The `.gpif` is written **before** the container, because
the container's existence (`.gp` **or** `.gpx`) is the decoder's idempotency
marker; this ordering guarantees that whenever the marker exists, the `.gpif`
does too.

## Output files written by the enricher

For each tab directory that has `metadata.json` but no audio yet, `enricher-py`
writes (at most) two files:

- `audio.json` — written **first**. Contains provenance (YouTube video id, title,
  channel, duration, format) and a `status` field (`ok` or `no_match`).
- `audio.<ext>` — the downloaded audio file (e.g. `audio.opus`, `audio.m4a`),
  renamed into place **last** as the enricher's commit marker. Its presence
  (any `audio.*` file matching `audio.<ext>`) is the done gate — the enricher
  skips this tab on subsequent runs.

**`no_match` tabs:** when no suitable YouTube candidate is found, only `audio.json`
is written (with `"status": "no_match"`). These tabs are permanently skipped
unless re-enriched with `enricher run --retry-failed`.

**Commit ordering:** `audio.json` is written first; the audio file is renamed in
last. This guarantees that whenever the audio file exists, the sidecar does too.
Downloads land in a temp directory and are only renamed in on success, so a
partial download never satisfies the done gate.

**Re-scrape / self-healing:** a scraper re-scrape calls `rmtree(<tab_id>)` before
re-committing, which wipes any `audio.*` files too. The tab will be re-enriched
on the next `enricher run`. No extra state is required.

**Decoder interaction:** the decoder ignores `audio.*` files entirely — they play
no role in the `<stem>.xtz` → `<stem>.gp`/`.gpx` pipeline.
