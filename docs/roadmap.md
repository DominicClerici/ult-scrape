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

Two sub-problems:

**2a. Symbolic extraction.** A robust `.gpif` → internal score model parser:
notes with string/fret/tick/duration, tempo map, tracks, techniques, repeats
/ jumps expanded into linear time. This is needed by *everything* downstream
(tokenizer, synthesizer, aligner, eval) and must be a single shared library.
Phase 0 already seeds this library (`score-py/`, structure-scoped: tracks,
tunings, tempo map, repeat expansion, aggregate counts); Phase 2a designs the
note-level model and owns refactoring the internals toward a 1.0 API.

**2b. Tab ↔ real-audio alignment.** Open design problem. The scrapped
`aligner-py` (synth render + subsequence DTW) surfaced the real failure modes,
which the redesign must treat as requirements:

- performances contain **non-tab material** (intros/outros/solos) → subsequence
  or gap-tolerant alignment, not global DTW;
- **drift-then-snap** pathologies in the warp path;
- **half/double-time** tempo confusions;
- need for **confidence metrics** (fit cost, path deviation, coverage) that
  gate pairs in/out of the training set rather than trusting every alignment.

Candidate directions to evaluate in the phase design doc: improved
feature/DTW stack; beat-tracking + measure-level anchoring; training a small
alignment model (CTC forced alignment of the tab's pitch sequence against the
audio, as speech does with text); or a formulation that needs only *coarse*
alignment (see Phase 3's score-time output option). Imperfect coverage is
acceptable — a high-precision aligned subset beats a high-recall noisy one.

### Phase 3 — Representation: tokenizer + dataset builder

The single most consequential design choice. Requirements:

- Expresses **string+fret** (not just pitch), tuning/capo context, note
  durations, and a prioritized subset of techniques.
- **Multi-track-capable** (track/instrument tokens) even though v1 emits only
  guitar.
- Decodable back to a valid score → `.gp` file (round-trip tested against the
  corpus: gpif → tokens → gpif should preserve what we claim to model).

Key open decision — **output time base**:

- *Performance-time events* (MT3-style, absolute timestamps): easier to train,
  needs beat-tracking + quantization post-processing to become readable tabs.
- *Score-time tokens* (bars/beats directly, DadaGP-style): the model output
  **is** the tab, and it may reduce dependence on fine-grained alignment — but
  it is a harder learning problem.

Prior art to mine rather than reinvent: DadaGP's GP token vocabulary, MT3's
event vocabulary. Also in this phase: chunking strategy (train on ~15–30 s
windows with stitching at inference), augmentation design (pitch-shift with
fret-aware label transposition, time-stretch, EQ/noise/reverb), and optional
**source separation** (Demucs) as an input channel — the "other"/guitar stem
both simplifies the task and maps cleanly onto per-track supervision later.

### Phase 4 — Synthetic data engine

- Batch-render every tab to audio: multiple soundfonts / amp+cab sims / mix
  perturbations per song; guitar-stem-only and full-mix variants.
- Perfect labels come free from the score model. Scale is bounded only by disk
  and render throughput.
- Output feeds the same dataset builder as real audio — synthetic vs real is
  just a manifest flag.

### Phase 5 — Evaluation harness (before serious training)

- Automatic metrics: onset/pitch F1 (`mir_eval`-style), tab accuracy
  (string+fret exact match), technique F1, and post-quantization bar/rhythm
  accuracy.
- Fixed held-out real-audio test set (from Phase 0) + a synthetic test set.
- A qualitative loop: render predicted tabs side-by-side with ground truth for
  human listening/reading review.
- Every experiment reports the same scorecard; experiment tracking (e.g. W&B)
  from the first run.

### Phase 6 — Baseline model (v1: guitar only)

- Architecture: encoder–decoder transformer, spectrogram (or pretrained audio
  encoder, e.g. MERT/Whisper-encoder) → tab tokens. Evaluate **fine-tuning an
  existing AMT model** (MT3 family) vs training a compact model from scratch on
  synthetic pretraining — pick empirically, sized for the local GPU.
- Training recipe: synthetic pretrain → real-audio fine-tune → eval on held-out
  real songs.
- Success bar for the phase: on held-out real audio, output that a guitarist
  recognizes as "the song, mostly right" — not perfection.

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

- If Phase 3 chose performance-time output: beat/downbeat tracking →
  quantization to bars → rhythm cleanup. If score-time: mostly validation.
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
[`plans/phase_0.md`](../plans/phase_0.md)), **Phase 2** (highest risk),
**Phase 3** (most consequential design), then 4–6 together as the training
substrate.
