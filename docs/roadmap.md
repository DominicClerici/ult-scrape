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
| Corpus size | **Scrape the entire official catalogue (~100k tabs)** via the existing pipeline, at its fixed slow rate (revised 2026-07-06 from the original 2k–10k target; see [Phase 1](../plans/phase_1.md)) | More real data is the highest-value input and the pipeline already exists; the catalogue is finite and uniform-quality, so selection adds nothing — order is the only lever. |
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

**Expanded: see [`plans/phase_1.md`](../plans/phase_1.md)** (2026-07-06).
Locked shape — a pure ops phase (policies + `scripts/` glue, no new
subsystems):

- The catalogue is **~100k official tabs**; scrape **all of it eventually**
  at the pipeline's fixed slow rate — no numeric target. Training runs are
  **manual** and train on everything available at each cut (until the corpus
  is very large, ≥50k).
- **Seeded-random enqueue order** (stable hash shuffle), so the corpus is an
  unbiased sample of the catalogue at every instant — diversity arrives in
  expectation, no distribution shift; `manifest/requests/` priorities jump
  the queue (re-enrichment consumed mechanically, discovery strata via
  facet-scoped discovery runs).
- Ops = one manual command (`scripts/maintain.sh`): decode → enrich (new
  arrivals only, trickle-paced) → regenerate manifest → **external-drive
  backup** of audio + the scraper/enricher DBs (the irreplaceable classes;
  `.xtz` stays in git LFS, renders are regenerable and not backed up).
- Snapshot = regenerate + **git-commit the manifest** before each training
  run: manifest hash = corpus snapshot ID, git history = retention.
- Re-enrichment: `retry-audio` script over Phase 0 `bad` verdicts + Phase 2
  audio-reason alignment failures + Phase 7 requests, **eval-split songs
  first**; retries keyed to `yt-dlp` upgrades.

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

**Expanded: see [`plans/phase_7.md`](../plans/phase_7.md)** (2026-07-06).
Deliberately an **evidence-contingent framework** (Phase 6's evidence doesn't
exist yet): the iteration *machinery* is locked; concrete priorities are
named slots M5/M6 fill. Locked shape:

- **Cycle protocol**: one headline lever per cycle (+ pilot-scale side
  ablations), tiered by cost (T0 decode-only / T1 fine-tune / T2 pretrain);
  acceptance only by **paired-bootstrap 95% CI over songs**; test set
  release-gated with logged consultations; val grows via versioned eval-set
  extensions; every cycle recorded in `docs/model-py/cycles/` + a JSONL
  registry (no new project — this is a process over `model-py`/`eval-py`).
- **Levers picked evidence-first** from a trigger-signature inventory
  (roadmap EV order is the tie-breaking prior); pre-wired M4 gates (VST →
  Phase 4, CTC → Phase 2) execute as decided, never re-argued; T2 cycles
  admitted only by fired trigger or measured T0/T1 saturation.
- **Cloud ladder**: rung 1 = one big GPU, identical single-GPU code (shard
  conversion + DDP only at an amendment-gated rung 2); ~$300 standing
  per-cycle cap with pre-stated estimates.
- **Corpus growth enters as data-lever cycles only** (snapshot bumps are
  themselves the tested change); error analysis feeds Phase 1 via
  `manifest/requests/`.
- **Bass** admitted once the human floor is met, as a normal T2 cycle;
  headline stays guitar Tab F1 with bass F1 separate; drums/vocals capped at
  a demand write-up.
- **Exit = saturation + floor**: two consecutive no-significant-gain cycles
  *and* median blinded recognizability ≥ 4/5; saturating below the floor
  forces an escalation review. **Phase 8 may start early in parallel**
  against a pinned interim checkpoint; Phase 7 continues as a background
  loop after exit (like Phase 1).

### Phase 8 — From model output to readable tabs

**Expanded: see [`plans/phase_8.md`](../plans/phase_8.md)** (2026-07-06).
Locked shape — a **concrete, oracle-first design** (unlike Phase 7's
framework: the stitcher's inputs are symbolic and exactly simulable, so the
machinery is buildable and falsifiable before any checkpoint exists):

- **Model-free `stitch-py/`** + `model-py predict --song` (the Phase 5
  `song`-mode contract made concrete): overlapped fixed-duration slices →
  per-window predictions → track clustering (Hungarian + canonical-order
  prior, chained), anchor+content bar merging with interior-preference
  overlap consensus (overlap doubles as malformed-span repair material),
  content-weighted header voting, piecewise-constant tempo fit,
  detect+minimal-repair playability, Krumhansl key inference → `.gp` via
  the Phase 3 writer. A `transcribe` driver (subprocess over `model-py`)
  is the entry point Phase 9 wraps. MusicXML deferred to demand.
- **Pre-designed escalations, evidence-triggered**: two-pass anchor-guided
  re-slicing (trigger: slice-suite gap / seam-dominated errors) and
  forced-header re-decode (trigger: header stats) — Phase 2/4-style.
- **Feasibility bar = committed degradation curves**: stitched quality vs
  injected anchor/header/structure noise, measured on oracle predictions;
  M5's real statistics are read off the curves to gate real-checkpoint
  integration — no invented thresholds.
- **Song-level eval in `eval-py`**: headline = stitched song Tab F1 (real
  test, aligned-coverage) + the **stitching tax** (song F1 − window F1 —
  isolates pipeline loss from model loss); frozen real + synthetic
  full-song eval sets. (Beat-tracking + quantization remain a contingency
  only if Phase 6's M1 gate had forced the performance-time fallback.)

### Phase 9 — Product: upload mp3 → get tabs

**Expanded: see [`plans/phase_9.md`](../plans/phase_9.md)** (2026-07-06).
Locked shape — concrete, oracle-first (Phase 8's genre: the whole service is
buildable checkpoint-free against the oracle-backed driver), with **sizing as
a named evidence slot** (M5 / Phase 7-exit per-window latency × the overlap
factor; "~real-time or better" is a measured report, not a requirement):

- **v1 = private tool with public-ready seams**: **`serve-py/`** — FastAPI +
  SQLite job queue + one GPU worker (the scraper's skeleton with the GPU as
  the scarce resource), on the training box behind a tunnel + shared token;
  an advisory GPU lease serializes jobs against Phase 7 background training.
- Pipeline per job: upload (any ffmpeg format) or YouTube URL → normalize →
  (Demucs only per Phase 6's stem verdict — pipeline config, never
  user-facing) → `stitch-py transcribe` (subprocess per job; a warm
  `model-py serve --spool` predictor is pre-designed, triggered by measured
  cold-start fraction) → result page: alphaTab render + playback, `.gp`
  download, honest diagnostics panel from `assembly_meta.json` (no invented
  confidence score). One user control: tuning/capo forcing (Phase 8's
  forced-header path). The old bullet's "quantization" step is trimmed —
  Phase 8 made it contingency-only.
- Jobs retained with full provenance (every job pins its checkpoint — any
  pinned Phase 7 registry checkpoint may serve) + per-job delete + a
  flag-bad-result feedback feed (`manifest/requests/feedback.jsonl`) into
  the Phase 7 background loop.
- **Public-exposure gate** (all four, together): legal review (incl. the
  YouTube path) + abuse controls/real auth + the measured sizing/cost
  readout + Phase 7's ≥ 4/5 human floor. Accounts, pricing, and batch tiers
  stay parked behind it — "the model earns them" made checkable.

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
model — done, see [`plans/phase_6.md`](../plans/phase_6.md)), **Phase 7**
(iteration framework — done, see [`plans/phase_7.md`](../plans/phase_7.md);
expanded as an evidence-contingent framework since Phase 6's evidence doesn't
exist yet), **Phase 8** (stitching/export — done, see
[`plans/phase_8.md`](../plans/phase_8.md); a concrete oracle-first design
whose machinery needs no checkpoint — real-checkpoint integration is gated
on its committed degradation curves read against Phase 6's M5 statistics),
**Phase 9** (product — done, see [`plans/phase_9.md`](../plans/phase_9.md);
same oracle-first genre: the service is buildable checkpoint-free, sizing is
an evidence slot, and the product decisions the roadmap deferred are parked
behind a defined public-exposure gate), and finally **Phase 1** (continuous
corpus ops — done, see [`plans/phase_1.md`](../plans/phase_1.md); planned
last since every other phase's deferrals defined its inputs: seeded-random
scrape order over the ~100k catalogue, manual maintenance/backup routine,
manifest-commit snapshots, and the feedback-loop consumers).
All ten phases are now fully planned.
Implementation proceeds in dependency order:
Phase 0 → 2 → 3 → 4 → 5 → 6 → 7, with Phase 1 continuous, Phase 8's
oracle-first machinery buildable any time after Phase 3 (its
real-checkpoint stage waits for M5), and Phase 9's service buildable any
time after Phase 8's driver exists (real serving waits for the first
pinned checkpoint).
