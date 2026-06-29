# aligner-py — audio↔symbolic alignment (vertical slice) — design

> Spec for the first phase of the dataset-building work that follows the
> scraper → decoder → enricher pipeline. Produced via brainstorming on
> 2026-06-29. Status: **approved design, pending spec review**.

## 1. Context

`ult-scrape` already produces, per tab, a decoded Guitar Pro file (`.gp` +
extracted `.gpif`) and best-available source audio (`audio.<ext>` + `audio.json`)
inside a frozen filesystem [output contract](../../output-contract.md). The next
goal is to turn this into a **training dataset for an automatic music
transcription (AMT) model**: audio in → multi-instrument / tablature symbolic
output.

The `.gp` files are community transcriptions; the audio is a *separate* YouTube
recording. They are **not time-aligned**, and may differ in tempo, key/tuning,
capo, arrangement, and structure. Frame-level audio↔note alignment is the core
risk in using real audio as supervision.

### Data strategy (decided)

- **Hybrid (strategy C):** synthetic audio rendered from `.gp` for the bulk of
  training (perfectly aligned by construction), fine-tuned / evaluated on a small
  set of **real audio aligned to `.gp`**.
- We are **solving real-audio time alignment now**, staged: build a
  **precision vertical slice** first — align a handful of hand-picked tabs and
  verify them by hand — *before* building the expensive transposition / structural
  salvage machinery needed to generalize to the full corpus.

### Key synergy

The synth renderer required for the synthetic branch (B) is also the **reference
front-end for alignment**: render `.gp` → reference audio → align reference to the
real recording via DTW. We build the renderer once. For this slice it only needs
to be *pitch-accurate*, not realistic.

## 2. Goals / non-goals

**Goals (this phase):**

- A new decoupled `aligner-py` project that reads the `output/` tree and writes an
  `align.json` sidecar per tab.
- Render `.gp` → pitch-accurate reference audio.
- Align reference ↔ real audio via chroma-CENS + DTW; emit a warp function and
  confidence metrics.
- Two human-verification deliverables (listen + look) so alignment quality can be
  judged by hand on the slice.
- Update the output contract and docs in the same change.

**Non-goals (deferred to stage 2 — see §9):** transposition/key-shift search,
capo & tuning normalization, subsequence/partial DTW for structural mismatch,
stem-assisted (Demucs) chroma, a SQLite work queue, auto-gating thresholds, and
the *training-grade* multi-stem synthetic renderer.

## 3. Architecture

A fourth decoupled project, mirroring `enricher-py`'s conventions:

- Python ≥ 3.13, installed as a CLI (`align`).
- Shares **no code** with the other three projects; communicates only through the
  `output/` filesystem tree.
- Reads a tab directory only when it is "ready": `metadata.json` present, at least
  one decoded `.gp` present, and (for alignment) `audio.*` present.
- Writes `align.json` **last** as its commit marker (consistent with the existing
  atomic-commit ordering used by scraper/decoder/enricher).
- No SQLite queue in this phase: operates on an **explicit list of tab_ids** given
  on the CLI. The queue is added when generalizing to corpus scale (stage 2).

### Module map (proposed, mirrors `enricher-py`)

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings (`.env`): output dir, soundfont path, tool paths, feature/DTW params. |
| `app/discover.py` | Walk `output/`; per-tab readiness + filesystem state. |
| `app/render.py` | `.gp` → MIDI (MuseScore CLI) → reference WAV (FluidSynth). |
| `app/features.py` | chroma-CENS extraction for reference + real audio. |
| `app/align.py` | DTW, warp-path → warp function, confidence metrics (pure where possible). |
| `app/output.py` | Atomic commit of `align.json` (+ inspect artifacts). |
| `app/inspect.py` | Build the listen overlay + the look plot. |
| `app/cli.py` | `run` / `inspect` / `status`. |

## 4. Reference renderer (`app/render.py`)

DTW needs pitch content, not a realistic mix, so the renderer stays cheap and is
offloaded to external CLI tools (same "binary on PATH" pattern as `ffmpeg` /
`yt-dlp` in `enricher-py`):

1. **`.gp` → MIDI via MuseScore 4 CLI** (`mscore -o ref.mid <stem>.gp`). This
   avoids writing a Guitar Pro 7 `.gpif` XML parser and handles every GP version,
   preserving per-track instruments. **Fallback:** `pyguitarpro` if MuseScore
   proves unworkable. *(Open for override at spec review.)*
2. **MIDI → reference WAV via FluidSynth** + a General MIDI soundfont (e.g.
   `FluidR3_GM`).

The same module is later upgraded for branch B (better soundfonts, per-stem
rendering) — out of scope here.

## 5. Alignment core (`app/features.py`, `app/align.py`)

1. Extract **chroma-CENS** (tempo/loudness-robust) from `ref.wav` and the real
   `audio.*`.
2. **`librosa.sequence.dtw`** with a cosine metric → accumulated cost matrix +
   warping path. This slice uses **global DTW** only (no subsequence, no
   transposition).
3. Reduce the warping path to a **monotonic warp function**, stored as downsampled
   `(symbolic_time_s → real_time_s)` anchor pairs (~every 0.5 s). Consumers
   interpolate between anchors.
4. Emit two **confidence signals**:
   - **fit quality** — mean cosine distance along the path;
   - **path diagonality / deviation** — how far the path strays from a straight
     diagonal (a structural-mismatch detector).
   These are recorded now and become the **auto-gating thresholds** in stage 2;
   the §6 visual tool is how we calibrate them.

## 6. Verification deliverables ("D": listen + look)

Both proofs are produced by `align inspect <tab_id>`:

- **Listen — `align_overlay.<ext>`:** the warped reference synth panned hard-left,
  the real audio panned hard-right, mixed to one stereo file. If the two ears stay
  locked together through the song, the alignment is good.
- **Look — PNG plot:** the real audio's chroma / spectrogram with warped note
  onsets drawn on top, plus the DTW warping path and the cost curve. Drift shows
  up as onsets sliding off the energy; this view is used to pick the confidence
  thresholds reused in stage 2.

## 7. Output artifact + contract change

New sidecar `output/<tab_id>/align.json`, written **last** as its commit marker.
Proposed contents:

```json
{
  "tab_id": "eagles/hotel-california-official-1910943",
  "status": "ok",
  "aligned_at": "2026-06-29T12:00:00",
  "aligner_version": "0.1.0",
  "source": { "gp": "<stem>.gp", "audio": "audio.opus" },
  "confidence": { "fit_cost": 0.12, "path_deviation": 0.03 },
  "offset_s": 1.84,
  "warp": [[0.0, 1.84], [0.5, 2.36], "…(symbolic_s, real_s) anchor pairs…"],
  "tools": { "musescore": "4.x", "fluidsynth": "2.x", "soundfont": "FluidR3_GM" }
}
```

`status` ∈ `ok` | `rejected` | `no_audio` | `no_gp`. **`rejected`** means alignment
ran but confidence was below the (slice-tuned) threshold.

This **extends the output contract**. The same change updates:
`docs/output-contract.md`, `OVERVIEW.md` (add the new component + doc page), and a
new `docs/aligner-py/overview.md`. The decoder/enricher ignore `align.json`; only
`aligner-py` reads it.

## 8. CLI surface (`app/cli.py`)

```bash
cd aligner-py
pip install -e ".[dev]"          # needs MuseScore + FluidSynth + ffmpeg on PATH
align run <tab_id> [<tab_id> …]  # render → align → write align.json
align inspect <tab_id>           # build the listen overlay + look plot
align status [<tab_id> …]        # counts by align.json status
```

## 9. Deferred to stage 2

Named here so they are not lost when the slice proves out:

- Transposition / key-shift search (try chroma rotations; record detected shift).
- Capo & tuning normalization.
- Subsequence / partial DTW for structural mismatch (missing solos, differing
  repeats), with trimming/flagging.
- Stem-assisted chroma (run Demucs `htdemucs_6s`, align on a harmonic stem).
- SQLite work queue + `scan`/backoff/recovery for corpus scale (mirror
  `enricher-py`).
- Auto-gating thresholds derived from the slice's calibrated confidence metrics.
- Training-grade multi-stem synthetic renderer for branch B.
- Possible rename to a broader `dataset-py` once the shared renderer + synthetic
  corpus + stem separation live alongside alignment.

## 10. Dependencies

- **External (on PATH):** MuseScore 4 (`mscore`), FluidSynth, `ffmpeg`, a GM
  soundfont.
- **Python:** `librosa` (features + DTW), `numpy`, `soundfile`, `matplotlib`
  (inspect plot), plus the project's standard config/test tooling.

## 11. Testing

- **Deterministic, tool-free by default**, matching repo convention. Alignment math
  (warp-path → warp function, confidence metrics) is pure and unit-tested on
  synthetic feature sequences with known offsets.
- A small fixture pair (short rendered reference + a tempo-shifted copy) verifies
  the DTW recovers the known warp.
- Real-tool paths (MuseScore/FluidSynth render, end-to-end alignment on a real
  tab) are gated behind an `integration` marker, as in the other projects.

## 12. Open items for spec review

1. **MuseScore CLI** as the GP→MIDI dependency (vs `pyguitarpro`). Default:
   MuseScore.
2. **Project name** `aligner-py` (vs broader `dataset-py`). Default: `aligner-py`.
3. Soundfont choice / whether to vendor one or require it on the host.
