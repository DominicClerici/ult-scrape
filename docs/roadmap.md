# ML Roadmap — audio → Guitar Pro tabs (AMT)

> Part of the [documentation map](../OVERVIEW.md). This is the **high-level
> conceptual roadmap** for turning the scraped corpus into a trained automatic
> music transcription (AMT) model: a user uploads an mp3, the system outputs
> guitar tablature. Each phase below will be expanded into its own detailed
> design doc in future sessions; this page stays the map of the whole journey.

## Where we are today

The data pipeline (scraper → decoder → enricher) is built and has produced a
pilot corpus:

- **~509 songs** with trustworthy, professionally-transcribed Guitar Pro tabs
  (decoded to `.gp`/`.gpx` + `.gpif` XML).
- **~495 of them** have full source audio (YouTube best-match, webm/opus).
- The tabs are **multi-track** (rhythm/lead guitars, bass, drums, pitched
  vocals) with tuning, string/fret, tempo maps, and technique annotations —
  unusually rich ground truth.

## Locked decisions (from the initial planning session, 2026-07-01)

| Decision | Choice | Rationale |
|---|---|---|
| v1 transcription scope | **Guitar only** (all guitar tracks in the mix) | Core use case; simplest eval; bass/vocals/drums later. The **token format must be multi-track-capable from day one** so later scope costs no redesign. |
| Corpus size | **Scale to thousands** (2k–10k songs) via the existing pipeline | More real data is the highest-value input and the pipeline already exists. |
| Compute strategy | **Local GPU first, cloud when needed** | Prove everything at small scale locally; escalate to rented A100/H100 for the runs that demonstrably need it. |
| Alignment approach | **Open design problem** | A prior DTW-based `aligner-py` was built and scrapped (removed `90d1a0c`). Its learnings inform the redesign (see Phase 2) but the approach is not presumed. |

## The two hard problems

The model architecture is the *commodity* part of this project — spectrogram
encoder + token decoder is well-trodden (MT3 and descendants). The
differentiating, risk-carrying work is:

1. **Ground truth construction** — the tab and the YouTube recording are not
   time-aligned. The tab lives in score time (bars/ticks with its own tempo
   map); the recording is a performance with intros, outros, tempo drift,
   sometimes different structure. Training needs per-note timestamps in the
   real audio, *or* a formulation that sidesteps them.
2. **Tab representation** — tablature is more than pitches: string+fret,
   rhythm/bars, tuning/capo, techniques (bends, slides, palm mutes, harmonics).
   The token vocabulary and output formulation determine what the model can
   ever express.

Both are addressed before any serious model training.

## The strategic lever: synthetic audio

Every GP file can be **rendered to audio** (MIDI synthesis via soundfonts, amp
sims, multiple timbres/mixes per song). Rendered audio is *perfectly aligned*
with the tab by construction. This gives:

- **Unlimited pretraining data** from the tab corpus alone — mitigates both the
  modest real-audio count and alignment fragility.
- A **curriculum**: pretrain on synthetic (clean → degraded/augmented), then
  fine-tune on aligned real recordings, evaluate on held-out real recordings.
- A debugging tool: a model that fails on synthetic audio has a
  representation/architecture bug, not a data bug.

This is the same shape as SynthTab-style work and is the main reason a
local-GPU-first strategy is credible.

## Phases

Phases 0–2 are data work, 3–5 build the training substrate, 6–7 are modeling,
8–9 are productization. Phase 1 (corpus scaling) runs in the background
throughout.

### Phase 0 — Corpus audit & hygiene

**Expanded: see [`plans/phase_0.md`](../plans/phase_0.md)** (2026-07-01). Know
exactly what we have before building on it.

- Parse every `.gpif`: inventory tracks per song (instrument types, tunings,
  capo, 6/7-string, drop tunings), technique frequency, tempo-map complexity,
  song lengths. This **starts the shared score-model library** (`score-py/`,
  a structure-scoped pull-forward of Phase 2a — repeat expansion and tempo
  maps, but no note-level model; Phase 2a owns that design).
- **Audio verification**: the enricher's YouTube match can be wrong (covers,
  live versions, remasters at different pitch). Flag suspects via
  repeat-expanded duration mismatch and a 12-rotation pitch-class↔chroma
  comparison (no synthesis needed); alignment fit cost backfills from
  Phase 2. Wrong-audio pairs are poison for training — flagged (graded
  `ok`/`suspect`/`bad`), never deleted.
- Dedup as an invariant (the pilot corpus measured zero duplicates) with
  cross-artist cover flagging, and the **held-out split fixed now**: a
  deterministic hash of UG `artist_id` → 85/5/10 train/val/test, so future
  artists auto-classify and no human ever picks test artists.
- Deliverable: `audit-py/` producing `manifest/manifest.jsonl` (single
  regenerable JSONL every later phase consumes) + a corpus report.

### Phase 1 — Scale the corpus (continuous, background)

- Use discovery + scraper + enricher to grow toward 2k–10k official tabs.
- Prioritize diversity: tunings, genres, acoustic vs distorted, tempo range.
- Storage/ops: LFS strategy, re-run cadence, enrichment retry policy.
- The corpus is *versioned*: dataset snapshots are immutable inputs to training
  runs (reproducibility).

### Phase 2 — Ground truth: symbolic extraction + alignment (redesign)

**Expanded: see [`plans/phase_2.md`](../plans/phase_2.md)** (2026-07-01). Two
sub-problems:

**2a. Symbolic extraction.** Extend `score-py/` (`gpscore`) with the
note-level model and freeze the 1.0 API — needed by *everything* downstream
(tokenizer, synthesizer, aligner, eval). Locked shape: a **two-layer** model
(faithful document model with a technique superset + derived linear-time
performance view), exact rational (`Fraction`) musical time, GPIF writer
deferred to Phase 3.

**2b. Tab ↔ real-audio alignment.** Locked approach: **staged coarse-to-fine
with measured escalation** in a fresh `aligner-py/`. Baseline = upgraded
classical stack (chroma + onset features, subsequence multi-resolution DTW,
synctoolbox-class) with score-side features computed **directly from the
score model — no audio rendering in 2b** — plus local onset-snap refinement;
a CTC forced-aligner is the pre-designed escalation, built only if measurement
demands it (it needs Phase 4 renders). The scrapped aligner's validated
structural learnings carry over (trusted notated tempo, half/double-time
snapping, gap-detection-before-tempo, subsequence matching); its plain
chroma-CENS/DTW core does not. Output is a **tiered, measured contract**:
per-segment grades (`onset_grade` ≤ 50 ms / `beat_grade` ≤ ~250 ms /
`unusable`) calibrated against a ~30-song hand-labeled eval set, written to
`manifest/alignment/` + `manifest/alignment.jsonl` (settling Phase 0's
backfill question; `output/` stays untouched). Transposed pairs are aligned
and annotated but not consumed by default. A high-precision aligned subset
beats a high-recall noisy one — and synthetic data (Phase 4) carries the bulk
of training regardless.

### Phase 3 — Representation: tokenizer + dataset builder

**Expanded: see [`plans/phase_3.md`](../plans/phase_3.md)** (2026-07-01). The
single most consequential design choice — now locked, grounded in a full-corpus
census (509 `.gpif`s):

- **Time base settled: score-time tokens + per-bar time anchors** (DadaGP-style
  symbolic output — the model output *is* the tab — with Whisper-timestamp-style
  bar-onset anchors). Consequence: **`beat_grade` alignment suffices** for real
  audio, lowering what Phase 2 must deliver; Phase 8 shrinks to validation.
  Fallback to performance-time stays cheap because dataset records store
  symbolic windows, never token IDs.
- Vocabulary (~500–600 tokens): bar-major multi-track interleave, predicted
  per-track headers (tuning/capo — forceable at inference), fused
  `NOTE(string,fret)`, factored symbolic rhythm (exact round-trip), 12
  census-justified Tier-1 techniques with 3-point quantized bends; dynamics and
  tail techniques dropped **with accounting**.
- `gpscore.tokens` + the GPIF writer (GP7/8 dialect, `.gp` packaging) live in
  `score-py/`; corpus-wide round-trip tests under a "modeled projection".
- `dataset-py/` builds per-song records (`dataset/<snapshot>/` + index; snapshot
  = index hash); windows are bar-aligned (~20 s target), sampled at load time.
  Augmentation: tuning-shift transposition offline (rescues Phase 2's transposed
  pairs), audio-domain augs on-the-fly. Demucs: N-channel input contract
  reserved; mix vs mix+stem is a Phase 6 ablation.

### Phase 4 — Synthetic data engine

**Expanded: see [`plans/phase_4.md`](../plans/phase_4.md)** (2026-07-02).
Locked shape:

- **`render-py/`** (house-pattern CLI) renders every `parse_ok` tab —
  alignment and even source audio are not needed, so `bad`-pair tabs become
  usable training material. Output: `renders/<tab_id>/<variant>/` (mix +
  guitar-bus FLACs + `render_meta.json` with the full recipe, realized bar
  grid, and per-note onsets — the CTC-escalation training export). Ingested
  by `dataset-py` as `source: "synthetic"` records.
- **Pluggable backends over an articulated per-string event stream**: open
  stack v1 (FluidSynth/sfizz), commercial-VST adapter (SynthTab-style) as
  the pre-designed escalation, triggered only by Phase 6 transfer
  measurement. All 12 Tier-1 techniques audibly voiced — enforced by a CI
  audibility test.
- **Signal chain = pedalboard + free NAM amp captures + cab IRs**, tone
  identity in a versioned recipe library; hash-pinned asset registry.
- **Seeded stochastic variants** (samples, tones, tempo ×0.8–1.2, mix,
  vocal-line instrumental distractors, humanization with realized-time
  labels) + a canonical clean variant 0 per song. Split inherited strictly;
  val/test rendered with **held-out timbres** (eval-tagged assets).

### Phase 5 — Evaluation harness (before serious training)

**Expanded: see [`plans/phase_5.md`](../plans/phase_5.md)** (2026-07-02).
Locked shape:

- **`eval-py/`** (house-pattern CLI + importable `tabeval` library, imported
  by Phase 6 for training-time val metrics). Model-free: Phase 6 writes a
  `predictions.jsonl` of raw token sequences per window; the harness
  detokenizes with a pinned `gpscore.tokens` and scores files.
- **Eval unit**: frozen bar-aligned windows (primary) + a frozen offset-slice
  suite measuring bar-alignment dependence; song-level stitched eval stays in
  Phase 8. Eval sets are **committed, versioned manifests** — no silent drift.
- **Dual note correspondence, symbolic primary**: DP bar alignment + exact
  within-bar onset matching feeds tab/technique/rhythm metrics on all data
  (so `beat_grade` real windows are fully scoreable); mir_eval time-domain
  matching feeds onset/pitch F1 (synthetic + `onset_grade` real); anchors
  scored separately; Hungarian track assignment + order metric.
- **Headline: Tab F1 (string+fret) on the real held-out test set**,
  song-macro, both tiers included with per-tier facets. Synthetic test =
  canonical + 2 held-out-timbre variants per test song (settles Phase 4's
  open question); facet gaps are the Phase 6 escalation discriminators.
- **Self-validated harness**: oracle ceiling, corruption-sensitivity CI
  (metrics must move monotonically and selectively), floor baselines.
  Human-correlation protocol ships now, executes early Phase 6.
- Qualitative loop: static HTML bundle (real / GT-render / prediction-render
  audio + side-by-side alphaTab notation). W&B as a thin sink over canonical
  local `scorecard.json`.

### Phase 6 — Baseline model (v1: guitar only)

**Expanded: see [`plans/phase_6.md`](../plans/phase_6.md)** (2026-07-02).
Locked shape:

- **`model-py/`** (plain-PyTorch single-GPU loop, 16 GB envelope, multi-day
  runs): **MERT-v1-95M (24 kHz, pinned) + fresh ~40M decoder (3k ctx)** as
  primary, compact from-scratch mel model as pilot-scale control — the
  "empirical pick" is a bounded 1–2-day bake-off with a zero-shot real-val
  transfer probe; MT3 fine-tuning dropped (vocab mismatch guts the transfer
  value). Stem channel = shared-encoder feature concat (ablated at pilot
  scale on free render stems).
- **Recipe**: clean warmup → full variant mix pretrain; staged
  transfer-guarded MERT unfreeze; real fine-tune = mixed replay with real
  oversampled. Greedy decoding, headers predicted; checkpoint selection on a
  frozen cheap val subset (settles Phase 5's open question).
- **Gated milestone sequence** M0–M6: plumbing sanity → **representation
  pilot with a pre-committed numeric gate** (canonical synthetic; Tab F1
  ≥ 60 % and ≥ 0.75× oracle, else the Phase 3 performance-time fallback
  review) → bake-off → full pretrain → **transfer measurement** (owns the
  Phase 4 VST gate and Phase 2 CTC/data gate via pre-committed discriminator
  logic) → real fine-tune (headline Tab F1) → **human-correlation
  checkpoint** (success bar: median blinded recognizability ≥ 3/5), required
  before Phase 7 iteration.

### Phase 7 — Scale & iterate

The loop that actually produces quality: error analysis → targeted fixes →
retrain.

- Levers, roughly in expected-value order: more real aligned data (Phase 1
  feeds this), better alignment precision, augmentation breadth, model size
  (cloud runs enter here), representation refinements, source-separation
  input.
- Extend scope when guitar is strong: **bass** first (nearly free — same
  format, cleaner in the mix), then evaluate drums/vocals demand.

### Phase 8 — From model output to readable tabs

- Phase 3 chose score-time output, so this is mostly validation plus
  stitching: cross-window track matching, header (tuning/capo) majority
  voting, anchor-based window merging. (Beat-tracking + quantization only if
  Phase 6 ever falls back to performance-time.)
- Playability post-processing (fret-assignment sanity, impossible-stretch
  repair), key/tuning inference.
- Export: tokens → `.gp` (e.g. via PyGuitarPro / gpif writer) + MusicXML;
  in-browser rendering via alphaTab.

### Phase 9 — Product: upload mp3 → get tabs

- Inference pipeline: upload → (separation) → chunked transcription →
  stitching → quantization → export/render.
- Simple web service (the team already runs FastAPI); GPU inference sized for
  ~real-time or better per song.
- Product decisions deferred until the model earns them: hosting, accounts,
  pricing, batch vs interactive.

## Cross-cutting concerns

- **Reproducibility**: immutable dataset snapshots + manifests; every training
  run pins a snapshot hash.
- **Legal/ethical**: UG tabs and YouTube audio are third-party content — fine
  as private research/training inputs; the *dataset itself is never
  redistributed*. Revisit before any commercial launch.
- **New sub-projects follow the repo pattern**: decoupled directories
  (`dataset-py`/`model-py`-style) communicating through explicit contracts,
  documented in the map, with the shared score-model library (Phase 2a) as the
  one deliberate shared dependency among the ML-side projects.

## Sequencing & next steps

```
Phase 1 (scale corpus) ─────────────────────────────────────▶ continuous
Phase 0 (audit) ─▶ Phase 2 (score model + alignment) ─▶ Phase 3 (tokenizer)
                                        │                     │
                                        └──▶ Phase 4 (synth engine)
                                                              │
                              Phase 5 (eval harness) ─▶ Phase 6 (baseline)
                                                              │
                                    Phase 7 (iterate) ─▶ Phase 8 ─▶ Phase 9
```

Next planning sessions expand phases into detailed designs, in this order:
**Phase 0** (cheap, informs everything — done, see
[`plans/phase_0.md`](../plans/phase_0.md)), **Phase 2** (highest risk — done,
see [`plans/phase_2.md`](../plans/phase_2.md)), **Phase 3** (most
consequential design — done, see [`plans/phase_3.md`](../plans/phase_3.md)),
**Phase 4** (synth engine — done, see
[`plans/phase_4.md`](../plans/phase_4.md)), **Phase 5** (eval harness —
done, see [`plans/phase_5.md`](../plans/phase_5.md)), **Phase 6** (baseline
model — done, see [`plans/phase_6.md`](../plans/phase_6.md)). Phases 0 and 2–6 are now
fully planned; Phase 1 is continuous ops, and Phases 7–9 are expanded when
Phase 6's implementation produces the evidence they depend on. Implementation
proceeds in dependency order: Phase 0 → 2 → 3 → 4 → 5 → 6, with Phase 1
continuous.
