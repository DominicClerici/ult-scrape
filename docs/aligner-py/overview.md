# aligner-py — overview

> Part of the [documentation map](../../OVERVIEW.md). The **fourth** decoupled
> project. It reads decoded tabs and their source audio from the shared `output/`
> tree and aligns the `.gp` symbolic score to the real recording, writing an
> `align.json` sidecar per tab. It shares no code with `scraper-py`, `decoder-rs`,
> or `enricher-py`.

## What it does

For each `output/<tab_id>/` that has a `metadata.json`, at least one decoded `.gp`,
and a `audio.*` file, the aligner renders the `.gp` to a pitch-accurate reference
WAV (via MuseScore 4 CLI → FluidSynth), extracts chroma-CENS features from both the
reference and the real recording, and aligns them with a **gap-aware**, trusted-tempo
DTW pipeline. The result is a monotonic **warp function** (anchor pairs mapping
symbolic time → real time), an explicit **`gaps`** list (real-audio dead regions),
a **`coverage`** health metric, and two **confidence metrics** (`fit_cost`,
`path_deviation`, computed on active regions only). If `fit_cost`, `path_deviation`,
and `coverage` are all within the configured thresholds the tab is marked `ok`;
otherwise `rejected`. The output is written to `align.json` (written last as the
commit marker). See the [output contract](../output-contract.md) for the artifact
details.

### Gap-aware tempo alignment

A `.gp`'s notated tempo is often a constant factor off the real recording (or
notated in half/double-time), and dead stretches — silent intros/outros or
mid-song breaks the tab doesn't account for — pull a naive DTW path off-diagonal
and, worse, **corrupt tempo estimation** if the tempo is derived from that same
path (a long gap tilts the regression line). The aligner (`align_gap_aware`)
breaks that circular dependency by detecting gaps *before* estimating tempo, and
trusts the notated tempo as the primary source rather than deriving it:

1. **Detect silence/activity (tempo-free).** RMS-energy envelope the real audio
   (`GAP_FRAME_S` frames, `SILENCE_RMS_DB` floor) to find dead regions —
   contiguous below-floor spans of at least `MIN_GAP_S` — tagged `lead`,
   `trailing`, or `internal`. This needs no tempo knowledge, so it runs first and
   cannot be skewed by a bad tempo guess.
2. **Coarse DTW at the notated tempo.** Render the reference once at the score's
   own tempo and run one `dtw_path` against the real audio (both trimmed to their
   detected active span). This path is used only to estimate tempo and seed gap
   placement, not as the final warp.
3. **Robust tempo on active regions only.** Fit the path slope (`robust_tempo`)
   while masking out any path frame that falls in a detected dead region on
   either side, plus one MAD outlier-rejection pass. Excluding dead frames is
   what keeps a long gap from tilting the tempo estimate — the fix for the
   circular-dependency failure the old two-pass design had.
4. **Snap to a clean factor, or DTW-fallback.** `snap_tempo_factor` snaps the
   robust ratio to the nearest of `TEMPO_SNAP_FACTORS` (`0.5, 1, 1.5, 2, 3` —
   covers half/double/triple-time notation) if within `TEMPO_SNAP_TOL`;
   `tempo_source` records which (`notated`, `notated_x2`, …). An off-grid ratio
   falls back to the raw DTW-derived value, clamped to `[TEMPO_MIN, TEMPO_MAX]`
   (`tempo_source: "dtw_fallback"`). The reference is **re-rendered only if the
   chosen factor ≠ 1** (pitch-exact tempo scaling, not a resample); at factor 1
   the coarse-pass DTW is reused instead of re-rendering and re-aligning.
5. **Gap-aware compose + gaps + coverage.** Build the final anchor warp so that,
   in a real-audio dead region, the warp holds symbolic time and advances real
   time (a steep segment) instead of following a meaningless chroma match on
   silence. Emit the detected dead regions as `gaps` on the **original,
   untrimmed** real timeline, and compute `coverage` — the fraction of the
   symbolic timeline that maps to real content outside every gap. `mode` is
   `"global"` (warp is a 2-point line) only when there are **no internal gaps**
   and the residual `path_deviation` is ≤ `TEMPO_RESIDUAL_THRESHOLD`; an internal
   gap always forces `"local"` mode (elastic warp with a gap-holding segment)
   even at constant tempo.

**Why this ordering matters:** tempo must be estimated *after* gap detection,
never before — deriving tempo from a DTW path that includes a long silent
stretch tilts the fit and corrupts every downstream metric (the exact failure
this pipeline fixes). Because real-audio silence detection needs no tempo
information, it can safely run first and break that circularity.

This implements **strategy C** from the design spec
([2026-06-29-aligner-py-vertical-slice-design.md](../superpowers/specs/2026-06-29-aligner-py-vertical-slice-design.md)):
hybrid training data — synthetic audio (perfectly aligned by construction) for bulk
training, with a small set of real-audio-aligned tabs for fine-tuning / evaluation.
The same renderer built here is the reference front-end for that synthetic branch.
The gap-aware pipeline itself supersedes that spec's original "two-pass tempo
alignment" section per the follow-up
[trusted-tempo + gap-aware alignment design](../superpowers/specs/2026-07-01-aligner-tempo-gap-redesign-design.md).

## Module map

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings (`.env`): output dir, soundfont path, tool paths, feature/DTW params. |
| `app/discover.py` | Walk `output/`; per-tab readiness + filesystem state (`find_tab`, `iter_ready_tabs`, `find_gp`, `find_audio_file`, `read_align_status`). |
| `app/render.py` | `.gp` → MIDI (MuseScore CLI) → reference WAV (FluidSynth). Injectable `Renderer` with configurable binaries and soundfont. `render_corrected` re-renders at a globally scaled tempo via `scale_midi_tempo` (mido rewrites every `set_tempo` event; pitch-exact), reusing `ref.mid` so MuseScore runs once. The MuseScore step tolerates a nonzero exit when a non-empty MIDI was produced: MuseScore 4 on macOS exports correctly but can SIGABRT during teardown ("mutex lock failed") *after* the file is flushed, so output-presence (not exit code) is the success signal there; FluidSynth is still gated strictly on exit code. |
| `app/features.py` | Chroma-CENS extraction and audio loading (`chroma_cens`, `load_audio`, `hop_seconds`, `energy_envelope`, `detect_dead_regions`). `load_audio` decodes to mono float32 via libsndfile (soundfile) first — tool-free, covers WAV/FLAC/OGG/Opus — and falls back to an `ffmpeg` subprocess for containers libsndfile can't open (e.g. WebM), sidestepping librosa's deprecated audioread path. `energy_envelope` computes a tempo-free per-frame RMS-in-dB envelope; `detect_dead_regions` thresholds it into `(start_s, end_s, kind)` dead regions (`lead`/`trailing`/`internal`). No filesystem writes. |
| `app/align.py` | Pure DTW building blocks: `dtw_path`, `estimate_tempo` (naive path-slope tempo, used as the safety-net fallback), `robust_tempo` (masked/MAD-filtered path-slope tempo excluding dead-region frames), `snap_tempo_factor` (snap a ratio to a clean half/double/triple-time factor or fall back to the raw ratio), `path_to_anchors`, `path_deviation`, `compose_anchors` (gap-aware: folds tempo ratio + silence leads + final-pass residual into symbolic→real anchors, holding symbolic time across real dead regions), `coverage` (fraction of the symbolic timeline mapped outside every gap), and `align_features` (single-pass primitive, still used as the simple/fallback path). `AlignResult` = anchors, `fit_cost`, `path_deviation`, `offset_s`, `tempo_ratio`, `mode`, `gaps`, `coverage`, `tempo_source`. |
| `app/pipeline.py` | Gap-aware orchestration (`align_gap_aware`, `align_tab`): detect real-audio dead regions (tempo-free) → coarse DTW at notated tempo → robust tempo on active regions → snap to a clean factor / DTW-fallback → re-render only if the factor ≠ 1 → final DTW → gap-aware compose + `gaps` + `coverage` → write `align.json`. `align_tab` folds `coverage` (alongside `fit_cost`/`path_deviation`) into the `ok`/`rejected` decision. The only module that drives rendering during alignment. |
| `app/output.py` | Atomic commit of `align.json` via temp + `os.replace`. |
| `app/inspect.py` | Build the listen overlay (`align_overlay.wav`) and look plot (`align_plot.png`). Developer-facing; not part of the consumed contract. |
| `app/cli.py` | `run` / `inspect` / `status` entry points. |

## Readiness gates & idempotency

- A tab is **ready** when `metadata.json` is present (the scraper's commit marker).
- `align run` additionally requires a decoded `.gp` file; if none is found the tab
  gets `status: "no_gp"` and is not retried automatically.
- `align run` additionally requires an `audio.*` file (the enricher's commit marker);
  if none is found the tab gets `status: "no_audio"`.
- **`align.json` is the aligner's commit marker.** A tab is considered done when
  `align.json` exists; `align run` re-aligns and overwrites it unconditionally for
  every tab you name — there is no automatic skip.
- A re-scrape `rmtree` wipes `align.json` along with all other artifacts — the tab
  will be re-aligned on the next `align run`. See
  [output contract — re-scrape](../output-contract.md#re-scrape--idempotency).

## `align.json` schema

```json
{
  "aligned_at": "2026-06-29T12:00:00",
  "aligner_version": "0.1.0",
  "confidence": { "fit_cost": 0.12, "path_deviation": 0.03 },
  "offset_s": 1.84,
  "tempo_ratio": 1.034,
  "tempo_source": "notated",
  "mode": "local",
  "coverage": 0.86,
  "gaps": [
    { "real_start_s": 0.0, "real_end_s": 1.84, "kind": "lead" },
    { "real_start_s": 132.0, "real_end_s": 190.5, "kind": "internal" }
  ],
  "source": { "gp": "tab-download-ssid-1910943.gp", "audio": "audio.opus" },
  "status": "ok",
  "tools": { "fluidsynth": "fluidsynth", "musescore": "mscore", "soundfont": "FluidR3_GM.sf2" },
  "warp": [[0.0, 1.84], [131.2, 132.0], [131.7, 190.5], [157.1, 216.0]]
}
```

| Field | Meaning |
|---|---|
| `status` | `ok` — `fit_cost`, `path_deviation`, **and** `coverage` all within threshold. `rejected` — aligned but one of those missed. `no_gp` — no decoded `.gp` found. `no_audio` — no `audio.*` found. |
| `source.gp` / `source.audio` | Filenames of the inputs used (null when not applicable). |
| `confidence.fit_cost` | Mean cosine distance along the final DTW path, computed on active regions only (dead frames excluded; lower = better). |
| `confidence.path_deviation` | Residual deviation of the final path from a straight diagonal, active regions only (also part of the global-vs-local decision). |
| `offset_s` | Estimated time offset (seconds) of the real recording relative to the symbolic score (= `warp[0]` real time; equals the end of a leading gap when one exists). |
| `tempo_ratio` | Tempo correction applied on top of the notated tempo (real seconds per symbolic second). `1.0` means the notated tempo already matched; the reference is only re-rendered when this is not `1.0`. |
| `tempo_source` | Where `tempo_ratio` came from: `"notated"` (no correction), `"notated_x2"` / `"notated_x0.5"` / `"notated_x1.5"` / `"notated_x3"` (snapped to a clean half/double/triple-time factor within `TEMPO_SNAP_TOL`), or `"dtw_fallback"` (no clean factor fit; raw DTW-derived ratio used, clamped to `[TEMPO_MIN, TEMPO_MAX]`). |
| `mode` | `"global"` — one constant tempo explains the song **and** there are no internal gaps (`warp` is a 2-point line). `"local"` — a residual elastic warp was kept, or an internal gap forced it (`warp` has ~`step_s`-spaced anchors, with steep gap-holding segments across dead regions). |
| `coverage` | Fraction of the symbolic timeline `[0, s_max]` that warps to real content outside every gap. Low `coverage` flags a tab that only matched part of the recording even when `fit_cost`/`path_deviation` look fine. |
| `gaps` | Real-audio dead regions detected by RMS energy, on the **original, untrimmed** real timeline: `{real_start_s, real_end_s, kind}` with `kind` one of `"lead"`, `"trailing"`, `"internal"`. Consumers should drop any window overlapping a gap. |
| `warp` | `[symbolic_time_s, real_time_s]` anchor pairs (2 points in global mode with no internal gaps; downsampled ~every `step_s` seconds otherwise, with steep segments across gaps). Consumers interpolate between anchors. |
| `tools` | Tool names/paths used at align time (MuseScore, FluidSynth, soundfont). |
| `aligned_at` | ISO-8601 timestamp at align time. |
| `aligner_version` | `app.__version__` at align time. |

`confidence`, `offset_s`, `tempo_ratio`, `tempo_source`, `mode`, and `coverage`
are `null` (and `warp`/`gaps` are `[]`) for `no_gp` and `no_audio` statuses.

## Inspection artifacts

`align inspect <tab_id>` produces two developer-facing files that are **not** part of
the consumed contract (they can be deleted without consequence):

- `align_overlay.wav` — a stereo file with the real audio panned hard-right and an
  alignment sonification panned hard-left. Play it in stereo (not collapsed to mono)
  and listen for the two ears staying locked through the song. The left channel has
  two modes:
  - default — a **click track**: short identical clicks at the reference's detected
    onsets warped onto the real timeline. Monotone tapping is expected; only the
    *timing* carries information (do the taps land on the beats?).
  - `align inspect <tab_id> --music` — the **rendered reference audio** itself,
    re-rendered at the stored `tempo_ratio` (pitch-exact) and rebased onto the real
    timeline, then loudness-matched to the real channel, so you hear both as music
    side by side. In `global` mode this is a pure time-shift with no pitch wobble;
    in `local` mode only the small residual warp stretches. More intuitive than
    clicks for spotting drift.
- `align_plot.png` — real audio chroma / spectrogram with warped note onsets drawn
  on top, plus the DTW warping path and cost curve. Drift shows up as onsets sliding
  off the energy; use this view to calibrate confidence thresholds. Detected gaps
  (from `align.json`'s `gaps` list) are shaded as translucent red spans on the
  chroma axis.

## Commands

```bash
cd aligner-py
pip install -e ".[dev]"          # needs MuseScore + FluidSynth + ffmpeg on PATH
align run <tab_id> [<tab_id> …]  # render → align → write align.json per tab
align inspect <tab_id> [--music] # build align_overlay.wav + align_plot.png
                                 #   --music: left = warped reference audio, not clicks
align status [<tab_id> …]        # counts by align.json status (all tabs if no ids given)
python3 -m pytest                # unit tests (tool-free by default)
python3 -m pytest -m integration # end-to-end; needs mscore + fluidsynth + a fixture
```

Configuration is read from a `.env` file (or environment variables); see
`app/config.py` for the full key list (`OUTPUT_DIR`, `MUSESCORE_BIN`,
`FLUIDSYNTH_BIN`, `SOUNDFONT`, `SAMPLE_RATE`, `HOP_LENGTH`, `STEP_S`,
`FIT_COST_THRESHOLD`, `DEVIATION_THRESHOLD`, `TEMPO_RESIDUAL_THRESHOLD`,
`TEMPO_MIN`, `TEMPO_MAX`, `MIN_GAP_S`, `SILENCE_RMS_DB`, `GAP_FRAME_S`,
`TEMPO_SNAP_FACTORS`, `TEMPO_SNAP_TOL`, `COVERAGE_THRESHOLD`). The gap-aware
pipeline's keys: `MIN_GAP_S` is the shortest below-floor span counted as a dead
region, `SILENCE_RMS_DB` is the RMS-energy floor (dBFS) for the tempo-free
silence detector, `GAP_FRAME_S` is that detector's envelope frame length,
`TEMPO_SNAP_FACTORS`/`TEMPO_SNAP_TOL` control snapping the robust tempo fit to a
clean half/double/triple-time factor, and `COVERAGE_THRESHOLD` is the minimum
`coverage` for `ok` status. `TEMPO_RESIDUAL_THRESHOLD` is still the
global-vs-local cutoff (subject to the no-internal-gaps rule above), and
`TEMPO_MIN`/`TEMPO_MAX` still clamp the `dtw_fallback` tempo ratio.
`SILENCE_TOP_DB` (the old leading/trailing `trim_silence` threshold) has been
**removed** — it is superseded by the `SILENCE_RMS_DB`/`detect_dead_regions`
energy detector, which covers leading, trailing, and internal silence uniformly.

## Deferred / future

Named here so they are not lost when the slice proves out (from §9 of the design spec):

- Transposition / key-shift search (try chroma rotations; record detected shift).
- Capo & tuning normalization.
- Subsequence / partial DTW for structural mismatch (missing solos, differing
  repeats), with trimming/flagging.
- Stem-assisted chroma (run Demucs `htdemucs_6s`, align on a harmonic stem).
- SQLite work queue + `scan`/backoff/recovery for corpus scale (mirror
  `enricher-py`).
- Auto-gating thresholds derived from the slice's calibrated confidence metrics.
- Training-grade multi-stem synthetic renderer for the synthetic-audio branch.
- Possible rename to a broader `dataset-py` once the shared renderer + synthetic
  corpus + stem separation live alongside alignment.
