# Phase 4 — Synthetic data engine

> Expanded from [the roadmap](../docs/roadmap.md#phase-4--synthetic-data-engine)
> in the 2026-07-02 planning session. Decisions here are **binding inputs** to
> later phases. Prior art leaned on: **SynthTab** (ICASSP 2024 — VST-rendered
> DadaGP tabs improve tab transcription; validates the escalation path),
> **Slakh2100** (sample-library rendering at scale), and the roadmap's
> SynthTab-shaped curriculum strategy.

## Goal & scope

Render every eligible tab into training audio with **perfect labels by
construction**: multiple timbres/tempos/mixes per song, full-mix and
guitar-bus products, feeding the Phase 3 dataset builder as `source:
"synthetic"` records. This is the strategic lever the roadmap leans on —
synthetic carries the bulk of pretraining; real aligned audio is fine-tune
and eval material.

A deliberate consequence of "labels come from the score, not the pairing":
**synthetic rendering needs no audio and no alignment**, so tabs whose
YouTube pairing is `suspect`/`bad` — and tabs with no audio at all — are
still fully usable synthetic training material. Eligibility is
`score.parse_ok`, not the pairing verdict.

**Out of scope:** model training and the synthetic→real transfer measurement
(Phase 6 — but the escalation trigger it feeds is defined here); metrics
(Phase 5); the commercial-VST backend adapter (pre-designed escalation,
built only if Phase 6 measurement demands it); neural singing synthesis;
the CTC forced-aligner itself (Phase 2 escalation — but its training-data
export ships here from day one); re-fingering or symbolic augmentation
(Phase 3 owns augmentation policy); WebDataset/shard packaging (Phase 7).

**Sequencing note:** Phases 0/2a/3 are planned but not yet implemented.
This phase consumes their *contracts*: `gpscore` 1.0 performance view
(Phase 2a), `manifest/manifest.jsonl` (Phase 0), and Phase 3's dataset
record shape. `render-py` needs no Phase 2b alignment output at all.

## Inputs / outputs

**Consumes:**

- `gpscore` 1.0 (Phase 2a): `score.performance()` — per-note onset/duration,
  pitch, string/fret, dynamics, technique flags; expanded bar/beat grid;
  tempo map. The renderer is a pure consumer of the performance view.
- `manifest/manifest.jsonl` (Phase 0): eligibility (`parse_ok`, dedup) and
  the artist-hash split, which synthetic records **inherit strictly**.
- `output/` (read-only): `.gpif` only. Never `audio.*`; never writes there.
- External assets (soundfonts, SFZ libraries, NAM amp captures, cab IRs) via
  a hash-pinned registry — see Design.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Renderer | `render-py/` | Code — house-pattern CLI (`scan`/`run --jobs N`/`status`, SQLite queue) |
| Render tree | `renders/<tab_id>/<variant_id>/` | Derived, fingerprint-keyed (expensive build cache, like `manifest/alignment/`) |
| Per-variant audio | `mix.flac`, `gtr_bus.flac` (+ per-track stems / DI behind flags) | Derived — mono FLAC @ 24 kHz |
| Render metadata | `render_meta.json` per variant | Derived — full recipe, realized bar grid, per-note realized onsets (the CTC export), fingerprints; written last as the commit marker |
| Asset registry | `render-py/assets.json` + `render-py/recipes/` | Committed, versioned — hash-pinned assets with license notes and `train`/`eval` tags; the tone-recipe library |
| Render report | `renders/report.md` | Derived — coverage, throughput, disk, recipe/asset usage distributions |

**Later-phase consumers:** `dataset-py` ingests `renders/` and emits the
same record shape as real audio (`source: "synthetic"`, variant id, trivial
alignment from the realized grid) — the roadmap's "synthetic vs real is just
a manifest flag" made concrete. Phase 5 takes the synthetic test set
(held-out songs × held-out timbres, canonical variant included) and drives
`render-py render-file` (subprocess) for review-bundle GT/prediction audio;
Phase 8's song-view bundles reuse the same entry point. Phase 6
pretrains on this and runs the transfer measurement that decides the VST
escalation. Phase 2's CTC aligner, if ever triggered, trains on the
per-note realized onsets.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Synthesis backend | **Pluggable backends over an articulated event stream; open stack v1 (FluidSynth SF2 + sfizz SFZ); commercial-VST adapter (SynthTab-style, via DawDreamer/Pedalboard) pre-designed as measured escalation** | VST-first (SynthTab validated it, but real license cost, slower renders, Windows automation friction — and our amp-sim stage + full-mix setting differ from SynthTab's setup); open-stack-only (no escape hatch if the domain gap bites); in-house DSP synth (a research project of its own) | Same evidence-first shape as Phase 2's CTC decision: render the whole corpus today at high throughput and zero cost; if Phase 6's synthetic→real transfer shows a timbre-shaped gap, add the VST adapter — SynthTab is proof the escalation works. Backends are dumb players of the event stream, so escalation is an adapter, not a redesign. |
| Technique realization | **All 12 Tier-1 techniques audibly voiced, enforced**: realization matrix in the backend contract + CI audibility test (render technique-dense fixture with/without each technique; assert measurable audio difference) | Voice only the easy family, keep labels (≈200 songs of palm-mute labels become context-only supervision noise); strip unvoiced techniques from synthetic labels (synthetic/real label distributions diverge; anti-trains technique-shaped contexts) | A labeled-but-inaudible technique teaches prediction from context statistics, not sound — poison for the bulk of training. GM programs 28 (Muted Guitar) and 31 (Guitar Harmonics) make the timbral switches cheap; everything else is pitch-bend/velocity/timing. Honors why Phase 3 kept these 12. |
| Signal chain | **Pedalboard as the one effects host: free NAM (Neural Amp Modeler) VST3 captures + cab-IR convolution + EQ/comp/reverb; tone identity = versioned, serialized recipes** sampled per track per variant; waveshaper-only chains kept in the pool as cheap diversity | Pure DSP waveshaping (weakest exactly on high-gain, the corpus's dominant timbre); commercial amp-sim suites (marginal gain over NAM at real cost); no amp stage / pre-distorted soundfonts (bakes static tone; loses palm-mute-into-gain dynamics) | The amp/cab stage launders sample sterility — high-gain compression and cab filtering mask most of what makes soundfonts sound fake — so it's the main domain-gap lever, and NAM captures are trained to sound like specific real amps, for free. Risk (Windows VST3 hosting) gated by an early smoke test with a NAM-core CLI wrapper as fallback host. |
| Non-guitar tracks | Drums: GM kits + drum-bus chain. Bass: same open stack + bass recipe family. **Vocals: instrumental distractor rendering** — vocal MIDI lines through a rotating pool of sustained melodic patches (choir/synth lead/strings) at realistic level; some variants omit vocals | Omit vocals (model first meets vocal interference during scarce real fine-tuning — classic melody-bleed failure mode); neural singing synthesis (heavyweight dependency for a distractor) | What matters for robustness is a pitched melodic distractor in the vocal register with the vocal rhythm, not phonetic realism. Vocal-absent variants double as instrumental-section realism. Bass rendering also pre-pays Phase 7's bass extension. |
| Stored products per variant | **Full mix + guitar-bus stem** (sum of guitar tracks, post-chain), mono FLAC @ 24 kHz; per-track stems and clean DI behind flags, default off | Mix only (N-channel contract gets no synthetic data; stem ablation forces re-render); all stems + DI always (~track-count× disk at 10k scale with no identified consumer) | Minimal set with actual downstream consumers: Phase 6's mix-vs-mix+stem ablation, and guitar-only records derivable in `dataset-py` without re-rendering. Flags keep the maximal set reachable per-run. |
| Render storage format | **Mono FLAC @ 24 kHz** stored (matches the Phase 3 record parameter); internal processing at 44.1 kHz stereo, downmixed at write | Store 44.1/48 kHz stereo (≈4× disk for headroom) | Seeded determinism makes fidelity reversible: any future higher-rate need is an overnight re-render, not an archival concern. `output/` originals justified 24 kHz for real audio; for synthetic, the renderer itself is the original. |
| Variant policy | **Seeded stochastic variants + one canonical**: variant fully determined by `(tab_id, variant_index, renderer_version)`; seeds sample every axis (per-track sample source, tone recipe, global tempo scale ~×0.8–1.2, mix gains/pan/master chain, distractor presence/timbre, micro-detune); full recipe recorded in `render_meta.json`; **variant 0 = canonical** (notated tempo, neutral mix, clean-ish tone, no distractors, no humanization); count is a builder param (~6 at pilot) | Curated fixed grid (spends disk on combinatorial redundancy); single variant + on-the-fly augs (sample source, tone, and tempo cannot be created at load time) | Decorrelates timbre/mix from content — the point of paying N× disk. Recipe-in-metadata makes Phase 6's clean→degraded curriculum a dataloader filter and makes error analysis by tone queryable. Canonical variant is the "fails here ⇒ representation bug" probe. Disk math: pilot 509 × 6 ≈ 35 GB; 10k × 6 ≈ 700 GB with variant count as the knob. |
| Humanization | **On, with realized-time labels**: seeded per-variant onset jitter (order ±5–15 ms), velocity jitter, chord strum spread, slow tempo drift (±1–3 % around the variant tempo scale), applied in the event stream *before* synthesis; **anchors and per-note onset exports computed from realized times**; variant 0 unhumanized | No humanization (grid-perfect timing is itself a domain gap on exactly the dimension anchors supervise); humanize but keep notated-time labels (deliberate label noise) | The renderer knows what it perturbed, so realized-time labeling keeps alignment exact by construction — symbolic tokens untouched, only the score→performance mapping gets human. Magnitudes are deferred tunables. |
| Pipeline architecture | **Decoupled `render-py/` + `renders/` derived tree, ingested by `dataset-py`**: house-pattern CLI; fingerprint-keyed idempotency (`gpif_sha256` + renderer version + recipe-library version + variant seed); `render_meta.json` written last as commit marker | Renderer writes dataset records directly (couples renderer to the record format; forks the roadmap's single-builder requirement) | Matches the repo's filesystem-contract pattern and Phase 2's precedent for expensive derived trees (`manifest/alignment/`). `dataset-py` stays the single producer of records. |
| Asset management | **Committed hash-pinned registry** (`render-py/assets.json`: name, source URL, sha256, license note, `train`/`eval` tag) + `fetch-assets` command into a git-ignored local assets dir | Committing assets to git/LFS (gigabytes of third-party content, license exposure); unpinned ad-hoc downloads (kills reproducibility) | Reproducibility from pinning, not from vendoring; licenses audited once, in one place; link-rot mitigated by hashes + a private backup of the fetched set. |
| Split hygiene & synthetic eval | Synthetic records **inherit the song's artist-hash split, strictly** — training never sees any render of a val/test song. **Timbre held out too**: an eval-reserved slice of the recipe/sample/capture pool is used exclusively for val/test renders (assets tagged in the registry) | Same-pool test renders (timbre memorization invisible until real-audio eval); allowing degraded renders of val/test songs into training (leakage) | Settles Phase 0's open question (synthetic renders of val/test artists in train: **no**). Unseen-song × unseen-timbre is the honest proxy for real-world transfer and catches timbre overfitting before Phase 6 burns real-audio eval on it. Canonical variant included in the synthetic test set. |

## Design

### `render-py/` — the renderer

House-pattern CLI (`scan` / `run --jobs N` / `status` / `fetch-assets` /
`report`; SQLite queue; mirrors `enricher-py`). Depends on `gpscore` + the
manifest; never on other projects' internals; never writes into `output/`
or `manifest/`.

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings: manifest dir, output dir (read-only), renders dir, assets dir, variant count, product flags (`--stems`, `--di`), jobs. |
| `app/events.py` | `gpscore` performance view → **articulated event stream**: per-string note events with realized pitch envelopes (bend/vibrato/slide curves), velocities (dynamics + accent/ghost), gate times (staccato/let-ring), patch selectors (palm-mute/dead/harmonics), strum/grace timing. Humanization transforms live here (seeded), producing the **realized timeline** all labels derive from. |
| `app/backends/` | `Backend` protocol: consume event stream → dry per-track audio. `fluidsynth.py` (SF2, channel-per-string, RPN bend range, program switches) and `sfizz.py` (SFZ) in v1; `vst.py` is the pre-designed escalation adapter. Each backend publishes a **realization matrix** (technique → mechanism) as part of its contract. |
| `app/recipes.py` | Tone-recipe library: load, validate, seeded sampling by track family (electric/acoustic/bass/drums/distractor) and split tag. Recipes are serialized data under `render-py/recipes/`, versioned. |
| `app/chain.py` | Pedalboard chain construction from a recipe: NAM VST3 hosting, IR convolution, EQ/comp/reverb/gain staging. Hosting smoke test + NAM-core CLI fallback behind one interface. |
| `app/mix.py` | Bus summing (guitar bus, full mix), distractor leveling, master chain, loudness normalization, stereo→mono downmix, FLAC encode @ 24 kHz. |
| `app/assets.py` | Registry parsing, fetch, sha256 verification, `train`/`eval` tag resolution. |
| `app/queue.py` / `app/repo.py` | SQLite work queue keyed on `(tab_id, variant_index)`; `scan` skips fingerprint-current variants. |
| `app/output.py` | Atomic per-variant writes; `render_meta.json` last (commit marker); regeneration of `renders/report.md`. |
| `app/inspect.py` | Developer diagnostics: play/export a variant with a click-track overlay from the realized grid; per-technique A/B render for the audibility fixture. |
| `app/render_file.py` | **One-off rendering** (added 2026-07-06): `render-py render-file <gpif|gp> --recipe canonical -o <dir>` renders an arbitrary score file — not tied to `output/` or the queue — with a named recipe; thin importable wrapper around the same event-stream pipeline. This is the subprocess entry point Phase 5's review bundle and Phase 8's song view use to voice GT and **predicted** fragments (predictions exist nowhere in `output/`; they arrive as `.gp`/`.gpif` files written by the `gpscore` writer). |

**Channel-per-string** is structural: independent bends within a chord are
impossible on one MIDI channel; strings ≤ 10 < 16 channels; also handles
arbitrary tunings exactly. Realization logic is written once in
`events.py` — backends are dumb players.

**Technique realization map (v1 open stack):**

| Technique | Mechanism |
|---|---|
| Bend (3-point) | Per-string pitch-bend curve, wide RPN bend range, exact quarter-tone targets |
| Vibrato | Low-amplitude pitch-bend LFO |
| Slide (6 kinds) | Pitch-bend glides differing in start/end pitch and timing |
| HOPO | Overlapped legato + reduced attack velocity |
| Palm mute | Program switch → GM 28 *Muted Guitar* (+ shortened decay) |
| Dead note | Muted patch, very short envelope |
| Harmonics | Program switch → GM 31 *Guitar Harmonics* at sounding pitch |
| Ghost / accent | Velocity down / up |
| Staccato | Gate to a fraction of notated duration |
| Let-ring | Sustain to next same-string note / bar boundary |
| Grace | Short pre-onset or on-beat note per notation |
| Brush up/down | Few-ms direction-ordered per-string stagger |

### Pipeline per `(tab, variant)`

1. Seed RNG from `(tab_id, variant_index, renderer_version)`.
2. Parse `.gpif` → `Score` → `performance()`.
3. Sample the variant recipe: tempo scale, per-track sample assets + tone
   recipes (family-constrained, split-tag-constrained), mix params,
   distractor policy, humanization params. Variant 0 = canonical constants.
4. Build the articulated event stream; apply humanization → realized
   timeline (bar grid + per-note onsets).
5. Synthesize each track (backend) → dry stems at 44.1 kHz.
6. Per-track chains → wet stems; sum guitar bus and full mix; master chain
   + loudness normalization; downmix; encode FLAC @ 24 kHz.
7. Write products atomically; `render_meta.json` last.

### `render_meta.json` (v1)

```jsonc
{
  "schema_version": 1,
  "renderer_version": "0.1.0",
  "recipe_library_version": "…",
  "inputs": { "gpif_sha256": "…" },
  "variant": { "index": 0, "canonical": true, "seed": "…" },
  "recipe": { /* every sampled parameter: tempo_scale, per-track assets +
                 tone recipes (with asset sha256s), mix, distractor,
                 humanization params */ },
  "products": { "mix": "mix.flac", "gtr_bus": "gtr_bus.flac" },
  "realized": {
    "duration_s": 214.2,
    "bar_grid": [[ "0/1", 0.0 ], …],        // [score_qn, realized_s] per bar
    "note_onsets": [ [track, "onset_qn", onset_s, dur_s, pitch, string, fret], … ]
  },                                          // the CTC-aligner training export
  "split": "train"                            // inherited from the manifest
}
```

`dataset-py` builds a synthetic record from this: same symbolic dump as a
real record, alignment = the realized bar grid (trivially `onset_grade`
everywhere), `source: "synthetic"` + variant id, audio = the stored FLAC(s).

### Escalation contract (VST backend)

Trigger: Phase 6's synthetic→real transfer measurement shows a
timbre-shaped gap (error analysis by technique/timbre on real eval, with
the timbre-held-out synthetic test as the discriminator between "timbre
gap" and "representation gap"). Action: purchase 2–3 articulation-sampled
guitar instruments, implement `backends/vst.py` (DawDreamer or Pedalboard
hosting, per-product keyswitch maps), tag the new assets in the registry,
re-render. Nothing upstream or downstream changes. The decision and its
evidence go in Phase 6's report — same discipline as Phase 2's CTC gate.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Domain gap: model aces synthetic, transfers nothing to real (the classic synth-corpus failure) | Measured, not argued: Phase 6 transfer eval on real audio + the timbre-held-out synthetic test separate timbre gap from representation gap; the VST escalation is pre-designed with its trigger documented; the NAM amp stage already targets the worst gap (high-gain timbre). |
| Technique washout: labeled techniques inaudible in renders | The audibility CI fixture asserts each of the 12 changes the audio measurably; human listening spot-checks per recipe family; realization matrix is a tested backend contract. |
| Pedalboard↔NAM VST3 hosting flaky on Windows | Day-one smoke test (before any other chain work); fallback host (NAM-core CLI wrapper) behind the same `chain.py` interface. |
| Free soundfont/SFZ quality too low even post-amp | Curation pass with listening during asset registry assembly; the recipe pool only admits assets that pass; worst case the VST escalation exists. |
| Float-DSP nondeterminism breaks "re-render = same bytes" | Pin exact asset hashes + library versions; claim byte-determinism per machine/config and metadata-determinism universally; the fingerprint key (inputs + versions + seed) is what reproducibility rests on, not cross-machine bit-equality. |
| Disk blowout at 10k-song scale | Variant count and product flags are knobs; disk math documented (~700 GB at 10k × 6); re-render is overnight-cheap, so stored variants can be pruned and regenerated rather than hoarded. |
| Render throughput becomes the bottleneck | Measure on the pilot early (`report.md` publishes songs/hour); FluidSynth + pedalboard are well above realtime and parallel via `--jobs`; NAM inference is the likely hot spot — profile before optimizing. |
| Asset link rot | sha256 pinning detects silent changes; keep a private backup archive of the fetched asset set. |
| Distractor timbres teach "ignore choir patch" instead of "ignore vocals" | Rotating timbre pool + vocal-absent variants prevent fixation on one distractor sound; real fine-tuning provides true vocals; if Phase 6 error analysis shows vocal bleed, revisit (singing synthesis is the known upgrade). |

## Acceptance criteria

- Full pilot corpus (every `parse_ok` tab, all splits) renders through the
  queue with resumability; re-run with unchanged inputs/versions is a
  fingerprint-hit no-op.
- Determinism: re-rendering a variant on the same machine/config yields
  byte-identical `render_meta.json` and audio; the fingerprint key
  `(gpif_sha256, renderer_version, recipe_library_version, seed)` is
  enforced.
- **Audibility CI green for all 12 techniques** on the technique-dense
  fixture, per shipped backend.
- Windows NAM-VST3 hosting smoke test resolved (hosted or fallback adopted)
  and documented.
- Canonical-variant validation: realized note onsets match the `gpscore`
  performance view exactly (variant 0); humanized variants stay within
  declared jitter bounds.
- `dataset-py` ingests a rendered subset and emits schema-valid
  `source: "synthetic"` records; a mixed real+synthetic snapshot builds
  end-to-end; a listening spot-check confirms window audio ↔ detokenized
  notation correspondence on synthetic windows.
- Split hygiene enforced mechanically: no train-split record references an
  `eval`-tagged asset; no val/test song appears in any training record.
- Human listening pass: ≥ 10 songs across recipe families (incl. high-gain,
  acoustic, vocal-distractor cases) judged plausible-as-training-audio;
  canonical variants audibly match score playback.
- Asset registry complete: every asset hash-pinned with license note and
  `train`/`eval` tag; `fetch-assets` reproduces the asset dir from scratch.
- `renders/report.md` publishes coverage, throughput, disk, and recipe/asset
  usage distributions.
- Unit tests deterministic and asset/network-free by default (synthetic
  fixtures, tiny embedded SF2); real-asset render paths behind the
  `integration` marker (repo convention).
- Docs current per CLAUDE.md: `docs/render-py/overview.md` written,
  `OVERVIEW.md` map + roadmap updated.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Humanization magnitudes, tempo-scale range, detune range, loudness targets, distractor level policy | Tunable parameters; the fields/mechanisms are fixed and recorded per variant, so recalibration is a re-render knob, not a redesign. |
| Variant count at 10k scale (and pruning policy) | Builder parameter + documented disk math; re-render is cheap, snapshots pin what training actually used. |
| Per-track stems / DI retention defaults | Flags exist; consumers (beyond mix + guitar bus) haven't materialized. |
| VST backend adapter + instrument purchases | Pre-designed escalation with a documented Phase 6 trigger; building it without evidence inverts the repo's measure-first discipline. |
| Recipe pool size/curation specifics | Data, not code — versioned library grows by listening; report tracks usage. |
| Neural singing synthesis | Known upgrade path if Phase 6 shows vocal-bleed errors; distractor pool is the v1 answer. |
| Drum/bass recipe sophistication | GM kits + simple chains suffice for distractor-grade realism; bass rendering quality matters only when Phase 7 extends output scope. |

## Open questions for later phases

- **Phase 5:** synthetic test-set composition (canonical + how many
  stochastic variants per test song); use `realized.note_onsets` for exact
  onset/pitch F1 on synthetic; define the scorecard's synthetic vs real
  sections.
- **Phase 6:** design the transfer measurement that owns the VST-escalation
  gate; curriculum schedule as a filter over recipe metadata (clean →
  degraded); mix-only vs mix+`gtr_bus` ablation; report whether NAM-stage
  throughput constrains pretraining data volume.
- **Phase 2 (conditional):** if the CTC forced-aligner triggers, confirm
  `render_meta.realized` fields suffice as its training export (e.g. does it
  also want per-note offsets/velocities?); additive schema change if so.
- **Phase 1 / ops:** render cadence as the corpus grows (render-on-arrival
  vs batch); disk budget ownership at 10k songs; whether discovery should
  prioritize under-represented timbre/tuning material given synthetic
  coverage now includes audio-less tabs.
- **Phase 7:** bass output extension can reuse this renderer unchanged;
  evaluate whether drum rendering realism needs an upgrade if drums ever
  enter output scope.
