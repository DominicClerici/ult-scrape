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
reference and the real recording, and aligns them with global DTW. The result is a
monotonic **warp function** (anchor pairs mapping symbolic time → real time) and two
**confidence metrics** (`fit_cost`, `path_deviation`). If confidence is within the
configured thresholds the tab is marked `ok`; otherwise `rejected`. The output is
written to `align.json` (written last as the commit marker). See the
[output contract](../output-contract.md) for the artifact details.

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
| `app/render.py` | `.gp` → MIDI (MuseScore CLI) → reference WAV (FluidSynth). Injectable `Renderer` with configurable binaries and soundfont. |
| `app/features.py` | Chroma-CENS extraction and audio loading (`chroma_cens`, `load_audio`, `hop_seconds`). Pure; no filesystem side-effects. |
| `app/align.py` | DTW on chroma features → warp path → monotonic warp function → `AlignResult` (anchors, `fit_cost`, `path_deviation`, `offset_s`). Pure. |
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
  `align.json` exists; `align run` skips it unless re-invoked explicitly.
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
  "source": { "gp": "tab-download-ssid-1910943.gp", "audio": "audio.opus" },
  "status": "ok",
  "tools": { "fluidsynth": "fluidsynth", "musescore": "mscore", "soundfont": "FluidR3_GM.sf2" },
  "warp": [[0.0, 1.84], [0.5, 2.36]]
}
```

| Field | Meaning |
|---|---|
| `status` | `ok` — aligned and within confidence thresholds. `rejected` — aligned but below threshold. `no_gp` — no decoded `.gp` found. `no_audio` — no `audio.*` found. |
| `source.gp` / `source.audio` | Filenames of the inputs used (null when not applicable). |
| `confidence.fit_cost` | Mean cosine distance along the DTW path (lower = better). |
| `confidence.path_deviation` | How far the path strays from a straight diagonal (structural-mismatch indicator). |
| `offset_s` | Estimated time offset (seconds) of the real recording relative to the symbolic score. |
| `warp` | Downsampled `[symbolic_time_s, real_time_s]` anchor pairs (~every `step_s` seconds). Consumers interpolate between anchors. |
| `tools` | Tool names/paths used at align time (MuseScore, FluidSynth, soundfont). |
| `aligned_at` | ISO-8601 timestamp at align time. |
| `aligner_version` | `app.__version__` at align time. |

`confidence` and `offset_s` are `null` (and `warp` is `[]`) for `no_gp` and
`no_audio` statuses.

## Inspection artifacts

`align inspect <tab_id>` produces two developer-facing files that are **not** part of
the consumed contract (they can be deleted without consequence):

- `align_overlay.wav` — warped reference synth panned hard-left, real audio
  panned hard-right, mixed to one stereo file. If the two ears stay locked through
  the song the alignment is good.
- `align_plot.png` — real audio chroma / spectrogram with warped note onsets drawn
  on top, plus the DTW warping path and cost curve. Drift shows up as onsets sliding
  off the energy; use this view to calibrate confidence thresholds.

## Commands

```bash
cd aligner-py
pip install -e ".[dev]"          # needs MuseScore + FluidSynth + ffmpeg on PATH
align run <tab_id> [<tab_id> …]  # render → align → write align.json per tab
align inspect <tab_id>           # build align_overlay.wav + align_plot.png
align status [<tab_id> …]        # counts by align.json status (all tabs if no ids given)
python3 -m pytest                # unit tests (tool-free by default)
python3 -m pytest -m integration # end-to-end; needs mscore + fluidsynth + a fixture
```

Configuration is read from a `.env` file (or environment variables); see
`app/config.py` for the full key list (`OUTPUT_DIR`, `MUSESCORE_BIN`,
`FLUIDSYNTH_BIN`, `SOUNDFONT`, `SAMPLE_RATE`, `HOP_LENGTH`, `STEP_S`,
`FIT_COST_THRESHOLD`, `DEVIATION_THRESHOLD`).

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
