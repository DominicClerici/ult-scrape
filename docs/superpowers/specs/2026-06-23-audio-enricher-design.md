# Audio Enrichment Service (`enricher-py`) — design

> Brainstorm spec. Status: **approved, pending implementation plan.** This captures
> the *why* behind locked decisions; the as-built docs will live under
> `docs/enricher-py/` once implemented.

## 1. Purpose & scope

We are building a dataset to train an audio→tab model (input: an mp3, output: a
guitar tab). The scraping half is done — `scraper-py` captures encrypted `.xtz`
tabs and `decoder-rs` decrypts them to Guitar Pro `.gp`/`.gpif`. The missing
ingredient is the **source audio** each tab was transcribed from. UG does not
provide it.

This component, **`enricher-py`**, is a separate enrichment step that, for each
scraped tab, finds and downloads the **highest-quality full-length audio we can
find** and stores it beside the tab.

**Scope for this iteration is "A": find + download a candidate audio file per
tab.** Verifying the audio is the *correct* recording, and time-aligning audio to
the tab, are explicitly **later, separate steps** (see Non-goals and §15).

## 2. Context & investigation findings

UG was checked first (logged-in, four diverse Pro tabs) to see whether it already
exposes the recording or a link to it. **It does not, reliably:**

- Each tab page hydrates `window.UGAPP.store.page.data` with clean identity
  metadata (`tab.artist_name`, `artist_id`, `song_name`, `song_id`,
  `recording.album_id`, `tab_view.meta.tonality`, `.tuning`, cover image URLs).
- `tab.recording` *has* the right-shaped fields (`video_urls`,
  `recording_artists`, `performance`) but they were **null/empty on every tab**.
- `tab_view.song_image` is an 11-char YouTube ID — but it is a community **"video
  lesson"** (e.g. *"…How to Play On Guitar - Guitar Lesson Tutorial part1"*),
  frequently wrong-content, and sometimes a **dead/private video**. It is **not**
  the master recording. Do not use it as a source. (Documented so future work
  doesn't fall for it.)
- No ISRC / Spotify / Apple / Deezer / streaming identifiers anywhere in the
  store. No `schema_org`.

**Source decision — YouTube, Topic-first (`yt-dlp`).** "Full audio" eliminates
streaming APIs (Spotify/Apple/Deezer expose only 30-second previews — useful for
future *verification*, not as the audio itself) and indie-only sources
(Bandcamp/SoundCloud — poor coverage for UG's mainstream catalog). YouTube is the
only free source with both near-universal coverage and full-length audio. Within
YouTube, the **`<Artist> - Topic`** channels are auto-generated label-delivered
"Art Tracks" (Content ID) — i.e. the actual studio master — so a *Topic-first*
selection often yields the real recording, not a random upload. `yt-dlp` does
search + format selection + download with no API key or quota.

## 3. Repo-philosophy fit

`ult-scrape` is **decoupled projects that share no code, communicating only
through the `output/` filesystem tree** (the [output contract](../../output-contract.md)).
`enricher-py` is a **third such project**, a sibling to `scraper-py` and
`decoder-rs`:

- It **shares no code** with the other two.
- Its **only input** is the `output/` tree; its only output is new files in that
  tree.
- Its input gate is **`metadata.json`** — the same universal "this tab is ready"
  marker the decoder uses. It never depends on the decoder having run.

It is written in **Python** (yt-dlp is Python) and has its own venv and its own
SQLite database. It follows `scraper-py`'s internal conventions: a single
SQL-owning `repo.py` module, an injectable clock for deterministic tests, and
network/tooling hidden behind a Protocol with a fake for unit tests.

## 4. Non-goals (this iteration)

- **No match verification.** We pick the best candidate by heuristic and record
  our confidence; we do not confirm it is the exact recording. (Scope B.)
- **No time-alignment.** (Scope C.)
- **No song-level dedup.** Audio is stored **per tab directory**; idempotency is
  "does this tab dir already have an audio file." (Different tab versions of the
  same song each get their own download.)
- **No streaming-service integration.**
- **No re-encoding.** We keep the native best-audio stream as-is.
- **No `scraper-py` changes.** The clean-metadata capture is deferred and only
  *documented* now (see §5 and the CAPTURE_NOTE deliverable). The enricher works
  off the slug today.
- **No HTTP API.** CLI only (jobs come from the filesystem, not external callers).

## 5. Identity → search query

The enricher's input is `metadata.json`. Its primary identity source is the slug
already in `tab_id` / `route`:

```
metallica/nothing-else-matters-guitar-pro-225441
eagles/hotel-california-guitar-pro-382996
```

A **normalizer** turns the route into a query:

1. Split on the first `/` into `artist` and `song` segments.
2. From the song segment, strip the trailing UG type/id cruft:
   `-guitar-pro-<digits>`, `-official`, `-ver<digits>`, `-tab(s)?`, and any
   trailing numeric id.
3. Replace hyphens with spaces, collapse whitespace.
4. Query = `"<artist> <song>"` (e.g. `"eagles hotel california"`).

This is deterministic and unit-tested with golden cases.

**Deferred enhancement (documented, not built): `CAPTURE_NOTE.md`.** We proved UG
exposes clean fields for free in `window.UGAPP.store.page.data`. A future small
`scraper-py` change can write a `song` block into `metadata.json`
(`artist_name`, `artist_id`, `song_name`, `song_id`, `album_id`, `tonality`,
`tuning`). The enricher will then **prefer that block when present and fall back
to slug-parsing when absent** — no breaking change. We ship a
`enricher-py/CAPTURE_NOTE.md` describing exactly which fields to capture, where
they live in the store, the proposed `metadata.json` shape, and the warning that
`song_image` is a video-lesson pointer (not the recording). The actual scraper
change is out of scope for this iteration.

## 6. Output-contract extension (per tab directory)

For each enriched tab, two new artifacts are written into the **existing** tab
directory, atomically (temp + rename):

```
output/<tab_id>/
  <existing: *.xtz, metadata.json, *.gp, *.gpif>
  audio.opus | audio.m4a     # native best-audio, no re-encode; one per tab/song
  audio.json                 # provenance + status sidecar
```

- **One audio file per tab dir** (a tab is one song). Extension reflects the
  native codec (`.opus` for Opus/WebM, `.m4a` for AAC).
- **Commit ordering:** download to a temp path → `ffprobe` + sha256 the temp file
  → write `audio.json` → **rename the audio file in last as the commit marker**.
  Guarantee: whenever the audio file exists, `audio.json` exists too.
- **`audio.json` schema** (`indent=2, sort_keys=True`):

```json
{
  "status": "ok",
  "query": "eagles hotel california",
  "source": {
    "platform": "youtube",
    "video_id": "…",
    "url": "https://www.youtube.com/watch?v=…",
    "channel": "Eagles - Topic",
    "channel_is_topic": true,
    "title": "Hotel California",
    "duration_s": 391,
    "view_count": 12345678
  },
  "selection": {
    "reason": "topic_channel_exact_artist",
    "confidence": 0.95,
    "candidates_considered": 5,
    "runners_up": [ { "video_id": "…", "title": "…", "score": 0.61 } ]
  },
  "audio_file": "audio.opus",
  "format": {
    "codec": "opus",
    "bitrate_kbps": 160,
    "sample_rate": 48000,
    "channels": 2,
    "byte_size": 7654321,
    "sha256": "…"
  },
  "enriched_at": "2026-06-23T12:00:00",
  "enricher_version": "0.1.0",
  "yt_dlp_version": "…"
}
```

  For `status: "no_match"` or `"failed"`: `audio_file`, `source`, and `format`
  are `null`; `selection`/`last_error` explain why. **No audio file is written.**

- **Idempotency / "done" gate** (your rule): a tab dir is done if an
  `audio.<ext>` file exists. A terminal `audio.json` with `status: "no_match"`
  (no audio file) is the marker that stops us re-hammering YouTube for permanent
  misses; `--retry-failed` overrides it.
- **Source of truth (DB vs filesystem) — explicit to avoid ambiguity:** the
  `enricher.db` job row is the source of truth for the job *lifecycle*
  (attempts, backoff, transient `failed`). The **filesystem is the source of
  truth for completion** and makes the system robust to DB loss: an `audio.<ext>`
  file means "done" and a `no_match` `audio.json` means "permanent miss," both
  independent of the DB. So if `enricher.db` is deleted, a fresh `scan`
  reconstructs correct state from the tree (done where audio exists, skip where
  `no_match`) without re-downloading. **`failed` is DB-only and retryable** (we do
  not write a `failed` sidecar) — a transient exhaustion becomes `pending` again
  on a fresh DB or with `--retry-failed`, whereas `no_match` is a deliberate
  "searched, nothing fit" verdict persisted to the tree.
- **Interaction with the decoder:** none. The decoder gates on `metadata.json`
  and walks `.xtz`; extra `audio.*` files don't affect it.
- **Interaction with re-scrape:** a re-scrape does `rmtree(<tab_id>)` then
  re-commits, wiping `audio.*` along with everything else, so the tab is naturally
  re-enriched on the next run — self-healing, exactly like the decoder.

## 7. Queue & worker (durable, pausable, recoverable)

This is the robustness the queue+worker model buys us. All state lives in
`enricher.db`; audio is committed atomically. **A terminated run resumes cleanly
on restart.**

### Database

A single SQL-owning `repo.py` (repo convention) and an injectable clock
(`repo.clock["t"]`) for deterministic backoff/recovery tests. One table:

```
jobs(
  tab_id          TEXT PRIMARY KEY,
  status          TEXT NOT NULL,   -- pending | working | done | no_match | failed
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,            -- ISO ts; backoff gate for pending
  claimed_at      TEXT,            -- set on claim; basis for stale reclaim
  worker_id       TEXT,
  query           TEXT,
  chosen_video_id TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
)
```

### Discovery / enqueue (`scan`)

Walk `output/`. For each dir containing `metadata.json`:
- if `audio.<ext>` exists → ensure job is `done`, skip;
- else if `audio.json` exists with `no_match` and not `--retry-failed` → skip
  (permanent miss recorded on the tree);
- else consult the DB job: if it is terminal (`done`/`failed`) and not
  `--retry-failed` → skip; `--retry-failed` resets `no_match`/`failed` →
  `pending`;
- else upsert a `pending` job (`next_attempt_at = now`).

### Worker pool

Unlike the scraper's single browser worker, downloads are independent, so a
**bounded concurrent pool** (default ~2; configurable) drains the queue, with:
- **Polite global rate-limiting** (minimum interval between YouTube network ops;
  honor `yt-dlp` `--sleep-requests`/`--retries`).
- **Exponential backoff** on transient failures (esp. HTTP 429):
  `next_attempt_at = now + base * 2^attempts`, capped.

### State transitions

- `claim`: `pending` with `next_attempt_at <= now` → `working` (set `claimed_at`,
  `worker_id`).
- success (audio committed) → `done`.
- searched but nothing clears the confidence threshold → `no_match` (terminal;
  write `audio.json` `no_match`; only `--retry-failed` resets it to `pending`).
- transient error → `attempts += 1`; if `attempts < MAX_ATTEMPTS` → back to
  `pending` with backoff; else → `failed` (terminal until `--retry-failed`).

### Pause (CLI interaction)

- `SIGINT`/`SIGTERM` sets a **draining** flag: workers stop claiming new jobs,
  finish or cleanly abort in-flight downloads (delete temp files), release their
  claimed jobs back to `pending`, flush, and exit `0`.
- A **second `SIGINT`** forces immediate exit; any still-`working` job is left for
  startup reclaim.

### Crash / restart recovery

- On `run` startup, **reclaim** stale jobs: reset all `working` → `pending`
  (single-machine CLI; a lockfile/PID guard prevents two concurrent `run`s, so
  any `working` row is by definition an interrupted prior run). `claimed_at`
  cleared.
- **Sweep orphaned temp files** (partial downloads) in tab dirs / temp area.
  Because the audio file is renamed in only on full success, a partial never
  satisfies the "done" gate.
- Re-running is therefore always safe and simply continues where it left off.

## 8. Selection heuristic (quality crux for scope A)

Even without verification, *which* candidate we pick determines dataset quality.
Topic-first, over the top-`N` (`ytsearchN`, default 5) results:

1. **Prefer uploader matching `^<artist> - Topic$`** (case-insensitive) → label
   Art Track = studio master. Highest score.
2. else a **verified/official artist channel** whose title matches the song.
3. else best **title match**, after **rejecting** titles containing:
   `lesson`, `tutorial`, `how to play`, `cover`, `karaoke`, `backing track`,
   `instrumental` (configurable), `live`, `remix`, `8-bit`, `reaction`; reject
   **Shorts** and anything **< `MIN_DURATION_S`** (default 60 s).
4. **Tie-break** on view count.

Title similarity is normalized-token overlap of `artist`+`song` against the
candidate title/channel. Below a configurable confidence threshold → `no_match`.
Record `reason`, `confidence`, and `runners_up` in `audio.json` so the future
verification step has a head start.

**Duration caveat:** GP arrangement length ≠ recording length, so duration is only
a loose floor here, **not** a hard match. True duration/fingerprint matching is
the deferred scope-B verification step.

## 9. Download & probe

- `yt-dlp` with `-f bestaudio` (configurable), **no re-encode** — keep the native
  container/codec.
- Download to a temp path; on success `ffprobe` for codec/bitrate/sample
  rate/channels/duration, compute sha256, then commit per §6 ordering.
- All network/tooling sits behind a `Searcher` (`search(query) -> [Candidate]`)
  and `Downloader` (`download(video_id, dest) -> DownloadResult`) Protocol; real
  implementations wrap `yt-dlp`, fakes back the unit tests.

## 10. CLI surface

```
enricher scan                              # walk output/, enqueue pending tabs
enricher run [--jobs N] [--limit N]        # drain the queue (Ctrl-C = graceful pause)
            [--retry-failed] [--quiet]
enricher status                            # counts by state
```

- Global: `--output-dir <path>`, `--db <path>`.
- `run` may accept `--scan` to scan then drain in one invocation.
- Single-instance lockfile guards `run`.

## 11. Configuration (`.env`, like scraper-py)

`OUTPUT_DIR`, `ENRICHER_DB`, `MAX_CONCURRENCY` (default 2), `YTDLP_FORMAT`
(default `bestaudio`), `SEARCH_RESULTS` (default 5), `MIN_DURATION_S` (default
60), `MAX_ATTEMPTS` (default 5), `BACKOFF_BASE_S`, `RATE_LIMIT_MIN_INTERVAL_S`,
`REJECT_KEYWORDS`, `CONFIDENCE_THRESHOLD`. Shipped `.env.example`.

## 12. Testing strategy

Deterministic and network-free by default, mirroring the repo:
- `Searcher`/`Downloader` fakes (mirror the fake `BrowserSession`).
- Injectable clock for backoff and `working`→`pending` recovery tests.
- **Golden tests** for the slug→query normalizer and the selection heuristic
  (candidate fixtures → asserted pick + reason).
- Queue/repo tests: claim, transitions, backoff gating, terminal markers,
  startup reclaim, `--retry-failed`.
- Atomic-commit tests: simulate interruption between temp-write and rename;
  assert no partial passes the "done" gate.
- Live `yt-dlp` behind an `integration` marker (excluded by default).

## 13. Documentation updates required (part of implementation)

- New `docs/enricher-py/overview.md` (+ pages for the queue/worker and the
  selection heuristic as warranted).
- `OVERVIEW.md` map: add the new project + doc pages.
- `docs/architecture.md`: now **three** projects sharing the one filesystem seam.
- `docs/output-contract.md`: add the `audio.<ext>` + `audio.json` artifacts, the
  commit ordering, and the re-scrape/idempotency interaction.
- `CLAUDE.md`: add `enricher-py` rows to the read-map and doc-currency tables, and
  its commands.
- `enricher-py/CAPTURE_NOTE.md` (the deferred-capture note, §5/§14).

## 14. `CAPTURE_NOTE.md` (content to ship)

`enricher-py/CAPTURE_NOTE.md` will document the deferred `scraper-py` enhancement:

- **What:** capture clean song identity into `metadata.json` so the enricher gets
  a better query and a stable dedup key, instead of parsing the slug.
- **Where it lives:** `window.UGAPP.store.page.data` on each tab page (the page
  the scraper already loads): `tab.artist_name`, `tab.artist_id`,
  `tab.song_name`, `tab.song_id`, `tab.recording.album_id`,
  `tab_view.meta.tonality`, `tab_view.meta.tuning`.
- **Proposed shape:** a `song` block added to `metadata.json` (additive; the
  decoder ignores it).
- **Consumption:** enricher prefers `song` when present, falls back to slug.
- **Warning:** `tab_view.song_image` is a YouTube **video-lesson** id (often a
  tutorial, sometimes dead) — **not** the master recording; do not source from
  it. `tab.recording.video_urls`/`recording_artists` are empty on tab pages.

## 15. Future iterations (out of scope, recorded for continuity)

- **Scope B — verification:** confirm the candidate is the correct recording
  (e.g. fingerprint a streaming 30-s preview, by ISRC, against the download;
  duration/loudness sanity; reject wrong versions). The `selection` block and
  `runners_up` we record now feed this.
- **Scope C — alignment:** time-align audio to the tab timeline to produce
  training-ready pairs.
- **Song-level dedup** to avoid re-downloading the same recording across tab
  versions.
