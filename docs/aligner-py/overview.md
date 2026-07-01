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
reference and the real recording, and aligns them with a **two-pass** DTW. The
result is a monotonic **warp function** (anchor pairs mapping symbolic time → real
time) and two **confidence metrics** (`fit_cost`, `path_deviation`). If confidence
is within the configured thresholds the tab is marked `ok`; otherwise `rejected`.
The output is written to `align.json` (written last as the commit marker). See the
[output contract](../output-contract.md) for the artifact details.

### Two-pass tempo alignment

A `.gp`'s notated tempo is often a constant factor off the real recording, and
leading/trailing silence in either signal pulls the DTW path off-diagonal. The
aligner handles both:

1. **Trim silence** from the reference and the real audio (`librosa.effects.trim`,
   `SILENCE_TOP_DB`); the trimmed leads are folded back so the stored warp lives on
   the **original, untrimmed** real timeline.
2. **Pass 1 (coarse):** DTW at the score's tempo; a least-squares line through the
   warp path gives the global **tempo ratio** `r` (real seconds per symbolic
   second), clamped to `[TEMPO_MIN, TEMPO_MAX]`.
3. **Re-render** the reference with its MIDI tempo scaled by `r` (pitch-exact — a
   resample would shift chroma and break the second pass).
4. **Pass 2 (fine):** DTW against the tempo-corrected reference. If the residual
   `path_deviation` is ≤ `TEMPO_RESIDUAL_THRESHOLD` the song is explained by one
   constant tempo (`mode: "global"`, warp = a 2-point line); otherwise a local
   elastic warp is kept (`mode: "local"`). Either way `tempo_ratio` is recorded.

An implausible pass-1 ratio (outside the clamp) skips correction and falls back to
the single-pass local warp as a safety net (`tempo_ratio: 1.0`, `mode: "local"`).

This implements **strategy C** from the design spec
([2026-06-29-aligner-py-vertical-slice-design.md](../superpowers/specs/2026-06-29-aligner-py-vertical-slice-design.md)):
hybrid training data — synthetic audio (perfectly aligned by construction) for bulk
training, with a small set of real-audio-aligned tabs for fine-tuning / evaluation.
The same renderer built here is the reference front-end for that synthetic branch.

## Module map

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings (`.env`): output dir, soundfont path, tool paths, feature/DTW params. |
| `app/discover.py` | Walk `output/`; per-tab readiness + filesystem state (`find_tab`, `iter_ready_tabs`, `find_gp`, `find_audio_file`, `read_align_status`). |
| `app/render.py` | `.gp` → MIDI (MuseScore CLI) → reference WAV (FluidSynth). Injectable `Renderer` with configurable binaries and soundfont. `render_corrected` re-renders at a globally scaled tempo via `scale_midi_tempo` (mido rewrites every `set_tempo` event; pitch-exact), reusing `ref.mid` so MuseScore runs once. The MuseScore step tolerates a nonzero exit when a non-empty MIDI was produced: MuseScore 4 on macOS exports correctly but can SIGABRT during teardown ("mutex lock failed") *after* the file is flushed, so output-presence (not exit code) is the success signal there; FluidSynth is still gated strictly on exit code. |
| `app/features.py` | Chroma-CENS extraction and audio loading (`chroma_cens`, `load_audio`, `hop_seconds`, `trim_silence`). `load_audio` decodes to mono float32 via libsndfile (soundfile) first — tool-free, covers WAV/FLAC/OGG/Opus — and falls back to an `ffmpeg` subprocess for containers libsndfile can't open (e.g. WebM), sidestepping librosa's deprecated audioread path. `trim_silence` strips leading/trailing near-silence and returns the removed lead/trail durations. No filesystem writes. |
| `app/align.py` | Pure DTW building blocks: `dtw_path`, `estimate_tempo` (path slope = tempo ratio), `path_to_anchors`, `path_deviation`, `compose_anchors` (folds tempo ratio + silence leads + pass-2 residual into symbolic→real anchors), and `align_features` (single-pass primitive). `AlignResult` = anchors, `fit_cost`, `path_deviation`, `offset_s`, `tempo_ratio`, `mode`. |
| `app/pipeline.py` | Two-pass orchestration (`align_two_pass`, `align_tab`): render → trim → coarse DTW → estimate/clamp tempo → re-render corrected → fine DTW → decide global/local → write `align.json`. The only module that drives rendering during alignment. |
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
  "mode": "global",
  "source": { "gp": "tab-download-ssid-1910943.gp", "audio": "audio.opus" },
  "status": "ok",
  "tools": { "fluidsynth": "fluidsynth", "musescore": "mscore", "soundfont": "FluidR3_GM.sf2" },
  "warp": [[0.0, 1.84], [157.1, 162.3]]
}
```

| Field | Meaning |
|---|---|
| `status` | `ok` — aligned and within confidence thresholds. `rejected` — aligned but below threshold. `no_gp` — no decoded `.gp` found. `no_audio` — no `audio.*` found. |
| `source.gp` / `source.audio` | Filenames of the inputs used (null when not applicable). |
| `confidence.fit_cost` | Mean cosine distance along the (pass-2) DTW path (lower = better). |
| `confidence.path_deviation` | Residual deviation of the tempo-corrected path from a straight diagonal (also the global-vs-local decision signal). |
| `offset_s` | Estimated time offset (seconds) of the real recording relative to the symbolic score (= `warp[0]` real time). |
| `tempo_ratio` | Global tempo correction applied (real seconds per symbolic second). `1.0` means no correction; the reference was re-rendered at this ratio before the final alignment. |
| `mode` | `"global"` — one constant tempo explained the song (`warp` is a 2-point line). `"local"` — a residual elastic warp was kept (`warp` has ~`step_s`-spaced anchors). |
| `warp` | `[symbolic_time_s, real_time_s]` anchor pairs (2 points in global mode; downsampled ~every `step_s` seconds in local mode). Consumers interpolate between anchors. |
| `tools` | Tool names/paths used at align time (MuseScore, FluidSynth, soundfont). |
| `aligned_at` | ISO-8601 timestamp at align time. |
| `aligner_version` | `app.__version__` at align time. |

`confidence`, `offset_s`, `tempo_ratio`, and `mode` are `null` (and `warp` is `[]`)
for `no_gp` and `no_audio` statuses.

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
  off the energy; use this view to calibrate confidence thresholds.

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
`FIT_COST_THRESHOLD`, `DEVIATION_THRESHOLD`, `SILENCE_TOP_DB`,
`TEMPO_RESIDUAL_THRESHOLD`, `TEMPO_MIN`, `TEMPO_MAX`). The tempo keys drive the
two-pass alignment: `SILENCE_TOP_DB` is the trim threshold, `TEMPO_RESIDUAL_THRESHOLD`
the global-vs-local cutoff, and `TEMPO_MIN`/`TEMPO_MAX` clamp the tempo ratio.

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
