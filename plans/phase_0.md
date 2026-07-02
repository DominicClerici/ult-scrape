# Phase 0 — Corpus audit & hygiene

> Expanded from [the roadmap](../docs/roadmap.md#phase-0--corpus-audit--hygiene)
> in the 2026-07-01 planning session. Decisions here are **binding inputs** to
> later phases.

## Goal & scope

Know exactly what the corpus contains before anything is built on it, and
encode that knowledge in a machine-readable **manifest** that every later phase
consumes. Concretely:

- Parse every `.gpif` into a structural inventory: tracks, instrument types,
  tunings, capo, string counts, technique frequencies, tempo-map complexity,
  song durations (with repeats expanded).
- Verify each tab↔audio pairing and grade it (`ok` / `suspect` / `bad`) —
  wrong-audio pairs are training poison and must be flagged now.
- Enforce dedup invariants and flag cross-artist covers.
- Fix the train/val/test split **now**, by artist, deterministically, before
  model work looks at any test material.
- Produce a human-readable corpus report to inform Phase 1 (diversity
  priorities) and Phase 3 (vocabulary decisions).

**Out of scope:** note-level score modeling (ties, per-note durations, grace
notes — Phase 2a); any audio synthesis or rendering (Phase 4); tab↔audio
*alignment* (Phase 2b); fixing or re-enriching bad pairs (Phase 1 ops);
dataset snapshot/versioning mechanics (Phase 1).

## Inputs / outputs

**Consumes** (read-only): the frozen [output contract](../docs/output-contract.md)
— `output/<artist>/<song>/` with `metadata.json` (has canonical `song_id`,
`artist_id`, `tonality`, `tuning`), decoded `.gpif`, `audio.<ext>` +
`audio.json`. Phase 0 never modifies `output/`.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Manifest | `manifest/manifest.jsonl` | **Derived** — deterministically regenerable; one JSON record per tab, sorted by `tab_id` |
| Overrides | `manifest/overrides.json` | **Hand-maintained input** — split pins, cover-pair links, manual verdict overrides; merged in at generation time |
| Corpus report | `manifest/report.md` | Derived — distributions, suspect lists, technique census |
| Shared score library | `score-py/` (package `gpscore`) | Code — the one deliberate shared dependency among ML-side projects |
| Audit tool | `audit-py/` | Code — the CLI that generates the above |

Later-phase consumers: Phase 2 selects alignment candidates from the manifest
and **backfills** its alignment-confidence fields; Phases 3/4 select training
material by flags + split; Phase 5 takes the test split as its fixed eval set.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Manifest form | Single JSONL file at `manifest/manifest.jsonl`, fully regenerable; human decisions live in separate `overrides.json` | Per-tab sidecar `audit.json` (walks 10k dirs, aggregate needs a build step anyway); SQLite (binary, not diffable, awkward as an immutable snapshot artifact) | JSONL is the ML-corpus lingua franca: diffable, greppable, trivial at 10k rows, snapshot = one file + hash. Derived vs. hand-maintained data kept strictly separate so regeneration never loses human decisions. |
| GPIF parsing | Start the shared score-model library **now**, scoped to structure: tracks/tunings/capo, tempo map, master bars with repeat/jump expansion to a linear timeline, duration-in-seconds, per-track note & technique **counts** | Throwaway XML stats extractor (can't compute repeat-expanded duration → cripples the duration check; throwaways become load-bearing); full Phase 2a pull-forward (locks note-level design before aligner/tokenizer requirements exist) | The duration-mismatch check *requires* tempo map + repeat expansion — already a big fraction of a real parser, and no mature Python `.gpif` parser exists (PyGuitarPro reads GP3–5 binary only; alphaTab is TS/C#). Building it once in the shared lib avoids paying twice. Phase 2a owns the note-level design and may refactor internals; the audit only uses aggregate outputs, so the public surface stays small. |
| Audio verification depth | Metadata heuristics + repeat-expanded duration mismatch + **12-rotation pitch-class vs. chroma comparison** (no synthesis) | Duration-only (misses same-length covers and pitch-shifted remasters — the worst poison); synthetic-render fit (pulls forward Phase 4 synthesis *and* the note-level model) | The rotation check catches wrong-song (no correlation peak) and wrong-key (peak at rotation ≠ 0, which also reports the offset) using only note pitches (string+fret+tuning — no timing needed) and cheap librosa chroma. Strongest check available without contradicting the parsing decision. |
| Verification output | Graded flags in the manifest: `ok` / `suspect:<reason>` / `bad:<reason>`; nothing deleted | Hard filtering / deleting bad pairs | Later phases decide what to consume per use case; evidence is preserved; re-enrichment (Phase 1) can target `bad` pairs. |
| Dedup | Uniqueness **invariant** at manifest generation (`song_id` and normalized `(artist, song_name)`), not a workstream; violations flagged with fixed winner policy (best audio flag → most tracks → latest `scraped_at`) and `duplicate_of` on losers; fuzzy same-title matching **across** artists flags `possible_cover` | Fuzzy dedup clustering machinery now | Measured on the real corpus 2026-07-01: 509 tabs, 509 unique `song_id`s, zero duplicate name pairs — official tabs are one-per-song. Machinery is speculative until Phase 1 scaling produces actual collisions. Covers matter for **split leakage**, hence the cross-artist flag. |
| Split assignment | Deterministic hash of UG `artist_id` (stable canonical ID) → bucket 0–99; `sha1`, **not** Python `hash()` (salted per-process) | Hand-picked frozen artist list (one-shot at 193 artists, new artists excluded from test, selection bias) | No human selects test artists; every future Phase 1 artist is auto-classified; the test set grows with the corpus. Overrides pin cover-pairs to one side and contaminated artists to train. |
| Split fractions | train/val/test = **85 / 5 / 10** (buckets 0–84 / 85–89 / 90–99) | 90/5/5 (test too thin today: ~10 artists); 80/10/10 (permanently gives up 5% training data) | Test weighted over val: final eval on real audio is the scarce resource, while val can lean on synthetic data later. ~20 test artists / ~50 songs today; ~1k songs at 10k scale. |
| Project layout | Two projects: `score-py/` (pure library, no I/O opinions, no knowledge of `output/` layout) + `audit-py/` (CLI: walks `output/`, writes `manifest/`) | Single `corpus-py/` containing both (Phase 2a would immediately extract the library — guaranteed churn) | Matches the repo's decoupled-projects pattern and the roadmap's designation of the score-model library as the one deliberate shared dependency. |
| Tooling | Python ≥ 3.13 (matches sibling projects); stdlib `xml.etree` for gpif (lxml only if profiling demands); `librosa` + `ffmpeg` for chroma; deterministic fixture-based tests | — | Repo conventions. |

## Design

### `score-py/` — shared structural score model

Installable package `gpscore`. Public surface (Phase 0 scope):

- `parse_gpif(path | bytes) -> Score`
- `Score`: `tracks: list[Track]`, `tempo_map`, `master_bars`,
  `expand_repeats() -> LinearTimeline`, `duration_seconds() -> float`,
  `time_signatures`, warnings list (unparseable constructs are recorded, never
  silently dropped).
- `Track`: `name`, `instrument_ref`, `kind` (guitar / bass / drums / vocals /
  other — classified from GPIF instrument refs like `e-gtr6`, `e-bass4`,
  `drmkt`, `s-gtr6`), `string_count`, `tuning` (MIDI numbers), `capo`,
  `note_count`, `technique_counts: dict[str, int]` (bend, slide, hammer/pull,
  palm-mute, harmonic, vibrato, tap, let-ring, trill, whammy, dead-note …),
  `pitch_class_histogram` (from string+fret+tuning; no timing).

Repeat/jump expansion covers plain repeats, alternate endings, and the common
D.C./D.S./Coda directives; anything unexpandable raises a warning flag on the
score rather than guessing. Both GP6-era (`.gpx`-extracted) and GP7/8-era
(`.gp`-extracted) `.gpif` dialects must parse — the corpus contains both.

**Contract with Phase 2a:** the *public API above* is stable enough for
audit-py; internals (and the note-level model to come) are Phase 2a's to
design and refactor. `gpscore` is pre-1.0 until Phase 2a signs off.

### `audit-py/` — the audit CLI

Decoupled CLI in the style of `enricher-py`. Subcommands:

- `audit run [--output-dir DIR] [--manifest-dir DIR] [--jobs N]` — full pass:
  parse, check, dedup, split, write `manifest.jsonl` + `report.md`.
- `audit report` — regenerate `report.md` from an existing manifest.

Pipeline per tab: read `metadata.json` + `audio.json` → `gpscore.parse_gpif`
→ audio checks → flags → record. Chroma extraction (librosa on decoded audio
via ffmpeg) is the slow step; parallelized with `--jobs`.

**Determinism:** records sorted by `tab_id`; no timestamps inside records
(generation metadata lives in a single header record); two runs over the same
`output/` + same code produce byte-identical files. This is what makes
"manifest hash = dataset snapshot ID" work in Phase 1.

### Manifest record schema (v1)

One JSON object per line; first line is a header record
(`{"schema_version": 1, "generator": "audit-py x.y.z", ...}`). Per-tab record:

```jsonc
{
  "tab_id": "acdc/demon-fire-official-3430592",
  "song": { "song_id": 4662719, "artist_id": 2025, "artist_name": "AC/DC",
             "song_name": "Demon Fire", "tonality": "E" },
  "files": { "gpif": "...", "gp": "...", "audio": "audio.webm" },   // present files
  "score": {
    "parse_ok": true, "warnings": [],
    "bar_count": 123, "expanded_bar_count": 156,
    "duration_s": 217.4, "tempo_bpm_min": 116, "tempo_bpm_max": 120,
    "tempo_change_count": 2, "time_signatures": ["4/4"],
    "tracks": [ { "name": "...", "kind": "guitar", "string_count": 6,
                  "tuning": [40,45,50,55,59,64], "capo": 0,
                  "note_count": 1843, "technique_counts": {"bend": 31, ...} } ],
    "guitar_track_count": 4
  },
  "audio": {
    "present": true, "duration_s": 217, "codec": "opus",
    "source": { "video_id": "...", "channel_is_topic": false, "title": "..." },
    "enricher": { "confidence": 1.0, "reason": "official_channel" }
  },
  "checks": {
    "duration_delta_s": 0.4, "duration_ratio": 1.002,
    "title_flags": [],                       // e.g. ["live", "cover"]
    "chroma_rotation": 0, "chroma_corr": 0.91,
    "alignment": null                        // reserved: Phase 2 backfills
  },
  "verdict": { "grade": "ok", "reasons": [] },   // or suspect:/bad: reasons
  "dup": { "duplicate_of": null, "possible_cover_of": [] },
  "split": "train"                                // train | val | test
}
```

### Check → verdict logic

- `bad`: no audio; score parse failure; duration ratio outside a wide band
  (e.g. beyond ±15% after repeat expansion); chroma correlation weak at every
  rotation (wrong song).
- `suspect`: duration in the grey band; chroma peak at rotation ≠ 0
  (transposed source — usable later *if* Phase 2 handles the offset, so it is
  not `bad`); title red-flag keywords ("live", "cover", "remix", "sped up",
  "8D"); enricher low-confidence fuzzy match; unexpandable repeats.
- Exact numeric thresholds are tuned during implementation against a
  hand-labeled sample of known-good and known-bad pairs (deferred — doesn't
  constrain the design; the *fields* are fixed, thresholds are code).
- `overrides.json` can force a verdict per `tab_id` (with a required note).

### Split mechanics

`bucket = int.from_bytes(sha1(str(artist_id).encode()).digest()[:4], "big") % 100`
→ 0–84 train, 85–89 val, 90–99 test. Overrides (in `overrides.json`):
`pin_split: {artist_id: "train"|...}` for contamination,
`same_side: [[tab_id, tab_id], ...]` for cover pairs (resolved by pinning the
cover's artist to the original artist's bucket side). The pilot corpus was
handled during pipeline development, so artists whose *content* was studied in
depth (e.g. alignment experiments) should be pinned to train as they're
identified.

### `report.md` contents

Corpus counts by verdict/split; track-kind and string-count distributions;
tuning census (which tunings, how many songs each); capo usage; technique
frequency table (Phase 3 reads this to pick the modeled subset); tempo and
duration distributions; time-signature census; suspect/bad tab list with
reasons; artists-per-split table.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| GPIF dialect variance (GP6 `.gpx`-derived vs GP7/8 `.gp`-derived) breaks parsing | Parse **all 509** files in CI-style acceptance run; `parse_ok=false` + warnings are manifest data, not crashes; fixtures from both dialects in tests. |
| Repeat/jump expansion wrong → duration check misfires | Warnings on unexpandable constructs downgrade the check to `suspect`, never `bad`; spot-check expanded durations against audio durations of `official_channel`-confidence pairs (they should cluster near ratio 1.0 — a systematic offset exposes expansion bugs). |
| Chroma check unreliable on distorted/drum-heavy mixes | Tune thresholds on labeled sample; consider harmonic–percussive separation (librosa HPSS) before chroma if raw chroma is noisy; a weak check flags `suspect`, not `bad`. |
| Manifest schema too weak, later phases re-parse gpifs anyway | Schema reviewed against Phase 2/3/4/5 stated needs before freezing v1; JSONL + header versioning makes additive evolution cheap. |
| Split leakage via covers/renames | `possible_cover` fuzzy title match across artists + `same_side` overrides; dedup invariant re-asserted on every regeneration as Phase 1 scales. |
| Throwaway-becomes-load-bearing (inverse: `gpscore` API ossifies before Phase 2a) | Explicit pre-1.0 contract: audit-py consumes aggregates only; Phase 2a has refactoring rights over internals and the note-level model. |

## Acceptance criteria

- `audit run` completes over the full corpus; **100% of tabs** get a manifest
  record (parse failures appear as records with `parse_ok=false`, not gaps).
- Manifest generation is **deterministic**: two consecutive runs are
  byte-identical.
- ≥95% of gpifs parse with no warnings; every warning type is enumerated in
  the report.
- Duration + chroma checks computed for every tab with audio; the
  `official_channel` high-confidence subset clusters at duration ratio ≈ 1.0
  and chroma rotation 0 (sanity that the checks themselves work).
- Split assigned to every artist; fractions within ±3 points of 85/5/10 by
  artist count; override mechanism demonstrated (at least the cover-pin path
  exercised in tests).
- `report.md` renders with all sections above; suspect/bad lists are
  spot-checked by hand (listen to ~10 flagged pairs) and the checks' verdicts
  hold up.
- Unit tests green, network-free, fixture-based (repo convention); both gpif
  dialects covered.
- Docs current per CLAUDE.md: `docs/score-py/overview.md` and
  `docs/audit-py/overview.md` exist, `OVERVIEW.md` map and roadmap updated.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Numeric check thresholds | Fields and flag taxonomy are fixed; thresholds are tunable code, calibrated on a labeled sample during implementation. |
| Note-level score model (ties, per-note durations, techniques-per-note) | Phase 2a scope; audit consumes aggregates only. |
| Fuzzy dedup clustering | Zero duplicates measured in the pilot corpus; the invariant will surface collisions the moment Phase 1 produces them. |
| Synthesis-based verification / alignment fit cost | Manifest reserves `checks.alignment`; Phase 2 backfills. |
| Dataset snapshot/versioning mechanics | Phase 1 scope; determinism + single-file manifest already make snapshot = file hash. |
| Genre/diversity metadata per tab | Needs Phase 1 discovery-side data; additive manifest change. |
| Re-enrichment of `bad`-audio tabs | Phase 1 ops; the manifest tells it exactly which tabs to retry. |

## Open questions for later phases

- **Phase 1:** manifest regeneration cadence as the corpus grows; who runs it
  and when; snapshot naming/retention. Should discovery prioritize diversity
  gaps the report exposes (tunings, 7-string, acoustic)?
- **Phase 2:** the backfill contract for `checks.alignment` (fields, who
  writes them, how backfill coexists with deterministic regeneration —
  likely a second derived file joined on `tab_id`). Whether `suspect`
  transposed pairs (chroma rotation ≠ 0) can be rescued by transposing labels.
- **Phase 2a:** note-level score model design; owns `gpscore` internals
  refactor and the 1.0 API freeze.
- **Phase 3:** technique vocabulary cutoffs — decide from the report's
  technique frequency census, not intuition.
- **Phase 5:** whether the val split should also be artist-disjoint from val
  *of synthetic renders* (same artist synthetic-in-train question).
