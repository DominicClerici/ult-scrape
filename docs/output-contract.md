# The Output Contract (the seam between scraper and decoder)

> Part of the [documentation map](../OVERVIEW.md). This is the **shared
> interface** between all four projects: [`scraper-py`](./scraper-py/overview.md)
> (producer), [`decoder-rs`](./decoder-rs/overview.md) (decoder consumer),
> [`enricher-py`](./enricher-py/overview.md) (audio consumer/writer), and
> [`aligner-py`](./aligner-py/overview.md) (alignment consumer/writer). All four
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
  align.json                        # (added later by aligner-py) audio↔.gp alignment + confidence
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
This deletes any `.gp`/`.gpif` the decoder previously wrote, any `audio.*`/`audio.json`
the enricher wrote, and any `align.json` the aligner wrote — the system is
self-healing with no extra state. See
[decoder idempotency](./decoder-rs/pipeline.md#idempotency).

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

## Output files written by the aligner

For each tab directory that has `metadata.json`, at least one decoded `.gp`, and an
`audio.*` file, `aligner-py` writes one file:

- `align.json` — written **last** (via temp + `os.replace`) as the aligner's commit
  marker. Its presence is the done gate for consumers — `align run` re-aligns and
  overwrites it unconditionally each time you name the tab.

**`status` values:** `ok` (aligned; `fit_cost`, `path_deviation`, **and**
`coverage` all within thresholds), `rejected` (aligned but any one of those
missed), `no_gp` (no decoded `.gp` found), `no_audio` (no `audio.*` found). For
`no_gp` and `no_audio`, `align.json` is still written (so the tab is not retried
automatically), but `confidence`, `offset_s`, `tempo_ratio`, `mode`,
`tempo_source`, and `coverage` are `null`, and `warp` and `gaps` are `[]`.

**Tempo fields:** alignment runs a five-stage pipeline — silence/gap detection
first (tempo-free, so a long dead stretch can't tilt the tempo estimate), then a
coarse DTW at the notated tempo, a robust tempo fit that masks internal dead regions
from the slope calculation, a snap to a clean factor (or a DTW-derived fallback),
and a final gap-aware alignment (see
[aligner overview](./aligner-py/overview.md#gap-aware-tempo-alignment)). Both DTW
passes run in **subsequence mode**, so a leading/trailing stretch the tab doesn't
cover — a non-silent intro, outro, or jam — is skipped rather than stretched over;
`offset_s` is then the real time the tab's first note maps to (it can be tens of
seconds when a recording opens with an uncovered intro).
`tempo_ratio` is the applied real/symbolic tempo ratio (`1.0` = none);
`tempo_source` records where it came from: `notated` (rendered tempo already
matched), `notated_x2` / `notated_x0.5` / `notated_x1.5` / `notated_x3` (snapped
to a clean half/double/triple-time factor), or `dtw_fallback` (no clean factor
fit within tolerance, so the raw DTW-derived ratio was used, clamped to
`[TEMPO_MIN, TEMPO_MAX]`). `mode` is `"global"` (one constant tempo explains the
song) or `"local"` (a residual elastic warp was kept); `warp` is a 2-point line
**only** in `global` mode with no internal gaps — any internal gap forces
`local` mode (with a gap-holding segment in the warp) even when the tempo itself
is otherwise constant. Consumers still interpolate `warp` either way; the tempo
fields are informational.

**Gap / coverage fields:** `gaps` is the list of real-audio dead regions detected
by RMS energy on the **original, untrimmed** real timeline — each entry is
`{real_start_s, real_end_s, kind}` with `kind` one of `lead` (before the first
real content), `trailing` (after the last), or `internal` (mid-recording).
`gaps` is `[]` for `no_gp`/`no_audio`. Consumers (e.g. the Phase-0 export) should
drop any window that overlaps a gap rather than treat it as aligned audio.
`coverage` is the fraction of the symbolic timeline that warps to real content
outside every gap — a low value flags a tab that only matched part of the
recording even when the local fit otherwise looks fine; `coverage` is `null` for
`no_gp`/`no_audio`.

**Commit ordering:** `align.json` is the sole output file and is renamed in last.
The temp + `os.replace` write guarantees the file is never observed partially
written.

**Re-scrape / self-healing:** a scraper re-scrape calls `rmtree(<tab_id>)` before
re-committing, which wipes `align.json` too. The tab will be re-aligned on the next
`align run`. No extra state is required.

**Inspection artifacts (developer-facing, not part of the contract):**
`align inspect <tab_id>` additionally writes `align_overlay.wav` and
`align_plot.png` into the tab directory for manual verification. These files are not
consumed by any other project and can be deleted without breaking any pipeline.

**Other projects:** `scraper-py`, `decoder-rs`, and `enricher-py` ignore
`align.json` entirely — it plays no role in their pipelines.
