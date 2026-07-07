# Notes

Deferred issues we've decided to deal with later. Remove entries when resolved.

## `metadata.json` optional fields vs Phase 0's manifest/split assumptions

**Found 2026-07-06 (pre-implementation gap review). Deliberately deferred — address when implementing Phase 0 (`audit-py`), not before.**

Phase 0's plan assumes `metadata.json` "has canonical `song_id`, `artist_id`,
`tonality`, `tuning`". In reality (see `docs/output-contract.md`), those live
in an **optional** `song` sub-object, and each sub-field is **omitted when
blank** — e.g. `output/pearl-jam/superblood-wolfmoon-official-3081698/metadata.json`
has no `song.tuning`. Consequences to handle in `audit-py`:

- The split assignment hashes `song.artist_id`. A tab with a missing `song`
  block (or missing `artist_id`) has no split. Define a fallback — likely
  hashing the artist slug from `tab_id` — and flag such records in the
  manifest rather than crashing or silently skipping.
- All `song.*` reads in the manifest extractor must treat every field as
  optional (`tonality`, `tuning`, `album_id` included).
- Related shape facts for the extractor: in `audio.json`, `confidence`/`reason`
  live under `selection.*`, the topic-channel flag is `source.channel_is_topic`,
  and `no_match` records carry **no** confidence field.

## Environment

All ML-side work (Phases 0–9: audit, alignment, rendering, training, serving)
runs on a single **Windows machine**. Plans referencing "the training box",
the 16 GB GPU, and the Windows NAM-VST3 smoke test all mean this one machine —
no cross-machine data movement to design for.
