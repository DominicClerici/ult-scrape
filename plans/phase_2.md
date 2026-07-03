# Phase 2 — Ground truth: symbolic extraction + alignment

> Expanded from [the roadmap](../docs/roadmap.md#phase-2--ground-truth-symbolic-extraction--alignment-redesign)
> in the 2026-07-01 planning session. Decisions here are **binding inputs** to
> later phases. The scrapped `aligner-py` (removed at `90d1a0c`; docs
> recoverable via `git show 90d1a0c^:docs/aligner-py/overview.md`) is prior art:
> its *structural* learnings carry over; its chroma-CENS + librosa-DTW core does
> not — it was scrapped because results were still not good enough and it was
> built on `.gp` audio rendering instead of a proper score model.

## Goal & scope

Produce training-grade ground truth for every eligible tab:

- **2a — Symbolic extraction.** Extend `score-py/` (`gpscore`) with the
  note-level score model: every note with string/fret/position/duration/
  techniques, parsed from both GPIF dialects, exposed as a faithful document
  model plus a derived linear-time performance view. Freeze the 1.0 API.
- **2b — Tab ↔ real-audio alignment.** A new `aligner-py/` that maps each
  tab's expanded score timeline onto its real recording and grades the result
  in **measured, calibrated tiers** — per segment, not per song — gating what
  enters the training set.

**Out of scope:** audio synthesis/rendering of any kind (Phase 4); the
tokenizer and the GPIF *writer* (Phase 3); fret-aware label transposition
(Phase 3); the CTC forced-aligner (planned escalation, triggered only by
measurement — see Design); fixing/re-enriching pairs whose audio is bad
(Phase 1 ops); dataset windowing/chunking (Phase 3).

**Sequencing note:** Phase 0 (`plans/phase_0.md`) is planned but not yet
implemented. Phase 2 consumes its manifest and its structural `gpscore` layer;
Phase 0 implementation must land first (or concurrently, with 2a building
directly on the structural layer as it appears).

## Inputs / outputs

**Consumes:**

- `manifest/manifest.jsonl` (Phase 0): candidate selection — pairs graded
  `ok`, plus `suspect` transposed pairs (chroma rotation ≠ 0) which are
  aligned but annotated. `bad` pairs are skipped.
- `manifest/overrides.json` (Phase 0): the aligner-eval annotation set pins
  its artists to the train split here.
- The frozen [output contract](../docs/output-contract.md), read-only:
  `.gpif` + `audio.<ext>` per tab. **Phase 2 never writes into `output/`.**
- `score-py/` structural layer (Phase 0): tracks, tunings, tempo map,
  repeat/jump expansion.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Note-level score model | `score-py/` (`gpscore` 1.0) | Code — document model + performance view; the shared dependency for Phases 3/4/5 |
| Aligner | `aligner-py/` | Code — decoupled CLI (scan/run/status, SQLite queue, mirrors `enricher-py`) |
| Per-tab alignment | `manifest/alignment/<tab_id>.json` | Derived, input-fingerprinted (expensive: a keyed build cache, not cheaply regenerable) |
| Alignment summary | `manifest/alignment.jsonl` | Derived — one line per tab, joins `manifest.jsonl` on `tab_id` |
| Aligner eval set | `aligner-py/eval/anchors/*.json` + docs | Hand-labeled — ~30 songs × ~20 anchors; committed, versioned |
| Calibration report | `manifest/alignment_report.md` | Derived — tier distributions, calibration curves, escalation verdict |

**Later-phase consumers:** Phase 3's dataset builder cuts training windows
from tier-graded segments and reads the tier distribution before choosing its
token time base; Phase 4 renders from the `gpscore` performance view; Phase 5
inherits the alignment proxies and the eval-anchor tooling.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Precision contract with Phase 3 | **Tiered + measured**: each aligned region graded `onset_grade` (est. median note-onset error ≤ 50 ms), `beat_grade` (≤ ~250 ms), or `unusable`, via confidence proxies calibrated on a labeled set | Hard ≤50 ms or reject (discards beat-grade data score-time training could use); beat-grade-only target (silently forecloses Phase 3's performance-time option on real audio) | Accuracy is a measured per-pair property, not a design toggle. Phase 3 chooses its time base knowing the actual tier distribution instead of inheriting a prejudgment. 50 ms is the `mir_eval` onset standard; ~250 ms suffices for bar/beat anchoring. |
| 2a model architecture | **Two-layer**: faithful document model (Score→Track→Bar→Voice→Beat→Note, technique **superset**, warnings for unrecognized constructs) + derived performance view (`score.performance()`) | Single flat note-event model (loses round-trip and Phase 8 export; technique additions reopen the parser); lossless XML wrapper (stringly-typed pain for every consumer) | Two consumers want score structure (tokenizer, eval), two want linear performance time (synth, aligner). Prior art (music21, alphaTab) converges on document + flattened view. Parse every recognizable technique; Phase 3 picks its modeled subset from the Phase 0 census. |
| Musical time representation | **Exact rationals** (`Fraction`, quarter-note units) in the document layer; seconds always derived via the tempo map, never stored | Integer ticks at fixed resolution (eventually collides with odd/nested tuplets, rounds silently); floats (accumulating error, fuzzy equality) | GPIF rhythms are symbolic note values with tuplets; rationals are exact for all of them. Ground truth must not round. |
| GPIF writer | **Deferred to Phase 3** | Build now and round-trip-test the parser (stronger validation, big 2a scope growth) | The two-layer model guarantees writability; the writer adds no design constraints on the model. Phase 3 builds it beside the round-trip test that needs it. |
| 2b approach | **Staged coarse-to-fine with measured escalation**: (1) upgraded classical baseline — chroma + DLNCO-style onset features, subsequence multi-resolution DTW (synctoolbox/MrMsDTW), gap-aware + trusted-tempo structure carried over from the scrapped design; (2) local onset-snap refinement; (3) **CTC forced-aligner as escalation**, built only if the calibrated tier distribution says the baseline is insufficient | CTC-first (best expected robustness on distorted mixes, but pulls Phase 4 synthesis forward and spends the effort before measurement says it's needed); DTW++ with no escalation plan (risks re-scrapping) | The scrapped aligner used plain chroma-CENS + librosa DTW — well below state of the art; synctoolbox-class stacks reach ~20–50 ms on score↔audio sync. Whether that survives rock/metal full mixes is measurable, not arguable — so buy the measurement first and let it pick the escalation. The last attempt died partly from never being validated; this is the correction. |
| Score-side reference signal | **Features computed directly from `gpscore` note events** (pitch classes + onsets → same feature space as the audio) — no audio render anywhere in 2b | Synthesized WAV render (resurrects the MuseScore/FluidSynth toolchain; adds nothing DTW measurably exploits); both compared (doubles reference-side work for a comparison the sync literature already ran) | How synctoolbox works; deterministic, fast, and kills the brittle toolchain that plagued the old aligner. Rendering returns in Phase 4 where it belongs. |
| Source separation (Demucs) | **Mandatory ablation in the calibration study**, not a presumption | Baked into the pipeline (adds a heavyweight dependency before evidence); ignored (plausibly the biggest single win on full mixes) | Cheap to evaluate with the harness we're building anyway; adopt if and only if it moves the tier distribution. |
| Aligner evaluation | **Three-part harness**: synthetic-warp CI suite (known tempo factors / inserted gaps / drift, assert recovery); **~30 hand-labeled real songs** (stratified by mix difficulty, ~15–25 anchors each: section-boundary downbeats, first note, distinctive hits); corpus-wide proxies calibrated against the labels | No hand labels (tier boundaries uncalibrated on real mixes — the exact blind spot that sank attempt #1); ~100 labeled songs (3–4 days annotation for precision the calibration can't use yet; grow later if curves are unstable) | The tier contract is meaningless without ground truth *for the aligner*. ~30 songs ≈ one focused annotation day and gives usable per-stratum counts. |
| Eval-set split hygiene | Every hand-labeled song's **artist is pinned to the train split** via Phase 0 `overrides.json` | — | Studied-in-depth content must never reach the model's test set; exactly the contamination case Phase 0's override mechanism anticipated. |
| Transposed pairs (rotation ≠ 0) | **Align + annotate** (`pitch_offset_semitones` in the artifact), **excluded from training by default** | Reject outright (discards recoverable Eb-standard/remaster pairs and the offset info); rescue now via label transposition (pulls Phase 3 augmentation machinery forward) | Feature-space rotation makes aligning them nearly free; consuming them safely requires fret-aware transposition, which is Phase 3's machinery and Phase 3's call. |
| Gating granularity | **Segment-level tiers**: the warp is partitioned into contiguous regions tagged `onset_grade`/`beat_grade`/`unusable`; song-level tier is a derived summary | Song-level only (discards the good 80% of partially-aligned songs — solos, extended outros) | Training consumes 15–30 s windows anyway; per-region quality converts "coverage" from a rejection criterion into harvested data. Requires windowed proxies, validated per-window in calibration. |
| Project layout | 2a extends **`score-py/`**; 2b is a fresh **`aligner-py/`** (decoupled CLI: `scan`/`run`/`status`, SQLite queue, mirrors `enricher-py`) | Start `dataset-py` now (guesses at the shape of two undesigned phases) | Repo pattern; the old name is honest and git history disambiguates. |
| Artifact location | **`manifest/` derived tree**: `manifest/alignment/<tab_id>.json` (full warp + segments, fingerprinted) + `manifest/alignment.jsonl` (compact summary, joined on `tab_id`). `output/` untouched | `align.json` in `output/` (the old contract — re-opens the frozen output contract and ties expensive derived data to re-scrape `rmtree` semantics) | Settles Phase 0's open question ("likely a second derived file joined on `tab_id`"). `manifest.jsonl`'s reserved `checks.alignment` field stays null; consumers join `alignment.jsonl` instead. |
| Carried over from the scrapped aligner (validated, paradigm-independent) | Trust the **notated tempo**; half/double-time **snap factors** (0.5, 1, 1.5, 2, 3); **gap detection before tempo estimation** (breaks the circular dependency); **subsequence** matching (non-tab intros/outros); confidence-metric gating rather than trusting every alignment | — | These were the redesign's sound structural insights; only the feature/DTW core beneath them is replaced. |

## Design

### 2a — `gpscore` note-level model (in `score-py/`)

**Layer 1 — document model.** Faithful parse of the GPIF graph
(MasterBars → Bars → Voices → Beats → Notes, with shared Rhythm objects):

- `Score`: existing structural fields (Phase 0) + `bars`, and the note graph.
- `Bar` / `Voice` / `Beat`: beats carry `Rhythm` (note value + dots +
  tuplet as exact `Fraction`), dynamics, grace/brush/arpeggio/tremolo flags,
  rest-ness, lyrics.
- `Note`: `string`, `fret`, `midi_pitch`, tie origin/destination, and a
  **technique superset** parsed from note/beat Properties: bend (with bend
  points), slide (all GPIF slide flag variants), hammer/pull, palm-mute,
  harmonic (type + fret), vibrato, let-ring, tap/slap/pop, trill, whammy,
  dead/ghost note, accent, staccato. Drum-track notes carry their percussion
  articulation/MIDI number.
- All positions/durations are `Fraction` quarter-notes. Unrecognized
  constructs append structured warnings (never silently dropped). Both GPIF
  dialects (GP6 `.gpx`-derived, GP7/8 `.gp`-derived) must parse; fixtures
  from both in tests.

**Layer 2 — performance view.** `score.performance() -> Performance`:

- Repeats/jumps expanded (reusing Phase 0's expansion), ties merged into
  single sounding notes, grace notes resolved to real time, tempo map applied.
- `PerformanceNote`: `track_index`, `onset_qn: Fraction` (expanded
  timeline), `onset_s: float`, `duration_s: float`, `midi_pitch`, `string`,
  `fret`, `dynamic`, technique flags.
- The expanded **bar/beat grid** (`onset_qn`/`onset_s` per beat) — the
  anchor vocabulary for the aligner and for Phase 3 score-time tokens.
- Invariants (tested): onsets monotonic per track, durations > 0, expanded
  duration equals Phase 0's `duration_seconds()`.

**1.0 freeze:** the public surface is the document classes, `performance()`,
and the warnings taxonomy. Internals stay refactorable; additive evolution
(new technique fields) is non-breaking.

### 2b — `aligner-py/`

Modules (decoupled CLI, mirrors `enricher-py`; depends on `gpscore` + the
manifest, never on other projects' internals):

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings: manifest dir, output dir (read-only), feature/DTW params, tier thresholds, jobs. |
| `app/features.py` | Real-audio side: decode (soundfile → ffmpeg fallback), chroma + onset-based features (DLNCO-style), silence/dead-region detection (RMS envelope). Optional Demucs front-end behind a flag (ablation). |
| `app/reference.py` | Score side: `gpscore` performance view → feature matrices (pitch-class activations + onset impulses on the expanded timeline). Chroma rotation for transposed candidates. |
| `app/align.py` | Subsequence multi-resolution DTW (synctoolbox MrMsDTW where usable; thin local implementation otherwise), robust tempo fit on active regions, snap factors, warp composition with gap holding. |
| `app/refine.py` | Onset-snap refinement: within a small window around each warp-predicted note onset, snap to the nearest detected audio onset; emits per-window agreement stats. |
| `app/tiers.py` | Windowed confidence proxies (fit cost, path deviation, onset-snap agreement rate, cross-resolution self-consistency) → calibrated per-window error estimate → contiguous segment tiers. Calibration model fitted from the labeled anchor set. |
| `app/queue.py` / `app/repo.py` | SQLite work queue: `scan` (manifest → pending, skipping fingerprint-current results), `run --jobs N`, `status`. |
| `app/output.py` | Atomic write of `alignment/<tab_id>.json`; regeneration of `alignment.jsonl` + `alignment_report.md`. |
| `app/inspect.py` | Developer diagnostics: warp plot with segment tiers + gaps shaded; click-track overlay rebuilt on demand (not a stored artifact). |
| `eval/` | Anchor annotation format + loader, calibration/eval scripts, the synthetic-warp suite's generators. |

**Pipeline per tab** (ordering carried from the scrapped redesign):

1. Detect real-audio dead regions (tempo-free RMS envelope).
2. Build score-side features at the notated tempo (rotated by the manifest's
   chroma rotation when ≠ 0).
3. Coarse subsequence DTW → robust tempo slope on active regions only →
   snap to clean factor or fall back (recorded as `tempo_source`).
4. Final subsequence multi-resolution DTW at the corrected tempo (a feature
   re-scale — no render).
5. Onset-snap refinement of the warp around note onsets.
6. Windowed proxies → calibrated error estimates → contiguous segment tiers;
   gaps and low-confidence spans become `unusable` segments.
7. Atomic artifact write.

### Alignment artifact schema (v1)

`manifest/alignment/<tab_id>.json`:

```jsonc
{
  "schema_version": 1,
  "aligner_version": "0.1.0",
  "inputs": { "gpif_sha256": "…", "audio_sha256": "…" },   // staleness / idempotency key
  "status": "aligned",                  // aligned | failed:<reason> | skipped:<reason>
  "pitch_offset_semitones": 0,          // ≠ 0 ⇒ transposed pair: annotated, not consumed by default
  "tempo_ratio": 1.0,
  "tempo_source": "notated",            // notated | notated_x2 | … | dtw_fallback
  "warp": [[0.0, 1.84], …],             // [score_time_s (expanded), real_time_s] anchors
  "gaps": [ { "real_start_s": 132.0, "real_end_s": 190.5, "kind": "internal" } ],
  "segments": [                         // contiguous, non-overlapping, cover the warp span
    { "score_start_qn": "0/1", "score_end_qn": "256/1",
      "score_start_s": 0.0, "score_end_s": 131.2,
      "real_start_s": 1.84, "real_end_s": 133.0,
      "tier": "onset_grade",            // onset_grade | beat_grade | unusable
      "est_median_onset_error_ms": 22,
      "proxies": { "fit_cost": 0.11, "onset_snap_agreement": 0.87, "self_consistency_ms": 18 } }
  ],
  "summary": { "onset_grade_s": 190.4, "beat_grade_s": 12.0, "unusable_s": 15.0 }
}
```

`manifest/alignment.jsonl`: one line per tab — `tab_id`, `status`,
`pitch_offset_semitones`, `tempo_source`, tier-duration summary,
`aligner_version` — for cheap joins against `manifest.jsonl`.
Segment times are given in both expanded score time (`Fraction` as strings,
plus seconds) and real time, so Phase 3 can cut windows on either axis.

### Calibration & escalation procedure

1. Annotate the ~30-song eval set (stratified: clean/acoustic, pop full-mix,
   distorted rock/metal, plus known-hard cases from the old aligner).
2. Run the baseline (± Demucs ablation, ± reference-track-selection ablation)
   over the eval set; compute true onset errors at the anchors.
3. Fit the proxy→error calibration (simple monotonic model; per-window);
   validate tier precision cross-validated over songs: **`onset_grade`
   precision ≥ 90 %, `beat_grade` precision ≥ 90 %** at their thresholds.
4. Run the full pilot corpus; publish `alignment_report.md` with the tier
   distribution.
5. **Escalation gate:** if the fraction of corpus-duration at `onset_grade` +
   `beat_grade` is judged insufficient for Phase 3 (reviewed with the tier
   distribution in hand — no fixed number is pre-committed), design the CTC
   forced-aligner as a Phase 2 extension after Phase 4's renderer exists.
   The decision and its evidence go in the report.

### Tooling & dependencies

Python ≥ 3.13 to match siblings, **but** `synctoolbox` depends on `numba`,
whose 3.13 support may lag — resolve at implementation start: pin
`aligner-py` to 3.12, or implement the needed MrMsDTW/DLNCO pieces locally
on `librosa`/`numpy` (the algorithms are published; the local-implementation
fallback is acceptable). `librosa`, `soundfile` + `ffmpeg` (decode), `numpy`/
`scipy`; `demucs` optional (ablation flag only). No MuseScore, no FluidSynth.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Handcrafted features still fail on distorted full mixes (the attempt-#1 killer) | This time it's *measured*: the labeled eval set + tier calibration expose it per-stratum, the Demucs ablation is the first lever, and the CTC escalation path is pre-designed with its trigger documented. Failure produces evidence, not vibes. |
| Windowed proxies calibrate poorly (segment tiers unreliable) | Calibration is validated per-window against labeled anchors with cross-validation; if per-window calibration fails, fall back to song-level tiers (contract field shapes unchanged — one segment per song). |
| `synctoolbox`/`numba` incompatible with Python 3.13 | Known at day one (import test); pin 3.12 for `aligner-py` or implement MrMsDTW/DLNCO locally. |
| 2a technique parsing rabbit hole (GPIF property zoo) | Superset parsing is bounded by *recognized* properties; everything else is a structured warning. The Phase 0 acceptance run enumerates warning frequencies — high-frequency warnings get parsed, tail stays warned. |
| Annotation errors in the eval set | Anchors are sparse and salient (downbeats, first notes); annotate with audible-click verification; disagreement between proxies and labels on a specific song triggers re-listening before it triggers re-tuning. |
| Phase 0 not yet implemented (this phase consumes its outputs) | Sequencing dependency made explicit; 2a can start against the structural layer as Phase 0 lands; 2b candidate selection needs the manifest and comes after. |
| Tier thresholds ossify into false precision (50 ms / 250 ms) | Thresholds are contract *names*; the artifact stores the continuous `est_median_onset_error_ms`, so Phase 3 can re-cut tiers without re-alignment. |

## Acceptance criteria

- **2a:** 100 % of corpus `.gpif`s parse into the note-level model (failures
  are structured warnings/records, not crashes); both dialects covered by
  fixtures; performance-view invariants tested; expanded durations match
  Phase 0's structural computation; `gpscore` 1.0 API documented and frozen.
- **2b baseline:** aligner runs the full pilot corpus (all `ok` + transposed
  `suspect` pairs) through the queue with resumability; artifacts +
  `alignment.jsonl` + report generated; re-run with unchanged inputs is a
  fingerprint-hit no-op.
- **Calibration:** tier precision ≥ 90 % for `onset_grade` and `beat_grade`
  on the labeled set (cross-validated); synthetic-warp CI suite green and
  network-free; Demucs and reference-track ablations reported with numbers.
- **Escalation verdict documented:** the report states the corpus tier
  distribution and the explicit go/no-go on the CTC extension, with evidence.
- Eval-set artists pinned to train in `overrides.json`.
- Unit tests deterministic and network/tool-free by default (repo
  convention); heavy audio paths behind the `integration` marker.
- Docs current per CLAUDE.md: `docs/score-py/` updated for the note-level
  model, `docs/aligner-py/` written fresh, `OVERVIEW.md` map + roadmap
  updated, Phase 0's open-questions list updated (backfill contract settled).

## Deferred items

| Item | Why deferring is safe |
|---|---|
| GPIF writer (score → `.gpif`) | The two-layer document model guarantees writability; Phase 3 builds it beside the round-trip test that needs it. |
| CTC forced-aligner | Pre-designed as the escalation path with a documented trigger; needs Phase 4 renders for training data. Building it without evidence repeats attempt #1's mistake in reverse. |
| Fret-aware label transposition (consuming transposed pairs) | Offsets are recorded in the artifact; Phase 3 owns the augmentation machinery that makes them consumable. |
| Reference track selection (all pitched tracks vs guitar-only vs guitar+bass) | An ablation inside the calibration study — the harness decides it empirically; the artifact schema doesn't depend on it. |
| Annotation tool choice (Sonic Visualiser vs small bespoke tapper) | Format (`eval/anchors/*.json`) is fixed; the tool only produces it. |
| Exact numeric proxy thresholds | Continuous error estimates are stored; tier cut-points are calibrated code, re-cuttable without re-alignment. |
| Symbolic-side structural mismatch (tab has a section the recording lacks) | Shows up as a compressed/low-confidence warp region → `unusable` segment; segment gating already quarantines it. A dedicated detector is an optimization, not a correctness need. |

## Open questions for later phases

- **Phase 3:** choose the token time base *from the measured tier
  distribution*; decide whether/how to consume `beat_grade` segments and
  transposed pairs (fret-aware transposition); build the GPIF writer +
  round-trip test; window-cutting policy across segment boundaries.
- **Phase 4:** the renderer doubles as the CTC training-data source if the
  escalation triggers — design its label export with that in mind; consider
  sharing `aligner-py`'s feature code via `gpscore` utilities if drift becomes
  a problem (today: deliberate duplication over a new shared dep).
- **Phase 1:** pairs whose alignment fails for audio reasons (wrong video
  despite Phase 0 `ok`) feed the re-enrichment queue — define the feedback
  loop (`alignment.jsonl` → enricher retry list).
- **Phase 5:** the anchor-annotation tooling and labeled set are reusable for
  qualitative model eval; decide whether model-eval metrics reuse the
  aligner's proxy implementations.
