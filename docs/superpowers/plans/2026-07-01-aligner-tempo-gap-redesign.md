# Aligner Trusted-Tempo + Gap-Aware Alignment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the aligner's derive-tempo-then-re-render loop with trusted-notated-tempo rendering + energy-based silence/gap detection, so leading/internal silence and half/double-time tabs stop breaking alignment.

**Architecture:** Per tab the new pipeline runs **silence-detect (tempo-free) → coarse DTW at notated tempo → robust tempo on active regions only → snap to clean factor or DTW-fallback → gap-aware final warp**. Real-audio dead regions are detected by RMS energy (no tempo needed), reported explicitly in `align.json` as `gaps`, and a new `coverage` metric gates the `ok`/`rejected` decision.

**Tech Stack:** Python ≥3.13, numpy, librosa, soundfile, mido, pydantic-settings, pytest. Tests are tool-free and deterministic by default; real render/align is behind the `integration` marker.

## Global Constraints

- Python ≥ 3.13; all new tests must be **tool-free and deterministic** (no MuseScore/FluidSynth/ffmpeg, no network) and pass under the default `pytest -m 'not integration'`.
- Work inside `aligner-py/` only. This project **shares no code** with `scraper-py`, `decoder-rs`, `enricher-py`; do not import across projects.
- `align.json` is written atomically with the existing `_write_json_atomic` (temp + `os.replace`); it is the aligner's commit marker — keep that contract.
- The output-contract change (`gaps`, `coverage`, `tempo_source`) MUST land with its doc updates in the same change: `docs/output-contract.md`, `docs/aligner-py/overview.md`, and the config docs (see Task 9). A code change whose docs weren't updated is **incomplete**.
- Follow the user's global rules: no comments that restate code; do not delete files you didn't create; do not push to a remote.
- Run tests from `aligner-py/` with `python3 -m pytest`.
- `AlignResult` is a frozen dataclass — new fields get defaults so existing constructors keep working.

**Semantics fixed by the spec (`docs/superpowers/specs/2026-07-01-aligner-tempo-gap-redesign-design.md`):**
- `gaps`: real-audio dead regions on the **original real timeline**, each `{real_start_s, real_end_s, kind}` with `kind ∈ {"lead","trailing","internal"}`.
- `tempo_source ∈ {"notated","notated_x2","notated_x0.5","notated_x1.5","notated_x3","dtw_fallback"}`.
- `tempo_ratio` = notated-tempo × snapped clean factor, or the clamped DTW-fallback ratio. It is "real seconds per symbolic second"; it is the `ratio` passed to `Renderer.render_corrected`.
- `mode`: `"global"` (constant tempo, 2-point line) is only allowed when there are **no internal gaps** and `path_deviation ≤ tempo_residual_threshold`; otherwise `"local"`.
- `ok` requires `fit_cost ≤ fit_cost_threshold` **and** `path_deviation ≤ deviation_threshold` **and** `coverage ≥ coverage_threshold`.

---

## File Structure

- `app/config.py` — add the new settings keys + a `snap_factors()` parser (Task 1).
- `app/features.py` — add `energy_envelope`, `detect_dead_regions`; retire `trim_silence` once the pipeline stops using it (Tasks 2, 7).
- `app/align.py` — add `robust_tempo`, `snap_tempo_factor`, `coverage`; extend `AlignResult` (Tasks 3, 4, 5, 6).
- `app/output.py` — serialize `gaps`, `coverage`, `tempo_source` (Task 6).
- `app/pipeline.py` — rewrite to the 5-stage ordering; fold `coverage` into status (Task 7).
- `app/inspect.py` — shade gap regions on the plot (Task 8).
- `docs/output-contract.md`, `docs/aligner-py/overview.md` — contract + overview updates (Task 9).
- `tests/` — new/updated tests colocated per module.

---

### Task 1: Config keys for silence + tempo snapping

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` gains `min_gap_s: float`, `silence_rms_db: float`, `gap_frame_s: float`, `tempo_snap_factors: str`, `tempo_snap_tol: float`, `coverage_threshold: float`, and a method `snap_factors() -> list[float]` parsing the comma string.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_new_alignment_defaults():
    s = Settings(_env_file=None)
    assert s.min_gap_s == 3.0
    assert s.silence_rms_db == -40.0
    assert s.gap_frame_s == 0.1
    assert s.tempo_snap_tol == 0.05
    assert s.coverage_threshold == 0.6
    assert s.snap_factors() == [0.5, 1.0, 1.5, 2.0, 3.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py::test_new_alignment_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'min_gap_s'`

- [ ] **Step 3: Add the keys and parser**

In `app/config.py`, add after the `tempo_max` line (inside `Settings`):

```python
    min_gap_s: float = 3.0
    silence_rms_db: float = -40.0
    gap_frame_s: float = 0.1
    tempo_snap_factors: str = "0.5,1,1.5,2,3"
    tempo_snap_tol: float = 0.05
    coverage_threshold: float = 0.6

    def snap_factors(self) -> list[float]:
        return [float(x) for x in self.tempo_snap_factors.split(",") if x.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(aligner): config keys for energy silence + tempo snapping"
```

---

### Task 2: Energy envelope + dead-region detection (tempo-free)

**Files:**
- Modify: `app/features.py`
- Test: `tests/test_features.py`

**Interfaces:**
- Produces:
  - `energy_envelope(y: np.ndarray, sample_rate: int, frame_s: float) -> tuple[np.ndarray, np.ndarray]` — returns `(frame_times_s, rms_db)`, per-frame RMS in dBFS (full-scale = 1.0).
  - `detect_dead_regions(y: np.ndarray, sample_rate: int, *, floor_db: float, min_gap_s: float, frame_s: float) -> list[tuple[float, float, str]]` — dead intervals `(start_s, end_s, kind)` with `kind ∈ {"lead","trailing","internal"}`, sorted by `start_s`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_features.py` (keep existing imports; add the new names):

```python
from app.features import detect_dead_regions, energy_envelope


def _tone(sr, dur_s, freq=220.0, amp=0.3):
    t = np.arange(int(sr * dur_s)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_energy_envelope_flags_silence_low_and_tone_high():
    sr = 22050
    y = np.concatenate([np.zeros(sr, dtype=np.float32), _tone(sr, 1.0)])
    times, rms_db = energy_envelope(y, sr, frame_s=0.1)
    assert times.shape == rms_db.shape
    assert rms_db[2] < -60.0          # inside the silent second
    assert rms_db[-3] > -30.0         # inside the tone


def test_detect_dead_regions_finds_lead_internal_trailing():
    sr = 22050
    y = np.concatenate([
        np.zeros(int(sr * 4), dtype=np.float32),   # 4s lead silence
        _tone(sr, 2.0),                            # music
        np.zeros(int(sr * 4), dtype=np.float32),   # 4s internal silence
        _tone(sr, 2.0),                            # music
        np.zeros(int(sr * 4), dtype=np.float32),   # 4s trailing silence
    ])
    regions = detect_dead_regions(
        y, sr, floor_db=-40.0, min_gap_s=3.0, frame_s=0.1
    )
    kinds = [k for _, _, k in regions]
    assert kinds == ["lead", "internal", "trailing"]
    lead = regions[0]
    assert lead[0] == pytest.approx(0.0, abs=0.2)
    assert lead[1] == pytest.approx(4.0, abs=0.3)


def test_detect_dead_regions_ignores_short_gaps():
    sr = 22050
    y = np.concatenate([
        _tone(sr, 2.0),
        np.zeros(int(sr * 1.0), dtype=np.float32),  # 1s < min_gap_s
        _tone(sr, 2.0),
    ])
    regions = detect_dead_regions(
        y, sr, floor_db=-40.0, min_gap_s=3.0, frame_s=0.1
    )
    assert regions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_features.py -k "energy or dead" -v`
Expected: FAIL — `ImportError: cannot import name 'energy_envelope'`

- [ ] **Step 3: Implement the detector**

Add to `app/features.py` (after `trim_silence`):

```python
def energy_envelope(
    y: np.ndarray, sample_rate: int, frame_s: float
) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame RMS envelope in dBFS (full-scale = 1.0). Tempo-free.

    Frames are non-overlapping windows of ``frame_s`` seconds; the returned
    ``frame_times_s`` are frame centers. Silence maps to a large negative dB.
    """
    frame = max(1, int(round(frame_s * sample_rate)))
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=frame)[0]
    times = (np.arange(len(rms)) + 0.5) * frame / sample_rate
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    return times, rms_db.astype(float)


def detect_dead_regions(
    y: np.ndarray,
    sample_rate: int,
    *,
    floor_db: float,
    min_gap_s: float,
    frame_s: float,
) -> list[tuple[float, float, str]]:
    """Detect dead (near-silent) regions by RMS energy, tempo-free.

    Returns intervals ``(start_s, end_s, kind)`` where the envelope stays below
    ``floor_db`` for at least ``min_gap_s`` seconds. ``kind`` is ``"lead"`` when
    the region starts at the signal head, ``"trailing"`` when it reaches the
    tail, else ``"internal"``. Regions shorter than ``min_gap_s`` are dropped so
    normal note gaps are not mistaken for structural silence.
    """
    total_s = len(y) / sample_rate
    times, rms_db = energy_envelope(y, sample_rate, frame_s)
    dead = rms_db < floor_db
    half = frame_s / 2.0
    regions: list[tuple[float, float, str]] = []
    i = 0
    n = len(dead)
    while i < n:
        if not dead[i]:
            i += 1
            continue
        j = i
        while j < n and dead[j]:
            j += 1
        start_s = max(0.0, times[i] - half)
        end_s = min(total_s, times[j - 1] + half)
        if end_s - start_s >= min_gap_s:
            if start_s <= frame_s:
                kind = "lead"
            elif end_s >= total_s - frame_s:
                kind = "trailing"
            else:
                kind = "internal"
            regions.append((start_s, end_s, kind))
        i = j
    return regions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_features.py -v`
Expected: PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add app/features.py tests/test_features.py
git commit -m "feat(aligner): RMS energy envelope + dead-region detection"
```

---

### Task 3: Robust tempo estimate excluding dead frames

**Files:**
- Modify: `app/align.py`
- Test: `tests/test_align.py`

**Interfaces:**
- Consumes: `estimate_tempo` (existing fallback for degenerate paths).
- Produces: `robust_tempo(wp_asc: np.ndarray, ref_hop_s: float, real_hop_s: float, *, ref_dead: list[tuple[float, float]] = (), real_dead: list[tuple[float, float]] = ()) -> tuple[float, float]` — least-squares `(slope, intercept)` of real vs symbolic seconds along the path, dropping points whose ref-time falls in any `ref_dead` interval or whose real-time falls in any `real_dead` interval, plus one MAD-based outlier-rejection pass. `ref_dead`/`real_dead` are second-intervals on the **trimmed** clocks the path lives on.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_align.py` (extend the import line to include `robust_tempo`):

```python
from app.align import robust_tempo


def test_robust_tempo_ignores_dead_region_outliers():
    # Clean line real = 1.0*sym for 0..40 frames (hop 0.1 => 0..4s).
    sym = np.arange(0, 60)
    real = sym.astype(float).copy()
    # Frames 40..59 are a real dead stretch: real jumps ahead (a gap) => if
    # not excluded, the slope is dragged upward.
    real[40:] += 30.0
    wp = np.stack([sym, real], axis=1)
    # real seconds 4.0..? -> exclude real times >= 4.0s (dead region there)
    ratio, _ = robust_tempo(
        wp, 0.1, 0.1, ref_dead=[(4.0, 100.0)], real_dead=[]
    )
    assert abs(ratio - 1.0) < 0.05


def test_robust_tempo_matches_plain_fit_without_dead():
    sym = np.arange(0, 50)
    real = 1.5 * sym + 20
    wp = np.stack([sym, real], axis=1)
    ratio, intercept = robust_tempo(wp, 0.1, 0.1)
    assert abs(ratio - 1.5) < 1e-6
    assert abs(intercept - 2.0) < 1e-6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_align.py -k robust_tempo -v`
Expected: FAIL — `ImportError: cannot import name 'robust_tempo'`

- [ ] **Step 3: Implement `robust_tempo`**

Add to `app/align.py` (after `estimate_tempo`):

```python
def _in_any(values: np.ndarray, intervals) -> np.ndarray:
    mask = np.zeros(len(values), dtype=bool)
    for start, end in intervals:
        mask |= (values >= start) & (values <= end)
    return mask


def robust_tempo(
    wp_asc: np.ndarray,
    ref_hop_s: float,
    real_hop_s: float,
    *,
    ref_dead=(),
    real_dead=(),
) -> tuple[float, float]:
    """Slope/intercept of real vs symbolic seconds along the path, excluding
    points inside dead regions, with one MAD outlier-rejection pass.

    Excluding dead frames is what keeps a long gap from tilting the tempo
    estimate (the circular-dependency fix). Falls back to
    :func:`estimate_tempo` when too few points survive the masking.
    """
    sym = wp_asc[:, 0].astype(float) * ref_hop_s
    real = wp_asc[:, 1].astype(float) * real_hop_s
    keep = ~_in_any(sym, ref_dead) & ~_in_any(real, real_dead)
    if keep.sum() < 5 or np.ptp(sym[keep]) == 0:
        return estimate_tempo(wp_asc, ref_hop_s, real_hop_s)
    s, r = sym[keep], real[keep]
    slope, intercept = np.polyfit(s, r, 1)
    resid = np.abs(r - (slope * s + intercept))
    mad = np.median(resid) + 1e-9
    inliers = resid <= 4.0 * mad
    if inliers.sum() >= 5 and np.ptp(s[inliers]) > 0:
        slope, intercept = np.polyfit(s[inliers], r[inliers], 1)
    return float(slope), float(intercept)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_align.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/align.py tests/test_align.py
git commit -m "feat(aligner): robust tempo estimate excluding dead frames"
```

---

### Task 4: Snap the tempo ratio to a clean factor

**Files:**
- Modify: `app/align.py`
- Test: `tests/test_align.py`

**Interfaces:**
- Produces: `snap_tempo_factor(ratio: float, factors: list[float], tol: float) -> tuple[float, str]` — if `ratio` is within relative `tol` of a `factors` entry, returns `(factor, source)` where `source` is `"notated"` for factor `1.0` else `f"notated_x{factor:g}"`; otherwise returns `(ratio, "dtw_fallback")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_align.py` (extend import to include `snap_tempo_factor`):

```python
from app.align import snap_tempo_factor

FACTORS = [0.5, 1.0, 1.5, 2.0, 3.0]


def test_snap_identity_when_close_to_one():
    assert snap_tempo_factor(1.02, FACTORS, 0.05) == (1.0, "notated")


def test_snap_double_time():
    assert snap_tempo_factor(1.97, FACTORS, 0.05) == (2.0, "notated_x2")


def test_snap_half_time():
    assert snap_tempo_factor(0.51, FACTORS, 0.05) == (0.5, "notated_x0.5")


def test_snap_falls_back_for_arbitrary_ratio():
    factor, source = snap_tempo_factor(1.27, FACTORS, 0.05)
    assert source == "dtw_fallback"
    assert factor == 1.27
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_align.py -k snap -v`
Expected: FAIL — `ImportError: cannot import name 'snap_tempo_factor'`

- [ ] **Step 3: Implement `snap_tempo_factor`**

Add to `app/align.py`:

```python
def snap_tempo_factor(
    ratio: float, factors: list[float], tol: float
) -> tuple[float, str]:
    """Snap a measured tempo ratio to the nearest clean factor within relative
    ``tol`` (handles half/double-time notation). Returns ``(factor, source)``;
    an off-grid ratio falls back to ``(ratio, "dtw_fallback")``."""
    best = min(factors, key=lambda f: abs(ratio - f))
    if abs(ratio - best) <= tol * best:
        source = "notated" if best == 1.0 else f"notated_x{best:g}"
        return best, source
    return ratio, "dtw_fallback"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_align.py -k snap -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/align.py tests/test_align.py
git commit -m "feat(aligner): snap tempo ratio to clean half/double-time factor"
```

---

### Task 5: Coverage metric

**Files:**
- Modify: `app/align.py`
- Test: `tests/test_align.py`

**Interfaces:**
- Consumes: `map_time` (existing).
- Produces: `coverage(anchors: list[tuple[float, float]], gaps: list[tuple[float, float, str]], s_max: float, *, step_s: float = 0.5) -> float` — fraction of the symbolic timeline `[0, s_max]` whose warped real time lands **outside** every gap interval. Returns `1.0` when `s_max <= 0`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_align.py` (extend import to include `coverage`):

```python
from app.align import coverage


def test_coverage_full_when_no_gaps():
    anchors = [(0.0, 0.0), (10.0, 10.0)]
    assert coverage(anchors, [], 10.0, step_s=0.5) == pytest.approx(1.0)


def test_coverage_drops_symbolic_time_landing_in_a_gap():
    # identity warp; a gap covers real 4..6s => symbolic 4..6s is uncovered.
    anchors = [(0.0, 0.0), (10.0, 10.0)]
    cov = coverage(anchors, [(4.0, 6.0, "internal")], 10.0, step_s=0.5)
    assert cov == pytest.approx(0.8, abs=0.05)
```

Add `import pytest` at the top of `tests/test_align.py` if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_align.py -k coverage -v`
Expected: FAIL — `ImportError: cannot import name 'coverage'`

- [ ] **Step 3: Implement `coverage`**

Add to `app/align.py`:

```python
def coverage(
    anchors: list[tuple[float, float]],
    gaps: list[tuple[float, float, str]],
    s_max: float,
    *,
    step_s: float = 0.5,
) -> float:
    """Fraction of the symbolic timeline [0, s_max] that warps to real content
    outside every gap. A low value flags a tab that only matched part of the
    recording even when the local fit looks fine."""
    if s_max <= 0:
        return 1.0
    samples = np.arange(0.0, s_max + 1e-9, step_s)
    if len(samples) == 0:
        return 1.0
    real_t = np.array([map_time(anchors, float(s)) for s in samples])
    in_gap = np.zeros(len(real_t), dtype=bool)
    for start, end, _kind in gaps:
        in_gap |= (real_t >= start) & (real_t <= end)
    return float(np.mean(~in_gap))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_align.py -k coverage -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/align.py tests/test_align.py
git commit -m "feat(aligner): coverage metric for warped symbolic timeline"
```

---

### Task 6: Extend `AlignResult` + serialize new fields

**Files:**
- Modify: `app/align.py`, `app/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Produces: `AlignResult` gains `gaps: list[tuple[float, float, str]] = field(default_factory=list)`, `coverage: float = 1.0`, `tempo_source: str = "notated"`. `write_align` serializes `gaps` (as `{"real_start_s","real_end_s","kind"}` dicts), `coverage`, `tempo_source`; when `result is None` these are `[]`, `None`, `None`.

- [ ] **Step 1: Write the failing tests**

Update `tests/test_output.py::test_write_ok_payload` — construct the result with the new fields and assert they serialize:

```python
    res = AlignResult(
        anchors=[(0.0, 1.0), (0.5, 2.0)],
        fit_cost=0.12, path_deviation=0.03, offset_s=1.0,
        tempo_ratio=2.0, mode="global",
        gaps=[(0.0, 1.0, "lead"), (30.0, 95.0, "internal")],
        coverage=0.86, tempo_source="notated_x2",
    )
```

Then add assertions after the existing ones in that test:

```python
    assert data["tempo_source"] == "notated_x2"
    assert data["coverage"] == 0.86
    assert data["gaps"] == [
        {"real_start_s": 0.0, "real_end_s": 1.0, "kind": "lead"},
        {"real_start_s": 30.0, "real_end_s": 95.0, "kind": "internal"},
    ]
```

And extend `test_write_no_audio_payload` with:

```python
    assert data["gaps"] == []
    assert data["coverage"] is None
    assert data["tempo_source"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_output.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'gaps'`

- [ ] **Step 3: Extend `AlignResult`**

In `app/align.py`, change the top import line to include `field`:

```python
from dataclasses import dataclass, field
```

and extend the dataclass:

```python
@dataclass(frozen=True)
class AlignResult:
    anchors: list[tuple[float, float]]
    fit_cost: float
    path_deviation: float
    offset_s: float
    tempo_ratio: float
    mode: str
    gaps: list[tuple[float, float, str]] = field(default_factory=list)
    coverage: float = 1.0
    tempo_source: str = "notated"
```

- [ ] **Step 4: Serialize in `output.py`**

In `app/output.py`, inside the `if result is not None:` branch add:

```python
        gaps = [
            {"real_start_s": s, "real_end_s": e, "kind": k}
            for s, e, k in result.gaps
        ]
        coverage = result.coverage
        tempo_source = result.tempo_source
```

and in the `else:` branch add:

```python
        gaps = []
        coverage = None
        tempo_source = None
```

Then add the three keys to the `payload` dict (next to `tempo_ratio`):

```python
        "tempo_source": tempo_source,
        "coverage": coverage,
        "gaps": gaps,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_output.py tests/test_align.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/align.py app/output.py tests/test_output.py
git commit -m "feat(aligner): gaps/coverage/tempo_source on AlignResult + align.json"
```

---

### Task 7: Rewrite the pipeline to the 5-stage ordering

**Files:**
- Modify: `app/pipeline.py`, `app/features.py` (retire `trim_silence`), `tests/test_features.py`
- Test: `tests/test_cli.py` (existing, must still pass), `tests/test_pipeline.py` (new)

**Interfaces:**
- Consumes: `detect_dead_regions`, `energy_envelope` (Task 2); `robust_tempo`, `snap_tempo_factor`, `coverage`, `compose_anchors`, `dtw_path`, `path_deviation` (Tasks 3–5 + existing); `Renderer.render` / `render_corrected`; `chroma_cens`, `load_audio`, `hop_seconds`.
- Produces: `align_tab(tab, settings, renderer, *, now_iso) -> str` (unchanged signature) now producing `gaps`/`coverage`/`tempo_source` and folding `coverage` into the `ok`/`rejected` decision. New helper `align_gap_aware(gp, audio, settings, renderer, work) -> AlignResult` replaces `align_two_pass`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline.py`:

```python
import numpy as np
import soundfile as sf

from app.align import AlignResult
from app.config import Settings
from app.pipeline import align_gap_aware


class LeadSilenceRenderer:
    """Renders a 3s tone starting at t=0 (no lead); used against a real file
    that has 4s of lead silence, so the pipeline must detect a lead gap."""

    def __init__(self, sample_rate=22050):
        self.sample_rate = sample_rate

    def _tone(self, dur_s):
        t = np.arange(int(self.sample_rate * dur_s)) / self.sample_rate
        # sweep so chroma has structure the DTW can lock onto
        return (0.3 * np.sin(2 * np.pi * (110 + 40 * t) * t)).astype(np.float32)

    def render(self, gp, work_dir):
        from pathlib import Path
        out = Path(work_dir) / "ref.wav"
        sf.write(str(out), self._tone(3.0), self.sample_rate)
        return out

    def render_corrected(self, gp, work_dir, ratio):
        from pathlib import Path
        out = Path(work_dir) / "ref_corr.wav"
        n = max(1, int(len(self._tone(3.0)) * ratio))
        base = self._tone(3.0)
        idx = np.linspace(0, len(base) - 1, n)
        y = np.interp(idx, np.arange(len(base)), base).astype(np.float32)
        sf.write(str(out), y, self.sample_rate)
        return out


def test_align_gap_aware_detects_lead_gap(tmp_path):
    sr = 22050
    r = LeadSilenceRenderer(sr)
    tone = r._tone(3.0)
    real = np.concatenate([np.zeros(int(sr * 4), dtype=np.float32), tone])
    real_path = tmp_path / "audio.wav"
    sf.write(str(real_path), real, sr)
    gp = tmp_path / "song.gp"
    gp.write_bytes(b"PK")

    s = Settings(_env_file=None)
    s.sample_rate = sr
    result = align_gap_aware(gp, real_path, s, r, tmp_path)

    assert isinstance(result, AlignResult)
    assert any(k == "lead" for _, _, k in result.gaps)
    assert 0.0 <= result.coverage <= 1.0
    assert result.tempo_source in (
        "notated", "notated_x0.5", "notated_x1.5", "notated_x2",
        "notated_x3", "dtw_fallback",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'align_gap_aware'`

- [ ] **Step 3: Rewrite `pipeline.py`**

Replace the body of `app/pipeline.py` from the imports through `align_two_pass` with:

```python
from __future__ import annotations

import tempfile
from pathlib import Path

import app
from app.align import (
    AlignResult,
    compose_anchors,
    coverage,
    dtw_path,
    path_deviation,
    robust_tempo,
    snap_tempo_factor,
)
from app.config import Settings
from app.discover import find_audio_file, find_gp
from app.features import (
    chroma_cens,
    detect_dead_regions,
    hop_seconds,
    load_audio,
)
from app.output import write_align


def _tools(settings: Settings) -> dict:
    return {
        "musescore": settings.musescore_bin,
        "fluidsynth": settings.fluidsynth_bin,
        "soundfont": settings.soundfont.name,
    }


def _path_seconds(wp_asc, hop_s: float) -> tuple:
    return (
        wp_asc[:, 0].astype(float) * hop_s,
        wp_asc[:, 1].astype(float) * hop_s,
    )


def _edges(regions, total_s):
    """Return (lead_end_s, trail_start_s) from detected dead regions."""
    lead = next((e for s, e, k in regions if k == "lead"), 0.0)
    trail = next((s for s, e, k in regions if k == "trailing"), total_s)
    return lead, trail


def _shift(intervals, offset):
    """Shift (start, end[, kind]) intervals by -offset onto a trimmed clock,
    dropping the kind and clamping at 0."""
    out = []
    for iv in intervals:
        s, e = iv[0] - offset, iv[1] - offset
        out.append((max(0.0, s), max(0.0, e)))
    return out


def align_gap_aware(
    gp: Path, audio: Path, settings: Settings, renderer, work: Path
) -> AlignResult:
    """Render at the notated tempo, detect real-audio dead regions (tempo-free),
    estimate tempo on active regions only, snap to a clean factor (or DTW
    fallback), then align gap-aware. See the trusted-tempo design spec."""
    work = Path(work)
    sr = settings.sample_rate
    hop = settings.hop_length
    hop_s = hop_seconds(sr, hop)

    real_full = load_audio(audio, sr)
    real_total = len(real_full) / sr
    real_dead = detect_dead_regions(
        real_full, sr, floor_db=settings.silence_rms_db,
        min_gap_s=settings.min_gap_s, frame_s=settings.gap_frame_s,
    )
    real_lead, real_trail = _edges(real_dead, real_total)
    real_trim = real_full[int(real_lead * sr):int(real_trail * sr)]
    internal_gaps = [g for g in real_dead if g[2] == "internal"]
    real_dead_trim = _shift(internal_gaps, real_lead)

    # --- pass 1: render at notated tempo, coarse DTW ---
    ref1_wav = renderer.render(gp, work)
    ref1_full = load_audio(ref1_wav, sr)
    s_max = len(ref1_full) / sr
    ref1_dead = detect_dead_regions(
        ref1_full, sr, floor_db=settings.silence_rms_db,
        min_gap_s=settings.min_gap_s, frame_s=settings.gap_frame_s,
    )
    ref1_lead, ref1_trail = _edges(ref1_dead, s_max)
    ref1_trim = ref1_full[int(ref1_lead * sr):int(ref1_trail * sr)]

    wp1, fit1 = dtw_path(
        chroma_cens(ref1_trim, sr, hop), chroma_cens(real_trim, sr, hop)
    )
    ratio, _ = robust_tempo(
        wp1, hop_s, hop_s,
        ref_dead=_shift(
            [g for g in ref1_dead if g[2] == "internal"], ref1_lead
        ),
        real_dead=real_dead_trim,
    )
    factor, source = snap_tempo_factor(
        ratio, settings.snap_factors(), settings.tempo_snap_tol
    )
    if source == "dtw_fallback":
        factor = min(max(factor, settings.tempo_min), settings.tempo_max)

    # --- pass 2: re-render only if the factor is not identity ---
    if factor == 1.0:
        refc_lead, wp2, fit2 = ref1_lead, wp1, fit1
    else:
        refc_wav = renderer.render_corrected(gp, work, factor)
        refc_full = load_audio(refc_wav, sr)
        refc_dead = detect_dead_regions(
            refc_full, sr, floor_db=settings.silence_rms_db,
            min_gap_s=settings.min_gap_s, frame_s=settings.gap_frame_s,
        )
        refc_lead, refc_trail = _edges(refc_dead, len(refc_full) / sr)
        refc_trim = refc_full[int(refc_lead * sr):int(refc_trail * sr)]
        wp2, fit2 = dtw_path(
            chroma_cens(refc_trim, sr, hop), chroma_cens(real_trim, sr, hop)
        )

    deviation = path_deviation(wp2)
    mode = (
        "global"
        if not internal_gaps and deviation <= settings.tempo_residual_threshold
        else "local"
    )

    p2_sym, p2_real = _path_seconds(wp2, hop_s)
    anchors = compose_anchors(
        p2_sym_s=p2_sym, p2_real_s=p2_real, ratio=factor,
        ref_corr_lead=refc_lead, real_lead=real_lead,
        s_max=s_max, step_s=settings.step_s, mode=mode,
    )
    cov = coverage(anchors, real_dead, s_max, step_s=settings.step_s)
    return AlignResult(
        anchors=anchors, fit_cost=fit2, path_deviation=deviation,
        offset_s=anchors[0][1] if anchors else 0.0,
        tempo_ratio=factor, mode=mode,
        gaps=real_dead, coverage=cov, tempo_source=source,
    )
```

- [ ] **Step 4: Update `align_tab` to use the new helper + coverage gate**

In `app/pipeline.py`, in `align_tab`, replace the `align_two_pass` call and the `ok` computation:

```python
    with tempfile.TemporaryDirectory() as work:
        result = align_gap_aware(gp, audio, settings, renderer, Path(work))

    ok = (
        result.fit_cost <= settings.fit_cost_threshold
        and result.path_deviation <= settings.deviation_threshold
        and result.coverage >= settings.coverage_threshold
    )
```

- [ ] **Step 5: Retire `trim_silence`**

`trim_silence` is now unused. In `app/features.py` delete the `trim_silence` function. In `tests/test_features.py` delete `test_trim_silence_strips_lead_and_trail` and `test_trim_silence_returns_unchanged_for_pure_silence`, and remove `trim_silence` from the `from app.features import ...` line.

Verify nothing else imports it:

Run: `grep -rn trim_silence app tests`
Expected: no matches.

- [ ] **Step 6: Run the pipeline + cli + features tests**

Run: `python3 -m pytest tests/test_pipeline.py tests/test_cli.py tests/test_features.py -v`
Expected: PASS. (`test_cli.py` exercises `align_tab` through `cmd_run` with the `FakeRenderer`; it should still produce `ok`/`rejected`/`no_gp`/`no_audio`.)

- [ ] **Step 7: Run the full default suite**

Run: `python3 -m pytest`
Expected: PASS (all non-integration tests).

- [ ] **Step 8: Commit**

```bash
git add app/pipeline.py app/features.py tests/test_pipeline.py tests/test_features.py
git commit -m "feat(aligner): trusted-tempo + gap-aware pipeline; retire trim_silence"
```

---

### Task 8: Shade gaps on the inspect plot

**Files:**
- Modify: `app/inspect.py`, `app/cli.py` (pass gaps to `build_plot`)
- Test: `tests/test_inspect.py`

**Interfaces:**
- Consumes: `AlignResult.gaps` via `align.json` (read in `cmd_inspect`).
- Produces: `build_plot(..., gaps: list[tuple[float, float, str]] = ()) -> Path` shades each gap as a translucent span on the chroma axis (real timeline).

- [ ] **Step 1: Read how cmd_inspect calls build_plot**

Run: `grep -n "build_plot\|build_overlay\|align.json\|gaps" app/cli.py`
Expected: shows `cmd_inspect` loading `align.json` and calling `build_plot(...)`. Note the exact call site to thread `gaps` through.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_inspect.py`:

```python
def test_build_plot_accepts_gaps(tmp_path):
    import soundfile as sf

    from app.inspect import build_plot

    sr = 22050
    real = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype(np.float32)
    sf.write(str(tmp_path / "real.wav"), real, sr)
    ref = np.zeros(sr, dtype=np.float32)
    ref[sr // 2] = 1.0
    sf.write(str(tmp_path / "ref.wav"), ref, sr)

    out = build_plot(
        real_audio=tmp_path / "real.wav",
        anchors=[(0.0, 0.0), (1.0, 1.0)],
        ref_wav=tmp_path / "ref.wav",
        out_png=tmp_path / "plot.png",
        sample_rate=sr,
        hop_length=2048,
        gaps=[(0.2, 0.6, "internal")],
    )
    assert out.exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_inspect.py::test_build_plot_accepts_gaps -v`
Expected: FAIL — `TypeError: build_plot() got an unexpected keyword argument 'gaps'`

- [ ] **Step 4: Add the `gaps` parameter and shading**

In `app/inspect.py`, change the `build_plot` signature to add `gaps: list[tuple[float, float, str]] = ()` (as a keyword-only arg after `hop_length`), and after the `for t in real_onsets:` loop that draws onset lines on `ax0`, add:

```python
    for g_start, g_end, _kind in gaps:
        ax0.axvspan(g_start, g_end, color="r", alpha=0.2)
```

- [ ] **Step 5: Thread gaps through `cmd_inspect`**

In `app/cli.py` `cmd_inspect`, read `gaps` from the loaded `align.json` (default `[]`), convert each dict to a tuple, and pass to `build_plot(...)`:

```python
    gaps = [
        (g["real_start_s"], g["real_end_s"], g["kind"])
        for g in data.get("gaps", [])
    ]
```

and add `gaps=gaps` to the `build_plot(...)` call. (Use the exact variable name for the loaded JSON dict found in Step 1.)

- [ ] **Step 6: Run inspect + cli tests**

Run: `python3 -m pytest tests/test_inspect.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/inspect.py app/cli.py tests/test_inspect.py
git commit -m "feat(aligner): shade detected gaps on the inspect plot"
```

---

### Task 9: Documentation (contract + overview + config)

**Files:**
- Modify: `docs/output-contract.md`, `docs/aligner-py/overview.md`, `aligner-py/.env` (if it documents keys) or note the keys in the overview config list.

**Interfaces:** none (docs only). This task MUST land in the same branch as the code above (Global Constraints).

- [ ] **Step 1: Update the `align.json` section in `docs/output-contract.md`**

Find the aligner `align.json` block. Add the three new fields to the JSON example and the field table:
- `gaps` — array of `{real_start_s, real_end_s, kind}` (`kind ∈ lead|trailing|internal`); real-audio dead ranges on the **original real timeline**; empty for `no_gp`/`no_audio`; consumers (Phase-0 export) drop any window overlapping a gap.
- `coverage` — fraction of the symbolic timeline matched to real content; `null` for `no_gp`/`no_audio`.
- `tempo_source` — one of `notated | notated_x2 | notated_x0.5 | notated_x1.5 | notated_x3 | dtw_fallback`; `null` for `no_gp`/`no_audio`.
- Note that `warp` in `global` mode is a 2-point line **only when there are no internal gaps**; with internal gaps the mode is `local`.

- [ ] **Step 2: Update `docs/aligner-py/overview.md`**

- Replace the "Two-pass tempo alignment" subsection with the new ordering: silence-detect (tempo-free) → coarse DTW at notated tempo → robust tempo on active regions → snap to clean factor / DTW-fallback → gap-aware final warp. State the ordering rationale (a long gap must not tilt the tempo estimate).
- Update the **Module map** rows: `features.py` gains `energy_envelope` / `detect_dead_regions` (and drop `trim_silence`); `align.py` gains `robust_tempo`, `snap_tempo_factor`, `coverage`; `pipeline.py` describes `align_gap_aware`.
- Update the `align.json` **schema table** with `gaps`, `coverage`, `tempo_source` and the revised `tempo_ratio` / `mode` meanings.
- Update the config-key list at the bottom: add `MIN_GAP_S`, `SILENCE_RMS_DB`, `GAP_FRAME_S`, `TEMPO_SNAP_FACTORS`, `TEMPO_SNAP_TOL`, `COVERAGE_THRESHOLD`; note `SILENCE_TOP_DB` is removed (superseded by the energy detector).

- [ ] **Step 3: Update the config key list / .env docs**

If `aligner-py/.env.example` exists, add the six new keys with the Task-1 defaults and remove `SILENCE_TOP_DB`. If there is no `.env.example`, ensure the keys are documented in the overview config list (Step 2). Confirm `SILENCE_TOP_DB` is no longer referenced in `app/config.py`:

Run: `grep -rn "silence_top_db\|SILENCE_TOP_DB" app docs`
Expected: no matches (remove the `silence_top_db` field from `config.py` in this step if still present, and its assertion is already absent from `test_config.py`).

- [ ] **Step 4: Verify the suite still green**

Run: `python3 -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/output-contract.md docs/aligner-py/overview.md app/config.py
git commit -m "docs(aligner): document gaps/coverage/tempo_source contract + new config"
```

---

### Task 10: Integration smoke assertion for the new fields

**Files:**
- Modify: `tests/test_integration.py`

**Interfaces:** none. Keeps the integration template honest about the new schema.

- [ ] **Step 1: Add schema assertions to the integration template**

In `tests/test_integration.py::test_end_to_end_real_tab`, after a real tab is aligned (the template currently `pytest.skip`s), document/assert that the produced `align.json` contains the keys `gaps` (list), `coverage`, `tempo_source`, `tempo_ratio`, `mode`, and `warp`. Leave the `pytest.skip` in place (no fixture is committed) but update the docstring to mention the new fields a real fixture should exercise:

```python
    # A real fixture should produce align.json with: status, warp (non-empty),
    # tempo_ratio, tempo_source, mode, coverage, and a gaps list (possibly empty).
    pytest.skip("provide a real tab fixture to exercise the full render+align path")
```

- [ ] **Step 2: Run the integration marker to confirm it still skips cleanly**

Run: `python3 -m pytest -m integration tests/test_integration.py -v`
Expected: SKIPPED (no fixture) — no collection/import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(aligner): note new align.json fields in integration template"
```

---

## Real-Audio Validation (manual, after the code lands)

Not a code task — the human-in-the-loop confirmation from the spec §7:

1. User provides a handful of tab ids that currently align badly (long lead silence, mid-song gap, half/double-time).
2. For each: `align run <tab_id>` then `align inspect <tab_id>` (and `--music`).
3. Confirm by ear (stereo overlay locks through the song) and eye (`align_plot.png` — warped onsets sit on the energy; gap spans shade the real dead zones; warp function has clean steep segments at gaps).
4. Use the results to calibrate `SILENCE_RMS_DB`, `MIN_GAP_S`, `COVERAGE_THRESHOLD`, `TEMPO_SNAP_TOL` against reality (spec §9 open calibration items).

---

## Self-Review Notes

- **Spec coverage:** trusted notated tempo (Task 7 render + snap), tempo-free silence detection (Task 2), ordering fix / robust tempo on active-only (Tasks 3, 7), snap half/double-time + DTW fallback (Task 4, 7), explicit gaps + coverage (Tasks 2, 5, 6, 7), contract change (Task 6, 9), config keys (Task 1, 9), synthetic tests (Tasks 2–7) + real inspect (validation section), inspect plot gaps (Task 8). All spec sections map to a task.
- **`mode`/global-vs-local + internal gaps:** enforced in Task 7 (`global` only when `not internal_gaps and deviation ≤ threshold`).
- **Type consistency:** `gaps` are `(start_s, end_s, kind)` tuples everywhere in code; serialized to `{real_start_s, real_end_s, kind}` dicts only in `output.py` (Task 6) and read back to tuples in `cmd_inspect` (Task 8). `factor`/`ratio` is the `tempo_ratio` passed to `render_corrected`. `snap_tempo_factor` sources match the `tempo_source` enum in Global Constraints.
