# The Output Contract (the seam between scraper and decoder)

> Part of the [documentation map](../OVERVIEW.md). This is the **single
> interface** between [`scraper-py`](./scraper-py/overview.md) (the producer) and
> [`decoder-rs`](./decoder-rs/overview.md) (the consumer). The two projects share
> no code — only this filesystem layout. Treat it as **frozen**: a change here
> must be made on both sides at once.

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
  <name>.gp                         # (added later by decoder-rs) decrypted Guitar Pro ZIP
  <name>.gpif                       # (added later by decoder-rs) extracted Content/score.gpif XML
```

- `<tab_id>` is the canonical route, e.g. `eagles/hotel-california-official-1910943`.
  Because it contains a `/`, each tab directory is nested one level under
  `OUTPUT_DIR` (`OUTPUT_DIR/eagles/hotel-california-official-1910943/`).
- A tab may yield **more than one** `.xtz` artifact; each gets its own `.gp`/`.gpif`.

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
  ]
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

## `.xtz` file format (summary)

The `.xtz` bytes are stored **exactly as downloaded** — no transformation. The
binary format (20-byte header + ChaCha8 payload) is owned by the decoder and
documented in [XTZ format & cipher](./decoder-rs/xtz-format-and-cipher.md).

## Re-scrape & idempotency

On a re-scrape, the scraper does `rmtree(<tab_id>)` then re-commits the directory.
This deletes any `.gp`/`.gpif` the decoder previously wrote, which is exactly the
trigger that makes the decoder re-decode that tab on its next run — the system is
self-healing with no extra state. See
[decoder idempotency](./decoder-rs/pipeline.md#idempotency).

## Output files written by the decoder

For each pending `<stem>.xtz`, the decoder writes (atomically, temp + rename):

- `<stem>.gp` — the decrypted Guitar Pro ZIP (byte-for-byte the decrypted payload).
- `<stem>.gpif` — `Content/score.gpif` extracted from that ZIP, for convenience.

The `.gpif` is written **before** the `.gp`, because the `.gp`'s existence is the
decoder's idempotency marker; this ordering guarantees that whenever the marker
exists, the `.gpif` does too.
