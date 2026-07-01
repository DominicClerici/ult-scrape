# aligner-py — trusted-tempo + gap-aware alignment — design

> Redesign of the `aligner-py` time-alignment pipeline. Produced via
> brainstorming on 2026-07-01. Status: **approved design, pending spec review**.
> Supersedes the two-pass tempo strategy from the
> [vertical-slice design](./2026-06-29-aligner-py-vertical-slice-design.md) §
> "two-pass tempo alignment"; the rest of that spec (renderer, DTW building
> blocks, inspect artifacts, output-contract gating) still stands.

## 1. Context

`aligner-py` renders each decoded `.gp` to a pitch-accurate reference WAV,
extracts chroma-CENS from the reference and the real YouTube recording, aligns
them with DTW, and writes an `align.json` warp + confidence sidecar per tab (see
[aligner overview](../../aligner-py/overview.md) and the
[output contract](../../output-contract.md)).

The current pipeline (`align_two_pass`) **derives** the global tempo from the
DTW path (least-squares slope through the warp) and re-renders at that ratio,
and handles silence only via `librosa.effects.trim` on the leading/trailing
edges. In practice it aligns poorly on two common cases:

1. **Silence / gaps.** `trim` only strips leading/trailing near-silence down to a
   dB threshold. A long quiet intro/outro may not trim cleanly, and **mid-song
   gaps** — where the real recording has a long dead stretch the tab doesn't
   account for — are smeared into the elastic warp instead of detected. Example:
   the recording has 1 min of silence before the music, or a 1-min break
   mid-song, while the tab plays through (or rests only ~5s).

2. **Tempo estimation is backwards.** Deriving tempo from the DTW-path slope is
   **circular**: a long gap or silence block tilts the regression line, so a
   structural mismatch corrupts the tempo estimate, which then corrupts the
   re-render and every downstream metric.

### Decisions locked during brainstorming

- **Trust the notated tempo.** The `.gpif` carries the official tab's own tempo
  automation (e.g. one constant `84` BPM). We render at that tempo and use the
  DTW-derived tempo only as a *fallback*, not as the primary source. (No external
  BPM lookup, no audio beat-tracking — the notated tempo is the trusted source.)
- **Energy-gated DTW + explicit gaps.** Detect dead regions in the real audio by
  RMS energy (tempo-free); let the warp skip real-time across them; record them
  explicitly in `align.json` so Phase-0 export can drop windows that land in a
  dead zone.
- **Ordering is the crux.** Tempo must be estimated *after* silence/gap removal,
  never before — otherwise a long gap forces a bad tempo fallback (the exact
  failure we're fixing). Real-audio silence detection is tempo-free, so it can
  run first and break the circular dependency.
- **Half/double-time snapping.** Official tabs are often notated in half- or
  double-time; the notes are internally correct so it renders at exactly 0.5× or
  2× real duration. A robust coarse slope (computed on active regions only)
  reveals this; snap to the nearest clean factor, else fall back to the
  DTW-derived tempo and flag lower confidence.
- **Keep the single monotonic anchor warp** (not a segmented structure); gaps are
  represented as steep segments plus an explicit `gaps` list.

## 2. Goals / non-goals

**Goals:**

- Replace the derive-tempo-then-re-render loop with: render at the trusted
  notated tempo, sanity-check with a coarse DTW whose slope is computed on
  *active* regions only, snap to a clean factor when off, DTW-derive as fallback.
- Detect silence/dead regions in the real audio by RMS energy (tempo-free),
  covering leading / trailing / internal uniformly, and represent them as an
  explicit `gaps` list in `align.json`.
- Make the warp gap-aware: hold symbolic time and skip real time across dead
  regions instead of hallucinating a chroma match on silence.
- Add a `coverage` health metric (fraction of the symbolic timeline matched to
  real content) and fold it into the `ok`/`rejected` decision.
- Keep the output a single monotonic anchor warp; consumers still interpolate.
- Validate with synthetic fixtures (deterministic, in CI) **and** a small set of
  real known-bad tabs driven through `align inspect`.

**Non-goals (deferred):**

- External BPM lookup / audio beat-tracking as a tempo source.
- Symbolic-only gaps (tab has a section the recording doesn't) — v1 handles
  **real-audio dead regions**; the reverse is noted but deferred.
- Transposition / key-shift search, capo/tuning normalization, stem-assisted
  chroma, subsequence/partial DTW for structural salvage, SQLite work queue
  (all still deferred per the vertical-slice spec §9).
- Segmented / non-monotonic warp representation.

## 3. Pipeline ordering (the core)

Per tab, `align_tab` runs these stages in order. The ordering rule in one line:
**silence-detect (tempo-free) → coarse align → robust tempo on active-only →
snap/fallback → gap-aware final warp.**

### Stage 1 — Detect silence/activity (tempo-free)

- **Real audio:** compute an RMS-energy envelope (`GAP_FRAME_S` frames) and
  threshold it (`SILENCE_RMS_DB`) to find *dead regions* — contiguous below-floor
  spans longer than `MIN_GAP_S`. Leading, trailing, and internal dead regions all
  fall out of the same detection; leading/trailing silence stops being a special
  `trim` case. This needs **no tempo knowledge**.
- **Reference:** the render tells us exactly where the tab plays vs. rests; apply
  the same envelope treatment (or read note activity from the score) to get the
  reference's active span. Used to bound the active region for the tempo fit.

### Stage 2 — Coarse chroma DTW at the notated tempo

- Render the reference once at the notated BPM (`Renderer.render`), extract
  chroma-CENS for both signals, run one `dtw_path`. This yields a warp path used
  only to estimate tempo and to seed gap placement — not the final warp.

### Stage 3 — Robust tempo on active regions only

- Fit the path slope (`robust_tempo`) **excluding path frames where either side
  falls in a detected dead region** (masked least-squares, RANSAC-style rejection
  of residual outliers). The 1-min gap contributes zero to the slope — this is
  the fix for the ordering/circularity problem.
- The slope is the candidate global tempo ratio.

### Stage 4 — Snap to clean factor, or DTW-fallback

- `snap_tempo_factor`: if the robust ratio is within `TEMPO_SNAP_TOL` of a factor
  in `TEMPO_SNAP_FACTORS` (`0.5, 1, 1.5, 2, 3`), take that factor. Factor `1` (the
  notated tempo was already right) is a no-op. `tempo_source` records
  `"notated"`, `"notated_x2"`, `"notated_x0.5"`, etc.
- If the ratio is an arbitrary non-clean value, fall back to the DTW-derived ratio
  (`tempo_source: "dtw_fallback"`) and flag lower confidence.
- **Re-render only if the chosen factor ≠ 1** (`Renderer.render_corrected` at
  `notated × factor`, pitch-exact via `scale_midi_tempo`), then run the final DTW
  against the corrected reference. When the factor is `1`, reuse the Stage-2 DTW.

### Stage 5 — Gap-aware warp + explicit gaps + confidence

- Build the monotonic anchor warp from the final path, but in real-dead regions
  the warp **holds symbolic time and advances real time** (a steep segment)
  rather than following the (meaningless) chroma correspondence on silence.
- Emit `gaps` as the detected real-audio dead regions on the original real
  timeline, tagged `lead` / `trailing` / `internal`.
- Compute `fit_cost` and `path_deviation` on **active regions only** (dead frames
  excluded). Compute `coverage` = fraction of the symbolic timeline that maps into
  non-gap real content.

## 4. Output contract change (`align.json`)

New / changed fields (updates `docs/output-contract.md` **and** both aligner docs
in the same change):

```jsonc
{
  // ...existing: aligned_at, aligner_version, offset_s, source, tools, warp, status...
  "tempo_ratio": 1.0,          // notated-tempo × snapped factor (or DTW fallback ratio)
  "tempo_source": "notated",   // "notated" | "notated_x2" | "notated_x0.5" | "notated_x1.5" | "notated_x3" | "dtw_fallback"
  "mode": "global",            // constant tempo (global) vs residual drift (local); orthogonal to gaps
  "gaps": [                    // NEW: real-time dead ranges with no usable tab audio
    { "real_start_s": 0.0,   "real_end_s": 61.4,  "kind": "lead" },
    { "real_start_s": 132.0, "real_end_s": 190.5, "kind": "internal" }
  ],
  "coverage": 0.86,            // NEW: fraction of symbolic timeline matched to real content
  "confidence": { "fit_cost": 0.11, "path_deviation": 0.02 }  // active regions only
}
```

- `gaps` are detected purely from real-audio RMS energy (tempo-free). `kind` is
  `lead` (before first real music), `trailing` (after last), or `internal`.
  Phase-0 export drops any audio window overlapping a gap.
- `coverage` is the headline health signal: a tab matching only 40% of itself is
  suspect even if `fit_cost` looks fine.
- `warp` stays a single monotonic list of `[symbolic_s, real_s]` anchors; a gap
  shows up as a near-vertical (steep) segment. Global mode is no longer
  necessarily a 2-point line when internal gaps are present — it is piecewise
  linear with constant tempo between gaps.
- `offset_s` stays `warp[0]` real time (equals the end of a leading gap when one
  exists).
- `gaps` (`[]`), `coverage`, `tempo_source` (`null`) mirror the existing
  metric-nulling for `no_gp` / `no_audio` statuses.

### Status semantics

`ok` now requires **all** of: `fit_cost ≤ FIT_COST_THRESHOLD`,
`path_deviation ≤ DEVIATION_THRESHOLD`, and `coverage ≥ COVERAGE_THRESHOLD`.
Otherwise `rejected`. `no_gp` / `no_audio` unchanged.

## 5. Config keys

New keys (add to `app/config.py`, `.env.example`, and
`docs/aligner-py/overview.md` together):

| Key | Meaning | Default (starting point) |
|---|---|---|
| `MIN_GAP_S` | Min dead-region length to count as a gap | `3.0` |
| `SILENCE_RMS_DB` | RMS energy floor (dBFS) for dead-region detection | `-40.0` |
| `GAP_FRAME_S` | Envelope frame length for the RMS detector | `0.1` |
| `TEMPO_SNAP_FACTORS` | Clean factors to snap the robust ratio to | `0.5,1,1.5,2,3` |
| `TEMPO_SNAP_TOL` | Relative tolerance for snapping to a factor | `0.05` |
| `COVERAGE_THRESHOLD` | Min `coverage` for `ok` status | `0.6` |

`SILENCE_TOP_DB` (the old leading/trailing `trim` threshold) is superseded by the
energy-envelope detector. Decide during implementation whether to remove it or
retain it as a thin fallback; the spec's default is to **remove** it and route all
silence handling through the new detector. `TEMPO_MIN` / `TEMPO_MAX` /
`TEMPO_RESIDUAL_THRESHOLD` remain (clamp for the DTW-fallback ratio and the
global-vs-local drift cutoff). All defaults are starting points to calibrate
against the real known-bad set.

## 6. Module changes

- **`features.py`** — add `energy_envelope(y, sr, frame_s)` and
  `detect_dead_regions(y, sr, *, floor_db, min_gap_s, frame_s)` returning
  `[(start_s, end_s, kind)]` on the original timeline. `trim_silence` is removed
  or reduced to a thin wrapper over the envelope detector.
- **`align.py`** — add `robust_tempo` (masked/RANSAC slope excluding dead
  frames), `snap_tempo_factor`, `coverage`, and make `compose_anchors` gap-aware
  (hold symbolic / skip real across dead regions). `AlignResult` gains `gaps`,
  `coverage`, `tempo_source`.
- **`pipeline.py`** — rewrite `align_two_pass` (rename to reflect the new flow) to
  the Stage 1–5 ordering above. `align_tab` folds `coverage` into the status
  decision.
- **`output.py`** — serialize `gaps`, `coverage`, `tempo_source`; keep the atomic
  temp + `os.replace` commit and `align.json`-last marker.
- **`inspect.py`** — shade detected gap regions on `align_plot.png`; the audio
  overlay is unaffected.
- **`config.py`** — the new keys in §5.

## 7. Validation

- **Synthetic (CI, tool-free):** construct reference/real chroma or signal pairs
  with injected known tempo factors (0.5/1/2), leading silence, and internal
  gaps; assert the recovered `tempo_ratio`, `tempo_source`, `gaps` (positions and
  kinds), `coverage`, and warp anchors match ground truth within tolerance. TDD
  the estimator (`robust_tempo`, `snap_tempo_factor`, `detect_dead_regions`,
  gap-aware `compose_anchors`, `coverage`) this way. These stay browser/tool-free
  and deterministic like the existing unit tests.
- **Real inspect:** the user hands over a handful of tabs that currently align
  badly (silence/gap cases). Run `align inspect <tab_id>` before/after and confirm
  by ear (stereo overlay) and eye (plot with shaded gaps). Iterate on real
  examples; use them to calibrate the §5 defaults.

## 8. Docs to update (same change as the code)

- `docs/output-contract.md` — the `align.json` `gaps` / `coverage` /
  `tempo_source` fields and gap semantics.
- `docs/aligner-py/overview.md` — replace the "two-pass tempo alignment" section
  with the new ordering; module map; schema table; config key list.
- `.env.example` (if the aligner has one) / `app/config.py` — the new keys.
- `aligner-py`/root `CLAUDE.md` command list only if flags change (they don't).

## 9. Open calibration questions (resolve during implementation, not blocking)

- Final default values for `SILENCE_RMS_DB`, `MIN_GAP_S`, `COVERAGE_THRESHOLD`,
  `TEMPO_SNAP_TOL` — tune against the real known-bad set.
- Exact `coverage` definition (symbolic-duration-matched vs. real-duration-matched
  vs. both) — pick the one that best separates good from bad on the real set.
- Whether to keep `SILENCE_TOP_DB` as a fallback or remove it (default: remove).
