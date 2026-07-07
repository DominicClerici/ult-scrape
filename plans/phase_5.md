# Phase 5 — Evaluation harness

> Expanded from [the roadmap](../docs/roadmap.md#phase-5--evaluation-harness-before-serious-training)
> in the 2026-07-02 planning session. Decisions here are **binding inputs** to
> later phases. Prior art leaned on: **mir_eval** (transcription matching
> conventions, ±50 ms onset tolerance), **MT3** (multi-instrument onset/pitch
> F1 reporting), and the repo's own Phase 2 discipline (calibrated,
> self-validated measurement before anything trusts the numbers).

## Goal & scope

Before any serious training, build the harness that makes every experiment
comparable and every number trustworthy:

- **Automatic metrics** over the Phase 3 token vocabulary: tab accuracy
  (string+fret), note/onset/pitch F1, technique F1, rhythm/bar accuracy,
  anchor timing error, header accuracy — each computed via an explicitly
  chosen note-correspondence and validated against known corruptions.
- **Frozen test sets**: real held-out audio (Phase 0 test split × Phase 2
  alignment) and synthetic (Phase 4 held-out-timbre renders), pinned in
  committed, versioned eval manifests.
- **A predictions contract** — the file format any inference system writes
  and the harness scores; the harness itself never loads a model.
- **Harness self-validation**: oracle ceilings, corruption-sensitivity CI,
  floor baselines — the scorecard must be proven honest before Phase 6 reads
  it.
- **The qualitative loop**: a static HTML review bundle per experiment
  (listen + read, GT vs prediction), plus the human-correlation protocol
  Phase 6 executes on its first real outputs.
- **Experiment tracking** (W&B as a thin sink over canonical local
  artifacts) from the first run.

**Out of scope:** model architecture, training, and inference code (Phase 6
— it *writes* predictions; this phase *scores* them); song-level stitched
evaluation and cross-window track matching (Phase 8 — but the predictions
contract is designed to carry a `song` mode later); executing the
human-correlation study (requires model outputs; protocol + tooling ship
here, execution is a required early-Phase-6 checkpoint); alignment-quality
measurement itself (Phase 2 owns aligner calibration; this phase *consumes*
tiers); note-diff coloring in the review bundle (designed-for stretch item,
not a deliverable).

**Sequencing note:** Phases 0/2/3/4 are planned but not yet implemented.
This phase consumes their *contracts*: the manifest + split (Phase 0),
`gpscore` 1.0 + alignment tiers (Phase 2), `gpscore.tokens` + `project()` +
dataset records (Phase 3), and `renders/` + eval-tagged assets (Phase 4).
The matcher, metrics, oracle/corruption CI, and the predictions contract
need only `gpscore` and can be built as soon as Phase 3's tokenizer exists;
freezing the real eval set additionally needs Phase 2b's alignment output.

## Inputs / outputs

**Consumes:**

- `manifest/manifest.jsonl` (Phase 0): split labels (test = artist-hash
  buckets 90–99, val = 85–89), verdicts.
- `manifest/alignment/<tab_id>.json` + `alignment.jsonl` (Phase 2b):
  segment tiers gate real-audio window eligibility and label timing facets.
- `gpscore` 1.0 (Phase 2a) + `gpscore.tokens` (Phase 3): document model,
  `performance()`, tokenizer/detokenizer, `project()` — the comparison
  basis, per Phase 3's locked round-trip contract.
- `dataset/<snapshot>/` (Phase 3): symbolic dumps + audio for eval songs;
  eval windows are cut against a pinned snapshot.
- `renders/<tab_id>/<variant>/` + `render_meta.json` (Phase 4): synthetic
  eval audio; `realized.note_onsets` and the realized bar grid are the exact
  timing GT; eval-tagged assets define the held-out-timbre pool.
- `predictions.jsonl` (Phase 6+, the contract defined here).

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Eval harness | `eval-py/` | Code — house-pattern CLI + importable metrics library (`tabeval`) |
| Frozen eval sets | `eval-py/eval_sets/real_test_v1.json`, `synth_test_v1.json` (+ `*_val_v1.json`) | **Committed** — versioned window lists with fingerprints; drift only by explicit version bump |
| Scorecard | `<run_dir>/eval/scorecard.json` | Derived — all metrics × facets + versions/fingerprints; the canonical record of a run |
| Per-window verdicts | `<run_dir>/eval/windows.jsonl` | Derived — per-window metrics + per-note match verdicts (feeds error analysis and diff coloring) |
| Review bundle | `<run_dir>/eval/review/index.html` (+ media) | Derived — static, self-contained qualitative review page |
| Human-eval protocol | `eval-py/docs/human_eval.md` + rating tooling | Committed — rubric, sampling, blinding; executed early Phase 6 |
| W&B sink | inside `eval-py` | Code — optional thin logger over scorecard.json |

**Later-phase consumers:** Phase 6 scores every experiment through this
harness (and imports `tabeval` for training-time val metrics); its
transfer-measurement and VST/CTC escalation gates read the scorecard's
synthetic/real and canonical/stochastic facet gaps. Phase 7's iteration loop
lives in the tracker + per-window verdicts. Phase 8 extends the contract
with a `song` mode and reuses the bundle for stitched review. Phase 9 scores
the production pipeline against the same frozen sets.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Eval unit | **Frozen bar-aligned windows (primary) + frozen offset-slice suite (secondary)**: harness deterministically pre-cuts window lists per test song, pinned in committed eval manifests; the slice suite re-offsets the same windows' audio starts by frozen sub-bar amounts, scored with first/last-bar exclusion | Bar-aligned only (masks bar-start dependence and anchor-desync failure until Phase 8); slices only (every metric inherits boundary-artifact noise; front-loads Phase 8 problems); song-level (needs stitching, a Phase 8 deliverable) | Windows match the training unit, so eval isolates transcription from segmentation; the slice suite measures the *gap* between training-matched and inference-realistic conditions — itself a diagnostic Phase 6 wants — for the cost of reusing the same windows. |
| Predictions contract | **Raw token sequences**: `predictions.jsonl` of `{window_id, tokens, …}`; harness detokenizes via a pinned `gpscore.tokens` version | Pre-decoded score fragments (forks detokenizer responsibility across projects; hides token-level failure diagnostics) | One canonical, versioned detokenizer; malformed-span rate and decoder-health diagnostics come free; the harness stays model- and framework-agnostic. |
| Note correspondence | **Dual, symbolic primary**: (1) symbolic — DP bar alignment (Needleman–Wunsch over bar-content similarity) then exact rational within-bar onset matching — feeds tab/technique/rhythm metrics on *all* eval data; (2) time-domain mir_eval matching (±50 ms onset + pitch) feeds onset/pitch F1 on synthetic always and real `onset_grade` segments only; anchor quality scored separately | Time-domain only (real eval shrinks to `onset_grade`; anchor errors corrupt notation metrics — conflates the score-time bet's most likely failure with wrong notes); symbolic only (no audio-grounded F1; predicted timing never validated against the recording) | Notation metrics must be independent of both GT alignment tier (else `beat_grade` data is trainable but un-evaluable) and predicted-anchor quality (else desync masquerades as wrong notes). DP bar alignment prices bar insertions/deletions as their own error class instead of cascading. |
| Track assignment | **Hungarian (optimal bipartite) on pairwise note-overlap similarity** for note-level metrics; track-count error and order accuracy reported as separate metrics | Strict positional TRACK⟨i⟩↔GT-i (a consistent swap of two rhythm guitars zeroes both tracks despite a usable tab) | One identity mistake costs one metric, not the window; positional discipline (the trained behavior) stays visible in the order metric. |
| Headline metric | **Tab F1 (string+fret exact, symbolic correspondence) on the real held-out test set**; everything else is diagnostic columns on the same scorecard | Weighted composite (arbitrary weights, hides which sub-skill moved, invites Goodharting); no headline (an implicit one emerges anyway — better chosen deliberately) | It is the product goal, computable on all tiers, and hard to game: it requires note existence, pitch, and fingering simultaneously. |
| Aggregation | **Song-macro**: windows average per song, songs count equally; window-level distributions retained in `windows.jsonl` | Window-micro (smoother with ~50 test songs, but a 6-minute prog song counts triple a 2-minute punk song) | Matches "how many songs come out right"; per-window detail keeps the statistics inspectable. |
| Real-test tier policy | **Both `onset_grade` and `beat_grade` windows count in the headline**; per-tier facet columns always reported beside it | `onset_grade` only (strictest purity, but shrinks the set and biases it toward the clean/acoustic easy stratum) | Symbolic notation GT is tab-derived and tier-independent; `beat_grade` only means coarse window *placement* (±250 ms at edges of a ~20 s window). Facets keep purity visible; the headline stays representative of hard mixes. |
| Synthetic test composition | **Canonical variant 0 + 2 stochastic variants per test song**, stochastic variants rendered exclusively with eval-tagged (held-out) timbres; seeds frozen in the eval manifest | Canonical only (loses the timbre-generalization measurement that gates the VST escalation); +4 variants (~2× synthetic eval compute per experiment for marginal variance insight) | ≈150 test song-renders: cheap enough to run every experiment; variance across held-out timbres visible. Bumping later is a versioned eval-set change. Settles Phase 4's open question. |
| Eval-set freezing | **Committed, versioned eval manifests** (`eval-py/eval_sets/*.json`): window IDs, bar ranges, audio spans, facets, input fingerprints; scorecards record the eval-set version; aligner re-runs / corpus growth produce a *new* version by explicit act | Regenerate-on-the-fly from manifest+alignment (silent drift kills cross-experiment comparability — the exact failure the phase exists to prevent) | Comparability is the product; git history is the audit trail. Val sets (buckets 85–89) frozen the same way for Phase 6 checkpoint selection. |
| Harness self-validation | **Three-part, in CI**: oracle ceiling (tokenize GT → detokenize → score ≈ 100 % modulo projection, corpus-wide); corruption sensitivity (injected note-drops / fret-shifts / bar-deletes / anchor-jitter / flag-strips must move their metric monotonically and *selectively*); floor baselines (empty prediction, naive most-common-bar) | Trust the implementation (the unvalidated-measurement failure that sank aligner attempt #1, transplanted to eval) | The corruption suite proves the scorecard can't lie about which sub-skill broke; floors give Phase 6's first numbers context. |
| Human-correlation check | **Protocol + tooling ship in Phase 5; execution is a required early-Phase-6 checkpoint** (rubric, blinded sampling, rank-correlation of ratings vs Tab F1) before metric-driven iteration begins | Fully defer to Phase 6 (the "metrics don't track perceived quality" risk left to the phase busy training — protocol design deferred is usually protocol never) | Phase 5 has no model outputs to rate, so execution *cannot* happen here; designing it here keeps eval ownership where it belongs. |
| Qualitative loop | **Static self-contained HTML bundle per experiment**: sampled windows with A/B/C audio players (real audio / GT render / prediction render, canonical recipe via `render-py`) + side-by-side notation via **alphaTab**; per-note diff coloring is a designed-for stretch (verdicts exported in `windows.jsonl` regardless) | Minimal file dumps (high-friction review doesn't happen); diff coloring as hard requirement (alphaTab styling integration risk could slip the phase) | Low-friction listening+reading is what makes the qualitative loop real; the renderer and `.gp` writer already exist by contract, so the bundle is mostly assembly. |
| Project layout | **Single `eval-py/`**: house-pattern CLI (`freeze` / `score` / `report` / `bundle`) + importable `tabeval` library (matcher, metrics); depends on `gpscore`; harness is model-free (scores prediction files, never loads a model) | `gpscore.eval` + separate CLI (bloats the one shared dependency with mir_eval/alignment/audio concerns); metrics inside Phase 6's training repo (forks val-curve vs scorecard implementations) | Mirrors `dataset-py`'s CLI+library precedent: Phase 6 imports `tabeval` for training-time val metrics — one metric implementation everywhere. `gpscore` stays strictly representation. |
| Experiment tracking | **W&B as a thin optional sink; local `scorecard.json` is canonical** | MLflow local (self-hosted maintenance, weaker comparison UI); files only (cross-run comparison manual exactly when Phase 7 needs it) | Zero-maintenance, best run comparison, free personal tier; since the local artifact is the source of truth, outage/lock-in costs nothing and the choice is reversible. |

## Design

### `eval-py/` — the harness

House-pattern project (mirrors `dataset-py`): CLI + importable `tabeval`
package. Depends on `gpscore` (+ `mir_eval`, `numpy`, `soundfile`); invokes
`render-py render-file` **as a subprocess** for bundle audio (a tool
dependency, not an import — eval-py never loads the synthesis stack); reads
`manifest/`, `dataset/`, `renders/`; never writes into any of them. Python
≥ 3.13 (repo convention).

| Module | Responsibility |
|---|---|
| `tabeval/contract.py` | Predictions schema: `predictions.jsonl` — `{window_id, tokens: [int], model_id?, logprobs?}`; loader + validation; window-ID scheme (`<tab_id>:<eval_set>:<index>`). Designed to gain a `song` mode (Phase 8) without breaking `window` mode. |
| `tabeval/matching.py` | Symbolic correspondence: DP bar alignment over bar-content similarity → per-(bar, track) exact rational within-bar onset matching; Hungarian track assignment on note-overlap; emits per-note verdicts (`matched` / `wrong_fret` / `wrong_pitch` / `missing` / `extra`) and bar insert/delete ops. |
| `tabeval/timing.py` | Time-domain side: predicted note onsets from predicted anchors + within-bar symbolic accumulation; GT onsets from `realized.note_onsets` (synthetic) or the alignment warp (real, `onset_grade` only); mir_eval transcription matching; anchor error (median + P90 abs error vs GT bar onsets, % within one anchor bin). |
| `tabeval/metrics.py` | Metric definitions over match results (table below); song-macro aggregation; facet slicing. |
| `tabeval/oracle.py` | Oracle ceiling + corruption generators (note-drop, fret-shift ±1, bar-delete, anchor-jitter, flag-strip) for the sensitivity CI suite; floor baselines. |
| `app/freeze.py` | `eval freeze`: cut frozen window lists from a pinned dataset snapshot + alignment + renders; write versioned eval-set manifests (real/synth × val/test) including the offset-slice suite's frozen offsets. |
| `app/score.py` | `eval score --predictions … --eval-set …`: detokenize (pinned `gpscore.tokens`), match, compute, write `scorecard.json` + `windows.jsonl`. |
| `app/bundle.py` | `eval bundle`: sample windows (stratified: best/worst/random per facet), write GT + predicted fragments to `.gp` via `gpscore`, render their audio via `render-py render-file --recipe canonical` (subprocess), assemble the static HTML (embedded audio + alphaTab). |
| `app/report.py` | `eval report`: human-readable comparison across scorecards; regression check vs a named baseline run. |
| `app/wandb_sink.py` | Optional: push scorecard (+ bundle link) to W&B; never a data source. |
| `docs/human_eval.md` + `app/rate.py` | The human-correlation protocol: blinded sampling across the score range, listening rubric (recognizability / notation usefulness, 1–5 scales), rating capture, rank-correlation report. Executed early Phase 6. |

### The scorecard

Every metric × every facet; facets: `synth_canonical`, `synth_stochastic`
(held-out timbres), `real_onset_grade`, `real_beat_grade`, `real_all`
(headline), `slice_suite`. All numbers song-macro.

| Group | Metrics |
|---|---|
| Notation (symbolic matching, all facets) | **Tab F1** (onset+string+fret — headline on `real_all`); Note F1 (onset+pitch); duration exact-match rate; bar insert/delete rate; time-signature accuracy; technique F1 per Tier-1 flag + macro average (bends scored flag-level and 3-point-value-level); header metrics (tuning exact per track, capo, kind, window header-fully-correct rate); track-count error, track-order accuracy. |
| Timing (audio-grounded) | Anchor median/P90 error + % within one bin (synthetic exact; real `beat_grade`+); mir_eval onset/pitch F1 ±50 ms (synthetic + real `onset_grade`), offset F1 secondary. |
| Decoding health | Malformed-span rate, detokenizer diagnostic counts, empty/failed-window rate, predicted token-length distribution. |
| Meta | Eval-set version, vocab/gpscore versions, dataset-snapshot + alignment fingerprints, prediction file hash. |

The synthetic-canonical vs synthetic-stochastic gap and the synthetic vs
real gap are first-class report lines — they are the discriminators Phase
6's VST- and CTC-escalation gates read.

### Frozen eval sets

- **Real test:** test-split songs, verdict `ok`, alignment `aligned`;
  bar-aligned windows fully covered by `onset_grade ∪ beat_grade`
  (training's rule), each tagged with its tier facet. Per-song coverage
  accounting (songs/duration excluded and why) is part of the manifest and
  every report — good scores can't hide thin coverage.
- **Slice suite:** the same windows with frozen random sub-bar audio-start
  offsets; scored with first/last-bar exclusion; reported as its own facet
  (the bar-alignment-dependence gap).
- **Synthetic test:** every test-split `parse_ok` song × {canonical,
  2 frozen-seed stochastic variants on eval-tagged assets}.
- **Val twins** of both, from buckets 85–89, for Phase 6 checkpoint
  selection (cheaper subsets allowed, same freezing discipline).

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Metrics don't track perceived quality (Phase 6's bar is "a guitarist recognizes the song") | Human-correlation protocol shipped now, executed as a required early-Phase-6 checkpoint; rank-correlation reported before metric-driven iteration begins; qualitative bundle keeps ears in the loop weekly. |
| Harness itself mis-scores (unvalidated-measurement failure, aligner-attempt-#1 style) | Oracle ceiling + corruption-sensitivity + floor baselines in CI; any metric change must keep the corruption suite's monotonicity/selectivity assertions green. |
| DP bar alignment mis-pairs bars on degenerate predictions (e.g. all-rest output) | Floor baselines exercise exactly these inputs in CI; bar-alignment cost/ops are exported per window so pathological alignments are inspectable; empty/failed windows scored as zero-recall, never dropped. |
| `beat_grade` window-placement slop contaminates headline audio | Bounded by construction (±250 ms on ~20 s windows); per-tier facets expose any systematic onset_grade↔beat_grade score gap; policy is re-cuttable (a new eval-set version) if the gap proves large. |
| Eval-set drift destroys cross-experiment comparability | Committed versioned manifests + fingerprints; scorecards refuse to compare across eval-set versions without an explicit flag. |
| Real test set turns out thin after Phase 2 (few aligned test songs) | Coverage accounting makes it visible immediately; synthetic test carries interim signal; feeds Phase 1 re-enrichment and the Phase 2 escalation review rather than being papered over. |
| alphaTab/bundle work balloons | Bundle is assembly of existing contract pieces (`render-py` audio, `gpscore` `.gp` writer); diff coloring pre-declared a stretch; minimal file-dump fallback is a strict subset of the bundle code. |
| Two matching implementations drift apart | Both consume the same detokenized fragments and share note/verdict data structures; corruption CI covers both paths. |

## Acceptance criteria

- **Oracle ceiling:** tokenize→detokenize→score yields 100 % (modulo the
  modeled projection) on every notation metric over all parseable corpus
  scores; anchor error ≈ 0 on synthetic oracle input.
- **Corruption CI green:** each injected corruption moves its target metric
  monotonically with corruption rate and leaves non-target metrics within
  tolerance (selectivity), for all five corruption families.
- Frozen eval manifests committed (real/synth × val/test + slice suite),
  fingerprinted, with coverage accounting; regeneration with unchanged
  inputs is byte-identical.
- `eval score` runs end-to-end on a floor-baseline predictions file over
  both test sets, producing a complete `scorecard.json` + `windows.jsonl`;
  scores land at sane floor values.
- Slice-suite path exercised: same baseline scored under offset-slice
  conditions with edge-bar exclusion applied.
- Review bundle generated for a sample run: opens locally, audio A/B/C
  plays, alphaTab renders GT and predicted notation side by side, fully
  offline/self-contained.
- Human-eval protocol documented (rubric, sampling, blinding, correlation
  analysis) and its rating tooling runs on sample data.
- W&B sink demonstrated on one run; deleting W&B loses nothing (local
  artifacts complete).
- Unit tests deterministic and network-free by default; audio/render paths
  behind the `integration` marker (repo convention).
- Docs current per CLAUDE.md: `docs/eval-py/overview.md` written,
  `OVERVIEW.md` map + roadmap updated.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Song-level stitched eval (`song` mode) | Phase 8 scope; `contract.py` reserves the mode; window metrics are the Phase 6/7 workhorse. |
| Per-note diff coloring in the bundle | Verdicts already exported in `windows.jsonl`; coloring is purely a rendering feature over existing data. |
| Exact corruption rates, DP alignment costs, bar-similarity function details, slice-offset distribution | Tunable code constrained by the CI assertions (oracle = 100 %, corruption monotone/selective); no downstream contract depends on the constants. |
| Val-set size/cheapness tuning (subset for fast checkpoint selection) | Freezing discipline is fixed; the subset choice is a Phase 6 speed knob, versioned like any eval set. |
| Statistical significance machinery (bootstrap CIs over songs) | Additive report feature; per-song numbers in `windows.jsonl` already support it when Phase 7's close comparisons need it. |
| Human-eval execution | Impossible without model outputs; protocol shipped, execution pinned as an early-Phase-6 required checkpoint. |
| Offset F1 / sustain-quality metrics beyond duration match | Reported as secondary from day one; promoting them is a report change, not a matching change. |

## Open questions for later phases

- **Phase 6:** run the human-correlation checkpoint before metric-driven
  iteration; report the bar-aligned↔slice-suite gap (if large, inference-
  side segmentation needs attention earlier than Phase 8); use the
  synth-canonical vs synth-stochastic vs real facet gaps as the designed
  discriminators for the VST/CTC escalation gates; decide the cheap val
  subset for checkpoint selection.
- **Phase 7:** when experiments get close, add bootstrap CIs over songs to
  `eval report`; per-note verdicts + W&B are the error-analysis substrate —
  define the error-taxonomy dashboards there.
- **Phase 8:** extend the predictions contract with `song` mode (stitched
  output vs full-song GT); reuse the bundle for stitched review; define
  document-level metrics (header voting accuracy, cross-window consistency).
- **Phase 2 feedback:** the real-test coverage accounting is the concrete
  demand signal for the CTC-escalation review — if too few test songs
  align, that's evidence, recorded where Phase 2's gate expects it.
- **Phase 1:** test-split songs excluded for audio/alignment reasons feed
  the re-enrichment priority list (they're worth more than average songs —
  they're eval capacity).
