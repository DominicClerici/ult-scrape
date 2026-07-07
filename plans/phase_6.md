# Phase 6 — Baseline model (v1: guitar only)

> Expanded from [the roadmap](../docs/roadmap.md#phase-6--baseline-model-v1-guitar-only)
> in the 2026-07-02 planning session. Decisions here are **binding inputs** to
> later phases. Prior art leaned on: **MT3** (spectrogram encoder + token
> decoder AMT shape — the "commodity" architecture the roadmap assumes),
> **MERT** (music-domain SSL encoder at 24 kHz, strong on pitch-centric MIR
> tasks), and **SynthTab** (synthetic-pretrain → real-transfer for tab
> transcription, the shape of our curriculum and of the timbre-gap risk).

## Goal & scope

Train the first end-to-end model — audio window in, Phase 3 token sequence
out — through the pre-designed staged recipe: synthetic pretrain → real-audio
fine-tune → frozen-set evaluation. Beyond the checkpoint itself, this phase
**owns the decision gates earlier phases wired into it**:

- the **representation gate** (Phase 3's score-time bet, tested on canonical
  synthetic before anything expensive),
- the **synthetic→real transfer measurement** (Phase 4's VST-escalation gate
  and Phase 2's CTC-gate evidence),
- the **mix vs mix+stem ablation** (Phase 3's reserved N-channel contract),
- the **human-correlation checkpoint** (Phase 5's protocol, executed on the
  first real outputs before any metric-driven iteration).

Success bar (roadmap): on held-out real audio, output a guitarist recognizes
as "the song, mostly right" — operationalized at milestone M6 below.

**Out of scope:** the error-analysis → retrain iteration loop, model scaling,
cloud runs, beam/constrained decoding, bass output (Phase 7); song-level
stitching, header voting, playability post-processing, export UX (Phase 8);
serving/product (Phase 9); building the VST backend or the CTC aligner
themselves (this phase only produces the gate verdicts that trigger them —
Phases 4/2 own the builds).

**Sequencing note:** Phases 0/2/3/4/5 are planned but not yet implemented.
This phase consumes their *contracts*: dataset snapshots + dataloader library
(Phase 3), `renders/` + recipe metadata (Phase 4), frozen eval sets +
`predictions.jsonl` + `tabeval` (Phase 5), splits/verdicts (Phase 0),
alignment tiers (Phase 2b). Nothing in Phase 6 can run before those land;
its *design* depends only on their contracts.

## Inputs / outputs

**Consumes:**

- `dataset/<snapshot>/` + the `dataset-py` dataloader library (Phase 3):
  bar-aligned ~20 s windows sampled at load time, ≤ ~2k target tokens,
  tokenization via a pinned `gpscore.tokens`, 24 kHz mono FLAC, on-the-fly
  audio augs, N-channel input contract, `source: real|synthetic` + variant
  ids + recipe metadata (the curriculum filter key).
- `renders/` (Phase 4): synthetic records incl. `gtr_bus.flac` stems (free
  for the stem ablation) and eval-tagged held-out timbres.
- `eval-py` / `tabeval` (Phase 5): frozen eval manifests (real/synth ×
  val/test + slice suite), scorecard + facets, floor baselines, oracle
  ceiling, the human-eval protocol + rating tooling.
- `manifest/` (Phases 0/2b): splits, verdicts, alignment tiers (indirectly,
  via dataset records).

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Training/inference project | `model-py/` | Code — plain-PyTorch single-GPU training loop + prediction CLI |
| Run directories | `model-py/runs/<run_id>/` | Derived — config, pinned snapshot/vocab/git hashes, checkpoints, logs, eval outputs |
| Predictions | `runs/<run_id>/predictions/<eval_set>.jsonl` | Derived — Phase 5 contract; scored via `eval-py` |
| Gate reports | `runs/…/reports/` + `docs/model-py/phase6-report.md` | The phase's decision record: M1 representation verdict, bake-off, transfer measurement, VST/CTC gate verdicts, ablation, human-correlation result |
| Baseline checkpoint | `runs/<run_id>/checkpoints/` (pinned in the report) | Derived — the v1 model Phase 7 iterates on and Phase 8/9 build around |

**Later-phase consumers:** Phase 7 inherits the winning recipe, the
checkpoint, and the error-analysis surface (per-window verdicts + W&B);
Phase 8/9 inherit the inference model and its decoding conventions; Phases
2/4 receive their escalation-gate verdicts; Phase 1 receives data-starvation
evidence as re-enrichment priority.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Architecture strategy | **MERT-v1-95M (24 kHz) + fresh ~8-layer decoder as primary; compact from-scratch mel encoder-decoder as a pilot-scale control; MT3 fine-tuning dropped** | MT3-family fine-tune (vocab mismatch guts the transfer value — its decoder speaks performance-time MIDI events, not our score-time tab tokens; JAX/port friction; its edge is data efficiency and we are not data-poor); from-scratch only (gives up the real-audio feature-space head start exactly where the phase's #1 risk lives); MERT-only without control (loses the timbre-vs-representation discriminator a controlled pair provides) | The dominant risk is synthetic→real transfer, and an encoder pretrained on real music attacks it directly: synthetic pretraining then teaches the *decoder* the task rather than teaching the encoder what a guitar sounds like. Phase 3 chose 24 kHz records explicitly naming MERT. The from-scratch control makes the bake-off itself produce the first transfer data point: if MERT transfers and scratch doesn't, the gap is timbre-shaped (VST-gate evidence). |
| Empirical pick mechanics | **Bounded bake-off (M2)**: both candidates ~1–2 GPU-days on the same fixed synthetic subset; compared on synth-canonical val + a zero-shot real-val transfer probe; winner takes the full pretrain | Fully training all candidates (weeks of GPU for a choice pilot scale can make); paper-argument pick with no runs (the roadmap said *empirically*) | "Pick empirically" sized to a 16 GB local GPU; the loser is retired with its numbers recorded. |
| Stem channel mechanism | **Shared-weight encoder, one pass per channel, frame-wise feature concat → linear projection** to decoder width; mix-only = one pass | Feature sum/average (erases channel identity — the point of a stem); separate per-channel encoders (2× memory, no identified gain); waveform-domain only (deviates from Phase 3's reserved contract) | Ablation becomes a config flag; extensible to more channels (Phase 7 bass) with the same pattern. |
| Model shape | **Decoder ~40M trainable (d=512, 8 layers, 8 heads), 3k token context; conv downsampler ×3 on encoder frames (75 Hz → ~25 Hz) before cross-attention**; scale up only on measured underfit | d=768 × 10–12L ≈ 100M (halves throughput before any evidence the small one saturates); size as a bake-off axis (doubles pilot GPU-days); no downsampling (3× cross-attention cost for resolution far below the 100 ms anchor bins) | Windows cap at ~2k target tokens (Phase 3), so 3k context covers header + specials with margin. Unlimited synthetic data makes underfit unambiguous (train and val plateau together) — the scale-up trigger is measured, not guessed. Fits 16 GB bf16 with frozen encoder comfortably. |
| Compute envelope | **16 GB local GPU, bf16, multi-day (2–5 day) pretrain runs acceptable**; cloud deferred to Phase 7 | ~1-day cap (forces cloud immediately, against local-first strategy); weeks-long runs (poor iteration cadence for a *baseline* phase) | Matches the hardware on hand and the roadmap's local-GPU-first strategy; everything above is sized to it. |
| Pretrain curriculum | **Clean warmup → full mix**: first ~5 % of steps on canonical/clean-recipe variants, then the full variant mix + on-the-fly augs | Full mix from step 0 (simplest; kept as the fallback if the warmup measures useless); strict multi-stage clean→degraded (most schedule surface, weakest evidence of need) | One config field over Phase 4's recipe-metadata filter; honors the roadmap's curriculum sketch; droppable with evidence. |
| Real fine-tune policy | **Mixed replay**: real + synthetic in one stream, real oversampled to ~1:1 effective batch ratio to start (tuned on real val) | Pure real fine-tune (hundreds of songs, tier-gated — will overfit and forget the token grammar in hours); two-stage try-pure-then-replay (an extra stage to run for a predictable outcome) | Standard remedy for tiny fine-tune sets; keeps exact anchor supervision flowing from synthetic while real audio adapts timbre handling. |
| Encoder freeze/unfreeze | **Staged, transfer-guarded**: frozen until decoder val plateaus; then top ~4–6 MERT layers at 0.1× LR for the rest of pretrain and fine-tune; **revert if the real-val transfer probe degrades** | Frozen throughout (safest against synthetic-timbre overfit but leaves adaptation capacity unused); full unfreeze from step 0 (slowest, riskiest to the pretrained features) | Frozen-first gives fast cheap early epochs; the guard directly addresses the real risk of unfreezing — an encoder fine-tuned on FluidSynth renders losing real-audio generality. |
| M1 representation gate | **Numeric pre-commit + review zone**: pass if synth-canonical val **Tab F1 ≥ 60 % and ≥ 0.75× the oracle ceiling** with clean desync diagnostics (anchor error, bar insert/delete, malformed rate); below → structured fallback review with Phase 3's performance-time tokenizer swap on the table. Thresholds adjustable only by documented amendment | Evidence-review only (Phase 2/4 gate style — but M1 is a cheap fully-controlled experiment where a number is defensible, and a pre-commit resists rationalizing a marginal result); hard gate without review zone (a near-miss with obviously-fixable diagnostics would force a fallback prematurely) | This is Phase 3's designed "fails here ⇒ representation bug, not data bug" probe; the gate must fire *before* the multi-day pretrain spends. |
| VST/CTC escalation gates (M4) | **Pre-committed discriminator logic, evidence-review decision** (Phase 2/4 style): real ≪ synth-stochastic while synth-stochastic ≈ synth-canonical ⇒ timbre-shaped gap ⇒ VST case (Phase 4); early real-val overfit in fine-tune + thin real coverage accounting ⇒ data-starved ⇒ more-alignment/CTC case (Phase 2). Verdicts + evidence recorded in the phase report | Numeric pre-commits (facet-gap magnitudes are not predictable enough to bind honestly); no pre-wired logic (gate review degenerates into vibes — attempt-#1's failure mode) | The scorecard facets (Phase 5) were designed as exactly these discriminators; this writes down how they're read. |
| Stem ablation placement | **Pilot scale on synthetic**, beside/after M2, using free render stems (`gtr_bus.flac`); stems enter the real pipeline (Demucs via `dataset-py --stems`) only on a clear win | Fine-tune-stage ablation (needs Demucs up front; confounded by fine-tune noise on a small set); full pretrain ×2 (doubles the most expensive step for a question pilot scale can resolve) | Mix-only stays the default path; Phase 3's contract keeps the channel addition cheap if it ever wins. |
| Framework | **Plain PyTorch single-GPU loop**; HF `transformers` used only to load MERT weights at a **pinned revision hash** (`trust_remote_code` supply-chain guard) | PyTorch Lightning (abstractions fight custom replay mixing / staged schedules at single-GPU scale); HF Trainer (pays off only for HF-native models) | The phase's substance *is* the custom parts: replay-ratio batch mixing, curriculum filter, staged unfreeze, `tabeval` val hooks, deterministic resume. |
| Eval decoding | **Greedy, headers predicted (not forced)** | Beam search (a Phase 7 lever; adds a hyperparameter before a baseline exists); grammar-constrained decoding (hides malformed-span rate — a designed decoder-health diagnostic — exactly when it matters most) | Deterministic, fast over frozen sets, honest headline (header accuracy stays a measured skill; header *forcing* is a Phase 9 product feature). |
| Checkpoint selection | **Frozen cheap val subset** (~100–150 windows stratified across facets, versioned per Phase 5's eval-set discipline) scored with `tabeval` Tab F1 every N steps; full frozen val sets at stage ends; selection metric = real-val Tab F1 for fine-tune, synth-val for pretrain | Loss-based selection (loss and Tab F1 are known to diverge on structured outputs); full val every N steps (too slow to check often) | Settles Phase 5's open question ("decide the cheap val subset"); same freezing discipline so training-time numbers stay comparable across runs. |

## Design

### `model-py/` — the training/inference project

Decoupled project (repo pattern): depends on `gpscore`, `dataset-py`'s
dataloader library, and `tabeval`; reads `dataset/`, `renders/`, eval
manifests; writes only under `model-py/runs/`. Python ≥ 3.13 (3.12 fallback
if the torch stack lags, per the aligner precedent).

| Module | Responsibility |
|---|---|
| `app/config.py` | Run configs (TOML): stage definitions (warmup/pretrain/fine-tune), data filters (source, variant, recipe, split), model dims, replay ratio, unfreeze schedule, seeds. |
| `app/model.py` | Encoder wrapper (pinned MERT + per-channel shared passes + feature concat + projection + ×3 conv downsampler) and the ~40M transformer decoder (3k ctx, cross-attention). The from-scratch control swaps the encoder wrapper for a mel-CNN/transformer front. |
| `app/data.py` | Glue over the `dataset-py` dataloader: curriculum filter (recipe metadata), replay-ratio batch mixing (real oversampling), deterministic epoch seeding. |
| `app/train.py` | The loop: bf16, grad accumulation, checkpointing + exact resume, staged-unfreeze schedule, `tabeval` cheap-val hook every N steps, W&B thin sink (local artifacts canonical, per Phase 5). |
| `app/predict.py` | Greedy decode over a frozen eval manifest's windows → `predictions.jsonl` (Phase 5 contract); batch inference. |
| `app/report.py` | Gate-report assembly: M1 verdict, bake-off comparison, transfer-measurement facet gaps, ablation table — reading `eval-py` scorecards, never reimplementing metrics. |
| `runs/<run_id>/` | `config.toml`, pins (dataset-snapshot hash, vocab/gpscore versions, MERT revision, git SHA, seeds), checkpoints, logs, predictions, scorecards. |

Loss: standard cross-entropy over the token sequence, teacher forcing;
anchor tokens are ordinary vocabulary items (no auxiliary losses in v1).
Training windows come from the Phase 3 dataloader (bar-aligned, sampled per
epoch); eval windows come exclusively from frozen manifests — Phase 6 never
cuts its own eval windows, and inference-time slicing of arbitrary audio is
Phase 8/9's problem (the slice-suite facet measures our exposure to it).

### Milestones & gates

| # | Milestone | Gate / decision rule |
|---|---|---|
| M0 | Plumbing sanity: overfit one batch; oracle tokens through `predictions.jsonl` → scorecard; floor baselines reproduce Phase 5's numbers | No gate — proof the loop is closed end-to-end before any real run. |
| M1 | **Representation pilot**: small run, canonical-variant-0 synthetic only (~50–100 train songs), eval on synth-canonical val | **Pre-committed**: pass at Tab F1 ≥ 60 % and ≥ 0.75× oracle ceiling with clean desync diagnostics; else structured fallback review (performance-time tokenizer swap per Phase 3's safeguard — a tokenizer change, not a data rebuild). |
| M2 | **Bake-off**: MERT vs from-scratch control, same fixed synthetic subset, ~1–2 GPU-days each; **stem ablation** runs beside it at the same scale (mix vs mix+`gtr_bus`) | Winner by synth-canonical val Tab F1 *and* zero-shot real-val probe (transfer weighted when they disagree — transfer is the scarcer skill). Stem verdict: stems enter the real pipeline only on a clear win. |
| M3 | **Full synthetic pretrain**: winner config, all `parse_ok` tabs × variants, clean warmup → full mix, staged transfer-guarded unfreeze | Checkpoint selection on the cheap val subset; underfit check decides any model scale-up (documented amendment). |
| M4 | **Transfer measurement** (pre-fine-tune): full scorecards on synth-canonical / synth-stochastic / real val + per-technique error analysis | **VST gate** (Phase 4) and **CTC/data gate** (Phase 2) verdicts via the pre-committed discriminator logic; evidence recorded in the phase report. |
| M5 | **Real fine-tune** (mixed replay) → headline scorecard on frozen real test | Report Tab F1 headline + all facets; token-cap binding and bar-aligned↔slice-suite gap reported here (Phase 3/5 obligations). |
| M6 | **Human-correlation checkpoint**: execute Phase 5's protocol on M5 outputs | Required before any metric-driven iteration (Phase 7 entry condition). Success bar operationalized: median blinded recognizability rating ≥ 3/5 on sampled real-test windows, and rank-correlation of ratings vs Tab F1 reported. Rating < 3 with high Tab F1 ⇒ metric-validity review before iterating. |

### Reporting obligations inherited from earlier phases

All land in `docs/model-py/phase6-report.md` (+ scorecards): synthetic→real
transfer + VST verdict (Phase 4); CTC/data-starvation evidence (Phase 2);
representation-gate verdict (Phase 3); mix-vs-stem verdict (Phase 3); does
the ~2k token cap bind (Phase 3); slice-suite gap (Phase 5);
human-correlation result (Phase 5); whether tempo tokens look worth adding
(Phase 3 deferred observable); real-data demand signal → Phase 1
re-enrichment priorities.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Score-time decoding fails even on clean synthetic (the representation bet) | M1 fires first, cheap, with a pre-committed gate; fallback is a tokenizer swap (records store symbolic windows, Phase 3's safeguard); nothing expensive spends before the gate. |
| Synthetic→real transfer poor (the classic synth-corpus failure) | Measured, not argued: M2's zero-shot probe gives the first data point; M4's facet gaps discriminate timbre gap (→ VST escalation, Phase 4) from representation gap; MERT's real-audio features are the architectural hedge; the from-scratch control makes the diagnosis interpretable. |
| Unfreezing MERT on synthetic audio destroys real-audio generality | Transfer-guarded unfreeze: real-val probe watched at every checkpoint after unfreezing; revert on degradation (locked decision). |
| Real fine-tune data too thin after Phase 2 (few aligned songs) | Visible in coverage accounting before training; mixed replay reduces exposure; the shortfall itself is the CTC/data gate's evidence, routed to Phase 2's review and Phase 1's re-enrichment — not papered over. |
| 16 GB / days-per-run envelope too small for the pretrain the curriculum assumes | Frozen-encoder pretrain keeps step cost low; ~40M decoder sized for it; if underfit at full data says scale up, that's the documented cloud-run trigger (Phase 7's lever pulled early, with evidence). |
| Metrics don't track perceived quality | M6 is mandatory before iteration; rank correlation reported; divergence triggers a metric-validity review (Phase 5 owns the fix). |
| Val-set overfitting via frequent cheap-val checks | Cheap subset used for *selection* only; full frozen val at stage ends; test set touched only at M5/M6; all sets versioned (Phase 5 discipline). |
| Training-loop bugs masquerade as model failures | M0 closes the loop first (overfit-one-batch, oracle round-trip, floor reproduction); deterministic resume tested; runs pin every input hash. |
| MERT dependency risk (`trust_remote_code`, weight availability, license) | Revision hash pinned; weights cached locally at first fetch; license verified for private-research use at implementation start (legal posture per roadmap); the from-scratch control is the standing fallback architecture. |

## Acceptance criteria

- M0–M6 executed in order with artifacts; every gate verdict recorded with
  its evidence in the phase report (no silent gate skips).
- M1 verdict documented against the pre-committed thresholds.
- Bake-off report: both candidates' synth-val and real-probe numbers; winner
  justified; loser's config + numbers retained.
- Full synthetic pretrain completed on the winner; training curves + cheap-val
  Tab F1 logged; checkpoint selection reproducible from run artifacts.
- Transfer-measurement scorecards (synth-canonical / synth-stochastic / real
  val) produced via `eval-py`; **VST and CTC gate verdicts written** with the
  pre-committed discriminator logic applied.
- Real fine-tune completed; **headline Tab F1 on the frozen real test set
  reported** with all facets; token-cap and slice-suite obligations reported.
- Stem-ablation verdict recorded (pilot-scale synthetic numbers).
- Human-correlation checkpoint executed per Phase 5's protocol; rank
  correlation + success-bar verdict (median recognizability ≥ 3/5) reported.
- Reproducibility: every run dir pins dataset-snapshot hash, vocab/gpscore
  versions, MERT revision, git SHA, seeds; interrupted runs resume exactly;
  W&B optional with local artifacts complete (Phase 5 discipline).
- Unit tests deterministic and GPU/network-free by default (model shapes,
  batch mixing, curriculum filter, predict-file schema); training/inference
  paths behind the `integration` marker (repo convention).
- Docs current per CLAUDE.md: `docs/model-py/overview.md` written,
  `OVERVIEW.md` map + roadmap updated.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Optimizer constants (LR, schedule, warmup steps, batch/accumulation, label smoothing) | Tunable code; standard AdamW + cosine starting points; nothing downstream depends on them. |
| Exact warmup fraction, replay ratio, unfreeze depth/timing, cheap-val subset size | Config fields with locked *mechanisms*; values tuned on val and recorded per run. |
| Beam / constrained decoding, checkpoint averaging, LoRA-style tuning | Phase 7 levers over a baseline that must exist first; predictions contract unchanged. |
| Model scale-up (d768+, cloud) | Trigger is measured underfit at full data (documented amendment); Phase 7 owns scaling. |
| Demucs stems for real audio | Only on a stem-ablation win; `dataset-py --stems` contract already exists. |
| Auxiliary losses (anchor regression, CTC head) | v1 is plain CE by design; only reconsidered if M1/M4 diagnostics point at timing supervision specifically. |
| Tempo-token vocab addition | Phase 3 deferred observable; M5 error analysis reports whether it looks warranted; additive vocab change. |

## Open questions for later phases

- **Phase 7:** iteration priorities seeded by M5/M6 error analysis (per-note
  verdicts + W&B); model/cloud scaling trigger inherits M3's underfit
  evidence; beam/constrained decoding as first cheap levers; bass extension
  reuses the header `KIND` + track sections and the renderer unchanged.
- **Phase 8:** stitching consumes predicted anchors — M5 must report anchor
  error statistics on real audio (its feasibility input); window-header
  prediction accuracy is the input to header majority-voting design; the
  slice-suite gap tells Phase 8 how much inference-side segmentation care is
  needed.
- **Phase 4 (conditional):** on a VST-gate fire, the escalation contract in
  `plans/phase_4.md` executes; M4's per-technique/timbre error analysis
  should name which timbre families to buy first.
- **Phase 2 (conditional):** on a data-starvation verdict, the CTC review
  reads M4/M5 evidence together with the alignment coverage accounting.
- **Phase 1:** real-test/val songs excluded for audio/alignment reasons, and
  any genre/timbre strata where transfer is weakest, become discovery and
  re-enrichment priorities.
- **Phase 9:** greedy-decode latency per window (measured at M5) is the
  first input to inference-service sizing.
