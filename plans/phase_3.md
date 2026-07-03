# Phase 3 — Representation: tokenizer + dataset builder

> Expanded from [the roadmap](../docs/roadmap.md#phase-3--representation-tokenizer--dataset-builder)
> in the 2026-07-01 planning session. Decisions here are **binding inputs** to
> later phases. All vocabulary decisions below are grounded in a census run
> over the full pilot corpus (509 `.gpif`s, 212k notes, 288k beats) during
> this session — see "Corpus census" under Design.

## Goal & scope

Define the model's output language and the machinery that produces training
data in it:

- **The token vocabulary + tokenizer/detokenizer** (`gpscore.tokens`):
  score-time tokens with per-bar time anchors, string+fret notes, factored
  symbolic rhythm, a census-justified technique subset, multi-track-capable
  serialization.
- **The GPIF writer** (`gpscore`, deferred here from Phase 2a) and the
  corpus-wide **round-trip tests** that make "decodable back to a valid
  score" a tested contract.
- **The dataset builder** (`dataset-py/`): walks `manifest/` + `output/`,
  emits per-song training records (symbolic + aligned audio), with windowing
  and tokenization performed at *load time*, not bake time.
- **Augmentation design**: tuning-shift transposition (offline), audio-domain
  augs + mild time-stretch (on-the-fly).

**Out of scope:** model architecture and training (Phase 6); synthetic
rendering (Phase 4 — but the record contract for it is fixed here); metrics
(Phase 5); inference-time window stitching and cross-window track matching
(Phase 8/9 — but the anchor/serialization design here is what makes them
possible); true re-fingering augmentation (later phase, needs a playability
model); drum/vocal token vocabularies (later scope — the format reserves room);
running Demucs in production (Phase 6 ablation decides).

**Sequencing note:** Phases 0 and 2 are planned but **not yet implemented**
(no `score-py/`, `audit-py/`, `aligner-py/`, or `manifest/` exist on disk).
This plan consumes their *contracts*. The tokenizer + writer + round-trip
tests need only `gpscore` (Phase 2a); the dataset builder over real audio
additionally needs `manifest/alignment/` (Phase 2b). Implementation can be
staged accordingly.

## Inputs / outputs

**Consumes:**

- `gpscore` 1.0 (Phase 2a): document model (exact-`Fraction` rhythm,
  technique superset) + `performance()` view + expanded bar/beat grid.
- `manifest/manifest.jsonl` (Phase 0): verdicts, splits, per-tab metadata.
- `manifest/alignment/<tab_id>.json` + `alignment.jsonl` (Phase 2b):
  per-segment tiers and warps; the segment times in both score-qn and real
  seconds are exactly what the window cutter consumes.
- `output/` (read-only, frozen contract): `.gpif` + `audio.<ext>`.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Tokenizer/detokenizer + vocab | `score-py/` (`gpscore.tokens`) | Code — versioned vocabulary; the model's language, shared by Phases 5/6/8/9 |
| GPIF writer | `score-py/` (`gpscore`) | Code — GP7/8 `.gpif` dialect + `.gp` ZIP packaging; doubles as Phase 8's export path |
| Round-trip test suite | `score-py/tests/` | Code — corpus-wide tokenizer and writer round-trips under the modeled projection |
| Dataset builder | `dataset-py/` | Code — decoupled CLI (`scan`/`run`/`status`), mirrors `enricher-py` |
| Dataset snapshots | `dataset/<snapshot>/` (`songs/<tab_id>.…` + `index.jsonl`) | Derived — per-song records; snapshot ID = index hash |
| Build report | `dataset/<snapshot>/report.md` | Derived — coverage, drop accounting, window statistics |

**Later-phase consumers:** Phase 4 writes synthetic renders in the same
record shape (`source: synthetic`); Phase 5 evaluates in this vocabulary;
Phase 6 trains on snapshots; Phase 8 uses the detokenizer + writer for
export.

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| **Output time base** | **Score-time tokens + per-bar time anchors**: DadaGP-style symbolic tokens (the output *is* the tab), with each `BAR` token carrying a coarse absolute-time anchor (bar onset, ~100 ms bins relative to window start) — the Whisper-timestamp trick | Performance-time events (MT3-style: proven, but only `onset_grade` real data usable, notated rhythm never modeled, Phase 8 must build beat-tracking + quantization); pure score-time (no timing grounding → desync risk); defer to a post-Phase-2 gate (leaves every other decision designing against an unknown) | Output is directly a readable tab (Phase 8 shrinks to validation); **`beat_grade` alignment suffices** for real-audio training (anchor supervision at ~100 ms is what beat-grade provides) — this de-risks Phase 2 and weakens the CTC-escalation case; synthetic data (the training bulk) has exact bars by construction; anchors give the decoder timing grounding and make stitching/error-localization tractable. Phase 2's "choose from the measured tier distribution" is honored by making the choice *robust to* the distribution rather than dependent on it. |
| **Time-base fallback safeguard** | Dataset records store **symbolic windows + warp, never token IDs**; tokenization is a load-time view | Precomputed token shards (every vocab tweak = full rebuild) | If score-time training fails on *synthetic* audio in Phase 6 (a representation bug, detectable early/cheap), falling back to performance-time is a tokenizer swap, not a data rebuild. |
| **Multi-track serialization** | **Bar-major interleave**: per window — header, then per bar: `BAR` + anchor (+ time-sig/tempo on change), then per track: `TRACK<i>` + that track's beats | Per-track decode passes (inference ×N, needs external track-existence prediction, cross-track bar consistency unenforced); track-major concatenation (breaks audio↔token locality, duplicates anchors) | v1 already needs it (corpus songs have 2–4 guitar tracks); anchors shared once per bar; audio↔token temporal locality; adding bass later = more `TRACK` sections, no redesign. Dense 30 s/4-track windows ≈ 1.5–3k tokens — fits a 4k-context decoder. |
| **Tuning/capo/instrument metadata** | **Predicted window header**: decoder's first tokens declare per-track `TRACK<i> KIND TUNING(per-string pitches) CAPO` | Conditioning input only (Phase 9 then needs a standalone tuning detector; a wrong input silently corrupts every fret) | Tuning inference becomes part of the learned task (Phase 8 wanted it anyway); since the header is a decode *prefix*, it can be **forced** at inference when known — prediction subsumes conditioning. Stitching majority-votes headers across windows. Census: 40+ distinct tunings → per-string pitch encoding, not a named-tuning enum. |
| **Note token shape** | **Fused `NOTE(string,fret)`** — one token per note, ~300 vocab entries | Factored `STRING`+`FRET` (2 tokens/note for negligible vocab savings); pitch+string (fret derived via predicted tuning — header errors silently corrupt frets; round-trip via derivation) | DadaGP-proven; halves note-token count; round-trips exactly; pitch stays implicit via header tuning. |
| **Rhythm encoding** | **Factored symbolic**: `DUR:<value>.<dots>` per beat (8 values × 3 dot states) + `TUPLET:num:den` group token (~13 observed ratios) | Tick/bin durations (destroys notation: dotted-quarter ≡ quarter+tied-eighth; round-trip unpassable); fully fused value×dot×tuplet tokens (~300 mostly-unobserved sparse entries) | Maps 1:1 onto `gpscore` `Fraction` rhythms → trivial round-trip. Census: plain values cover ~97 % of beats; tuplets are almost entirely 3:2 (8.6k of ~9k tuplet beats). |
| **Beat position** | Implicit (durations accumulate within a bar; each `BAR`+anchor re-synchronizes) | Explicit beat-position tokens (redundant under bar-major serialization) | Drift can never cross a barline. |
| **Voices** | Collapse to voice 0; multi-voice bars raise a structured warning | Voice tokens | Measured: 54 multi-voice bars in 4 songs, of 354k non-empty bars. Not a modeling target. |
| **Technique subset (v1)** | **Tier 1 (census-justified, 12 techniques)**: slide (bitmask decomposed: shift/legato/in-from-below/in-from-above/out-down/out-up), brush up/down, staccato, accent, dead note, ghost note, let-ring, HOPO, grace (before/on), bend, palm-mute, vibrato (collapsed to one flag), harmonics (`HARM:<type>`) | Leaner TabCNN-era set (discards let-ring/vibrato/staccato/brush the corpus has in bulk); maximal ≥2-song coverage (tail techniques with 1–5 song coverage are unlearnable supervision noise) | Selection principle: coverage ≥ ~150 songs *or* acoustically critical; every Tier-1 item has ≥ 64-song coverage and a plausible acoustic signature. Whammy (28 songs), arpeggio, hairpin, fade, tap/slap/pop/trill (< 6 songs each) are parsed by `gpscore` but **dropped with accounting** by the tokenizer. |
| **Bend encoding** | **Quantized 3-point**: `BEND(origin, middle, dest)` in quarter-tone steps (GP's native 25 % increments); timing offsets dropped, default curve reconstructed on export | Binary flag (bend vs bend-and-release vs prebend audibly and notationally distinct — lost); full 5-point curve + offsets (large sub-vocabulary for ~2k instances) | Distinguishes bend / release / prebend / bend-and-release — what a tab reader needs — at ~3 tokens per bend. |
| **Dynamics** | **Dropped from the vocabulary** (declared unmodeled) | 8 dynamic-level tokens | Census: 94 % MF/F — transcriber-default noise, near-zero information. Ghost/accent/staccato carry the real local emphasis. Revisit later if evidence appears. |
| **GPIF writer target** | **Native GP7/8 `.gpif` dialect + `.gp` ZIP packaging, in `gpscore`** (write one dialect; read two) | PyGuitarPro/GP5 (second lossy dialect mapping, discarded when Phase 8 wants modern `.gp`); MusicXML-primary (weak tab/technique support — fine as a Phase 8 *extra* export) | One fidelity-bearing format; 509 corpus files as references; becomes Phase 8's export path directly. GPIF's fiddly corners mitigated by model-equality (not byte-equality) testing + real-app spot-checks. |
| **Round-trip contract** | **Modeled projection** `project(score)` (keeps exactly what the vocabulary claims) + two corpus-wide CI tests: tokenizer round-trip `project(score) == project(detok(tok(score)))` and writer round-trip `project(parse(write(score))) == project(score)`; plus manual Guitar Pro / alphaTab spot-checks | Byte-identical XML (impossible and pointless); spot-checks only (the exact way fidelity bugs hide) | Makes the roadmap's "preserve what we claim to model" a mechanical, corpus-wide assertion. The projection also produces per-song **drop accounting**. |
| **Window geometry** | **Bar-aligned, variable length, ~20 s target** (caps: ~30 s audio / ~2k target tokens, whichever binds; oversized single bars dropped with accounting); audio padded to fixed input length | Fixed-duration slices (partial bars → MT3 tie-section hack, output no longer a valid score fragment, anchors lose barline-resync semantics); 30 s target (dense 4-track windows push past 3k tokens) | Barline cuts make windows self-contained — a note held across a barline already has a notehead in the new bar (60k ties in corpus), so no special sustain handling. Windows are exactly renderable score fragments. Inference uses fixed-duration audio slices; stitching on predicted anchors is Phase 8/9. |
| **Window sourcing** | Real-audio windows must be fully covered by `onset_grade` ∪ `beat_grade` segments; `unusable` breaks the grid. Windows are **sampled** (random bar offsets per epoch) at load time. Tacet/rest-only windows included but **downsampled** (ratio tunable) | Fixed window grid (loses free augmentation); excluding tacet windows (model never learns silence → rests) | Enabled by song-level storage; consumes Phase 2's segment tiers exactly as designed. |
| **Transposition augmentation** | **Tuning-shift**: shift audio ±N semitones, transpose header tuning tokens, keep string/fret identical — exact by construction. Applied offline (builder flag). The same transform **rescues Phase 2's transposed pairs** (chroma rotation ≠ 0) into training | Punt transposition entirely (loses transposed-pair rescue; model inherits census E-standard dominance); re-fingering now (requires inventing a playability model) | Answers Phase 2's open question ("whether/how to consume transposed pairs"): yes, via header-tuning annotation — no fret rewriting needed. |
| **Audio-domain augmentation** | EQ / noise / reverb / gain — **on-the-fly** in the training dataloader; **mild time-stretch (±10 %)** on-the-fly with anchor rescaling | Baked into stored data (multiplies disk, freezes aug policy) | Label-free transforms belong at load time; Phase 4 renders at arbitrary tempo natively, so aggressive time-stretch on real audio is unnecessary. |
| **Source separation (Demucs)** | **N-channel input contract** (mix = 1 channel; mix + guitar stem = 2); stems an *optional* builder product behind a flag; mix-only vs mix+stem decided as a **Phase 6 ablation** | Bake separation in (all training + inference pays Demucs cost/artifacts before evidence); mix-only contract (retrofitting a channel later touches dataset format and model input layer) | Cheap to reserve, expensive to retrofit; consistent with Phase 2's evidence-first Demucs stance. |
| **Tokenizer home** | **`gpscore.tokens`** — leaf subpackage of `score-py/`, beside the writer | Inside `dataset-py` (inference/eval would import a corpus-walking CLI to decode tokens); own `tabtok-py/` (third project for one module; version-skew surface) | Pure symbolic transform travels with the model it transforms; preserves the roadmap's "one deliberate shared dependency" invariant; vocab versions with the `gpscore` API. |
| **Dataset storage** | **Per-song record files + `index.jsonl`** under `dataset/<snapshot>/`; snapshot ID = index hash | WebDataset shards now (cloud-streaming solution to a problem the local-GPU regime doesn't have); HF `datasets`/parquet (heavy dependency, clunky audio inspection, no local-first gain) | Dynamic window sampling wants local random access; debuggable/greppable; mechanically convertible to shards if Phase 7 cloud runs demand it. |
| **Record audio format** | **Mono FLAC @ 24 kHz** inside records (builder parameter) | Source-rate 48 kHz (2× disk for headroom no MT3/MERT-class frontend uses); 16 kHz (forecloses 24 kHz encoders like MERT) | Covers the realistic encoder space; `output/` retains originals, so a rebuild can re-decode at any rate — the call is reversible. |
| **Phase 4 contract** | Synthetic renders enter as the **same record shape**: same symbolic dump, trivial/perfect alignment, `source: "synthetic"` + variant id vs `source: "real"` | A separate synthetic pipeline/format | Roadmap requirement ("synthetic vs real is just a manifest flag") made concrete; builder and dataloader are source-agnostic. |

## Design

### Corpus census (measured 2026-07-01, full 509-song pilot corpus)

Facts the vocabulary is built on (`212,165` notes, `287,839` beats, 0 parse
failures with stdlib XML):

- **Rhythm**: plain NoteValues Whole…64th with 0–2 dots ≈ 97 % of beats;
  128th appears 8×. Tuplets: 3:2 dominates (≈8.6k beats); observed tail =
  2:2(?), 6:4, 5:4, 4:4, 2:3, 5:3, 7:6, 7:8, 6:6, 9:8, 7:4 — all supported
  as `TUPLET:num:den` tokens. No free-time bars.
- **Ties**: 60.5k endpoints in 507/509 songs.
- **Time signatures** (songs containing): 4/4 451, 6/8 52, 2/4 46, 3/4 32,
  6/4 17, 5/4 14, 12/8 10, 7/8 7, …, 13/16 1 → compositional `TS:num`/`TS:den`.
- **Tunings**: E-std 3,844 tracks; 40+ distinct tunings; string counts
  6 (5,279), 4 (547), 5 (288), 7 (32), 8 (29), 10 (5).
- **Techniques** (occurrences / songs): slide 8,192/459, brush 3,301/472,
  staccato 6,026/400, accent ~1,640/400, ghost 6,520/345, dead 5,198/353,
  let-ring 12,673/333, HOPO ~5,000/330, grace 1,047/263, bend ~1,950/204,
  palm-mute 4,154/200, vibrato 1,694/158, harmonics ~750/64; whammy
  63 beats/28 songs; tap 68/5, slap 42/2, pop 38/3, trill 7/1, glide 4/1.
- **Dynamics**: 94 % MF/F. **Voices**: 54 multi-voice bars / 4 songs.

### Token vocabulary (v1 sketch)

Estimated total ≈ **500–600 tokens**:

```text
Window   := Header Bar+
Header   := WINDOW_START TrackDecl+
TrackDecl:= TRACK<i> KIND:<guitar|bass|…> STRPITCH:<midi>{n_strings} CAPO:<n>
Bar      := BAR ANCHOR:<t>            # t = bar onset, ~100ms bins in window
            [TS:<num> TS:<den>] [TEMPO:<bin>]     # on change only
            (TRACK<i> Beat+)+
Beat     := [GRACE:<before|on>] DUR:<value>.<dots> [TUPLET:<n>:<d>]
            [BeatFlags] (NOTE(string,fret) NoteFlags* | REST)+   # chord = multiple NOTEs
BeatFlags:= BRUSH:<up|down>
NoteFlags:= TIE | HOPO | PM | DEAD | GHOST | LETRING | STACCATO | ACCENT
          | VIBRATO | HARM:<type> | SLIDE:<kind>
          | BEND_O:<q> BEND_M:<q> BEND_D:<q>       # quarter-tone steps
```

Canonical serialization order (determinism → unique training targets):
tracks by GPIF index; notes within a beat by string number ascending; flags
in a fixed order. The detokenizer is total over well-formed sequences and
error-tolerant at inference (malformed spans skipped with diagnostics, never
crashes — Phase 8 relies on this).

### `gpscore` additions (in `score-py/`)

- `gpscore.tokens`: `Vocab` (versioned, serialized with snapshots),
  `tokenize_window(score_slice) -> list[int]`,
  `detokenize(tokens) -> ScoreFragment` (+ diagnostics), `project(score)`
  (the modeled projection), drop-accounting helpers.
- `gpscore.write_gpif(score) -> bytes` + `gpscore.write_gp(score, path)`
  (ZIP packaging). GP7/8 dialect only.
- Round-trip suites as described; fixtures from both input dialects.

### `dataset-py/` — the dataset builder

House-pattern CLI (`scan` / `run --jobs N` / `status`; SQLite queue; reads
`manifest/`, `output/`, writes `dataset/<snapshot>/`; never writes into
inputs). Pipeline per tab:

1. Eligibility: manifest verdict `ok` (or transposed-`suspect` when the
   tuning-shift rescue flag is on) + alignment status `aligned` with usable
   segments; split label attached from the manifest.
2. Parse `.gpif` → `Score`; compute the modeled projection + drop accounting.
3. Decode audio → mono FLAC @ 24 kHz (builder param). Optional Demucs stem
   behind `--stems`.
4. Emit record: symbolic dump (document + performance view, JSON),
   alignment segments (from `manifest/alignment/`), audio blob(s),
   provenance (`source`, hashes of inputs, `gpscore`/vocab versions).
5. `index.jsonl` (one line per record, sorted, deterministic) + `report.md`
   (coverage, drop accounting aggregates, window statistics, split counts).

Records are **song-level**; the training dataloader (thin library shipped in
`dataset-py`, importable by Phase 6) does window sampling (bar-aligned,
target ~20 s, tier-gated), tokenization via `gpscore.tokens`, on-the-fly
audio augs, and tacet downsampling.

**Idempotency/staleness**: input-fingerprint keyed, mirroring the aligner
(`gpif_sha256`, `audio_sha256`, alignment fingerprint, vocab version).

### Project layout

```
score-py/          # Phase 0/2a; grows gpscore.tokens + writer (this phase)
dataset-py/        # new: builder CLI + window-sampling dataloader library
dataset/<snapshot>/{songs/…, index.jsonl, report.md}   # derived data
```

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Score-time decoding proves too hard even with anchors (the representation bet) | Detectable **early and cheaply**: a Phase 6 pilot on *synthetic* audio isolates representation from data noise. Fallback (performance-time MT3-style vocabulary) is a tokenizer swap, not a data rebuild, because records store symbolic data. |
| GPIF writer output rejected by real Guitar Pro despite passing our parser | Round-trip tests assert model equality; acceptance additionally requires opening a stratified sample in Guitar Pro / alphaTab by hand. 509 reference documents to diff against. |
| Sequence length blowout on dense multi-track windows | Measured cap: window cutter enforces ~2k target tokens (drops to a shorter bar run); build report publishes the token-length distribution so Phase 6 sizes context with data. |
| Track-identity ambiguity across windows (multiple guitars) | Canonical track order recorded at training; cross-window matching is Phase 8's, designed against header + content overlap. Anchors localize errors per bar. |
| Phase 2 tier distribution turns out mostly `unusable` (little real training data) | The time-base choice already minimizes exposure (`beat_grade` suffices); synthetic (Phase 4) carries the bulk regardless; the builder runs on synthetic records without alignment at all. |
| Drop accounting reveals the Tier-1 cut discards something loved (e.g. whammy-heavy corpus growth in Phase 1) | Accounting is aggregated per build report; vocab is versioned — additive technique promotion is a minor vocab bump + rebuild-free retokenization. |
| 24 kHz storage forecloses a future high-rate encoder | Originals stay in `output/`; sample rate is a builder param; rebuild is mechanical. |

## Acceptance criteria

- **Tokenizer round-trip**: `project(score) == project(detok(tok(score)))`
  holds for **100 % of parseable corpus scores**; failures are structured
  diagnostics, not crashes.
- **Writer round-trip**: `project(parse(write(score))) == project(score)`
  holds corpus-wide; ≥ 10 written `.gp` files verified by hand in Guitar Pro
  (or alphaTab) — load, render, look correct.
- Vocabulary serialized + versioned; documented token grammar; deterministic
  serialization (same score → identical token stream, twice).
- `dataset-py` builds a snapshot over the full eligible pilot corpus through
  the queue with resumability; re-run with unchanged inputs is a
  fingerprint-hit no-op; `index.jsonl` deterministic (snapshot = hash works).
- Build report shows: drop accounting by construct type, token-length and
  window-count distributions, tier-sourced seconds by split, transposed-pair
  rescue counts.
- Dataloader demonstrated end-to-end: yields (padded audio, token) batches
  with augmentation on; a rendered spot-check confirms window audio and
  detokenized notation correspond (listen + read).
- Unit tests deterministic and network-free by default; audio-heavy paths
  behind the `integration` marker (repo convention).
- Docs current per CLAUDE.md: `docs/score-py/` updated (tokens + writer),
  `docs/dataset-py/overview.md` written, `OVERVIEW.md` map + roadmap updated.

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Cross-window track matching + header voting (stitching) | Phase 8/9 scope; enabled by anchors + canonical order + forceable headers decided here; doesn't constrain the vocabulary. |
| Exact caps/bins (anchor bin width, token cap, tacet downsample ratio, time-stretch range) | Tunable code; the *fields and mechanisms* are fixed; build report data will set them. |
| Re-fingering augmentation (same tuning, new frets) | Needs a playability model; tuning-shift already provides pitch diversity; additive later. |
| Drum/vocal token vocabularies | Header `KIND` + track sections reserve the space; v1 emits guitar tracks only. Lyrics explicitly excluded (ASR, not AMT). |
| WebDataset/shard conversion | Mechanical transform of per-song records; only needed if training moves to cloud streaming (Phase 7). |
| MusicXML export | Phase 8 nicety; the fidelity-bearing path (`.gp` via the gpscore writer) exists after this phase. |
| Tempo-token design detail (`TEMPO:<bin>` granularity, whether emitted at all vs derived from anchors) | Anchors already carry timing; whether an explicit tempo token helps is a Phase 6-observable; additive vocab change. |

## Open questions for later phases

- **Phase 4**: render tempo/mix variants must write correct trivial
  alignments in the record shape fixed here; decide variant-count per song
  and disk budget; stems from renders are free (per-track stems!) — align
  that with the N-channel input contract.
- **Phase 5**: metrics must speak this vocabulary — define tab accuracy
  (string+fret exact match), technique F1 over Tier-1 flags, anchor timing
  error, and post-detokenization score-level comparison; reuse
  `gpscore.tokens.project` as the comparison basis.
- **Phase 6**: pilot on synthetic-only first to validate the score-time bet
  cheaply; run the mix-only vs mix+stem ablation; choose audio frontend
  knowing records are 24 kHz; report whether the ~2k token cap binds.
- **Phase 8**: stitching on predicted anchors; header majority-vote +
  track-matching algorithm; playability post-processing operates on the
  detokenized score, upstream of the writer built here.
- **Phase 2 feedback**: this phase's time-base choice means `beat_grade`
  segments are fully consumable — when Phase 2's calibration report lands,
  the CTC-escalation decision should weigh that (pressure is lower than
  Phase 2's plan assumed).
