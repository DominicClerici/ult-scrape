# Phase 7 — Scale & iterate

> Expanded from [the roadmap](../docs/roadmap.md#phase-7--scale--iterate) in the
> 2026-07-06 planning session. Decisions here are **binding inputs** to later
> phases.
>
> **Genre note (deliberate):** Phases 0–6 are planned but not yet implemented,
> so the evidence this phase runs on (M4 transfer gaps, M5 error analysis, M6
> human correlation) does not exist yet. This plan is therefore an
> **evidence-contingent framework**: it locks the iteration *machinery* —
> cycle protocol, statistical discipline, lever inventory with pre-committed
> trigger signatures, exit criteria, scaling and scope-growth rules — and
> leaves concrete priorities as named **evidence slots** that Phase 6's
> outputs fill in. It contains no fictional error profiles and no speculative
> experiment backlog.

## Goal & scope

Run the loop that actually produces quality: error analysis → targeted fix →
retrain → measured verdict, repeated under statistical discipline until the
model is demonstrably good and the cheap levers are demonstrably spent. The
phase owns:

- the **iteration-cycle protocol** (the repeatable unit of work) and its
  bookkeeping;
- the **lever inventory** and the evidence-first policy for pulling levers,
  including *executing* the escalation verdicts Phase 6's M4 gate produces
  (VST → Phase 4, CTC/data → Phase 2);
- the **cloud-scaling ladder** and its budget discipline;
- the **bass scope extension** and its admission criterion;
- the **corpus-growth integration** policy (when Phase 1's new songs enter
  training) and the standing data-request feedback loop to Phase 1;
- the **exit decision** that unblocks Phase 8 with a v1 model.

**Entry condition** (from Phase 6): M6 executed — median blinded
recognizability ≥ 3/5 on sampled real-test windows and the rating↔Tab F1
rank correlation reported. A metric-validity divergence found at M6 is
resolved (Phase 5 owns the fix) before any metric-driven cycle runs.

**Out of scope:** song-level stitching, header voting, playability
post-processing, export (Phase 8 — which may start *in parallel*, see locked
decisions); serving/product (Phase 9); building the VST backend or CTC
aligner themselves (Phases 4/2 own the builds; this phase triggers and
consumes them); drums/vocals model or render work (demand write-up only).

## Inputs / outputs

**Consumes:**

- Phase 6: the baseline checkpoint + winning recipe; `runs/` with
  predictions, scorecards, per-note verdicts; the M0–M6 gate verdicts — M4's
  transfer measurement (VST/CTC discriminator evidence), M3's underfit
  evidence (the scaling trigger's seed), M6's human-correlation result.
- Phase 5: `tabeval` + frozen versioned eval manifests; `windows.jsonl`
  per-song/per-note numbers (the substrate for paired bootstrap CIs and the
  error taxonomy — both assigned to this phase by Phase 5); the human-eval
  protocol + rating tooling; W&B thin sink.
- Phase 4: `renders/` + recipe library (new variants on demand); the
  pre-designed VST escalation contract.
- Phase 3: dataset snapshots + dataloader (N-channel contract, `KIND`
  headers — bass costs no format change); the deferred WebDataset conversion
  (owned here, built only when needed).
- Phase 2: `manifest/alignment.jsonl` tiers; the pre-designed CTC escalation
  contract.
- Phases 0/1: growing `manifest/manifest.jsonl` + corpus; the artist-hash
  split (new songs auto-classify).

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Cycle docs | `docs/model-py/cycles/cycle_NNN.md` | The phase's decision record: one per cycle (hypothesis, lever, runs, CIs, verdict, error-analysis notes, cost) |
| Experiment registry | `model-py/experiments/registry.jsonl` | Machine-readable one-line-per-cycle mirror of the cycle docs (incl. logged test-set consultations) |
| Error-taxonomy report | `eval-py` report extension | Per-release error-class × facet breakdown over per-note verdicts |
| Data requests | `manifest/requests/requests.jsonl` | Standing priority feed to Phase 1 (discovery strata + re-enrichment list) |
| Release candidates | pinned checkpoints + human-eval reports | Each release = checkpoint + scorecards (incl. unconstrained malformed-rate) + human ratings + rank correlation |
| Exit report | `docs/model-py/phase7-exit.md` | Saturation + floor evidence; the **v1 checkpoint pin** Phase 8/9 build on; drums/vocals demand write-up |

**Later-phase consumers:** Phase 8 receives a pinned checkpoint (possibly an
interim one, early), anchor/header statistics per release, and the chosen
decoding configuration; Phase 9 receives per-window decode latency of that
configuration; Phase 1 consumes `requests.jsonl` continuously; Phases 2/4
receive executed-escalation demand when their gates fire.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Plan genre | **Evidence-contingent framework**: machinery + pre-committed triggers now; priorities are named evidence slots filled by M4/M5/M6 | Framework + speculative ranked backlog (fiction that anchors future decisions the evidence should own); defer the session until Phase 6 runs (forfeits planning value with no gain — the machinery is evidence-independent) | Matches how Phases 2/4/6 pre-wired gates: decide *procedures* before evidence exists, never *outcomes*. |
| Exit criteria | **Saturation + floor**: exit when (i) 2 consecutive cycles produce no CI-significant headline gain using the cheapest admissible levers with no fired trigger left unexecuted, AND (ii) a checkpoint holds **median blinded recognizability ≥ 4/5** on real test. Saturating *below* the floor forces an escalation review, not an exit. Floor amendable only by documented amendment | Numeric threshold only (numbers invented without evidence — the objection that killed numeric M4 gates); saturation only (lets "stuck" masquerade as "done"); no exit / purely consumer-driven (roadmap loses any definition of a v1 model) | Saturation is measurable and self-calibrating; the floor only bites when saturation happens at low quality — exactly when an exit should be blocked. |
| Phase 8 overlap | **Sanctioned, pinned**: Phase 8 may start once M5-era anchor/header statistics show stitching is feasible, building against an explicitly pinned interim checkpoint; newer checkpoints swap in as a config change. Phase 7 continues as a background loop after exit (like Phase 1) | Strict sequence per the roadmap arrow (serializes months of checkpoint-agnostic stitching/export engineering behind iteration that doesn't block it) | Phase 8's machinery is checkpoint-agnostic; pinning removes the moving-target risk that made strict sequencing attractive. |
| Cycle unit | **One headline lever per cycle** (accept/reject on frozen val), plus pilot-scale side ablations allowed (M2 bake-off pattern: small fixed subset, recorded numbers, no headline claims) | Strict one-change-no-side-runs (wastes idle compute, forbids the cheap probes that de-risk the next expensive cycle); batched changes (confounded attribution — regressions can't be localized without rerunning subsets) | Clean attribution where it matters (the headline decision) without banning cheap exploration. |
| Cycle cost tiers | **T0** decode-only (hours, no training) / **T1** fine-tune-only (hours–1 day) / **T2** full-pretrain (2–5 days or cloud); every lever tagged with its tier | Untiered levers (hides that a decode experiment and a pretrain differ by 100× in cost) | The tier is what the T2 gate and the saturation definition key on. |
| Acceptance bar | **Paired bootstrap over songs**: accept only if the 95% CI on the pre-declared metric excludes zero. Default metric = headline Tab F1 (real val, song-macro); a facet-targeted cycle pre-declares its facet and must also show headline-not-significantly-worse | Independent CI overlap (throws away song pairing — statistically weak, wastes retrains chasing significance); fixed minimum delta (an invented constant where a procedure works) | Per-song numbers already exist in `windows.jsonl` (Phase 5 assigned CIs here); paired is strictly more sensitive at zero extra cost. |
| Test-set hygiene | **Release-gated + logged**: val drives all cycle decisions; test touched only at release candidates and at most once per ~5 cycles as a drift check; every consultation logged in the registry | Test at every cycle (adaptive overfitting — test quietly becomes a second val); test only at exit (val↔test divergence discovered only at the very end) | Dozens of decisions against one set is training on it one bit at a time (Blum & Hardt's "ladder"); logging makes erosion visible. |
| Val growth | **Val extended periodically** with newly aligned val-split songs as new *versioned* eval-sets (Phase 5 discipline); headline claims name their eval-set version | Frozen val forever (adaptive overfit accumulates; sensitivity stays pilot-sized while the corpus grows 5–10×) | New artists auto-classify via the Phase 0 hash, so growth is contamination-free by construction. |
| Test growth | **Rare extension + transition report**: at most ~once or twice in the phase, when newly aligned test-split songs have accumulated substantially; each transition scores the current release on **both** versions | Frozen for the whole phase (headline continuity, but most of the test split's eval capacity goes unused and the phase's most important number rests on the pilot-era set) | Dual-version scoring keeps the headline series interpretable across the break. |
| Experiment record | **`docs/model-py/cycles/cycle_NNN.md` + `model-py/experiments/registry.jsonl`**; W&B stays the thin optional sink | Single growing log doc (unwieldy, not machine-queryable); W&B as primary record (contradicts Phase 5's locked local-canonical decision) | Greppable, reviewable, survives any third-party service. |
| Lever choice policy | **Evidence-first, roadmap order as prior**: each cycle picks the lever whose trigger signature best matches the current error taxonomy; the roadmap EV order breaks ties, cheapest tier breaks the rest. **Pre-wired M4 gates (VST/CTC) keep Phase 6's discriminator logic — Phase 7 executes those verdicts, never re-argues them** | Roadmap order as binding queue (freezes a pre-evidence guess into law); free choice (no protection against motivated reasoning) | The error taxonomy exists precisely to out-rank a guess made before any model ran. |
| T2 admission gate | **Trigger or saturation**: a T2 cycle requires a fired trigger (underfit evidence, VST/CTC gate, a facet gap naming it) or measured T0/T1 saturation — recorded in the cycle doc | Hard queue exhaust-T0/T1-first (blocks an obviously-indicated pretrain fix behind unrelated cheap experiments); no gate (multi-day/cloud spends become impulse purchases) | Protects the scarce resource without blocking evidence-backed escalation. |
| Constrained decoding | **Admissible as a T0 lever**, with the reporting rule that **unconstrained malformed-span rate is still measured and reported at every release** | Defer to Phase 8 (forfeits a cheap known quality win during the quality phase) | Preserves Phase 6's decoder-health diagnostic alongside the constrained path. |
| Cloud ladder | **Rung 1 = one big GPU (A100-80GB/H100-class), identical single-GPU code**, bigger model/batch; **rung 2 (multi-GPU DDP) requires rung-1 underfit/too-slow evidence + documented amendment** (first real code-architecture change); managed/multi-node services rejected | Straight to DDP (rewrites replay-mixing/unfreeze/resume logic against zero evidence one 80 GB GPU is insufficient); managed training (heavyweight for a local-first filesystem-contract stack) | A 16→80 GB step is 5× headroom with zero new failure modes; Phase 6's plain-single-GPU decision survives unmodified at rung 1. |
| Cloud data movement | **Copy the pinned snapshot subset** (filtered to the run's config) to instance NVMe; dataloader unchanged. **WebDataset shard conversion is a rung-2 prerequisite**, never built speculatively (settles Phase 3's deferral) | Build shards now (pays a format+tooling cost for a multi-node streaming problem rung 1 doesn't have) | Phase 3 already called the conversion mechanical; deferral costs nothing. |
| Cloud budget | **~$300 standing per-cycle cap**; every cloud cycle pre-states estimated GPU-hours and dollars in its cycle doc before launch, actuals recorded after; above-cap needs explicit sign-off | ~$100 (forces sign-off on most legitimate multi-day runs); ~$1000 (weakens the lean-run forcing function); no cap / approve each (makes the operator the bottleneck for every launch) | Covers a full H100 multi-day pretrain or a bake-off pair at neocloud rates; runaway spend is structurally impossible. |
| Bass admission | **At the human floor**: bass enters once a checkpoint holds median recognizability ≥ 4/5 on real test, as a normal T2 cycle (dataset-config change: bass tracks become targets) under the standard acceptance test | Only after phase exit (the phase never executes its own stated extension; bass then competes with productization attention); opportunistic (scope growth competes with error-fixing under no rule) | Guitar is demonstrably good before scope grows; extension isn't serialized behind full saturation; multi-task transfer may even help guitar — measured either way. |
| Bass metric | **Headline stays guitar Tab F1 permanently; bass gets its own bass Tab F1** (same machinery, `KIND`-filtered). The bass-admission cycle is accepted only if bass F1 clears a floor set from its first run (documented) **and** guitar headline is not significantly worse (paired CI) | Fold bass into one combined headline (redefines the tracked number mid-phase; a bass gain can mask a guitar regression; all prior cycles become incomparable) | Cross-cycle comparability is the phase's spine; scope growth must never blur it. |
| Drums / vocals | **Demand write-up only**, delivered at phase exit: who wants drum/vocal tabs, what each would cost (incl. Phase 4's drum-realism upgrade note). No model or render work in this phase | Allow extension cycles if demand looks strong (stacks second and third scope changes into the phase whose risk register names scope creep) | A product question answered with a document, not GPU-days. |
| Snapshot adoption | **Data-as-a-lever**: new dataset snapshots are adopted only by a cycle whose declared lever *is* the data bump (same config otherwise, judged by the standard paired-CI test on the unchanged eval set); all other cycles pin the current snapshot. Eval-set versions are independent of training snapshots | Cadence-based adoption (every comparison silently carries data drift — breaks one-lever attribution); continuous/live training (contradicts the locked immutable-snapshot decision) | Corpus growth accumulates in `output/`/`manifest/` until a data cycle harvests it; attribution stays clean. |
| Project layout | **No new project.** Phase 7 is a documented process over `model-py` + `eval-py`: cycle docs in `docs/model-py/cycles/`, registry in `model-py/experiments/`, requests in `manifest/requests/`; additive code (paired bootstrap, taxonomy report) lands in `eval-py` where Phase 5 assigned it | Dedicated `lab-py/` project (a project boundary with no contract behind it is ceremony — Phase 7 produces process artifacts, not a pipeline stage) | The repo's decoupled-projects pattern exists for contracts; there is none here. |

## Design

### The cycle protocol

One cycle = **error analysis → hypothesis → one lever → run(s) → paired-CI
verdict → registry entry**. Concretely:

1. **Pick the lever** (evidence-first policy): read the current
   error-taxonomy report; match against the trigger signatures in the lever
   inventory; roadmap EV order breaks ties, then cheapest tier. A T2 pick
   records its admission evidence (trigger or T0/T1 saturation).
2. **Declare before running** (in the cycle doc): lever + tier, hypothesis,
   pre-declared acceptance metric (headline, or facet + headline-not-worse),
   baseline checkpoint, training-snapshot version, eval-set versions, cost
   estimate (GPU-hours; dollars if cloud).
3. **Run.** Pilot-scale side ablations may ride along (recorded, no headline
   claims). Concurrent cycles allowed only when resource-disjoint (e.g. a T0
   decode study during a T2 pretrain); accept/reject decisions serialize in
   registry order against each cycle's declared baseline.
4. **Verdict**: paired bootstrap over songs on frozen val (95% CI excludes
   zero on the pre-declared metric). Accepted ⇒ the new checkpoint becomes
   the baseline for subsequent cycles. Rejected cycles are recorded with
   equal care — the registry is also the "already tried" index.
5. **Emit**: updated error-taxonomy notes and any data requests
   (`manifest/requests/`).

**Cycle doc contents** (`docs/model-py/cycles/cycle_NNN.md`): id, date,
lever + tier, hypothesis, trigger evidence, T2-admission evidence (if T2),
baseline pins (checkpoint / snapshot / eval-set / gpscore + vocab versions),
run ids, cost (estimate vs actual), scorecard deltas with paired CIs,
verdict, error-analysis notes, emitted requests, test consultations (if
any). One JSON line mirrors it in `model-py/experiments/registry.jsonl`.

**Releases**: a checkpoint claiming the human floor (or an exit claim) is a
*release candidate*: full frozen-test scorecard (logged consultation),
unconstrained malformed-rate reported (even if the release path decodes
constrained), Phase 5 human protocol executed, rating↔Tab F1 rank
correlation re-reported. Divergence (metric up, ratings flat) triggers the
M6-style metric-validity review before further metric-driven cycles.

### The lever inventory (with pre-committed trigger signatures)

Amendable only by documented amendment, like every locked table in this repo.

| Lever | Tier | Trigger signature | Executes in |
|---|---|---|---|
| Beam search + length normalization | T0 | Greedy-vs-oracle near-miss analysis shows recoverable errors; run early — nearly free | `model-py` |
| Checkpoint averaging | T0 | Noisy cheap-val trajectory around selection | `model-py` |
| Grammar-constrained decoding | T0 | Malformed-span rate is a top error class (reporting rule applies) | `model-py` |
| Replay ratio / fine-tune schedule tuning | T1 | Real-val overfit curves; Phase 6 deferred constants | `model-py` |
| **More real aligned data** (snapshot bump) | T1 | Fine-tune overfit signature + thin-coverage accounting; facet gaps by alignment tier | Phase 1/2 feed; `dataset-py` snapshot |
| Demucs stems for real audio | T1 | Errors concentrated in dense-mix / vocal-overlap windows (and Phase 6's pilot stem verdict, or evidence contradicting it) | `dataset-py --stems` |
| LoRA-style adapter fine-tune | T1 | Full fine-tune forgets token grammar (malformed rate rises on real fine-tune) | `model-py` |
| **CTC forced-aligner escalation** | T1→ | **Pre-wired M4 verdict**: data-starved signature (early real overfit + thin coverage) | Phase 2's escalation contract |
| Augmentation / variant breadth (new recipes, wider tempo, humanization) | T2 | Synth-canonical ≫ synth-stochastic gap (robustness shortfall), or real errors matching a missing degradation | Phase 4 renders; `model-py` retrain |
| **VST timbre escalation** | T2 | **Pre-wired M4 verdict**: timbre-shaped gap (real ≪ synth-stochastic ≈ synth-canonical); M4 names timbre families to buy first | Phase 4's escalation contract |
| Model scale-up (d768+, deeper) → cloud ladder | T2 | M3-style underfit (train + val plateau together at full data) persisting, or T0/T1 saturation with large oracle headroom | `model-py` + cloud rung 1 |
| Representation refinement (tempo tokens, anchor granularity, vocab tweaks) | T2 (most expensive: snapshot rebuild + full retrain) | M5-slot evidence: rhythm errors correlated with tempo changes (tempo tokens — Phase 3's deferred observable); systematic anchor drift patterns | Phase 3 amendment + `score-py`/`dataset-py` |
| Encoder swap / upgrade | T2 | Transfer gap persists **after** VST escalation; from-scratch-control evidence points encoder-side | `model-py` |
| **Bass extension** | T2 | **Admission = human floor met** (not an error signature) | `dataset-py` config + `model-py` retrain |

**Evidence slots** (deliberately unfilled until Phase 6 lands): the first
cycles' actual priorities (M5/M6 error analysis); the bass Tab F1 floor
(set from the first bass run); which M4 verdicts fire (determines whether
Phases 2/4 escalations execute at all); whether rung 1 of the cloud ladder
is ever needed.

### Error taxonomy & statistics (the `eval-py` additions)

Assigned to this phase by Phase 5; both are additive report features over
existing per-note verdicts in `windows.jsonl`:

- **Paired bootstrap**: `eval report --compare A B` resamples songs, reports
  the per-song delta distribution and 95% CI for headline + every facet.
- **Error-taxonomy report**: error classes — wrong pitch; wrong string/fret
  given correct pitch; rhythm/duration; technique (per Tier-1 flag); anchor
  timing; bar insert/delete; malformed spans; track assignment / header —
  cross-tabulated by facets: source (real/synth), variant/timbre family,
  alignment tier, tuning, technique presence, tempo bucket. Rendered into
  the scorecard and the W&B sink; the "trigger signature" column above is
  read directly off this report.

### The Phase 1 feedback loop

`manifest/requests/requests.jsonl` — appended by cycles, consumed by
Phase 1 ops. Two record kinds: `discovery` (strata where transfer is
weakest: genre/tuning/timbre descriptors + evidence pointer) and
`re-enrichment` (specific `tab_id`s whose audio failed; eval-split songs
first — they're eval capacity, worth more than average songs). Schema
details deferred to first implementation; the *existence and location* of
the feed is the contract.

### Exit & handoff

The exit report (`docs/model-py/phase7-exit.md`) records: the saturation
evidence (the two terminal cycles' CIs), the floor evidence (human-eval
report), the **v1 checkpoint pin** (run id + checkpoint + snapshot +
eval-set + vocab versions + decoding config), per-window decode latency
(Phase 9's sizing input), anchor-error and header-accuracy statistics
(Phase 8's inputs), the drums/vocals demand write-up, and the state of the
lever inventory (what was pulled, what never triggered). After exit,
Phase 7 continues as a background loop (like Phase 1): the protocol keeps
governing any further model work, with Phase 8/9 consuming pinned releases.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Iterating on noise (accepting chance improvements) | Paired bootstrap CI is the only acceptance path; rejected cycles recorded so the same noise isn't re-chased. |
| Adaptive overfitting to val across many cycles | Val periodically extended with fresh songs (new versioned eval-sets); headline claims name their eval-set version; test stays release-gated. |
| Test-set erosion under iteration pressure | Consultations capped (~1 per 5 cycles) and *logged in the registry* — erosion is visible, not silent. |
| Phase never ends / ends by vibes | Exit is a defined predicate (saturation + floor); saturating below the floor forces an escalation review instead of an exit. |
| Scope creep (bass too early, drums/vocals sneaking in) | Bass gated on the human floor; guitar headline protected by the not-significantly-worse rule; drums/vocals capped at a demand write-up. |
| Cloud cost burn | T2 admission gate + pre-stated estimates + $300 standing cap; DDP rung needs evidence + amendment. |
| Snapshot churn breaking comparability | Data-as-a-lever adoption: only data cycles bump snapshots; everything else pins. |
| Metric diverges from perceived quality as the model improves | Human protocol re-runs at every release candidate (the floor demands it anyway); rank correlation re-reported; divergence blocks metric-driven cycles pending the Phase 5 review. |
| Framework ossifies against surprising evidence | Everything amendable by documented amendment — the same discipline as every other phase's locked table; amendments live in this file's history. |
| Phase 8 builds on a moving target | Overlap is sanctioned only against explicitly pinned checkpoints; swap-ins are config changes. |

## Acceptance criteria

- Entry condition honored: no metric-driven cycle before M6's verdict is
  recorded (and any metric-validity divergence resolved).
- Every cycle has a cycle doc + registry line with the declared-before-run
  fields (lever, hypothesis, metric, pins, cost estimate); no undocumented
  runs influence decisions.
- Every acceptance verdict shows its paired-bootstrap CI; every T2 cycle
  shows its admission evidence; every cloud cycle shows estimate vs actual
  cost within the cap (or a recorded sign-off).
- Every test-set consultation is logged; eval-set versions named on every
  headline claim; any test extension ships its dual-version transition
  report.
- `eval-py` gained the paired-compare and error-taxonomy reports (unit-tested,
  deterministic, network-free by default — repo convention).
- `manifest/requests/requests.jsonl` exists and Phase 1 ops consume it.
- Fired M4 verdicts (if any) executed via Phases 2/4's contracts, with the
  executions' outcomes measured by ordinary cycles.
- If bass was admitted: the admission cycle shows the floor evidence, the
  bass F1 result, and the guitar headline paired CI.
- Exit report complete per the handoff spec (v1 pin, latency, anchor/header
  stats, demand write-up, lever-inventory state) — or the phase is
  explicitly still open.
- Docs current per CLAUDE.md: this plan, `docs/roadmap.md`, and
  `docs/model-py/` pages consistent; `OVERVIEW.md` map updated when the
  cycle-docs directory first exists.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Concrete first-cycle priorities | The central evidence slot — M5/M6 error analysis fills it; the lever-choice policy is locked so the filling is mechanical. |
| Bootstrap details (resample count, CI method variant) | Standard choices (e.g. 10k resamples, percentile CI); recorded in the `eval-py` implementation; no downstream contract depends on them. |
| `requests.jsonl` schema fields | Feed location + record kinds locked; fields evolve additively with Phase 1 ops' needs. |
| Beam widths, averaging windows, LoRA ranks | Ordinary hyperparameters inside T0/T1 cycles; recorded per cycle. |
| Cloud provider choice | Any neocloud meeting the rung-1 shape works; a per-cycle operational detail within the cap. |
| Shard format specifics | Only needed at cloud rung 2, which needs its own amendment anyway. |
| Bass Tab F1 floor value | Set from the first bass run's result (documented in that cycle doc) — no honest prior exists. |
| Val-extension cadence | "Periodically, as aligned val-split songs accumulate" suffices; each extension is a versioned eval-set release. |
| Human-eval rater count / sourcing per release | Phase 5's protocol owns the mechanics; releases inherit it. |

## Open questions for later phases

- **Phase 8:** define its own feasibility bar over the anchor-error and
  header-accuracy statistics Phase 7 releases publish (that bar, not
  Phase 7's exit, is what starts the overlap); extend the predictions
  contract with `song` mode (Phase 5's note); decide how stitching consumes
  the constrained-decoding configuration if it's adopted.
- **Phase 9:** inference-service sizing reads the exit report's per-window
  latency for the *chosen* decoding config (beam/constrained, not
  necessarily greedy); header *forcing* (vs prediction) becomes a product
  feature there.
- **Phase 1:** operationalize consumption of `manifest/requests/` (retry
  queues, discovery targeting); snapshot-cut mechanics when a data-lever
  cycle requests one.
- **Phases 2/4 (conditional):** their escalation contracts execute on fired
  verdicts; Phase 7 cycles then measure the escalations' actual payoff —
  which itself feeds the saturation predicate.
- **Post-exit scope:** if the drums/vocals demand write-up says "go," a new
  planning session scopes it (including Phase 4's drum-realism upgrade);
  it is deliberately not pre-planned here.
