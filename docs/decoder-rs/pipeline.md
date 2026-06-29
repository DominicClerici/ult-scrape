# decoder-rs — Pipeline (discover → decode → write)

> Part of the [documentation map](../../OVERVIEW.md) ·
> [decoder overview](./overview.md). Sources: `src/discover.rs`, `src/output.rs`,
> `src/gpx.rs`, `src/lib.rs`, `src/main.rs`.

The decoder is a one-shot batch: find pending `.xtz` files, decrypt each in
parallel, validate, and write outputs — with every file fully isolated so one bad
file never aborts the run.

```
discover(root, force) -> [pending .xtz paths]
  for each (rayon parallel):
    bytes      = read(path)                   # ENOENT (dir vanished mid-run) -> skip silently
    container  = decrypt_xtz(bytes)           # bad magic/short -> count failed
    decoded    = decode_container(container)  # classify PK→.gp / BCFZ→.gpx, extract score.gpif; unknown -> failed
    write_outputs(path, ext, container, gpif) # atomic: .gpif then .gp/.gpx
  print: decoded N | skipped N | failed N
```

## Discovery (`src/discover.rs`)

`discover(root, force) -> Discovery { pending: Vec<PathBuf>, already_decoded: usize }`.

A single `walkdir` pass collects:

- every directory that **directly contains `metadata.json`** → marks it *eligible*
  (this is the [commit-marker gate](../output-contract.md#the-commit-marker-metadatajson));
- every `*.xtz` file path.

Then, for each `.xtz` whose parent is eligible:

- if not `--force` and a sibling container exists — `<stem>.gp` **or**
  `<stem>.gpx` — → counted as `already_decoded` (skipped);
- otherwise → added to `pending`.

`.xtz` files in directories **without** `metadata.json` are ignored entirely
(the scraper hasn't committed them yet). Discovery is pure filesystem read — no
decryption, no writes.

## Decode + validate + write (`src/output.rs`, `decode_one` in `src/lib.rs`)

`decode_one(path)` chains:

1. `std::fs::read(path)` — raw `.xtz` bytes.
2. `cipher::decrypt_xtz(&data)` — see [cipher](./xtz-format-and-cipher.md).
3. `output::decode_container(&container)` — **classification + validation happens
   here**: switches on the decrypted payload's magic —
   - `PK\x03\x04` → GP7/8: extension `gp`, `score.gpif` from the ZIP's
     `Content/score.gpif` entry;
   - `BCFZ` → GP6: extension `gpx`, `score.gpif` from the BCFS filesystem (see
     [GPX/BCFZ format](./gpx-bcfz-format.md));
   - anything else → error.

   Returns `Decoded { extension, gpif }`.
4. `output::write_outputs(path, ext, &container, &gpif)` — writes both files
   **atomically** (temp file `.<name>.tmp` in the same dir, then rename): the
   container as `<stem>.<ext>` and the `<stem>.gpif`.

**Write ordering matters:** `.gpif` is written **before** the container
(`.gp`/`.gpx`). Because the container's existence is the idempotency marker, this
guarantees that whenever the marker exists the `.gpif` does too; a crash between
the two writes just causes a re-decode next run. Nothing is written unless
classification + validation pass.

## Orchestration & parallelism (`src/lib.rs`)

`run(&Options { root, force, jobs, quiet }) -> Summary { decoded, skipped, failed }`:

- Builds a `rayon` thread pool of `jobs` threads and `par_iter()`s the pending
  list — decoding is CPU-bound and embarrassingly parallel. Parallel jobs write
  distinct stems, so temp-file names never collide.
- Counts results with atomics. `skipped` comes from discovery's `already_decoded`.
- `--quiet` suppresses per-file `decoded`/`skip` lines (failures are always printed).

## CLI (`src/main.rs`)

```
decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

- `OUTPUT_DIR` resolution: positional arg → `$OUTPUT_DIR` env → repo-root
  `output/` (found by walking up from the current dir for the `scraper-py/` +
  `decoder-rs/` pair — robust to the launch dir) → `./output` outside the repo.
- `--jobs` defaults to `available_parallelism()` (CPU count), floored at 1.
- Always prints `decoded N | skipped N | failed N` and **exits 0**, even with
  per-file failures. (A `--strict` non-zero-on-failure flag could be added later
  for CI gating; not implemented.)

## Idempotency

A `<stem>.xtz` is **done** iff a sibling container exists — `<stem>.gp` (GP7/8)
**or** `<stem>.gpx` (GP6). So:

- Re-runs skip done files (counted as `skipped`); `--force` re-decodes regardless.
- A scraper **re-scrape wipes the whole tab dir** (including the `.gp`/`.gpif` the
  decoder wrote), which naturally makes that tab pending again — the scheme is
  self-healing with **no extra state files**. See
  [the output contract](../output-contract.md#re-scrape--idempotency).

## Error handling

Per-file `Result`, fully isolated — one bad file never aborts the batch:

| Condition | Behavior |
|---|---|
| Bad magic / input < 21 bytes | warn (`FAILED ...`), count `failed`, continue |
| Decrypts to neither `PK` nor `BCFZ` magic | warn, count `failed`, continue (covers wrong/edge-case key, or an unsupported GP3/4/5 payload) |
| GP7 ZIP without `Content/score.gpif`, or GP6 BCFZ that is truncated / has no `score.gpif` | warn, count `failed`, continue |
| File/dir vanished mid-run (`ENOENT`) | skip **silently** (scraper re-scrape race) — detected via `is_missing()` |
| Write error (disk full, perms) | warn, count `failed`, continue |

The `ENOENT`-is-not-a-failure rule is what makes it safe to run the decoder
**concurrently** with the scraper: if the scraper rmtree's a directory between
discovery and read, the decoder just moves on.

## Out of scope

- Decoding `.gp`/GPIF into other formats (MIDI/JSON/ASCII tab).
- Watch/daemon mode (one-shot only; re-invoke or cron).
- Parsing/validating `metadata.json` contents or sha256 (existence gate only).
- Any change to the scraper or its output contract.
