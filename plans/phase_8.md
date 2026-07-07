# Phase 8 — From model output to readable tabs

> Expanded from [the roadmap](../docs/roadmap.md#phase-8--from-model-output-to-readable-tabs)
> in the 2026-07-06 planning session. Decisions here are **binding inputs** to
> later phases.
>
> **Genre note (deliberate contrast with Phase 7):** Phase 8's evidence —
> Phase 6 M5's anchor-error and header-accuracy statistics — does not exist
> yet either, but unlike Phase 7 this phase's substance is *algorithms over
> symbolic data whose inputs can be simulated exactly*. Real corpus songs can
> be cut into windows, turned into **oracle predictions**, and corrupted with
> controlled noise (anchor jitter, header errors, malformed spans, dropped
> bars) at any severity. The plan is therefore a **concrete, oracle-first
> design**: everything is buildable and falsifiable before any checkpoint
> exists; only the final real-checkpoint integration stage is
> evidence-gated.

## Goal & scope

Everything between "the model transcribes ~20 s windows" and "a user gets a
complete, readable Guitar Pro file for a song":

- **The song-mode predictions contract** (the extension Phase 5 reserved) and
  `model-py predict --song`: slice arbitrary-length audio into overlapped
  windows, run the pinned checkpoint, write per-window token predictions.
- **`stitch-py/`** — a new, **model-free** project that assembles per-window
  predictions into one coherent `gpscore` Score: cross-window track
  clustering, anchor+content bar merging with overlap consensus, header
  (tuning/capo/kind) majority voting, tempo-map fitting, playability repair,
  key inference — then exports `.gp` via the Phase 3 `gpscore` writer.
- **Song-level evaluation** in `eval-py` (`song` mode): stitched song Tab F1,
  the **stitching tax**, and document-level diagnostics, over frozen real and
  synthetic full-song eval sets.
- **The degradation-curve rig and the feasibility bar** Phase 7 asked this
  phase to define: measured stitched-quality-vs-injected-noise curves,
  committed before M5's statistics exist, that turn "are real anchors good
  enough to stitch?" into a lookup instead of a judgment call.

**Out of scope:** serving, upload UX, accounts, hosting (Phase 9 — it wraps
the driver built here); source separation at inference (follows Phase 6's
stem-ablation verdict through `dataset-py`/`model-py`, not this project);
MusicXML export (deferred to demand — see locked decisions); beat-tracking +
quantization (contingency that exists only if Phase 6's M1 gate ever forced
the performance-time fallback — none of this design changes except the
stitcher's input granularity); model iteration and decoding-config search
(Phase 7); building the escalations (two-pass re-slicing, forced-header
re-decode) before their measured triggers fire.

**Sequencing note:** Phases 0–7 are planned but not yet implemented. This
plan consumes their *contracts*. Everything except the real-checkpoint
integration stage needs only `gpscore` (+ tokens/writer), `tabeval`, and the
corpus — it can be built in the sanctioned Phase 7 overlap window (or even
earlier) with oracle predictions. Real-checkpoint integration additionally
needs a pinned checkpoint (Phase 6 M5 or a Phase 7 release) and is gated on
the feasibility bar.

## Inputs / outputs

**Consumes:**

- `gpscore` + `gpscore.tokens` (Phases 2a/3): the error-tolerant detokenizer
  (malformed spans skipped with diagnostics — designed for this phase), the
  modeled projection, per-bar `ANCHOR` tokens (~100 ms bins), forceable
  window headers, canonical track order, the GP7/8 writer + `.gp` packaging
  (the export path — built in Phase 3, reused unchanged).
- `tabeval` (Phase 5): DP bar alignment, Hungarian track assignment, per-note
  verdict structures — imported for window↔window matching (precedented by
  `model-py`'s import).
- `model-py` (Phase 6): the prediction CLI this phase extends with `--song`;
  a pinned checkpoint + decoding config (from M5 or a Phase 7 release pin).
- M5 / Phase 7 release statistics: anchor error (median/P90, % within one
  bin), bar insert/delete rates, header accuracy, the slice-suite gap — the
  inputs the feasibility bar and the escalation triggers read.
- `render-py` renders (Phase 4): full-song synthetic audio for the synthetic
  song eval set; the canonical recipe for review-bundle audio.
- `manifest/` + `output/` + `dataset/` (Phases 0/2/3): alignment segments
  (real-song scoring coverage), frozen splits, GT symbolic dumps.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Stitcher/assembler | `stitch-py/` | Code — model-free CLI + library; depends on `gpscore` + `tabeval`, never loads a model |
| Song-mode predictions contract | `eval-py` (`tabeval/contract.py`) + `model-py predict --song` | Code — the reserved `song` mode made concrete |
| Transcribe driver | `stitch-py` (`transcribe` command) | Code — one-command audio → `.gp`, orchestrating `model-py` via subprocess; owns multi-pass escalations; the entry point Phase 9 wraps |
| Assembled outputs | `<run>/song/<song_id>/` → `song.gp`, `assembly_meta.json` | Derived — the tab + the machine-readable assembly record (votes, seams, repairs, tempo map, coverage) |
| Song-mode eval | `eval-py` (`--mode song`) + frozen `eval_sets/*_song_v1.json` | Code + committed manifests — document-level metrics incl. the stitching tax |
| Degradation curves + feasibility bar | `docs/stitch-py/degradation.md` (+ committed curve data) | The phase's decision instrument: stitched quality vs injected noise, per corruption family |

**Later-phase consumers:** Phase 9 wraps the driver (and reads its per-song
latency + the overlap-factor inference cost); Phase 7 reads song-mode
metrics as additional release columns and the stitching tax as a lever
discriminator (seam-dominated errors → the windowing escalation, not a model
lever); Phase 6's M5 report feeds the feasibility-bar lookup.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Plan genre | **Concrete, oracle-first design**: full pipeline locked now; development + validation on oracle/corrupted predictions cut from real corpus tabs; real-checkpoint integration is a final, feasibility-gated stage | Evidence-contingent framework (Phase 7 style — but stitching algorithms don't depend on *which* errors dominate; they must handle all of them); defer the session until M5 (forfeits the sanctioned overlap Phase 7 designed) | The inputs are symbolic and exactly simulable — the machinery is falsifiable today. Same spirit as Phase 5 building its corruption CI before any model existed. |
| Feasibility bar | **Derived from the measured degradation curve**: build the stitcher, measure stitched quality vs injected anchor-jitter / header-error / insert-delete severity, commit the curves; the bar = "M5's measured statistics land in the curve's acceptable region" (operationalized: interpolated stitching tax at M5's noise levels ≤ 2× the tax at zero noise; threshold amendable by documented amendment) | Pre-commit numeric thresholds now (invented constants without evidence — the move Phase 7 rejected for its gates); no bar (loses the designed go/no-go; risks integrating against unusable anchors) | No invented numbers: the committed curve is the pre-commit, and it is *our own machinery's* measured tolerance, not a guess about the model. |
| Pipeline decomposition | **Model-free `stitch-py` + `model-py predict --song`**: model-py slices audio + writes song-mode `predictions.jsonl`; stitch-py is purely symbolic (predictions → Score → `.gp`), never loads a model | Self-contained `transcribe-py` owning model invocation (drags torch/GPU into symbolic code; duplicates model-loading conventions; weakens oracle testing); stitching inside `model-py` (couples score assembly to the training codebase, against the repo pattern and the `eval-py` precedent) | Oracle and corrupted predictions are just other producers of the same file — the oracle-first strategy falls out of the seam placement. Filesystem-mediated, like every project boundary in the repo. |
| Windowing strategy | **Single-pass fixed-duration overlapped slices** (~20 s windows, ~50 % overlap at pilot; edge-bar trimming; both tunables set by the curve rig); **two-pass anchor-guided re-slicing pre-designed as escalation**, triggered by a large measured slice-suite gap or seam-dominated song-eval errors | Two-pass as primary (2× inference + pass-1 error coupling paid before evidence); single-pass with no designed escalation (reactive design later, against repo discipline) | Same staged-escalation shape as Phase 2's CTC and Phase 4's VST: cheap thing built, expensive thing pre-designed, a named measurement pulls the trigger. |
| Overlap as redundancy | Overlap regions are consensus *and* repair material: a bar lost to a malformed span in one window is recovered from its neighbor | Minimal overlap purely for joining | Reframes the detokenizer's error tolerance from "don't crash" into "self-healing"; costs nothing beyond the overlap already paid for. |
| Track identity | **Adjacent-window Hungarian assignment** on header similarity + overlap-content similarity, canonical track order as tie-breaker, **chained into song-level track clusters** | Positional trust in canonical order (one tacet-dropped track misaligns every later track in that window); global clustering over all windows (more machinery, no failure mode chaining misses) | Handles track-count drift between windows; Hungarian-on-content already validated by Phase 5's design. |
| Overlap consensus | **Interior-preference rule**: prefer the version of a bar farther from its window's edge; per-seam disagreement rate exported as a diagnostic | Logprob-confidence weighting (model-coupled, unproven — a Phase 7-style lever if seam errors persist); first-window-wins (ignores the known edge-bar weakness) | Deterministic and model-free; motivated by exactly the evidence the slice suite was built to produce. |
| Matching machinery | **Reuse `tabeval`** (DP bar alignment, Hungarian, verdict structures) for window↔window matching | Independent implementation (eval independence, at the cost of two drifting DP/Hungarian implementations) | One corruption-CI-validated matcher; import precedented by `model-py`; shared-bug risk covered by the oracle stitching test + human spot-checks. |
| Header reconciliation | **Content-weighted majority vote per track cluster (v1)** + per-window mismatch diagnostics; **forced-header re-decode pre-designed as escalation** (headers are a decode prefix — Phase 3 made them forceable for this), triggered by M5 header stats or song-eval mismatch damage exceeding the curve's tolerance; merges with the two-pass escalation if both fire | Always vote + re-decode (2× decode cost before evidence; unmeasurable oracle-first); vote-only with no designed escalation (accepts header-swap pitch damage permanently) | A wrong-header window's frets may shift pitch when the header is swapped — damage the oracle rig can measure; the escalation eliminates it by construction when evidence says it matters. |
| Tempo map | **Piecewise-constant robust fit** over anchor-derived bar onsets: constant tempo per span, new tempo mark only on persistent shift (hysteresis), integer BPM | Per-bar tempo (a tempo mark on ~every bar, driven by 100 ms anchor quantization noise); single constant tempo (playback desyncs wherever songs actually change tempo) | Reads like a human tab, follows real tempo changes, smooths anchor-bin noise. Inherits the scrapped aligner's validated learning: tempo is mostly constant — look for discrete changes, not continuous drift. |
| Playability post-processing | **Detect + minimal repair**: validators fire on *hard* physical violations only (impossible chord stretch, sub-capo fret, off-neck fret); repair = smallest same-pitch re-fingering under the voted tuning; every repair logged in `assembly_meta.json`; a **repair-neutrality test** proves violation-free input passes untouched | Detect-only (ships physically impossible tabs; the roadmap names stretch repair in scope); full re-fingering optimization (needs the playability model Phase 3 deferred; overrides the fingering style the model learned from professional tabs) | The model's fingering style is trusted; only local impossibilities are touched, minimally, auditable. |
| Key inference | **Krumhansl–Schmuckler pitch-profile estimation** over the stitched notes; one key per song (v1) | Default C/Am (accidentals everywhere on the standard-notation staff) | Deterministic, tiny, fills a cosmetic notation field; tuning — the field that matters — is already owned by header voting. |
| Song-level headline | **Stitched song-level Tab F1 on real test songs** (song-macro, aligned-coverage) + the **stitching tax** (song F1 minus the same songs' window-level F1) as a first-class diagnostic | Song F1 only (bad checkpoint and bad stitcher indistinguishable); window F1 stays headline (cannot see seam errors, header damage, track-cluster failures — the phase's whole subject) | The tax isolates what the *pipeline* loses beyond model error — the number that says whether Phase 8 itself is done well, independent of checkpoint quality. |
| Real-song scoring coverage | **Score within aligned-segment coverage only**; excluded spans reported in coverage accounting | Whole-file scoring (punishes predictions for intros/solos/outros the GT tab doesn't contain) | Mirrors the training-window gating rule; Phase 2's segments map exactly the scoreable spans. |
| Synthetic song eval | **Included**: test-split full-song renders, canonical + 1–2 eval-tagged held-out-timbre variants (mirrors Phase 5's window composition) | Real songs only (loses the noise-free control) | Renders are already full songs — nearly free; perfect GT and exact structure make it the clean testbed where the stitching tax is measured without alignment noise. |
| MusicXML export | **Deferred to demand** (roadmap bullet trimmed accordingly) | Build now (a second, lossier dialect — weak tab/technique support — with no identified consumer) | alphaTab, Guitar Pro, and MuseScore all handle `.gp`; Phase 3 already called MusicXML a "Phase 8 nicety"; building later is additive on the stitched Score. |
| Driver | **`stitch-py transcribe <audio>`**: subprocess-orchestrates `model-py predict --song` then assembly; owns multi-pass escalation orchestration; the single entry point Phase 9 wraps | Phase 9 composes the steps (leaves multi-pass orchestration homeless; makes Phase 8's own end-to-end validation ad hoc) | stitch-py stays model-free in-process (subprocess boundary = the same filesystem contract); Phase 8 needs the end-to-end path for its own acceptance anyway. |

## Design

### The stitching problem, sized on the corpus (measured 2026-07-06)

Full pilot corpus (509 `.gpif`, 494 with audio): bars/song median **91**
(p10 57, p90 137, max 236); song duration median **210 s** (p10 154,
p90 278, max 759). At 20 s windows / 10 s hop: **~20 windows and ~19 seams
per median song** (p90 27 windows, max 75). Median bar ≈ 2.3 s, so one
~100 ms anchor bin ≈ 4 % of a bar — anchors disambiguate bar identity with
wide margin unless predicted-anchor drift approaches a full bar; the
degradation curve measures exactly where that breaks.

### Song-mode predictions contract (the Phase 5 reservation, made concrete)

`predictions.jsonl`, one line per window, extending the window-mode schema:

```jsonc
{ "song_id": "…", "window_index": 3,
  "audio_span_s": [30.0, 50.0],            // slice offsets in the source audio
  "tokens": [ … ],                          // raw token ids (window-mode field)
  "pass": 0,                                // 0 = primary; 1 = escalation re-decode
  "model_id": "…", "decode_config": "…",   // provenance
  "logprobs": [ … ]                         // optional (window-mode field)
}
```

Producers: `model-py predict --song` (real), `stitch-py oracle` (GT +
corruptions). The stitcher is **decoding-config-agnostic** (answers Phase
7's open question): constrained or unconstrained, it consumes tokens through
the same error-tolerant detokenizer; the driver records the config in
provenance.

### `stitch-py/` — the assembler

House-pattern project: CLI + importable library; depends on `gpscore` +
`tabeval`; reads predictions + manifests; writes only its own output dir.
Python ≥ 3.13 (repo convention). No torch, no GPU, no network.

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings: window/hop/edge-trim (tunables set by the curve rig), consensus + vote parameters, repair thresholds, paths. |
| `app/ingest.py` | Load + validate song-mode predictions; detokenize per window (error-tolerant, diagnostics kept); place windows on the absolute timeline from `audio_span_s` + predicted anchors. |
| `app/tracks.py` | Adjacent-window Hungarian assignment (header + overlap-content similarity, canonical-order tie-break) chained into song-level track clusters; cluster-level diagnostics. |
| `app/grid.py` | Global bar-grid construction: overlap bar correspondence (anchor proximity refined by `tabeval` DP bar alignment), interior-preference consensus, malformed-span recovery from neighbor windows, seam-disagreement export. |
| `app/headers.py` | Content-weighted majority vote per track cluster (tuning/capo/kind); per-window mismatch diagnostics; emits the forced-header prefix set for the escalation path. |
| `app/tempo.py` | Piecewise-constant robust tempo fit over stitched bar onsets (hysteresis, integer BPM); writes the tempo map into the Score. |
| `app/playability.py` | Hard-violation validators + minimal same-pitch repair; repair log. |
| `app/key.py` | Krumhansl–Schmuckler key estimation over stitched notes. |
| `app/export.py` | Stitched Score → `gpscore.write_gp` (`song.gp`) + `assembly_meta.json`. |
| `app/oracle.py` | The rig: cut GT songs into oracle song-mode predictions; corruption injectors (anchor jitter, header errors, bar insert/delete, malformed spans, dropped windows, track-count noise); degradation-curve sweep driver. |
| `app/transcribe.py` | The driver: audio → (`model-py predict --song`, subprocess) → assemble → export; orchestrates escalation passes when configured. |

Assembly pipeline per song: ingest → detokenize → timeline placement →
track clustering → header vote (+ optional escalation re-decode) → bar-grid
consensus → tempo fit → playability pass → key → export. Every stage
appends to `assembly_meta.json`: track clusters, header votes + mismatches,
per-seam disagreement rates, recovered/dropped bars, repairs, tempo map,
coverage, timing. Phase 9 surfaces this record; song-mode eval consumes
parts of it.

### The degradation-curve rig (the phase's decision instrument)

For each corruption family × severity grid: generate corrupted oracle
predictions over a fixed corpus sample → assemble → score song-mode vs GT →
plot stitched Tab F1 and stitching tax vs severity. Families: anchor jitter
(σ in ms), header error rate, bar insert/delete rate, malformed-span rate,
dropped-window rate, track-count noise. Curves + the fixed sample manifest
are committed (`docs/stitch-py/degradation.md`). Uses: (i) set overlap /
edge-trim / consensus tunables (choose the config with the flattest curves);
(ii) the **feasibility bar** — when M5 publishes real anchor/header
statistics, read the curve at those coordinates; interpolated stitching tax
≤ 2× the zero-noise tax ⇒ integrate; worse ⇒ escalation review (two-pass /
forced-header) *before* integration effort; (iii) regression CI — a small
curve subset asserted monotone, Phase 5-style.

### Escalation contracts (pre-designed, evidence-triggered)

- **Two-pass anchor-guided re-slicing** (trigger: large M5 slice-suite gap,
  or song-eval seam errors dominating the taxonomy): pass 1 as today →
  stitched coarse bar grid → re-slice audio at predicted barlines (windows
  now bar-aligned, in-distribution) → pass 2 predictions → assemble as
  usual. Driver-level change only; contract carries `pass: 1`.
- **Forced-header re-decode** (trigger: M5 header accuracy, or measured
  header-mismatch damage above the curve's tolerance): after voting,
  re-decode all windows with the voted header forced as decode prefix
  (Phase 3 guarantee); assembly then sees header-consistent windows. If both
  escalations fire they merge into one combined second pass.

### `eval-py` additions (`song` mode)

`eval score --mode song`: input = assembled Scores (or the driver's output
dir) + GT; symbolic matching extends the window machinery to whole songs
(DP bar alignment over the full grid — same `tabeval` code path). New
document-level metrics: song Tab F1 (headline, real test, song-macro,
aligned-coverage), **stitching tax**, bar-count error, seam-local error rate
(errors within N bars of a seam vs interior), header-voting accuracy,
track-cluster accuracy (count + identity), tempo-map error (vs realized grid
on synthetic; vs alignment warp on real), repair counts. Frozen song
eval-sets committed as `eval_sets/real_test_song_v1.json` +
`synth_test_song_v1.json` (Phase 5 versioning discipline). The review bundle
gains a song view: full-song alphaTab rendering + audio, reusing the
existing bundle machinery.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Real anchor drift too large to identify bar correspondence (the score-time bet's tail risk lands here) | The degradation curve measures the tolerance *before* integration; the feasibility bar blocks wasted integration effort; the two-pass escalation is the pre-designed remedy; median bar ≈ 2.3 s vs 100 ms bins gives wide a-priori margin. |
| Track-count instability across windows corrupts clustering | Hungarian + chaining designed for count drift; track-count-noise corruption family quantifies tolerance; cluster diagnostics in `assembly_meta.json` localize failures. |
| Seam errors dominate (duplicated/dropped bars at joins) | Seam-local error rate is a first-class metric; interior-preference + overlap redundancy attack it; persistent dominance is the named trigger for the two-pass escalation. |
| Header-swap pitch damage (vote wins, frets lose) | Measured by the header-error corruption family; per-window mismatch diagnostics; forced-header re-decode escalation eliminates it by construction. |
| Playability repair mangles correct predictions | Repair-neutrality test in CI (violation-free input must pass byte-identical); hard-violation-only policy; every repair logged. |
| Tempo map garbage → useless playback despite correct notes | Tempo-map error metric on synthetic songs (realized grid = exact GT); piecewise fit smooths bin noise; human listening spot-check in acceptance. |
| Shared `tabeval` bug hides stitcher errors from eval | Oracle stitching test asserts end-to-end exactness independent of metric code; human spot-checks (Guitar Pro + listening) are matcher-independent. |
| Slice-suite gap larger than eval suggested (windows at arbitrary offsets much worse) | M5 publishes the gap before integration; edge-trim + overlap absorb moderate gaps; the two-pass escalation absorbs large ones. |
| Scope creep into Phase 9 product territory | Out-of-scope list is explicit; the driver is a CLI, not a service; anything user-facing beyond `.gp` + meta is Phase 9's. |
| Oracle-first design validates machinery against unrealistic noise shapes | Corruption families chosen to mirror the M5 scorecard's measured axes (anchor error, insert/delete, header accuracy, malformed rate) so the curve's coordinates are exactly what M5 reports; curves re-checked against real error profiles at integration. |

## Acceptance criteria

- **Oracle stitching test**: uncorrupted oracle song-mode predictions →
  assembled Score equals the GT modeled projection (modulo windowing
  artifacts: edge-trim coverage accounted) for **100 % of parseable corpus
  songs**; anchors reproduce the GT bar grid; exported `.gp` files load.
- **Degradation curves committed** for all six corruption families over the
  fixed sample, with the feasibility-bar readout procedure documented; a
  curve-subset regression test in CI (monotone, Phase 5-style).
- **Repair-neutrality**: playability pass leaves violation-free corpus
  scores untouched (corpus-wide CI assertion); injected hard violations are
  detected and repaired pitch-exactly.
- Song-mode contract implemented end-to-end: `model-py predict --song`
  writes it (integration-gated), `stitch-py oracle` writes it, `eval score
  --mode song` consumes assembled output and produces the document-level
  scorecard incl. stitching tax; frozen song eval-sets committed (real +
  synthetic) with coverage accounting.
- Driver runs end-to-end: `stitch-py transcribe` on a real test-song audio
  file (oracle-backed before integration; checkpoint-backed after) produces
  `song.gp` + `assembly_meta.json`; per-song wall-clock reported (Phase 9's
  sizing input, including the overlap factor).
- **Human spot-check**: ≥ 10 stitched songs (mixed real/synthetic sources)
  opened in Guitar Pro / alphaTab and listened against source audio — no
  seam artifacts a reader notices, tempo map musically sane, headers
  correct.
- Real-checkpoint integration stage: executed only after the feasibility-bar
  readout (M5 stats vs the committed curves) is recorded; the verdict +
  first real song-mode scorecard land in `docs/stitch-py/`.
- Unit tests deterministic and network/GPU-free by default; audio/subprocess
  paths behind the `integration` marker (repo convention).
- Docs current per CLAUDE.md: `docs/stitch-py/overview.md` written,
  `OVERVIEW.md` map + roadmap updated (including the MusicXML trim).

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Overlap fraction, edge-trim width, consensus/vote weights, repair thresholds, tempo-fit hysteresis constants | Tunables the degradation rig exists to set; mechanisms and fields are locked; values recorded in config + `assembly_meta.json`. |
| Logprob-confidence consensus | A measured lever (Phase 7 style) if seam disagreement persists after the positional rule; contract already carries optional logprobs. |
| Building the two escalations (two-pass re-slice, forced-header re-decode) | Pre-designed with named measured triggers; building before evidence inverts the repo's discipline. |
| MusicXML export | No identified consumer; additive on the stitched Score whenever demand appears. |
| Full re-fingering / playability optimization | Needs the playability model Phase 3 deferred; the model's learned fingering style is the v1 bet. |
| Per-section key changes | One key per song suffices for a cosmetic field; additive later. |
| Feasibility-bar threshold (2× zero-noise tax) refinement | Amendable by documented amendment once the curves exist and the number can be argued from data. |
| Streaming/progressive assembly (emit bars as windows decode) | Phase 9 UX concern; the assembler is batch by design and fast (symbolic only). |

## Open questions for later phases

- **Phase 6 (M5)**: report anchor error, bar insert/delete, header accuracy,
  and malformed rate in the units the degradation curves consume (they
  already appear in Phase 5's scorecard — this is a confirmation, not new
  work); publish the slice-suite gap prominently — it is this phase's
  windowing-escalation trigger.
- **Phase 7**: adopt song-mode metrics as additional release columns once
  integration lands; treat a seam-dominated error taxonomy as pointing at
  *this* phase's escalations, not at model levers; the stitching tax is the
  discriminator.
- **Phase 9**: wraps `stitch-py transcribe`; reads per-song latency + the
  overlap inference factor for service sizing; header *forcing* as a user
  feature (user declares tuning → skip voting, force everywhere) is a
  product decision built on the same forceable-prefix mechanism; upload
  formats, separation toggle, and progress UX are its scope.
- **Phase 5**: the song-mode contract + eval-set versions land in `eval-py`
  — same freezing discipline; no change to window-mode artifacts.
