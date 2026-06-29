# aligner-py Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `aligner-py`, a 4th decoupled project that aligns each tab's community Guitar Pro transcription to its real YouTube audio and writes an `align.json` sidecar, with human-verifiable overlays — proving alignment quality on a hand-picked handful of tabs before any corpus-scale machinery.

**Architecture:** A standalone Python CLI (`align`) that reads the shared `output/` tree (no shared code with the other three projects). Per tab: render `.gp` → pitch-accurate reference WAV (MuseScore CLI → MIDI → FluidSynth), extract chroma-CENS from reference and real audio, align via librosa DTW, and emit a warp function + confidence metrics to `align.json`. An `inspect` command produces a listenable click-overlay and a visual plot for hand-judging quality.

**Tech Stack:** Python ≥3.13, librosa (features + DTW), numpy, soundfile, matplotlib, pydantic-settings; external CLI tools MuseScore 4 (`mscore`), FluidSynth, ffmpeg.

## Global Constraints

- Python `requires-python = ">=3.13"`.
- **No shared code** with `scraper-py` / `decoder-rs` / `enricher-py`; communicate only through the `output/` filesystem tree.
- Read a tab only when ready: `metadata.json` present, ≥1 `.gp` present, and `audio.*` present (for alignment).
- `align.json` is written **last** as the commit marker, via atomic tmp + `os.replace`, `json.dumps(..., indent=2, sort_keys=True)`.
- **This phase is the precision vertical slice only.** Out of scope: transposition search, capo/tuning normalization, subsequence/partial DTW, stem-assisted chroma (Demucs), SQLite queue, auto-gating, training-grade synthetic renderer.
- Tests deterministic and external-tool-free by default; real MuseScore/FluidSynth/ffmpeg paths gated behind the `integration` pytest marker.
- Mirror `enricher-py` conventions: `from __future__ import annotations`, frozen dataclasses, `Settings(BaseSettings)`, `app.__version__`.
- Default GP→MIDI tool is **MuseScore CLI** (`mscore`); fallback `pyguitarpro` is explicitly deferred, not built here.

## File Structure

```
aligner-py/
  pyproject.toml            # project metadata, deps, `align` entrypoint, pytest config
  app/__init__.py           # __version__
  app/config.py             # Settings (output dir, tool paths, soundfont, feature/DTW params, thresholds)
  app/discover.py           # walk output/, per-tab readiness + filesystem state
  app/align.py              # PURE: DTW wrapper, warp-path → anchors, confidence metrics
  app/features.py           # chroma-CENS extraction + audio loading (librosa)
  app/render.py             # .gp → MIDI (mscore) → WAV (fluidsynth), injectable runner
  app/output.py             # atomic commit of align.json
  app/inspect.py            # warp-time mapping (pure) + click-overlay + plot artifacts
  app/cli.py                # run / inspect / status
  tests/__init__.py
  tests/conftest.py
  tests/test_version.py
  tests/test_config.py
  tests/test_discover.py
  tests/test_align.py
  tests/test_features.py
  tests/test_render.py
  tests/test_output.py
  tests/test_inspect.py
  tests/test_cli.py
  tests/test_integration.py # @integration end-to-end on a real tab
```

Repo-wide docs touched in Task 10: `docs/output-contract.md`, `OVERVIEW.md`, new `docs/aligner-py/overview.md`, root `CLAUDE.md`.

---

### Task 1: Project scaffold + version

**Files:**
- Create: `aligner-py/pyproject.toml`
- Create: `aligner-py/app/__init__.py`
- Create: `aligner-py/tests/__init__.py`
- Test: `aligner-py/tests/test_version.py`

**Interfaces:**
- Produces: `app.__version__ == "0.1.0"`; installable package exposing console script `align = "app.cli:main"`.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_version.py`:
```python
import app


def test_version_is_semver_string():
    assert app.__version__ == "0.1.0"
```

- [ ] **Step 2: Create package files**

`aligner-py/app/__init__.py`:
```python
__version__ = "0.1.0"
```

`aligner-py/tests/__init__.py`: (empty file)

`aligner-py/pyproject.toml`:
```toml
[project]
name = "ult-aligner"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "librosa",
    "numpy",
    "soundfile",
    "matplotlib",
    "pydantic",
    "pydantic-settings",
    "python-dotenv",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.scripts]
align = "app.cli:main"

[tool.pytest.ini_options]
addopts = "-m 'not integration'"
markers = ["integration: requires MuseScore + FluidSynth + ffmpeg + a soundfont"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 3: Install and run the test**

Run:
```bash
cd aligner-py && pip install -e ".[dev]" && python -m pytest tests/test_version.py -v
```
Expected: PASS. (`app.cli` does not exist yet; the console script is only resolved on invocation, so install + this test still pass.)

- [ ] **Step 4: Commit**

```bash
git add aligner-py/pyproject.toml aligner-py/app/__init__.py aligner-py/tests/__init__.py aligner-py/tests/test_version.py
git commit -m "feat(aligner): scaffold project + version"
```

---

### Task 2: Settings

**Files:**
- Create: `aligner-py/app/config.py`
- Test: `aligner-py/tests/test_config.py`

**Interfaces:**
- Produces:
  - `class Settings(BaseSettings)` with fields: `output_dir: Path = Path("../output")`, `musescore_bin: str = "mscore"`, `fluidsynth_bin: str = "fluidsynth"`, `soundfont: Path = Path("/usr/share/sounds/sf2/FluidR3_GM.sf2")`, `sample_rate: int = 22050`, `hop_length: int = 2048`, `step_s: float = 0.5`, `fit_cost_threshold: float = 0.5`, `deviation_threshold: float = 0.15`.
  - `def get_settings() -> Settings`.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_config.py`:
```python
from pathlib import Path

from app.config import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.output_dir == Path("../output")
    assert s.musescore_bin == "mscore"
    assert s.fluidsynth_bin == "fluidsynth"
    assert s.sample_rate == 22050
    assert s.hop_length == 2048
    assert s.step_s == 0.5
    assert 0.0 < s.fit_cost_threshold
    assert 0.0 < s.deviation_threshold


def test_env_override(monkeypatch):
    monkeypatch.setenv("SAMPLE_RATE", "44100")
    monkeypatch.setenv("MUSESCORE_BIN", "/opt/mscore")
    s = get_settings()
    assert s.sample_rate == 44100
    assert s.musescore_bin == "/opt/mscore"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/config.py`:
```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    output_dir: Path = Path("../output")

    musescore_bin: str = "mscore"
    fluidsynth_bin: str = "fluidsynth"
    soundfont: Path = Path("/usr/share/sounds/sf2/FluidR3_GM.sf2")

    sample_rate: int = 22050
    hop_length: int = 2048
    step_s: float = 0.5

    fit_cost_threshold: float = 0.5
    deviation_threshold: float = 0.15


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/config.py aligner-py/tests/test_config.py
git commit -m "feat(aligner): settings"
```

---

### Task 3: Discovery

**Files:**
- Create: `aligner-py/app/discover.py`
- Test: `aligner-py/tests/test_discover.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `GP_GLOB = "*.gp"`, constant `ALIGN_FILE = "align.json"`.
  - `@dataclass(frozen=True) class TabDir: tab_id: str; path: Path`.
  - `def find_gp(tab_dir: Path) -> Path | None` — first `*.gp`, sorted by name.
  - `def find_audio_file(tab_dir: Path) -> Path | None` — first existing of `AUDIO_EXTS`.
  - `def read_align_status(tab_dir: Path) -> str | None` — `align.json` `status`, or None.
  - `def find_tab(output_root: Path, tab_id: str) -> TabDir | None`.
  - `def iter_ready_tabs(output_root: Path) -> Iterator[TabDir]` — dirs with `metadata.json`.
  - `AUDIO_EXTS = (".opus", ".m4a", ".webm", ".mp3", ".ogg")`.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_discover.py`:
```python
import json
from pathlib import Path

from app.discover import (
    find_audio_file,
    find_gp,
    find_tab,
    iter_ready_tabs,
    read_align_status,
)


def _make_tab(root: Path, tab_id: str, *, gp=False, audio=False, meta=True):
    d = root / tab_id
    d.mkdir(parents=True)
    if meta:
        (d / "metadata.json").write_text("{}")
    if gp:
        (d / "song.gp").write_bytes(b"PK\x03\x04")
    if audio:
        (d / "audio.opus").write_bytes(b"\x00")
    return d


def test_find_gp_and_audio(tmp_path):
    d = _make_tab(tmp_path, "artist/song-1", gp=True, audio=True)
    assert find_gp(d) == d / "song.gp"
    assert find_audio_file(d) == d / "audio.opus"


def test_find_gp_missing(tmp_path):
    d = _make_tab(tmp_path, "artist/song-2")
    assert find_gp(d) is None
    assert find_audio_file(d) is None


def test_read_align_status(tmp_path):
    d = _make_tab(tmp_path, "artist/song-3")
    assert read_align_status(d) is None
    (d / "align.json").write_text(json.dumps({"status": "ok"}))
    assert read_align_status(d) == "ok"


def test_iter_ready_and_find_tab(tmp_path):
    _make_tab(tmp_path, "a/one", gp=True, audio=True)
    _make_tab(tmp_path, "a/two")
    (tmp_path / "a" / "nope").mkdir()  # no metadata.json
    ids = sorted(t.tab_id for t in iter_ready_tabs(tmp_path))
    assert ids == ["a/one", "a/two"]
    t = find_tab(tmp_path, "a/one")
    assert t is not None and t.tab_id == "a/one"
    assert find_tab(tmp_path, "a/missing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_discover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.discover'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/discover.py`:
```python
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = (".opus", ".m4a", ".webm", ".mp3", ".ogg")
ALIGN_FILE = "align.json"


@dataclass(frozen=True)
class TabDir:
    tab_id: str
    path: Path


def find_gp(tab_dir: Path) -> Path | None:
    gps = sorted(Path(tab_dir).glob("*.gp"))
    return gps[0] if gps else None


def find_audio_file(tab_dir: Path) -> Path | None:
    for ext in AUDIO_EXTS:
        p = Path(tab_dir) / f"audio{ext}"
        if p.exists():
            return p
    return None


def read_align_status(tab_dir: Path) -> str | None:
    p = Path(tab_dir) / ALIGN_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("status")
    except (ValueError, OSError):
        return None


def find_tab(output_root: Path, tab_id: str) -> TabDir | None:
    d = Path(output_root) / tab_id
    if (d / "metadata.json").exists():
        return TabDir(tab_id=tab_id, path=d)
    return None


def iter_ready_tabs(output_root: Path) -> Iterator[TabDir]:
    output_root = Path(output_root)
    for meta in output_root.rglob("metadata.json"):
        tab_dir = meta.parent
        rel = tab_dir.relative_to(output_root).as_posix()
        yield TabDir(tab_id=rel, path=tab_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_discover.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/discover.py aligner-py/tests/test_discover.py
git commit -m "feat(aligner): tab discovery"
```

---

### Task 4: Alignment core (DTW → warp + confidence)

This is the heart of the slice: pure functions over feature matrices, fully testable without external tools.

**Files:**
- Create: `aligner-py/app/align.py`
- Test: `aligner-py/tests/test_align.py`

**Interfaces:**
- Consumes: nothing (operates on numpy arrays).
- Produces:
  - `@dataclass(frozen=True) class AlignResult: anchors: list[tuple[float, float]]; fit_cost: float; path_deviation: float; offset_s: float`.
  - `def align_features(ref_chroma, real_chroma, *, ref_hop_s: float, real_hop_s: float, step_s: float = 0.5) -> AlignResult`. `anchors` is a list of `(symbolic_time_s, real_time_s)` pairs sampled every `step_s` of symbolic time; `offset_s` = real time at symbolic time 0; `fit_cost` = normalized accumulated DTW cost (lower = better); `path_deviation` ∈ [0, ~1] (lower = straighter).
  - `def map_time(anchors: list[tuple[float, float]], t_symbolic_s: float) -> float` — interpolate a single symbolic time onto the real timeline.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_align.py`:
```python
import numpy as np

from app.align import align_features, map_time


def _ramp_chroma(n_frames: int) -> np.ndarray:
    """12 x n one-hot chroma cycling through pitch classes — distinct per frame."""
    c = np.zeros((12, n_frames), dtype=float)
    for t in range(n_frames):
        c[t % 12, t] = 1.0
    return c


def test_recovers_known_2x_stretch():
    ref = _ramp_chroma(20)
    real = np.repeat(ref, 2, axis=1)  # real runs at 2x the reference's frame rate
    res = align_features(ref, real, ref_hop_s=0.1, real_hop_s=0.1, step_s=0.5)
    # symbolic time t should map to ~2t in real time
    last_sym, last_real = res.anchors[-1]
    assert isinstance(last_real, float)
    assert abs(last_real / last_sym - 2.0) < 0.2
    assert res.fit_cost < 0.1
    assert res.path_deviation < 0.2


def test_offset_and_monotonic():
    ref = _ramp_chroma(12)
    real = _ramp_chroma(12)
    res = align_features(ref, real, ref_hop_s=0.1, real_hop_s=0.1, step_s=0.2)
    reals = [r for _, r in res.anchors]
    assert reals == sorted(reals)  # warp function is monotonic non-decreasing
    assert res.offset_s == res.anchors[0][1]


def test_map_time_interpolates():
    anchors = [(0.0, 1.0), (1.0, 3.0)]  # slope 2, offset 1
    assert abs(map_time(anchors, 0.5) - 2.0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_align.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.align'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/align.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np


@dataclass(frozen=True)
class AlignResult:
    anchors: list[tuple[float, float]]
    fit_cost: float
    path_deviation: float
    offset_s: float


def _path_to_anchors(
    wp_asc: np.ndarray, ref_hop_s: float, real_hop_s: float, step_s: float
) -> list[tuple[float, float]]:
    sym = wp_asc[:, 0].astype(float) * ref_hop_s
    real = wp_asc[:, 1].astype(float) * real_hop_s
    t_max = float(sym[-1])
    targets = np.arange(0.0, t_max + 1e-9, step_s)
    real_at = np.interp(targets, sym, real)
    return [(float(t), float(r)) for t, r in zip(targets, real_at)]


def _path_deviation(wp_asc: np.ndarray) -> float:
    i = wp_asc[:, 0].astype(float)
    j = wp_asc[:, 1].astype(float)
    if i[-1] == 0 or j[-1] == 0:
        return 0.0
    return float(np.sqrt(np.mean((i / i[-1] - j / j[-1]) ** 2)))


def align_features(
    ref_chroma: np.ndarray,
    real_chroma: np.ndarray,
    *,
    ref_hop_s: float,
    real_hop_s: float,
    step_s: float = 0.5,
) -> AlignResult:
    cost_matrix, wp = librosa.sequence.dtw(
        X=ref_chroma, Y=real_chroma, metric="cosine"
    )
    wp_asc = wp[::-1]  # librosa returns the path end -> start
    fit_cost = float(cost_matrix[-1, -1] / len(wp_asc))
    anchors = _path_to_anchors(wp_asc, ref_hop_s, real_hop_s, step_s)
    deviation = _path_deviation(wp_asc)
    offset_s = anchors[0][1] if anchors else 0.0
    return AlignResult(
        anchors=anchors,
        fit_cost=fit_cost,
        path_deviation=deviation,
        offset_s=offset_s,
    )


def map_time(anchors: list[tuple[float, float]], t_symbolic_s: float) -> float:
    xs = [a for a, _ in anchors]
    ys = [b for _, b in anchors]
    return float(np.interp(t_symbolic_s, xs, ys))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_align.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/align.py aligner-py/tests/test_align.py
git commit -m "feat(aligner): DTW alignment core + warp function"
```

---

### Task 5: Features (chroma-CENS + audio loading)

**Files:**
- Create: `aligner-py/app/features.py`
- Test: `aligner-py/tests/test_features.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `def hop_seconds(sample_rate: int, hop_length: int) -> float` — `hop_length / sample_rate`.
  - `def chroma_cens(y: np.ndarray, sample_rate: int, hop_length: int) -> np.ndarray` — shape `(12, frames)`.
  - `def load_audio(path: Path, sample_rate: int) -> np.ndarray` — mono float signal at `sample_rate`.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_features.py`:
```python
import numpy as np

from app.features import chroma_cens, hop_seconds


def test_hop_seconds():
    assert abs(hop_seconds(22050, 2205) - 0.1) < 1e-9


def test_chroma_shape_on_synthetic_tone():
    sr = 22050
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)  # A4
    c = chroma_cens(y, sample_rate=sr, hop_length=2048)
    assert c.shape[0] == 12
    assert c.shape[1] > 0
    assert np.all(np.isfinite(c))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.features'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/features.py`:
```python
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


def hop_seconds(sample_rate: int, hop_length: int) -> float:
    return hop_length / sample_rate


def chroma_cens(y: np.ndarray, sample_rate: int, hop_length: int) -> np.ndarray:
    return librosa.feature.chroma_cens(
        y=y, sr=sample_rate, hop_length=hop_length
    )


def load_audio(path: Path, sample_rate: int) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    return y
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_features.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/features.py aligner-py/tests/test_features.py
git commit -m "feat(aligner): chroma-CENS feature extraction"
```

---

### Task 6: Reference renderer (.gp → MIDI → WAV)

**Files:**
- Create: `aligner-py/app/render.py`
- Test: `aligner-py/tests/test_render.py`

**Interfaces:**
- Consumes: nothing (subprocess runner is injectable for unit tests).
- Produces:
  - `class RenderError(RuntimeError)`.
  - `@dataclass class Renderer:` fields `musescore_bin: str = "mscore"`, `fluidsynth_bin: str = "fluidsynth"`, `soundfont: Path = Path(...)`, `sample_rate: int = 22050`, `runner: Callable = subprocess.run`.
    - `def gp_to_midi(self, gp: Path, midi_out: Path) -> Path`
    - `def midi_to_wav(self, midi: Path, wav_out: Path) -> Path`
    - `def render(self, gp: Path, work_dir: Path) -> Path` — returns the WAV path (`<work_dir>/ref.wav`).

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_render.py`:
```python
import subprocess
from pathlib import Path

import pytest

from app.render import RenderError, Renderer


class FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def test_render_invokes_both_tools_and_returns_wav(tmp_path):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        # emulate the tool writing its declared output (-o / -F arg)
        out = Path(cmd[cmd.index("-o") + 1]) if "-o" in cmd else \
            Path(cmd[cmd.index("-F") + 1])
        out.write_bytes(b"\x00")
        return FakeProc(returncode=0)

    r = Renderer(soundfont=tmp_path / "sf.sf2", runner=runner)
    gp = tmp_path / "song.gp"
    gp.write_bytes(b"PK")
    wav = r.render(gp, tmp_path)

    assert wav == tmp_path / "ref.wav"
    assert wav.exists()
    assert any("mscore" in c[0] or r.musescore_bin in c[0] for c in calls)
    assert any(r.fluidsynth_bin in c[0] for c in calls)


def test_render_raises_on_nonzero_exit(tmp_path):
    def runner(cmd, **kwargs):
        return FakeProc(returncode=1, stderr="boom")

    r = Renderer(soundfont=tmp_path / "sf.sf2", runner=runner)
    gp = tmp_path / "song.gp"
    gp.write_bytes(b"PK")
    with pytest.raises(RenderError):
        r.render(gp, tmp_path)


def test_render_raises_when_output_missing(tmp_path):
    def runner(cmd, **kwargs):
        return FakeProc(returncode=0)  # success but writes nothing

    r = Renderer(soundfont=tmp_path / "sf.sf2", runner=runner)
    gp = tmp_path / "song.gp"
    gp.write_bytes(b"PK")
    with pytest.raises(RenderError):
        r.render(gp, tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.render'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/render.py`:
```python
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class RenderError(RuntimeError):
    pass


@dataclass
class Renderer:
    musescore_bin: str = "mscore"
    fluidsynth_bin: str = "fluidsynth"
    soundfont: Path = Path("/usr/share/sounds/sf2/FluidR3_GM.sf2")
    sample_rate: int = 22050
    runner: Callable[..., subprocess.CompletedProcess] = field(
        default=subprocess.run
    )

    def _run(self, cmd: list[str], out: Path) -> Path:
        proc = self.runner(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RenderError(
                f"{cmd[0]} exited {proc.returncode}: {proc.stderr}"
            )
        if not out.exists():
            raise RenderError(f"{cmd[0]} produced no output at {out}")
        return out

    def gp_to_midi(self, gp: Path, midi_out: Path) -> Path:
        cmd = [self.musescore_bin, "-o", str(midi_out), str(gp)]
        return self._run(cmd, midi_out)

    def midi_to_wav(self, midi: Path, wav_out: Path) -> Path:
        cmd = [
            self.fluidsynth_bin, "-ni", "-F", str(wav_out),
            "-r", str(self.sample_rate), str(self.soundfont), str(midi),
        ]
        return self._run(cmd, wav_out)

    def render(self, gp: Path, work_dir: Path) -> Path:
        work_dir = Path(work_dir)
        midi = self.gp_to_midi(gp, work_dir / "ref.mid")
        return self.midi_to_wav(midi, work_dir / "ref.wav")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/render.py aligner-py/tests/test_render.py
git commit -m "feat(aligner): GP->MIDI->WAV reference renderer"
```

---

### Task 7: Output writer (align.json)

**Files:**
- Create: `aligner-py/app/output.py`
- Test: `aligner-py/tests/test_output.py`

**Interfaces:**
- Consumes: `app.align.AlignResult`.
- Produces:
  - `def write_align(*, tab_dir: Path, status: str, gp_name: str | None, audio_name: str | None, result: AlignResult | None, tools: dict, aligner_version: str, now_iso: str) -> Path` — writes `align.json` atomically, returns its path. When `result` is None (e.g. `no_audio`/`no_gp`), the `confidence`/`offset_s`/`warp` keys are present but null/empty.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/test_output.py`:
```python
import json
from pathlib import Path

from app.align import AlignResult
from app.output import write_align


def test_write_ok_payload(tmp_path):
    res = AlignResult(
        anchors=[(0.0, 1.0), (0.5, 2.0)],
        fit_cost=0.12, path_deviation=0.03, offset_s=1.0,
    )
    p = write_align(
        tab_dir=tmp_path, status="ok", gp_name="song.gp",
        audio_name="audio.opus", result=res,
        tools={"musescore": "4", "fluidsynth": "2", "soundfont": "FluidR3"},
        aligner_version="0.1.0", now_iso="2026-06-29T00:00:00",
    )
    assert p == tmp_path / "align.json"
    data = json.loads(p.read_text())
    assert data["status"] == "ok"
    assert data["source"] == {"gp": "song.gp", "audio": "audio.opus"}
    assert data["confidence"] == {"fit_cost": 0.12, "path_deviation": 0.03}
    assert data["offset_s"] == 1.0
    assert data["warp"] == [[0.0, 1.0], [0.5, 2.0]]
    assert data["aligner_version"] == "0.1.0"


def test_write_no_audio_payload(tmp_path):
    p = write_align(
        tab_dir=tmp_path, status="no_audio", gp_name="song.gp",
        audio_name=None, result=None, tools={},
        aligner_version="0.1.0", now_iso="2026-06-29T00:00:00",
    )
    data = json.loads(p.read_text())
    assert data["status"] == "no_audio"
    assert data["confidence"] is None
    assert data["warp"] == []
    assert data["offset_s"] is None


def test_atomic_no_tmp_left(tmp_path):
    write_align(
        tab_dir=tmp_path, status="no_gp", gp_name=None, audio_name=None,
        result=None, tools={}, aligner_version="0.1.0",
        now_iso="2026-06-29T00:00:00",
    )
    assert not (tmp_path / "align.json.tmp").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_output.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.output'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/output.py`:
```python
from __future__ import annotations

import json
import os
from pathlib import Path

from app.align import AlignResult


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_align(
    *,
    tab_dir: Path,
    status: str,
    gp_name: str | None,
    audio_name: str | None,
    result: AlignResult | None,
    tools: dict,
    aligner_version: str,
    now_iso: str,
) -> Path:
    tab_dir = Path(tab_dir)
    if result is not None:
        confidence = {
            "fit_cost": result.fit_cost,
            "path_deviation": result.path_deviation,
        }
        warp = [[s, r] for s, r in result.anchors]
        offset_s = result.offset_s
    else:
        confidence = None
        warp = []
        offset_s = None

    payload = {
        "status": status,
        "source": {"gp": gp_name, "audio": audio_name},
        "confidence": confidence,
        "offset_s": offset_s,
        "warp": warp,
        "tools": tools,
        "aligned_at": now_iso,
        "aligner_version": aligner_version,
    }
    out = tab_dir / "align.json"
    _write_json_atomic(out, payload)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_output.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/output.py aligner-py/tests/test_output.py
git commit -m "feat(aligner): align.json output writer"
```

---

### Task 8: Inspect artifacts (warp-time mapping + overlay + plot)

**Files:**
- Create: `aligner-py/app/inspect.py`
- Test: `aligner-py/tests/test_inspect.py`

**Interfaces:**
- Consumes: `app.align.map_time`, `app.features.load_audio`/`chroma_cens`.
- Produces:
  - `def warp_onsets(anchors, ref_onsets_s: list[float]) -> list[float]` — map each reference (symbolic) onset time onto the real timeline via `map_time`. **Pure; unit-tested.**
  - `def build_overlay(*, real_audio: Path, ref_wav: Path, anchors, out_wav: Path, sample_rate: int) -> Path` — detect onsets on `ref_wav`, warp them, synthesize a click track at the warped times, write a 2-channel WAV (clicks left, real right). **Integration.**
  - `def build_plot(*, real_audio: Path, anchors, ref_wav: Path, out_png: Path, sample_rate: int, hop_length: int) -> Path` — real-audio chroma image + warped onset lines + warp-path subplot. **Integration.**

- [ ] **Step 1: Write the failing test (pure mapping only)**

`aligner-py/tests/test_inspect.py`:
```python
from app.inspect import warp_onsets


def test_warp_onsets_maps_through_anchors():
    anchors = [(0.0, 1.0), (2.0, 5.0)]  # slope 2, offset 1
    out = warp_onsets(anchors, [0.0, 1.0, 2.0])
    assert out == [1.0, 3.0, 5.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_inspect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.inspect'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/inspect.py`:
```python
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from app.align import map_time
from app.features import chroma_cens, load_audio


def warp_onsets(
    anchors: list[tuple[float, float]], ref_onsets_s: list[float]
) -> list[float]:
    return [map_time(anchors, t) for t in ref_onsets_s]


def build_overlay(
    *,
    real_audio: Path,
    ref_wav: Path,
    anchors: list[tuple[float, float]],
    out_wav: Path,
    sample_rate: int,
) -> Path:
    real = load_audio(real_audio, sample_rate)
    ref = load_audio(ref_wav, sample_rate)
    ref_onsets = librosa.onset.onset_detect(
        y=ref, sr=sample_rate, units="time"
    ).tolist()
    real_onsets = warp_onsets(anchors, ref_onsets)
    clicks = librosa.clicks(
        times=real_onsets, sr=sample_rate, length=len(real)
    )
    stereo = np.stack([clicks, real], axis=1)
    sf.write(str(out_wav), stereo, sample_rate)
    return out_wav


def build_plot(
    *,
    real_audio: Path,
    anchors: list[tuple[float, float]],
    ref_wav: Path,
    out_png: Path,
    sample_rate: int,
    hop_length: int,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    real = load_audio(real_audio, sample_rate)
    chroma = chroma_cens(real, sample_rate, hop_length)
    ref = load_audio(ref_wav, sample_rate)
    ref_onsets = librosa.onset.onset_detect(y=ref, sr=sample_rate, units="time")
    real_onsets = warp_onsets(anchors, ref_onsets.tolist())

    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(12, 8))
    librosa.display.specshow(
        chroma, x_axis="time", y_axis="chroma",
        sr=sample_rate, hop_length=hop_length, ax=ax0,
    )
    for t in real_onsets:
        ax0.axvline(t, color="w", alpha=0.5, linewidth=0.5)
    ax0.set_title("real audio chroma + warped reference onsets")

    sym = [a for a, _ in anchors]
    real_t = [b for _, b in anchors]
    ax1.plot(sym, real_t)
    ax1.set_xlabel("symbolic time (s)")
    ax1.set_ylabel("real time (s)")
    ax1.set_title("warp function")

    fig.tight_layout()
    fig.savefig(str(out_png))
    plt.close(fig)
    return out_png
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aligner-py && python -m pytest tests/test_inspect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/inspect.py aligner-py/tests/test_inspect.py
git commit -m "feat(aligner): inspect overlays (warp mapping + click + plot)"
```

---

### Task 9: Worker pipeline + CLI

**Files:**
- Create: `aligner-py/app/cli.py`
- Create: `aligner-py/tests/conftest.py`
- Test: `aligner-py/tests/test_cli.py`

**Interfaces:**
- Consumes: all prior modules.
- Produces:
  - `def align_tab(tab, settings, renderer, *, now_iso: str) -> str` — runs the per-tab pipeline, writes `align.json`, returns the `status`. Steps: if `find_gp` is None → `no_gp`; if `find_audio_file` is None → `no_audio`; else render (temp dir), load+chroma both, `align_features`, decide `ok`/`rejected` from thresholds, write.
  - `def cmd_run(settings, tab_ids: list[str], renderer=None, *, now_iso=None) -> dict` — counts by status.
  - `def cmd_inspect(settings, tab_id: str, renderer=None) -> dict` — re-render + read `align.json` warp, write `align_overlay.wav` + `align_plot.png`, return paths.
  - `def cmd_status(settings, tab_ids: list[str] | None) -> dict`.
  - `def main(argv: list[str] | None = None) -> int` — argparse: `--output-dir`, `--quiet`; subcommands `run <tab_id...>`, `inspect <tab_id>`, `status [<tab_id...>]`.

- [ ] **Step 1: Write the failing test**

`aligner-py/tests/conftest.py`:
```python
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def tab_with_audio(tmp_path):
    """An output tree with one ready tab that has a .gp and real audio."""
    d = tmp_path / "artist" / "song-1"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    (d / "song.gp").write_bytes(b"PK\x03\x04")
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    y = 0.2 * np.sin(2 * np.pi * 220.0 * t)
    sf.write(str(d / "audio.opus"), y, sr, format="OGG", subtype="OPUS")
    return tmp_path, "artist/song-1", d


class FakeRenderer:
    """Writes a deterministic WAV instead of shelling out to MuseScore/FluidSynth."""

    def __init__(self, sample_rate=22050):
        self.sample_rate = sample_rate

    def render(self, gp: Path, work_dir: Path) -> Path:
        out = Path(work_dir) / "ref.wav"
        t = np.linspace(0, 1.0, self.sample_rate, endpoint=False)
        y = 0.2 * np.sin(2 * np.pi * 220.0 * t)
        sf.write(str(out), y, self.sample_rate)
        return out


@pytest.fixture
def fake_renderer():
    return FakeRenderer()
```

`aligner-py/tests/test_cli.py`:
```python
import json

from app.cli import cmd_run, cmd_status, main
from app.config import Settings


def _settings(root):
    s = Settings()
    s.output_dir = root
    return s


def test_run_writes_ok_or_rejected(tab_with_audio, fake_renderer):
    root, tab_id, d = tab_with_audio
    counts = cmd_run(_settings(root), [tab_id], renderer=fake_renderer,
                     now_iso="2026-06-29T00:00:00")
    data = json.loads((d / "align.json").read_text())
    assert data["status"] in ("ok", "rejected")
    assert data["status"] in counts and counts[data["status"]] == 1
    assert data["source"]["gp"] == "song.gp"


def test_run_no_audio(tmp_path, fake_renderer):
    d = tmp_path / "a" / "b"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    (d / "song.gp").write_bytes(b"PK")
    counts = cmd_run(_settings(tmp_path), ["a/b"], renderer=fake_renderer,
                     now_iso="2026-06-29T00:00:00")
    assert counts.get("no_audio") == 1
    assert json.loads((d / "align.json").read_text())["status"] == "no_audio"


def test_run_no_gp(tmp_path, fake_renderer):
    d = tmp_path / "a" / "c"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    counts = cmd_run(_settings(tmp_path), ["a/c"], renderer=fake_renderer,
                     now_iso="2026-06-29T00:00:00")
    assert counts.get("no_gp") == 1


def test_status_counts(tab_with_audio, fake_renderer):
    root, tab_id, d = tab_with_audio
    cmd_run(_settings(root), [tab_id], renderer=fake_renderer,
            now_iso="2026-06-29T00:00:00")
    counts = cmd_status(_settings(root), [tab_id])
    assert sum(counts.values()) == 1


def test_main_run_smoke(tab_with_audio, monkeypatch):
    root, tab_id, d = tab_with_audio
    # force the real-binary renderer path to be replaced by the fake
    from tests.conftest import FakeRenderer
    import app.cli as cli
    monkeypatch.setattr(cli, "_build_renderer", lambda s: FakeRenderer())
    rc = main(["--output-dir", str(root), "--quiet", "run", tab_id])
    assert rc == 0
    assert (d / "align.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aligner-py && python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Write implementation**

`aligner-py/app/cli.py`:
```python
from __future__ import annotations

import argparse
import tempfile
from datetime import datetime
from pathlib import Path

import app
from app.align import align_features
from app.config import Settings, get_settings
from app.discover import (
    find_audio_file,
    find_gp,
    find_tab,
    iter_ready_tabs,
    read_align_status,
)
from app.features import chroma_cens, hop_seconds, load_audio
from app.inspect import build_overlay, build_plot
from app.output import write_align
from app.render import Renderer


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_renderer(settings: Settings) -> Renderer:
    return Renderer(
        musescore_bin=settings.musescore_bin,
        fluidsynth_bin=settings.fluidsynth_bin,
        soundfont=settings.soundfont,
        sample_rate=settings.sample_rate,
    )


def _tools(settings: Settings) -> dict:
    return {
        "musescore": settings.musescore_bin,
        "fluidsynth": settings.fluidsynth_bin,
        "soundfont": settings.soundfont.name,
    }


def align_tab(tab, settings: Settings, renderer, *, now_iso: str) -> str:
    gp = find_gp(tab.path)
    if gp is None:
        write_align(tab_dir=tab.path, status="no_gp", gp_name=None,
                    audio_name=None, result=None, tools={},
                    aligner_version=app.__version__, now_iso=now_iso)
        return "no_gp"

    audio = find_audio_file(tab.path)
    if audio is None:
        write_align(tab_dir=tab.path, status="no_audio", gp_name=gp.name,
                    audio_name=None, result=None, tools={},
                    aligner_version=app.__version__, now_iso=now_iso)
        return "no_audio"

    with tempfile.TemporaryDirectory() as work:
        ref_wav = renderer.render(gp, Path(work))
        ref = load_audio(ref_wav, settings.sample_rate)
        real = load_audio(audio, settings.sample_rate)
        hop_s = hop_seconds(settings.sample_rate, settings.hop_length)
        result = align_features(
            chroma_cens(ref, settings.sample_rate, settings.hop_length),
            chroma_cens(real, settings.sample_rate, settings.hop_length),
            ref_hop_s=hop_s, real_hop_s=hop_s, step_s=settings.step_s,
        )

    ok = (
        result.fit_cost <= settings.fit_cost_threshold
        and result.path_deviation <= settings.deviation_threshold
    )
    status = "ok" if ok else "rejected"
    write_align(tab_dir=tab.path, status=status, gp_name=gp.name,
                audio_name=audio.name, result=result, tools=_tools(settings),
                aligner_version=app.__version__, now_iso=now_iso)
    return status


def cmd_run(settings: Settings, tab_ids: list[str], renderer=None,
            *, now_iso: str | None = None) -> dict:
    renderer = renderer or _build_renderer(settings)
    now_iso = now_iso or _now_iso()
    counts: dict[str, int] = {}
    for tab_id in tab_ids:
        tab = find_tab(settings.output_dir, tab_id)
        if tab is None:
            counts["not_found"] = counts.get("not_found", 0) + 1
            continue
        status = align_tab(tab, settings, renderer, now_iso=now_iso)
        counts[status] = counts.get(status, 0) + 1
    return counts


def cmd_inspect(settings: Settings, tab_id: str, renderer=None) -> dict:
    import json

    renderer = renderer or _build_renderer(settings)
    tab = find_tab(settings.output_dir, tab_id)
    if tab is None:
        raise SystemExit(f"tab not found: {tab_id}")
    data = json.loads((tab.path / "align.json").read_text())
    anchors = [(s, r) for s, r in data["warp"]]
    gp = find_gp(tab.path)
    audio = find_audio_file(tab.path)
    with tempfile.TemporaryDirectory() as work:
        ref_wav = renderer.render(gp, Path(work))
        overlay = build_overlay(
            real_audio=audio, ref_wav=ref_wav, anchors=anchors,
            out_wav=tab.path / "align_overlay.wav",
            sample_rate=settings.sample_rate)
        plot = build_plot(
            real_audio=audio, anchors=anchors, ref_wav=ref_wav,
            out_png=tab.path / "align_plot.png",
            sample_rate=settings.sample_rate, hop_length=settings.hop_length)
    return {"overlay": str(overlay), "plot": str(plot)}


def cmd_status(settings: Settings, tab_ids: list[str] | None) -> dict:
    if tab_ids:
        tabs = [t for t in (find_tab(settings.output_dir, i) for i in tab_ids)
                if t is not None]
    else:
        tabs = list(iter_ready_tabs(settings.output_dir))
    counts: dict[str, int] = {}
    for tab in tabs:
        st = read_align_status(tab.path) or "unaligned"
        counts[st] = counts.get(st, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="align")
    parser.add_argument("--output-dir")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("tab_ids", nargs="+")
    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("tab_id")
    p_status = sub.add_parser("status")
    p_status.add_argument("tab_ids", nargs="*")

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.output_dir:
        settings.output_dir = Path(args.output_dir)

    if args.command == "run":
        out = cmd_run(settings, args.tab_ids)
    elif args.command == "inspect":
        out = cmd_inspect(settings, args.tab_id)
    else:
        out = cmd_status(settings, args.tab_ids)

    if not args.quiet:
        print(out)
    return 0
```

- [ ] **Step 4: Run the full unit suite**

Run: `cd aligner-py && python -m pytest -v`
Expected: PASS (all tests, integration excluded by default).

- [ ] **Step 5: Commit**

```bash
git add aligner-py/app/cli.py aligner-py/tests/conftest.py aligner-py/tests/test_cli.py
git commit -m "feat(aligner): per-tab pipeline + run/inspect/status CLI"
```

---

### Task 10: Integration test + documentation/contract update

**Files:**
- Create: `aligner-py/tests/test_integration.py`
- Create: `docs/aligner-py/overview.md`
- Modify: `docs/output-contract.md`
- Modify: `OVERVIEW.md`
- Modify: `CLAUDE.md` (root)

**Interfaces:**
- Consumes: the full `align` CLI on a real tab.
- Produces: a runnable integration test + complete docs reachable from the map.

- [ ] **Step 1: Write the integration test**

`aligner-py/tests/test_integration.py`:
```python
import json
import shutil

import pytest

from app.cli import cmd_run
from app.config import Settings


pytestmark = pytest.mark.integration


def _tools_present() -> bool:
    return all(shutil.which(b) for b in ("mscore", "fluidsynth"))


@pytest.mark.skipif(not _tools_present(), reason="needs mscore + fluidsynth")
def test_end_to_end_real_tab(tmp_path):
    """Requires a real output/ tab fixture with a .gp + audio.* copied into tmp_path.

    Populate tmp_path/<tab_id>/ with metadata.json, a .gp, and audio.* before
    running, or point Settings.output_dir at a real output tree and pass its
    tab_id. This asserts the pipeline produces a well-formed align.json.
    """
    s = Settings()
    s.output_dir = tmp_path
    # NOTE: this test is a template — substitute a real tab_id + fixture files.
    pytest.skip("provide a real tab fixture to exercise the full render+align path")
```

- [ ] **Step 2: Run integration test (expected skip without tools/fixture)**

Run: `cd aligner-py && python -m pytest -m integration -v`
Expected: SKIPPED (no tools or no fixture) — confirms the marker wiring works.

- [ ] **Step 3: Write `docs/aligner-py/overview.md`**

Create `docs/aligner-py/overview.md` documenting: purpose (real-audio↔`.gp` alignment, strategy C), that it is the 4th decoupled project sharing only the `output/` tree, the module map (mirror the table style from `enricher-py/overview.md`), the `align.json` artifact, the `run`/`inspect`/`status` commands, external tool requirements (MuseScore, FluidSynth, ffmpeg, soundfont), and a "Deferred / future" section copying §9 of the design spec. Link back to `../output-contract.md` and the design spec.

- [ ] **Step 4: Update `docs/output-contract.md`**

In the directory-layout block, add the aligner artifact line:
```
  align.json                        # (added later by aligner-py) audio<->.gp alignment + confidence
```
Add an "Output files written by the aligner" section (mirroring the enricher section) describing: gates on `metadata.json` + `.gp` + `audio.*`; `align.json` written last (atomic) as the marker; `status` values `ok`/`rejected`/`no_audio`/`no_gp`; that the other three projects ignore `align.json`; and the `align_overlay.wav` / `align_plot.png` inspect artifacts (developer-facing, not part of the consumed contract). Note re-scrape `rmtree` self-healing wipes `align.json` too.

- [ ] **Step 5: Update `OVERVIEW.md` and root `CLAUDE.md`**

In `OVERVIEW.md`: add `aligner-py` to the component/doc map tables pointing at `docs/aligner-py/overview.md`. In `CLAUDE.md`: add `aligner-py` to the "What this repo is" list, the "Where to read about each part" table, a "Common commands" block, and the code→doc table row (`aligner-py/app/*` → `docs/aligner-py/overview.md`).

- [ ] **Step 6: Verify docs build/links and run full suite**

Run: `cd aligner-py && python -m pytest -v` (PASS) and manually confirm every new doc link resolves.

- [ ] **Step 7: Commit**

```bash
git add aligner-py/tests/test_integration.py docs/aligner-py/overview.md docs/output-contract.md OVERVIEW.md CLAUDE.md
git commit -m "docs(aligner): document align.json contract + integration test"
```

---

## Self-Review

**Spec coverage:**
- §3 modules → Tasks 2–9 (config, discover, render, features, align, output, inspect, cli) ✓
- §4 renderer (mscore→fluidsynth, injectable runner, fallback noted) → Task 6 ✓
- §5 chroma-CENS + DTW + warp anchors + fit/deviation confidence → Tasks 4–5 ✓
- §6 listen overlay + look plot → Task 8 ✓
- §7 `align.json` (status set, atomic last-write marker) + contract/doc updates → Tasks 7, 10 ✓
- §8 `run`/`inspect`/`status` CLI on explicit tab_ids, no DB → Task 9 ✓
- §9 deferred items → not built; copied into `docs/aligner-py/overview.md` (Task 10) ✓
- §11 testing (pure math unit-tested, tools behind `integration`) → Tasks 4, 6, 8 unit + Task 10 integration ✓

**Placeholder scan:** Every code step contains complete code. The integration test (Task 10) is intentionally a `skip`-guarded template because it requires a real binary + tab fixture that cannot be vendored — this is called out explicitly, not a hidden TODO.

**Type consistency:** `AlignResult` fields (`anchors`, `fit_cost`, `path_deviation`, `offset_s`) are consistent across Tasks 4, 7, 9. `map_time`/`warp_onsets` signatures match between Tasks 4 and 8. `Renderer.render(gp, work_dir) -> Path` matches its use in Task 9 and the `FakeRenderer` in conftest. `write_align(...)` keyword set is identical in Tasks 7 and 9. CLI funcs `cmd_run`/`cmd_inspect`/`cmd_status`/`align_tab` consistent between Task 9 interfaces and tests.
