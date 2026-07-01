# dataset-py Phase 0 — Guitar Tab Note-Event Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new decoupled `dataset-py` project that reads each tab's `.gpif` + `align.json` and writes `tab_notes.json` — the selected guitar track's notes as `(onset_s, duration_s, string, fret, …)` in real-audio seconds — plus a round-trip overlay/plot validator.

**Architecture:** A standalone Python CLI (`dataset`) mirroring `aligner-py`/`enricher-py` conventions. It shares no code with sibling projects; the `output/` filesystem contract is the only interface. Core is a self-written GPIF (GP6/7 XML) parser producing note events in the MuseScore reference timeline, projected through `align.json`'s piecewise-linear warp into the real-audio timeline.

**Tech Stack:** Python ≥ 3.13, `xml.etree.ElementTree` (stdlib, GPIF parsing), `numpy` (warp interp), `mido` + `fluidsynth` + `soundfile` + `librosa` + `matplotlib` (round-trip inspect), `pydantic`/`pydantic-settings` (config), `pytest` (tests).

## Global Constraints

- Python `requires-python = ">=3.13"`; package dir is `app/`, installed as console script `dataset = "app.cli:main"`.
- Tests deterministic and tool-free by default; live-tool tests marked `integration` and excluded via `addopts = "-m 'not integration'"`.
- JSON sidecars written **atomically** (`*.tmp` + `os.replace`), `indent=2, sort_keys=True`.
- Gate every tab on `metadata.json` existing (commit marker).
- Guitar track = per-track `<MIDI><Program> ∈ [24, 31]`, excluding `@$…$@` helper tracks and note-empty tracks.
- Qualifying songs have **1–2** guitar tracks; with 2, primary = **most notes, tie-break lowest track index**.
- The parser **must raise** on any repeat / alternate-ending (linear-score assumption guard).
- `string` index convention (**verified against real GPIF**): GPIF `<String>` value `0` = **lowest**-pitch string; tuning array is low→high `[40,45,50,55,59,64]`, so sounding `midi = tuning[string] + capo + fret`. Pinned by the pitch-sanity check. (Real note `String=2,Fret=7,Midi=57` → `tuning[2]+7 = 57` ✓.)
- Pitch tripwire is **tolerant**: exclude a tab as `pitch_mismatch` only when the mismatch **rate** exceeds `pitch_mismatch_max_rate` (default `0.05`). A convention/parser bug yields ≈100% mismatch (caught); a lone odd note does not nuke the song.
- Spec: `docs/superpowers/specs/2026-06-29-dataset-export-phase0-design.md`. Reference doc map: `OVERVIEW.md`, `docs/output-contract.md`.

---

### Task 1: Project scaffold + version

**Files:**
- Create: `dataset-py/pyproject.toml`
- Create: `dataset-py/app/__init__.py`
- Create: `dataset-py/app/config.py`
- Create: `dataset-py/tests/__init__.py`
- Test: `dataset-py/tests/test_version.py`

**Interfaces:**
- Produces: `app.__version__: str`; `app.config.Settings` (pydantic) with fields `output_dir: Path`, `musescore_bin: str`, `fluidsynth_bin: str`, `soundfont: Path`, `sample_rate: int`, `max_guitars: int`, `min_confidence_fit: float | None`; `app.config.get_settings() -> Settings`.

- [ ] **Step 1: Write `dataset-py/pyproject.toml`**

```toml
[project]
name = "ult-dataset"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "librosa",
    "mido",
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
dataset = "app.cli:main"

[tool.pytest.ini_options]
addopts = "-m 'not integration'"
markers = ["integration: requires MuseScore + FluidSynth + a soundfont"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 2: Write `dataset-py/app/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Write `dataset-py/app/config.py`**

```python
from __future__ import annotations

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

    # selection policy (swappable so the qualifying set can widen later)
    max_guitars: int = 2
    # tolerant pitch tripwire: exclude only when mismatch rate exceeds this
    pitch_mismatch_max_rate: float = 0.05


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Write `dataset-py/tests/__init__.py`** (empty file)

```python
```

- [ ] **Step 5: Write the failing test `dataset-py/tests/test_version.py`**

```python
import app
from app.config import get_settings


def test_version_string():
    assert app.__version__ == "0.1.0"


def test_settings_defaults():
    s = get_settings()
    assert s.max_guitars == 2
    assert s.sample_rate == 22050
```

- [ ] **Step 6: Install and run**

Run: `cd dataset-py && pip install -e ".[dev]" && python -m pytest -q`
Expected: PASS (2 passed). Console script `dataset` is registered (errors at import of `app.cli` until Task 8 — acceptable; not invoked here).

- [ ] **Step 7: Commit**

```bash
git add dataset-py/pyproject.toml dataset-py/app/__init__.py dataset-py/app/config.py dataset-py/tests/__init__.py dataset-py/tests/test_version.py
git commit -m "feat(dataset): scaffold ult-dataset project + config"
```

---

### Task 2: GPIF pool loader

Parse the GPIF XML into id-indexed pools and a track list. Pure stdlib `xml.etree`.

**Files:**
- Create: `dataset-py/app/gpif.py`
- Create: `dataset-py/tests/fixtures.py` (synthetic GPIF builder reused by later tests)
- Test: `dataset-py/tests/test_gpif_load.py`

**Interfaces:**
- Produces:
  - `app.gpif.Track` dataclass: `index: int`, `name: str`, `program: int | None`, `tuning: list[int]`, `capo: int`, `bar_ids: list[int]` (one Bar id per MasterBar, in order).
  - `app.gpif.Score` dataclass: `tracks: list[Track]`, `master_bars: list[MasterBar]`, `bars: dict[int, BarEl]`, `voices: dict[int, list[int]]` (voice id → beat ids), `beats: dict[int, Beat]`, `rhythms: dict[int, Rhythm]`, `notes: dict[int, NoteEl]`, `tempo_map: list[TempoEvent]`.
  - `MasterBar`: `time_num: int`, `time_den: int`, `bar_ids: list[int]`.
  - `BarEl`: `voice_ids: list[int]` (length 4, `-1` = empty).
  - `Beat`: `rhythm_ref: int`, `note_ids: list[int]`, `has_grace: bool`.
  - `Rhythm`: `note_value: str`, `dots: int`, `tuplet_num: int | None`, `tuplet_den: int | None`.
  - `NoteEl`: `string: int | None`, `fret: int | None`, `midi: int | None`, `dead: bool`, `slide: bool`, `bend: bool`, `hopo: bool`, `harmonic: bool`.
  - `TempoEvent`: `bar: int`, `position: float`, `bpm: float`, `unit: int`, `linear: bool`.
  - `app.gpif.load_gpif(path: Path) -> Score`.

- [ ] **Step 1: Write the synthetic GPIF builder `dataset-py/tests/fixtures.py`**

```python
"""Minimal but structurally faithful GPIF builder for tests.

Produces a one- or two-track GPIF with explicit pools so the parser can be
exercised without a 900 KB real file. Standard tuning, 4/4, one tempo marker.
"""
from __future__ import annotations


def _track_xml(idx: int, name: str, program: int, bar_ids: list[int]) -> str:
    bars = " ".join(str(b) for b in bar_ids)
    return f"""
  <Track id="{idx}">
    <Name><![CDATA[{name}]]></Name>
    <MidiConnection><PrimaryChannel>1</PrimaryChannel></MidiConnection>
    <Program>{program}</Program>
    <Staves><Staff><Properties>
      <Property name="CapoFret"><Fret>0</Fret></Property>
      <Property name="Tuning">
        <Pitches>40 45 50 55 59 64</Pitches>
        <Instrument>Guitar</Instrument>
      </Property>
    </Properties></Staff></Staves>
  </Track>"""


def make_gpif(
    *,
    tracks: list[tuple[int, str, int]],   # (track_index, name, program)
    note_count_per_track: list[int] | None = None,
) -> str:
    """Build a GPIF where each track plays `n` quarter notes (open low-E:
    GPIF string index 0 = lowest string, fret 0, MIDI 40) across one bar.

    Pools are shared/simple: each track gets its own Bar/Voice/Beats/Notes ids.
    """
    n_tracks = len(tracks)
    counts = note_count_per_track or [1] * n_tracks

    # --- build pools ---
    bars_xml, voices_xml, beats_xml, notes_xml = [], [], [], []
    bid = vid = beid = nid = 0
    track_bar_ids: list[list[int]] = []
    for ti, (_, _, _), n in zip(range(n_tracks), tracks, counts):
        beat_ids = []
        for _ in range(n):
            # one quarter note, open string 0 (low E), fret 0, midi 40
            notes_xml.append(
                f'<Note id="{nid}"><Properties>'
                f'<Property name="Fret"><Fret>0</Fret></Property>'
                f'<Property name="Midi"><Number>40</Number></Property>'
                f'<Property name="String"><String>0</String></Property>'
                f'</Properties></Note>'
            )
            beats_xml.append(
                f'<Beat id="{beid}"><Rhythm ref="0" />'
                f'<Notes>{nid}</Notes></Beat>'
            )
            beat_ids.append(beid)
            beid += 1
            nid += 1
        voices_xml.append(
            f'<Voice id="{vid}"><Beats>{" ".join(map(str, beat_ids))}</Beats></Voice>'
        )
        bars_xml.append(
            f'<Bar id="{bid}"><Clef>G2</Clef><Voices>{vid} -1 -1 -1</Voices></Bar>'
        )
        track_bar_ids.append([bid])
        bid += 1
        vid += 1

    tracks_xml = "".join(
        _track_xml(ti, name, prog, track_bar_ids[ti])
        for ti, (_, name, prog) in enumerate(tracks)
    )
    # one MasterBar listing each track's bar id
    mb_bar_ids = " ".join(str(track_bar_ids[ti][0]) for ti in range(n_tracks))
    rhythms_xml = '<Rhythm id="0"><NoteValue>Quarter</NoteValue></Rhythm>'

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <MasterTrack><Automations>
    <Automation><Type>Tempo</Type><Linear>false</Linear>
      <Bar>0</Bar><Position>0</Position><Value>120 2</Value></Automation>
  </Automations></MasterTrack>
  <Tracks>{tracks_xml}</Tracks>
  <MasterBars>
    <MasterBar><Time>4/4</Time><Bars>{mb_bar_ids}</Bars></MasterBar>
  </MasterBars>
  <Bars>{"".join(bars_xml)}</Bars>
  <Voices>{"".join(voices_xml)}</Voices>
  <Beats>{"".join(beats_xml)}</Beats>
  <Rhythms>{rhythms_xml}</Rhythms>
  <Notes>{"".join(notes_xml)}</Notes>
</GPIF>"""
```

- [ ] **Step 2: Write the failing test `dataset-py/tests/test_gpif_load.py`**

```python
from app.gpif import load_gpif
from tests.fixtures import make_gpif


def _write(tmp_path, xml):
    p = tmp_path / "t.gpif"
    p.write_text(xml, encoding="utf-8")
    return p


def test_loads_tracks_and_programs(tmp_path):
    xml = make_gpif(tracks=[(0, "Rhythm Guitar", 27), (1, "Vocal", 85)])
    score = load_gpif(_write(tmp_path, xml))
    assert [t.program for t in score.tracks] == [27, 85]
    assert score.tracks[0].name == "Rhythm Guitar"
    assert score.tracks[0].tuning == [40, 45, 50, 55, 59, 64]
    assert score.tracks[0].capo == 0
    assert score.tracks[0].bar_ids == [0]


def test_loads_pools(tmp_path):
    xml = make_gpif(tracks=[(0, "Guitar", 27)], note_count_per_track=[3])
    score = load_gpif(_write(tmp_path, xml))
    assert len(score.master_bars) == 1
    assert score.master_bars[0].time_num == 4 and score.master_bars[0].time_den == 4
    assert score.rhythms[0].note_value == "Quarter"
    assert score.beats[0].rhythm_ref == 0
    assert score.notes[0].fret == 0 and score.notes[0].string == 0
    assert score.notes[0].midi == 40


def test_tempo_map(tmp_path):
    xml = make_gpif(tracks=[(0, "Guitar", 27)])
    score = load_gpif(_write(tmp_path, xml))
    assert len(score.tempo_map) == 1
    assert score.tempo_map[0].bpm == 120.0
    assert score.tempo_map[0].unit == 2
    assert score.tempo_map[0].linear is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_gpif_load.py -q`
Expected: FAIL (`ModuleNotFoundError: app.gpif`).

- [ ] **Step 4: Write `dataset-py/app/gpif.py`**

```python
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MasterBar:
    time_num: int
    time_den: int
    bar_ids: list[int]


@dataclass(frozen=True)
class BarEl:
    voice_ids: list[int]


@dataclass(frozen=True)
class Beat:
    rhythm_ref: int
    note_ids: list[int]
    has_grace: bool


@dataclass(frozen=True)
class Rhythm:
    note_value: str
    dots: int
    tuplet_num: int | None
    tuplet_den: int | None


@dataclass(frozen=True)
class NoteEl:
    string: int | None
    fret: int | None
    midi: int | None
    dead: bool
    slide: bool
    bend: bool
    hopo: bool
    harmonic: bool


@dataclass(frozen=True)
class TempoEvent:
    bar: int
    position: float
    bpm: float
    unit: int
    linear: bool


@dataclass(frozen=True)
class Track:
    index: int
    name: str
    program: int | None
    tuning: list[int]
    capo: int
    bar_ids: list[int]


@dataclass(frozen=True)
class Score:
    tracks: list[Track]
    master_bars: list[MasterBar]
    bars: dict[int, BarEl]
    voices: dict[int, list[int]]
    beats: dict[int, Beat]
    rhythms: dict[int, Rhythm]
    notes: dict[int, NoteEl]
    tempo_map: list[TempoEvent]


def _ints(text: str | None) -> list[int]:
    return [int(x) for x in text.split()] if text and text.strip() else []


def _prop(parent: ET.Element, name: str) -> ET.Element | None:
    for p in parent.iter("Property"):
        if p.get("name") == name:
            return p
    return None


def _parse_track(idx: int, el: ET.Element) -> Track:
    name_el = el.find("Name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    prog_el = el.find("Program")
    program = int(prog_el.text) if prog_el is not None and prog_el.text else None
    tuning: list[int] = []
    capo = 0
    tun = _prop(el, "Tuning")
    if tun is not None:
        pit = tun.find("Pitches")
        if pit is not None:
            tuning = _ints(pit.text)
    capo_p = _prop(el, "CapoFret")
    if capo_p is not None:
        fret = capo_p.find("Fret")
        if fret is not None and fret.text:
            capo = int(fret.text)
    return Track(index=idx, name=name, program=program, tuning=tuning,
                 capo=capo, bar_ids=[])


def _parse_rhythm(el: ET.Element) -> Rhythm:
    nv = el.find("NoteValue")
    dot = el.find("AugmentationDot")
    tup = el.find("PrimaryTuplet")
    return Rhythm(
        note_value=(nv.text or "Quarter") if nv is not None else "Quarter",
        dots=int(dot.get("count", "1")) if dot is not None else 0,
        tuplet_num=int(tup.get("num")) if tup is not None else None,
        tuplet_den=int(tup.get("den")) if tup is not None else None,
    )


def _parse_note(el: ET.Element) -> NoteEl:
    def num(name: str, child: str) -> int | None:
        p = _prop(el, name)
        if p is None:
            return None
        c = p.find(child)
        return int(c.text) if c is not None and c.text else None

    has = lambda name: _prop(el, name) is not None  # noqa: E731
    return NoteEl(
        string=num("String", "String"),
        fret=num("Fret", "Fret"),
        midi=num("Midi", "Number"),
        dead=has("Muted"),
        slide=has("Slide"),
        bend=has("Bended") or has("Bend"),
        hopo=has("HopoOrigin") or has("HopoDestination"),
        harmonic=has("HarmonicType"),
    )


def load_gpif(path: Path) -> Score:
    root = ET.fromstring(Path(path).read_text(encoding="utf-8", errors="replace"))

    tracks_parent = root.find("Tracks")
    tracks = []
    if tracks_parent is not None:
        for i, tel in enumerate(tracks_parent.findall("Track")):
            tracks.append(_parse_track(i, tel))

    master_bars = []
    mb_parent = root.find("MasterBars")
    if mb_parent is not None:
        for mb in mb_parent.findall("MasterBar"):
            t = mb.find("Time")
            num, den = (4, 4)
            if t is not None and t.text and "/" in t.text:
                num, den = (int(x) for x in t.text.split("/"))
            master_bars.append(MasterBar(num, den, _ints(mb.findtext("Bars"))))

    # attach per-track bar id sequences (column ti of each MasterBar's <Bars>)
    track_bar_ids: dict[int, list[int]] = {t.index: [] for t in tracks}
    for mb in master_bars:
        for ti in range(len(tracks)):
            if ti < len(mb.bar_ids):
                track_bar_ids[ti].append(mb.bar_ids[ti])
    tracks = [
        Track(t.index, t.name, t.program, t.tuning, t.capo, track_bar_ids[t.index])
        for t in tracks
    ]

    bars: dict[int, BarEl] = {}
    bp = root.find("Bars")
    if bp is not None:
        for b in bp.findall("Bar"):
            bars[int(b.get("id"))] = BarEl(_ints(b.findtext("Voices")))

    voices: dict[int, list[int]] = {}
    vp = root.find("Voices")
    if vp is not None:
        for v in vp.findall("Voice"):
            voices[int(v.get("id"))] = _ints(v.findtext("Beats"))

    beats: dict[int, Beat] = {}
    btp = root.find("Beats")
    if btp is not None:
        for be in btp.findall("Beat"):
            r = be.find("Rhythm")
            beats[int(be.get("id"))] = Beat(
                rhythm_ref=int(r.get("ref")) if r is not None else -1,
                note_ids=_ints(be.findtext("Notes")),
                has_grace=be.find("GraceNotes") is not None,
            )

    rhythms: dict[int, Rhythm] = {}
    rp = root.find("Rhythms")
    if rp is not None:
        for r in rp.findall("Rhythm"):
            rhythms[int(r.get("id"))] = _parse_rhythm(r)

    notes: dict[int, NoteEl] = {}
    np_ = root.find("Notes")
    if np_ is not None:
        for n in np_.findall("Note"):
            notes[int(n.get("id"))] = _parse_note(n)

    tempo_map = []
    for auto in root.iter("Automation"):
        if (auto.findtext("Type") or "") != "Tempo":
            continue
        val = (auto.findtext("Value") or "120 2").split()
        bpm = float(val[0]); unit = int(val[1]) if len(val) > 1 else 2
        tempo_map.append(TempoEvent(
            bar=int(auto.findtext("Bar") or 0),
            position=float(auto.findtext("Position") or 0.0),
            bpm=bpm, unit=unit,
            linear=(auto.findtext("Linear") or "false").lower() == "true",
        ))
    tempo_map.sort(key=lambda e: (e.bar, e.position))

    return Score(tracks=tracks, master_bars=master_bars, bars=bars,
                 voices=voices, beats=beats, rhythms=rhythms, notes=notes,
                 tempo_map=tempo_map)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_gpif_load.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add dataset-py/app/gpif.py dataset-py/tests/fixtures.py dataset-py/tests/test_gpif_load.py
git commit -m "feat(dataset): GPIF pool loader (tracks, bars, beats, rhythms, notes, tempo)"
```

---

### Task 3: Guitar-track selection

**Files:**
- Create: `dataset-py/app/select.py`
- Test: `dataset-py/tests/test_select.py`

**Interfaces:**
- Consumes: `app.gpif.Score`, `app.gpif.Track`.
- Produces:
  - `app.select.note_count(score, track) -> int` (total notes the track plays across its bars/voices/beats).
  - `app.select.guitar_tracks(score) -> list[Track]` (program 24–31, not `@$…$@`, note_count > 0).
  - `app.select.select_track(score, *, max_guitars: int = 2) -> tuple[Track | None, str]` returning `(track, reason)`; `reason` is `"ok"` or an exclusion reason (`"no_guitar"`, `"too_many_guitars"`).

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_select.py`**

```python
from app.gpif import load_gpif
from app.select import guitar_tracks, note_count, select_track
from tests.fixtures import make_gpif


def _load(tmp_path, xml):
    p = tmp_path / "t.gpif"; p.write_text(xml, encoding="utf-8")
    return load_gpif(p)


def test_single_guitar_selected(tmp_path):
    s = _load(tmp_path, make_gpif(tracks=[(0, "Vocal", 85), (1, "Rhythm Guitar", 27)]))
    assert [t.index for t in guitar_tracks(s)] == [1]
    track, reason = select_track(s)
    assert reason == "ok" and track.index == 1


def test_helper_track_excluded(tmp_path):
    s = _load(tmp_path, make_gpif(tracks=[(0, "Guitar", 27), (1, "@$Chords$@", 25)]))
    assert [t.index for t in guitar_tracks(s)] == [0]


def test_two_guitars_primary_is_most_notes(tmp_path):
    xml = make_gpif(
        tracks=[(0, "Lead Guitar", 27), (1, "Rhythm Guitar", 27)],
        note_count_per_track=[2, 5],
    )
    s = _load(tmp_path, xml)
    track, reason = select_track(s)
    assert reason == "ok" and track.index == 1  # 5 notes > 2 notes


def test_two_guitars_tiebreak_lowest_index(tmp_path):
    xml = make_gpif(
        tracks=[(0, "Guitar A", 27), (1, "Guitar B", 27)],
        note_count_per_track=[3, 3],
    )
    s = _load(tmp_path, xml)
    track, _ = select_track(s)
    assert track.index == 0


def test_three_guitars_excluded(tmp_path):
    xml = make_gpif(tracks=[(0, "G1", 27), (1, "G2", 27), (2, "G3", 27)])
    s = _load(tmp_path, xml)
    track, reason = select_track(s)
    assert track is None and reason == "too_many_guitars"


def test_no_guitar_excluded(tmp_path):
    s = _load(tmp_path, make_gpif(tracks=[(0, "Vocal", 85)]))
    track, reason = select_track(s)
    assert track is None and reason == "no_guitar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_select.py -q`
Expected: FAIL (`ModuleNotFoundError: app.select`).

- [ ] **Step 3: Write `dataset-py/app/select.py`**

```python
from __future__ import annotations

from app.gpif import Score, Track

GUITAR_PROGRAMS = range(24, 32)


def _is_helper(name: str) -> bool:
    return name.strip().startswith("@$")


def note_count(score: Score, track: Track) -> int:
    total = 0
    for bar_id in track.bar_ids:
        bar = score.bars.get(bar_id)
        if bar is None:
            continue
        for vid in bar.voice_ids:
            if vid < 0:
                continue
            for beat_id in score.voices.get(vid, []):
                beat = score.beats.get(beat_id)
                if beat is not None:
                    total += len(beat.note_ids)
    return total


def guitar_tracks(score: Score) -> list[Track]:
    out = []
    for t in score.tracks:
        if t.program is None or t.program not in GUITAR_PROGRAMS:
            continue
        if _is_helper(t.name):
            continue
        if note_count(score, t) == 0:
            continue
        out.append(t)
    return out


def select_track(score: Score, *, max_guitars: int = 2) -> tuple[Track | None, str]:
    guitars = guitar_tracks(score)
    if not guitars:
        return None, "no_guitar"
    if len(guitars) > max_guitars:
        return None, "too_many_guitars"
    if len(guitars) == 1:
        return guitars[0], "ok"
    # primary = most notes, tie-break lowest track index
    primary = max(guitars, key=lambda t: (note_count(score, t), -t.index))
    return primary, "ok"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_select.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/select.py dataset-py/tests/test_select.py
git commit -m "feat(dataset): guitar-track selection (program + helper/empty exclusion, primary policy)"
```

---

### Task 4: Rhythm → duration in quarter notes

**Files:**
- Modify: `dataset-py/app/gpif.py` (append `rhythm_quarters`)
- Test: `dataset-py/tests/test_durations.py`

**Interfaces:**
- Produces: `app.gpif.rhythm_quarters(r: Rhythm) -> float` — duration in quarter-note units, applying dots and tuplet.

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_durations.py`**

```python
import math

from app.gpif import Rhythm, rhythm_quarters


def q(note_value, dots=0, num=None, den=None):
    return rhythm_quarters(Rhythm(note_value, dots, num, den))


def test_base_values():
    assert q("Whole") == 4.0
    assert q("Half") == 2.0
    assert q("Quarter") == 1.0
    assert q("Eighth") == 0.5
    assert q("16th") == 0.25
    assert q("32nd") == 0.125
    assert q("64th") == 0.0625


def test_dotted():
    assert q("Quarter", dots=1) == 1.5
    assert q("Quarter", dots=2) == 1.75
    assert q("Half", dots=1) == 3.0


def test_triplet():
    # eighth-note triplet: 3 in the space of 2 → each = 0.5 * 2/3
    assert math.isclose(q("Eighth", num=3, den=2), 0.5 * 2 / 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_durations.py -q`
Expected: FAIL (`ImportError: cannot import name 'rhythm_quarters'`).

- [ ] **Step 3: Append to `dataset-py/app/gpif.py`**

```python
_NOTE_VALUE_QUARTERS = {
    "Whole": 4.0, "Half": 2.0, "Quarter": 1.0, "Eighth": 0.5,
    "16th": 0.25, "32nd": 0.125, "64th": 0.0625, "128th": 0.03125,
}


def rhythm_quarters(r: Rhythm) -> float:
    base = _NOTE_VALUE_QUARTERS.get(r.note_value, 1.0)
    # dotted: each dot adds half of the previous increment
    dur = base * (2.0 - 0.5 ** r.dots)
    if r.tuplet_num and r.tuplet_den:
        dur *= r.tuplet_den / r.tuplet_num
    return dur
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_durations.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/gpif.py dataset-py/tests/test_durations.py
git commit -m "feat(dataset): rhythm-to-quarter-note duration (dots + tuplets)"
```

---

### Task 5: Track walk → note events in reference quarters

Walk one track's bars/voices/beats and emit note events positioned in cumulative quarter-note time. Raise on repeats.

**Files:**
- Create: `dataset-py/app/walk.py`
- Test: `dataset-py/tests/test_walk.py`

**Interfaces:**
- Consumes: `app.gpif.Score`, `app.gpif.Track`, `app.gpif.rhythm_quarters`.
- Produces:
  - `app.walk.RawNote` dataclass: `onset_q: float`, `dur_q: float`, `string: int | None`, `fret: int | None`, `midi: int | None`, `dead/slide/bend/hopo/harmonic: bool`.
  - `app.walk.RepeatError(Exception)`.
  - `app.walk.walk_track(score, track) -> list[RawNote]` (sorted by `onset_q`). Raises `RepeatError` if any MasterBar carries a repeat/alternate-ending.
  - `app.walk.bar_quarters(master_bar) -> float` (bar length in quarters from its time signature).

- [ ] **Step 1: Add a repeat flag to the GPIF loader**

Modify `dataset-py/app/gpif.py`: extend `MasterBar` and its parse to capture repeats.

Replace the `MasterBar` dataclass with:

```python
@dataclass(frozen=True)
class MasterBar:
    time_num: int
    time_den: int
    bar_ids: list[int]
    has_repeat: bool = False
```

In `load_gpif`, replace the `master_bars.append(...)` line with:

```python
            has_repeat = (
                mb.find("Repeat") is not None
                or mb.find("AlternateEndings") is not None
            )
            master_bars.append(
                MasterBar(num, den, _ints(mb.findtext("Bars")), has_repeat)
            )
```

- [ ] **Step 2: Write the failing test `dataset-py/tests/test_walk.py`**

```python
import math

import pytest

from app.gpif import load_gpif
from app.walk import RepeatError, walk_track


def _load(tmp_path, xml):
    p = tmp_path / "t.gpif"; p.write_text(xml, encoding="utf-8")
    return load_gpif(p)


def test_walk_emits_sequential_quarters(tmp_path):
    from tests.fixtures import make_gpif
    s = _load(tmp_path, make_gpif(tracks=[(0, "Guitar", 27)], note_count_per_track=[3]))
    notes = walk_track(s, s.tracks[0])
    assert [n.onset_q for n in notes] == [0.0, 1.0, 2.0]
    assert all(math.isclose(n.dur_q, 1.0) for n in notes)
    assert notes[0].string == 0 and notes[0].fret == 0 and notes[0].midi == 40


def test_walk_raises_on_repeat(tmp_path):
    # inject a repeat into the single MasterBar
    from tests.fixtures import make_gpif
    xml = make_gpif(tracks=[(0, "Guitar", 27)]).replace(
        "<Time>4/4</Time>", '<Time>4/4</Time><Repeat start="true" count="2" />'
    )
    s = _load(tmp_path, xml)
    with pytest.raises(RepeatError):
        walk_track(s, s.tracks[0])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_walk.py -q`
Expected: FAIL (`ModuleNotFoundError: app.walk`).

- [ ] **Step 4: Write `dataset-py/app/walk.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from app.gpif import MasterBar, Score, Track, rhythm_quarters


class RepeatError(Exception):
    """Raised when a score uses repeats/alternate endings (non-linear)."""


@dataclass(frozen=True)
class RawNote:
    onset_q: float
    dur_q: float
    string: int | None
    fret: int | None
    midi: int | None
    dead: bool
    slide: bool
    bend: bool
    hopo: bool
    harmonic: bool


def bar_quarters(mb: MasterBar) -> float:
    return 4.0 * mb.time_num / mb.time_den


def walk_track(score: Score, track: Track) -> list[RawNote]:
    out: list[RawNote] = []
    bar_start_q = 0.0
    for mb_i, mb in enumerate(score.master_bars):
        if mb.has_repeat:
            raise RepeatError(f"repeat/alternate ending at master bar {mb_i}")
        bar_id = track.bar_ids[mb_i] if mb_i < len(track.bar_ids) else -1
        bar = score.bars.get(bar_id)
        if bar is not None:
            for vid in bar.voice_ids:
                if vid < 0:
                    continue
                pos_q = bar_start_q
                for beat_id in score.voices.get(vid, []):
                    beat = score.beats.get(beat_id)
                    if beat is None:
                        continue
                    rhythm = score.rhythms.get(beat.rhythm_ref)
                    dur_q = rhythm_quarters(rhythm) if rhythm is not None else 0.0
                    for nid in beat.note_ids:
                        n = score.notes.get(nid)
                        if n is None:
                            continue
                        out.append(RawNote(
                            onset_q=pos_q, dur_q=dur_q,
                            string=n.string, fret=n.fret, midi=n.midi,
                            dead=n.dead, slide=n.slide, bend=n.bend,
                            hopo=n.hopo, harmonic=n.harmonic))
                    pos_q += dur_q
        bar_start_q += bar_quarters(mb)
    out.sort(key=lambda n: n.onset_q)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_walk.py tests/test_gpif_load.py -q`
Expected: PASS (re-running the loader test confirms the `MasterBar` change didn't break it).

- [ ] **Step 6: Commit**

```bash
git add dataset-py/app/gpif.py dataset-py/app/walk.py dataset-py/tests/test_walk.py
git commit -m "feat(dataset): track walk to note events in quarter-note time (+ repeat guard)"
```

---

### Task 6: Quarter-time → reference seconds (tempo map)

**Files:**
- Create: `dataset-py/app/tempo.py`
- Test: `dataset-py/tests/test_tempo.py`

**Interfaces:**
- Consumes: `app.gpif.Score`, `app.gpif.TempoEvent`, `app.gpif.bar_quarters` via `app.walk.bar_quarters`.
- Produces:
  - `app.tempo.tempo_points(score) -> list[tuple[float, float]]` — sorted `(absolute_quarter, bpm_in_quarter_terms)` change points; bpm normalized to quarter-note beats using the tempo `unit` (GPIF unit `2`=quarter; the `Value`'s second field).
  - `app.tempo.quarters_to_seconds(q: float, points: list[tuple[float, float]]) -> float` — piecewise-constant (step) integration.

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_tempo.py`**

```python
import math

from app.tempo import quarters_to_seconds, tempo_points


def test_constant_tempo_120():
    pts = [(0.0, 120.0)]  # 120 quarter-bpm → 0.5 s per quarter
    assert math.isclose(quarters_to_seconds(0.0, pts), 0.0)
    assert math.isclose(quarters_to_seconds(1.0, pts), 0.5)
    assert math.isclose(quarters_to_seconds(4.0, pts), 2.0)


def test_step_tempo_change():
    # 120 bpm for first 4 quarters (2.0 s), then 240 bpm (0.25 s/quarter)
    pts = [(0.0, 120.0), (4.0, 240.0)]
    assert math.isclose(quarters_to_seconds(4.0, pts), 2.0)
    assert math.isclose(quarters_to_seconds(6.0, pts), 2.0 + 2 * 0.25)


def test_tempo_points_from_score(tmp_path):
    from app.gpif import load_gpif
    from tests.fixtures import make_gpif
    p = tmp_path / "t.gpif"
    p.write_text(make_gpif(tracks=[(0, "Guitar", 27)]), encoding="utf-8")
    s = load_gpif(p)
    pts = tempo_points(s)
    assert pts == [(0.0, 120.0)]  # unit=2 (quarter) → bpm unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_tempo.py -q`
Expected: FAIL (`ModuleNotFoundError: app.tempo`).

- [ ] **Step 3: Write `dataset-py/app/tempo.py`**

```python
from __future__ import annotations

from app.gpif import Score
from app.walk import bar_quarters

# GPIF tempo unit codes → multiplier converting the stated bpm to quarter-bpm.
# unit 2 = quarter note (bpm already in quarters); 1 = half (×0.5 quarters per
# beat → 2× as many quarter-beats per minute); 3 = dotted-quarter; 4 = eighth.
_UNIT_TO_QUARTER_BPM = {1: 0.5, 2: 1.0, 3: 1.5, 4: 2.0}


def tempo_points(score: Score) -> list[tuple[float, float]]:
    # absolute quarter position of each MasterBar start
    bar_start_q = [0.0]
    for mb in score.master_bars:
        bar_start_q.append(bar_start_q[-1] + bar_quarters(mb))

    pts: list[tuple[float, float]] = []
    for ev in score.tempo_map:
        bar_q = bar_start_q[ev.bar] if ev.bar < len(bar_start_q) else 0.0
        abs_q = bar_q + ev.position  # position is in quarter beats from bar start
        quarter_bpm = ev.bpm / _UNIT_TO_QUARTER_BPM.get(ev.unit, 1.0)
        pts.append((abs_q, quarter_bpm))
    if not pts:
        pts = [(0.0, 120.0)]
    pts.sort(key=lambda p: p[0])
    return pts


def quarters_to_seconds(q: float, points: list[tuple[float, float]]) -> float:
    seconds = 0.0
    for i, (start_q, bpm) in enumerate(points):
        end_q = points[i + 1][0] if i + 1 < len(points) else float("inf")
        seg_start = max(start_q, 0.0)
        if q <= seg_start:
            break
        seg_end = min(q, end_q)
        if seg_end > seg_start:
            seconds += (seg_end - seg_start) * (60.0 / bpm)
        if q <= end_q:
            break
    return seconds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_tempo.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/tempo.py dataset-py/tests/test_tempo.py
git commit -m "feat(dataset): tempo map quarter-time to reference seconds (step integration)"
```

---

### Task 7: Pitch sanity check + string convention

**Files:**
- Create: `dataset-py/app/pitch.py`
- Test: `dataset-py/tests/test_pitch.py`

**Interfaces:**
- Consumes: `app.gpif.Track`, `app.walk.RawNote`.
- Produces:
  - `app.pitch.expected_midi(track, string, fret) -> int` — `tuning[string] + capo + fret` (GPIF string 0 = lowest).
  - `app.pitch.pitch_mismatches(track, notes) -> list[tuple[int, int, int]]` — list of `(index, expected, actual)` where a note's stored `midi` disagrees with the fretted pitch (ignoring notes with no string/fret/midi).

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_pitch.py`**

```python
from app.gpif import Track
from app.pitch import expected_midi, pitch_mismatches
from app.walk import RawNote

STD = Track(index=0, name="G", program=27,
            tuning=[40, 45, 50, 55, 59, 64], capo=0, bar_ids=[])


def _note(string, fret, midi):
    return RawNote(0.0, 1.0, string, fret, midi, False, False, False, False, False)


def test_expected_midi_string_convention():
    # GPIF string 0 = lowest (low E, MIDI 40); string 5 = high E (MIDI 64)
    assert expected_midi(STD, 0, 0) == 40
    assert expected_midi(STD, 5, 0) == 64
    assert expected_midi(STD, 0, 3) == 43
    # real-file anchor: string 2, fret 7 → 57
    assert expected_midi(STD, 2, 7) == 57


def test_capo_offsets_pitch():
    capo2 = Track(0, "G", 27, [40, 45, 50, 55, 59, 64], 2, [])
    assert expected_midi(capo2, 0, 0) == 42


def test_mismatch_detected():
    good = _note(0, 0, 40)
    bad = _note(0, 0, 99)
    assert pitch_mismatches(STD, [good]) == []
    assert pitch_mismatches(STD, [bad]) == [(0, 40, 99)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_pitch.py -q`
Expected: FAIL (`ModuleNotFoundError: app.pitch`).

- [ ] **Step 3: Write `dataset-py/app/pitch.py`**

```python
from __future__ import annotations

from app.gpif import Track
from app.walk import RawNote


def expected_midi(track: Track, string: int, fret: int) -> int:
    # GPIF string 0 = lowest-pitch string; tuning is low→high (direct index).
    return track.tuning[string] + track.capo + fret


def pitch_mismatches(track: Track, notes: list[RawNote]) -> list[tuple[int, int, int]]:
    out = []
    for i, n in enumerate(notes):
        if n.string is None or n.fret is None or n.midi is None:
            continue
        if not (0 <= n.string < len(track.tuning)):
            continue
        exp = expected_midi(track, n.string, n.fret)
        if exp != n.midi:
            out.append((i, exp, n.midi))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_pitch.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/pitch.py dataset-py/tests/test_pitch.py
git commit -m "feat(dataset): fretted-pitch sanity check + string-index convention"
```

---

### Task 8: Warp projection (reference → real seconds)

**Files:**
- Create: `dataset-py/app/project.py`
- Test: `dataset-py/tests/test_project.py`

**Interfaces:**
- Produces:
  - `app.project.project_seconds(t_ref: float, warp: list[list[float]]) -> float` — piecewise-linear interpolation of `ref→real` over warp anchors `[[ref, real], …]`; clamps to the endpoints outside the domain.
  - `app.project.load_warp(tab_dir: Path) -> dict | None` — reads `align.json`, returns its dict if `status == "ok"`, else `None`.

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_project.py`**

```python
import json

from app.project import load_warp, project_seconds

WARP = [[0.0, 1.0], [1.0, 3.0], [2.0, 4.0]]  # ref→real


def test_interpolates_within_domain():
    assert project_seconds(0.0, WARP) == 1.0
    assert project_seconds(0.5, WARP) == 2.0   # midway 1.0..3.0
    assert project_seconds(1.0, WARP) == 3.0
    assert project_seconds(1.5, WARP) == 3.5


def test_clamps_outside_domain():
    assert project_seconds(-5.0, WARP) == 1.0
    assert project_seconds(99.0, WARP) == 4.0


def test_load_warp_ok(tmp_path):
    (tmp_path / "align.json").write_text(json.dumps({"status": "ok", "warp": WARP}))
    data = load_warp(tmp_path)
    assert data["warp"] == WARP


def test_load_warp_rejected_returns_none(tmp_path):
    (tmp_path / "align.json").write_text(json.dumps({"status": "rejected", "warp": []}))
    assert load_warp(tmp_path) is None


def test_load_warp_missing_returns_none(tmp_path):
    assert load_warp(tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_project.py -q`
Expected: FAIL (`ModuleNotFoundError: app.project`).

- [ ] **Step 3: Write `dataset-py/app/project.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def project_seconds(t_ref: float, warp: list[list[float]]) -> float:
    ref = np.array([a[0] for a in warp], dtype=float)
    real = np.array([a[1] for a in warp], dtype=float)
    return float(np.interp(t_ref, ref, real))  # np.interp clamps to endpoints


def load_warp(tab_dir: Path) -> dict | None:
    p = Path(tab_dir) / "align.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError):
        return None
    if data.get("status") != "ok" or not data.get("warp"):
        return None
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_project.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/project.py dataset-py/tests/test_project.py
git commit -m "feat(dataset): piecewise-linear warp projection (ref to real seconds)"
```

---

### Task 9: `tab_notes.json` writer

**Files:**
- Create: `dataset-py/app/output.py`
- Test: `dataset-py/tests/test_output.py`

**Interfaces:**
- Produces:
  - `app.output.NoteEvent` TypedDict-ish dict shape (documented).
  - `app.output.write_tab_notes(*, tab_dir, status, reason, source, selected_track, notes, exporter_version, now_iso) -> Path` — writes `tab_notes.json` atomically; returns its path.
  - `app.output.write_excluded(*, tab_dir, reason, source, exporter_version, now_iso) -> Path` — convenience for `status="excluded"`.

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_output.py`**

```python
import json

from app.output import write_excluded, write_tab_notes


def test_write_ok_payload(tmp_path):
    notes = [{"onset_s": 1.5, "duration_s": 0.5, "string": 5, "fret": 0,
              "midi_pitch": 40,
              "tech": {"slide": False, "bend": False, "hopo": False,
                       "dead": False, "harmonic": False}}]
    p = write_tab_notes(
        tab_dir=tmp_path, status="ok", reason=None,
        source={"gpif": "x.gpif", "audio": "audio.webm",
                "align": {"warp_ref": "align.json"}},
        selected_track={"index": 1, "name": "Rhythm Guitar", "program": 27,
                        "tuning": [40, 45, 50, 55, 59, 64], "capo": 0},
        notes=notes, exporter_version="0.1.0", now_iso="2026-06-29T00:00:00")
    assert p == tmp_path / "tab_notes.json"
    data = json.loads(p.read_text())
    assert data["schema_version"] == 1
    assert data["status"] == "ok"
    assert data["notes"][0]["string"] == 5
    assert data["selected_track"]["program"] == 27
    assert not (tmp_path / "tab_notes.json.tmp").exists()


def test_write_excluded_payload(tmp_path):
    p = write_excluded(
        tab_dir=tmp_path, reason="too_many_guitars",
        source={"gpif": "x.gpif"}, exporter_version="0.1.0",
        now_iso="2026-06-29T00:00:00")
    data = json.loads(p.read_text())
    assert data["status"] == "excluded"
    assert data["reason"] == "too_many_guitars"
    assert data["notes"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_output.py -q`
Expected: FAIL (`ModuleNotFoundError: app.output`).

- [ ] **Step 3: Write `dataset-py/app/output.py`**

```python
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA_VERSION = 1


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_tab_notes(
    *,
    tab_dir: Path,
    status: str,
    reason: str | None,
    source: dict,
    selected_track: dict | None,
    notes: list[dict],
    exporter_version: str,
    now_iso: str,
) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "exporter_version": exporter_version,
        "status": status,
        "reason": reason,
        "source": source,
        "selected_track": selected_track,
        "notes": notes,
        "exported_at": now_iso,
    }
    out = Path(tab_dir) / "tab_notes.json"
    _write_json_atomic(out, payload)
    return out


def write_excluded(
    *, tab_dir: Path, reason: str, source: dict,
    exporter_version: str, now_iso: str,
) -> Path:
    return write_tab_notes(
        tab_dir=tab_dir, status="excluded", reason=reason, source=source,
        selected_track=None, notes=[], exporter_version=exporter_version,
        now_iso=now_iso)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_output.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/output.py dataset-py/tests/test_output.py
git commit -m "feat(dataset): tab_notes.json atomic writer (ok + excluded)"
```

---

### Task 10: Discovery + export pipeline

Tie it together: find a tab's gpif/audio/warp, parse, select, walk, convert to real seconds, write.

**Files:**
- Create: `dataset-py/app/discover.py`
- Create: `dataset-py/app/pipeline.py`
- Test: `dataset-py/tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above; `app.config.Settings`.
- Produces:
  - `app.discover.TabDir` dataclass `tab_id: str`, `path: Path`; `find_tab(root, tab_id)`, `iter_ready_tabs(root)`, `find_gpif(tab_dir)`, `find_audio_file(tab_dir)`, `read_dataset_status(tab_dir)`.
  - `app.pipeline.export_tab(tab: TabDir, settings, *, now_iso) -> str` — returns a status string: `"ok"`, `"excluded"`, `"no_gpif"`, `"no_align"`, `"repeat"`, `"pitch_error"`. Writes `tab_notes.json` for terminal states (`ok`/`excluded`); skips writing for `no_gpif`/`no_align` (inputs not ready).

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_pipeline.py`**

```python
import json

from app.config import get_settings
from app.discover import TabDir
from app.pipeline import export_tab
from tests.fixtures import make_gpif


def _tab(tmp_path, *, gpif=True, align=True, two_guitars=False):
    d = tmp_path / "artist" / "song-1"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    if gpif:
        tracks = [(0, "Vocal", 85), (1, "Rhythm Guitar", 27)]
        counts = [0, 3]
        if two_guitars:
            tracks = [(0, "Rhythm Guitar", 27), (1, "Lead Guitar", 27),
                      (2, "Solo Guitar", 27)]
            counts = [3, 2, 1]
        (d / "x.gpif").write_text(
            make_gpif(tracks=tracks, note_count_per_track=counts), encoding="utf-8")
    (d / "audio.webm").write_bytes(b"\x00")
    if align:
        # warp: ref seconds → real seconds, +10s offset, identity slope
        warp = [[0.0, 10.0], [10.0, 20.0]]
        (d / "align.json").write_text(json.dumps({"status": "ok", "warp": warp}))
    return TabDir(tab_id="artist/song-1", path=d)


def test_export_ok_projects_to_real_seconds(tmp_path):
    tab = _tab(tmp_path)
    status = export_tab(tab, get_settings(), now_iso="2026-06-29T00:00:00")
    assert status == "ok"
    data = json.loads((tab.path / "tab_notes.json").read_text())
    assert data["status"] == "ok"
    assert data["selected_track"]["index"] == 1
    # first note at ref 0.0s → real 10.0s
    assert data["notes"][0]["onset_s"] == 10.0
    assert len(data["notes"]) == 3


def test_export_excluded_three_guitars(tmp_path):
    tab = _tab(tmp_path, two_guitars=True)
    status = export_tab(tab, get_settings(), now_iso="2026-06-29T00:00:00")
    assert status == "excluded"
    data = json.loads((tab.path / "tab_notes.json").read_text())
    assert data["reason"] == "too_many_guitars"


def test_export_no_align_skips(tmp_path):
    tab = _tab(tmp_path, align=False)
    status = export_tab(tab, get_settings(), now_iso="2026-06-29T00:00:00")
    assert status == "no_align"
    assert not (tab.path / "tab_notes.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_pipeline.py -q`
Expected: FAIL (`ModuleNotFoundError: app.discover`).

- [ ] **Step 3: Write `dataset-py/app/discover.py`**

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = (".opus", ".m4a", ".webm", ".mp3", ".ogg")


@dataclass(frozen=True)
class TabDir:
    tab_id: str
    path: Path


def find_gpif(tab_dir: Path) -> Path | None:
    gps = sorted(Path(tab_dir).glob("*.gpif"))
    return gps[0] if gps else None


def find_audio_file(tab_dir: Path) -> Path | None:
    for ext in AUDIO_EXTS:
        p = Path(tab_dir) / f"audio{ext}"
        if p.exists():
            return p
    return None


def read_dataset_status(tab_dir: Path) -> str | None:
    p = Path(tab_dir) / "tab_notes.json"
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

- [ ] **Step 4: Write `dataset-py/app/pipeline.py`**

```python
from __future__ import annotations

import app
from app.config import Settings
from app.discover import TabDir, find_audio_file, find_gpif
from app.gpif import load_gpif
from app.output import write_excluded, write_tab_notes
from app.pitch import pitch_mismatches
from app.project import load_warp, project_seconds
from app.select import select_track
from app.tempo import quarters_to_seconds, tempo_points
from app.walk import RepeatError, walk_track


def _note_dict(n, onset_s: float, dur_s: float) -> dict:
    return {
        "onset_s": round(onset_s, 4),
        "duration_s": round(dur_s, 4),
        "string": n.string,
        "fret": n.fret,
        "midi_pitch": n.midi,
        "tech": {"slide": n.slide, "bend": n.bend, "hopo": n.hopo,
                 "dead": n.dead, "harmonic": n.harmonic},
    }


def export_tab(tab: TabDir, settings: Settings, *, now_iso: str) -> str:
    gpif = find_gpif(tab.path)
    if gpif is None:
        return "no_gpif"
    audio = find_audio_file(tab.path)
    warp_data = load_warp(tab.path)
    source = {
        "gpif": gpif.name,
        "audio": audio.name if audio else None,
        "align": {"warp_ref": "align.json"},
    }
    if warp_data is None:
        return "no_align"
    source["align"].update({
        "tempo_ratio": warp_data.get("tempo_ratio"),
        "confidence": warp_data.get("confidence"),
    })
    warp = warp_data["warp"]

    score = load_gpif(gpif)
    track, reason = select_track(score, max_guitars=settings.max_guitars)
    if track is None:
        write_excluded(tab_dir=tab.path, reason=reason, source=source,
                       exporter_version=app.__version__, now_iso=now_iso)
        return "excluded"

    try:
        raws = walk_track(score, track)
    except RepeatError:
        write_excluded(tab_dir=tab.path, reason="repeat", source=source,
                       exporter_version=app.__version__, now_iso=now_iso)
        return "repeat"

    mism = pitch_mismatches(track, raws)
    if raws and len(mism) / len(raws) > settings.pitch_mismatch_max_rate:
        write_excluded(tab_dir=tab.path, reason="pitch_mismatch", source=source,
                       exporter_version=app.__version__, now_iso=now_iso)
        return "pitch_error"

    pts = tempo_points(score)
    notes = []
    for n in raws:
        onset_ref = quarters_to_seconds(n.onset_q, pts)
        off_ref = quarters_to_seconds(n.onset_q + n.dur_q, pts)
        onset_real = project_seconds(onset_ref, warp)
        off_real = project_seconds(off_ref, warp)
        notes.append(_note_dict(n, onset_real, max(0.0, off_real - onset_real)))

    selected = {
        "index": track.index, "name": track.name, "program": track.program,
        "tuning": track.tuning, "capo": track.capo,
    }
    write_tab_notes(tab_dir=tab.path, status="ok", reason=None, source=source,
                    selected_track=selected, notes=notes,
                    exporter_version=app.__version__, now_iso=now_iso)
    return "ok"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_pipeline.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add dataset-py/app/discover.py dataset-py/app/pipeline.py dataset-py/tests/test_pipeline.py
git commit -m "feat(dataset): export pipeline (discover -> select -> walk -> project -> write)"
```

---

### Task 11: CLI (`scan` / `run` / `status`)

**Files:**
- Create: `dataset-py/app/cli.py`
- Test: `dataset-py/tests/test_cli.py`

**Interfaces:**
- Consumes: `app.pipeline.export_tab`, `app.discover.*`.
- Produces: `app.cli.cmd_run`, `app.cli.cmd_scan`, `app.cli.cmd_status`, `app.cli.main(argv) -> int`.
  - `scan`: list tab_ids that are ready (have gpif + `align.json` status ok) and lack `tab_notes.json`.
  - `run [tab_ids…]`: export named tabs, or all ready tabs if none named; print status counts.
  - `status [tab_ids…]`: counts by `tab_notes.json` status (`unexported` when absent).

- [ ] **Step 1: Write the failing test `dataset-py/tests/test_cli.py`**

```python
import json

from app.cli import main
from tests.fixtures import make_gpif


def _ready_tab(root, name="artist/song-1"):
    d = root / name
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    (d / "x.gpif").write_text(
        make_gpif(tracks=[(0, "Rhythm Guitar", 27)], note_count_per_track=[2]),
        encoding="utf-8")
    (d / "audio.webm").write_bytes(b"\x00")
    (d / "align.json").write_text(json.dumps(
        {"status": "ok", "warp": [[0.0, 0.0], [10.0, 10.0]]}))
    return d


def test_run_all_ready(tmp_path, capsys):
    _ready_tab(tmp_path)
    rc = main(["--output-dir", str(tmp_path), "run"])
    assert rc == 0
    assert "ok" in capsys.readouterr().out


def test_status_counts(tmp_path, capsys):
    d = _ready_tab(tmp_path)
    main(["--output-dir", str(tmp_path), "run"])
    main(["--output-dir", str(tmp_path), "status"])
    out = capsys.readouterr().out
    assert "ok" in out
    assert (d / "tab_notes.json").exists()


def test_scan_lists_ready_unexported(tmp_path, capsys):
    _ready_tab(tmp_path)
    main(["--output-dir", str(tmp_path), "scan"])
    assert "artist/song-1" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: app.cli`).

- [ ] **Step 3: Write `dataset-py/app/cli.py`**

```python
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.discover import (
    find_audio_file,
    find_gpif,
    find_tab,
    iter_ready_tabs,
    read_dataset_status,
)
from app.pipeline import export_tab
from app.project import load_warp


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_ready(tab) -> bool:
    return (
        find_gpif(tab.path) is not None
        and find_audio_file(tab.path) is not None
        and load_warp(tab.path) is not None
    )


def cmd_run(settings: Settings, tab_ids: list[str], *, now_iso=None) -> dict:
    now_iso = now_iso or _now_iso()
    if tab_ids:
        tabs = [t for t in (find_tab(settings.output_dir, i) for i in tab_ids)
                if t is not None]
    else:
        tabs = [t for t in iter_ready_tabs(settings.output_dir) if _is_ready(t)]
    counts: dict[str, int] = {}
    for tab in tabs:
        st = export_tab(tab, settings, now_iso=now_iso)
        counts[st] = counts.get(st, 0) + 1
    return counts


def cmd_scan(settings: Settings) -> list[str]:
    out = []
    for tab in iter_ready_tabs(settings.output_dir):
        if _is_ready(tab) and read_dataset_status(tab.path) is None:
            out.append(tab.tab_id)
    return out


def cmd_status(settings: Settings, tab_ids: list[str] | None) -> dict:
    if tab_ids:
        tabs = [t for t in (find_tab(settings.output_dir, i) for i in tab_ids)
                if t is not None]
    else:
        tabs = list(iter_ready_tabs(settings.output_dir))
    counts: dict[str, int] = {}
    for tab in tabs:
        st = read_dataset_status(tab.path) or "unexported"
        counts[st] = counts.get(st, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataset")
    parser.add_argument("--output-dir")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("tab_ids", nargs="*")
    sub.add_parser("scan")
    p_status = sub.add_parser("status")
    p_status.add_argument("tab_ids", nargs="*")

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.output_dir:
        settings.output_dir = Path(args.output_dir)

    if args.command == "run":
        out = cmd_run(settings, args.tab_ids)
    elif args.command == "scan":
        out = cmd_scan(settings)
        if not args.quiet:
            for tid in out:
                print(tid)
        return 0
    else:
        out = cmd_status(settings, args.tab_ids)

    if not args.quiet:
        print(out)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dataset-py && python -m pytest tests/test_cli.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add dataset-py/app/cli.py dataset-py/tests/test_cli.py
git commit -m "feat(dataset): CLI scan/run/status"
```

---

### Task 12: Round-trip inspect (overlay + plot) + integration test

Render exported notes onto the real-audio timeline (MIDI→FluidSynth) and overlay; plot real-audio spectrogram with onset markers. Mirrors `aligner-py/app/inspect.py`.

**Files:**
- Create: `dataset-py/app/inspect.py`
- Modify: `dataset-py/app/cli.py` (add `inspect` subcommand)
- Test: `dataset-py/tests/test_inspect.py` (unit: MIDI build; gated integration: full render)

**Interfaces:**
- Consumes: `tab_notes.json` notes, the real audio file.
- Produces:
  - `app.inspect.notes_to_midi(notes: list[dict], out_mid: Path, *, program: int = 27) -> Path` — write a type-0 MIDI placing each note at its `onset_s` with `duration_s` (via `mido`, using a fixed tempo and ticks/beat so absolute seconds are honored).
  - `app.inspect.build_overlay(real_audio, midi_wav, out_wav, *, sample_rate) -> Path` — mix real audio (L) + synthesized notes (R) into a stereo WAV.
  - `app.inspect.build_plot(real_audio, notes, out_png, *, sample_rate, hop_length) -> Path` — spectrogram of real audio with vertical onset lines.
  - `app.cli.cmd_inspect(settings, tab_id) -> dict`.

- [ ] **Step 1: Write the failing unit test `dataset-py/tests/test_inspect.py`**

```python
import mido

from app.inspect import notes_to_midi


def test_notes_to_midi_places_onsets(tmp_path):
    notes = [
        {"onset_s": 0.0, "duration_s": 0.5, "midi_pitch": 40},
        {"onset_s": 1.0, "duration_s": 0.5, "midi_pitch": 45},
    ]
    out = notes_to_midi(notes, tmp_path / "t.mid")
    mid = mido.MidiFile(out)
    ons = [m for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0]
    assert len(ons) == 2
    # absolute time of second note ≈ 1.0 s
    total = 0.0
    for msg in mid:  # mido iterates with real seconds in msg.time
        total += msg.time
        if msg.type == "note_on" and msg.velocity > 0 and msg.note == 45:
            assert abs(total - 1.0) < 0.05
            break
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dataset-py && python -m pytest tests/test_inspect.py -q`
Expected: FAIL (`ModuleNotFoundError: app.inspect`).

- [ ] **Step 3: Write `dataset-py/app/inspect.py`**

```python
from __future__ import annotations

import subprocess
from pathlib import Path

import librosa
import matplotlib
import mido
import numpy as np
import soundfile as sf

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_TICKS_PER_BEAT = 480
_TEMPO_US = 500000  # 120 bpm → 1 beat = 0.5 s → 1 tick = 0.5/480 s


def _s_to_ticks(seconds: float) -> int:
    beats = seconds / (_TEMPO_US / 1_000_000)
    return int(round(beats * _TICKS_PER_BEAT))


def notes_to_midi(notes: list[dict], out_mid: Path, *, program: int = 27) -> Path:
    mid = mido.MidiFile(ticks_per_beat=_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=_TEMPO_US, time=0))
    track.append(mido.Message("program_change", program=program, time=0))

    events = []  # (abs_tick, kind, note)
    for n in notes:
        pitch = n.get("midi_pitch")
        if pitch is None:
            continue
        on = _s_to_ticks(n["onset_s"])
        off = _s_to_ticks(n["onset_s"] + max(0.02, n["duration_s"]))
        events.append((on, "on", pitch))
        events.append((off, "off", pitch))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "off" else 1))

    prev = 0
    for tick, kind, pitch in events:
        delta = tick - prev
        prev = tick
        if kind == "on":
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=delta))
    mid.save(out_mid)
    return Path(out_mid)


def render_midi(midi: Path, out_wav: Path, *, soundfont: Path,
                fluidsynth_bin: str, sample_rate: int) -> Path:
    subprocess.run(
        [fluidsynth_bin, "-ni", "-F", str(out_wav), "-r", str(sample_rate),
         str(soundfont), str(midi)],
        check=True, capture_output=True)
    return Path(out_wav)


def build_overlay(real_audio: Path, synth_wav: Path, out_wav: Path,
                  *, sample_rate: int) -> Path:
    real, _ = librosa.load(str(real_audio), sr=sample_rate, mono=True)
    synth, _ = librosa.load(str(synth_wav), sr=sample_rate, mono=True)
    n = max(len(real), len(synth))
    real = np.pad(real, (0, n - len(real)))
    synth = np.pad(synth, (0, n - len(synth)))
    stereo = np.stack([real, 0.6 * synth], axis=1)
    sf.write(str(out_wav), stereo, sample_rate)
    return Path(out_wav)


def build_plot(real_audio: Path, notes: list[dict], out_png: Path,
               *, sample_rate: int, hop_length: int) -> Path:
    y, _ = librosa.load(str(real_audio), sr=sample_rate, mono=True)
    S = librosa.amplitude_to_db(
        np.abs(librosa.stft(y, hop_length=hop_length)), ref=np.max)
    fig, ax = plt.subplots(figsize=(14, 4))
    librosa.display.specshow(S, sr=sample_rate, hop_length=hop_length,
                             x_axis="time", y_axis="log", ax=ax)
    for n in notes:
        ax.axvline(n["onset_s"], color="cyan", alpha=0.4, linewidth=0.5)
    ax.set_title("real audio + exported guitar onsets")
    fig.savefig(out_png, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return Path(out_png)
```

- [ ] **Step 4: Add the `inspect` subcommand to `dataset-py/app/cli.py`**

Add these imports near the top of `cli.py`:

```python
import json
import tempfile

from app.inspect import build_overlay, build_plot, notes_to_midi, render_midi
```

Add this function above `main`:

```python
def cmd_inspect(settings: Settings, tab_id: str) -> dict:
    tab = find_tab(settings.output_dir, tab_id)
    if tab is None:
        raise SystemExit(f"tab not found: {tab_id}")
    notes_path = tab.path / "tab_notes.json"
    if not notes_path.exists():
        raise SystemExit(f"no tab_notes.json for {tab_id}; run 'dataset run {tab_id}'")
    data = json.loads(notes_path.read_text())
    notes = data.get("notes", [])
    audio = find_audio_file(tab.path)
    if audio is None:
        raise SystemExit(f"no audio for {tab_id}")
    program = (data.get("selected_track") or {}).get("program") or 27
    with tempfile.TemporaryDirectory() as work:
        mid = notes_to_midi(notes, Path(work) / "n.mid", program=program)
        synth = render_midi(
            mid, Path(work) / "n.wav", soundfont=settings.soundfont,
            fluidsynth_bin=settings.fluidsynth_bin, sample_rate=settings.sample_rate)
        overlay = build_overlay(audio, synth, tab.path / "dataset_overlay.wav",
                                sample_rate=settings.sample_rate)
    plot = build_plot(audio, notes, tab.path / "dataset_overlay.png",
                      sample_rate=settings.sample_rate, hop_length=settings.hop_length)
    return {"overlay": str(overlay), "plot": str(plot)}
```

In `main`, add the subparser (after the `status` parser) and dispatch:

```python
    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("tab_id")
```

and in the dispatch chain, before the final `else`:

```python
    elif args.command == "inspect":
        out = cmd_inspect(settings, args.tab_id)
```

- [ ] **Step 5: Add a gated integration test to `dataset-py/tests/test_inspect.py`**

```python
import json
import shutil

import pytest

from app.cli import cmd_inspect
from app.config import get_settings


@pytest.mark.integration
def test_inspect_end_to_end(tmp_path):
    if not shutil.which("fluidsynth"):
        pytest.skip("fluidsynth not installed")
    # requires a real exported tab + audio under output_dir; smoke-checks artifacts
    d = tmp_path / "artist" / "song-1"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    (d / "tab_notes.json").write_text(json.dumps({
        "notes": [{"onset_s": 0.0, "duration_s": 0.5, "midi_pitch": 40}],
        "selected_track": {"program": 27}}))
    # a 1-second silent audio file
    import numpy as np
    import soundfile as sf
    sf.write(str(d / "audio.webm"), np.zeros(22050), 22050, format="OGG", subtype="OPUS")
    s = get_settings(); s.output_dir = tmp_path
    res = cmd_inspect(s, "artist/song-1")
    assert (d / "dataset_overlay.wav").exists()
    assert (d / "dataset_overlay.png").exists()
    assert res["plot"].endswith(".png")
```

- [ ] **Step 6: Run unit tests (integration excluded by default)**

Run: `cd dataset-py && python -m pytest tests/test_inspect.py -q`
Expected: PASS (1 passed, 1 deselected). Then full suite: `python -m pytest -q` → all green.

- [ ] **Step 7: Commit**

```bash
git add dataset-py/app/inspect.py dataset-py/app/cli.py dataset-py/tests/test_inspect.py
git commit -m "feat(dataset): round-trip inspect overlay + plot, CLI inspect subcommand"
```

---

### Task 13: Real-data validation on the qualifying aligned tab

Run the real pipeline end-to-end on `the-1975/if-youre-too-shy-let-me-know-official-3206441` (the one currently-aligned qualifying tab) and eyeball the overlay/plot. No new code unless a real-file edge case surfaces.

**Files:** none (validation), unless a bug fix is needed (then add a regression test first).

- [ ] **Step 1: Export the real tab**

Run:
```bash
cd dataset-py && pip install -e . >/dev/null
dataset --output-dir ../output run "the-1975/if-youre-too-shy-let-me-know-official-3206441"
```
Expected: `{'ok': 1}`. A `tab_notes.json` appears in that tab dir.

- [ ] **Step 2: Sanity-check the output**

Run:
```bash
python3 - <<'PY'
import json
d=json.load(open("../output/the-1975/if-youre-too-shy-let-me-know-official-3206441/tab_notes.json"))
print("status", d["status"], "track", d["selected_track"]["name"], "notes", len(d["notes"]))
print("first 3:", d["notes"][:3])
print("onset range", d["notes"][0]["onset_s"], "→", d["notes"][-1]["onset_s"])
PY
```
Expected: `status ok`, selected track `Rhythm Guitar (Clean)`, a few hundred notes, onsets within the audio duration (~0–276 s). If `status` is `excluded`/`repeat`/`pitch_error`, investigate before proceeding — add a failing unit test reproducing the real-file structure, fix `gpif.py`/`walk.py`, then re-run.

- [ ] **Step 3: Build and review the round-trip overlay (requires fluidsynth + soundfont)**

Run:
```bash
dataset --output-dir ../output inspect "the-1975/if-youre-too-shy-let-me-know-official-3206441"
```
Expected: `dataset_overlay.wav` (L=real audio, R=synthesized guitar at exported onsets) and `dataset_overlay.png` written. Listen: the synthesized guitar should track the real guitar; on the plot, cyan onset lines should land on real-audio note attacks. Note any drift in the PLAN status log.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A dataset-py
git commit -m "fix(dataset): handle <real-file edge case> surfaced in validation"
```
(Skip if no code changes were needed.)

---

### Task 14: Documentation

**Files:**
- Create: `docs/dataset-py/overview.md`
- Modify: `docs/output-contract.md`
- Modify: `OVERVIEW.md`
- Modify: `CLAUDE.md`
- Modify: `PLAN_PHASES.md`

- [ ] **Step 1: Write `docs/dataset-py/overview.md`**

Document: purpose (Phase 0 export), the `dataset` CLI (`scan`/`run`/`status`/`inspect`), the selection rule (program 24–31, helper/empty exclusion, 1–2 guitars, primary=most-notes), the parse→project pipeline, the `tab_notes.json` schema (copy §7 from the spec), the round-trip artifacts, dependencies/build/test commands, and a link back to the spec.

- [ ] **Step 2: Update `docs/output-contract.md`**

Add `tab_notes.json` (+ `dataset_overlay.wav`/`dataset_overlay.png`) to the per-tab artifact list, gated on `metadata.json` + `align.json` status `ok`. Include the schema and note it is written by `dataset-py`.

- [ ] **Step 3: Update `OVERVIEW.md`**

Add `dataset-py` to the project map and `docs/dataset-py/overview.md` to the doc table so it's reachable from the map.

- [ ] **Step 4: Update `CLAUDE.md`**

Add `dataset-py` to "What this repo is" (the project list), add its common commands block (mirroring the aligner block), and add rows to the code→doc table (`aligner-py/app/` style row for `dataset-py/app/` → `docs/dataset-py/overview.md`; `tab_notes.json` contract → `docs/output-contract.md` + overview).

- [ ] **Step 5: Update `PLAN_PHASES.md` status log**

Append a dated entry: Phase 0 implemented (`dataset-py` exporter + round-trip validator); note validation result from Task 13; next action = Phase 1 (eval harness + frame-grid baseline).

- [ ] **Step 6: Commit**

```bash
git add docs/dataset-py/overview.md docs/output-contract.md OVERVIEW.md CLAUDE.md PLAN_PHASES.md
git commit -m "docs(dataset): document Phase 0 exporter + tab_notes.json contract"
```

---

## Self-Review

**Spec coverage:**
- §4 architecture / placement → Tasks 1, 10, 11 (new `dataset-py`, CLI scan/run/status). ✓
- §5 selection (program 24–31, helper + empty exclusion, 1–2 guitars, primary=most-notes) → Task 3. ✓
- §6 pipeline (linear walk + repeat assert, tempo map, pitch check, warp projection) → Tasks 4–8, 10. ✓
- §7 schema (`tab_notes.json`, whole-song, real seconds, model-agnostic) → Tasks 9, 10. ✓
- §8 round-trip inspect (overlay + plot) → Task 12. ✓
- §9 testing (unit tool-free; gated integration) → every task's tests + Task 12 integration. ✓
- §10 docs (output-contract, OVERVIEW, dataset-py overview, CLAUDE.md, PLAN_PHASES) → Task 14. ✓
- "Validate on the-1975 first" → Task 13. ✓

**Placeholder scan:** No "TBD/handle edge cases/similar to Task N". Doc task (14) names exact files + exact content to copy from the spec. ✓

**Type consistency:** `Score`/`Track`/`RawNote`/`NoteEl` field names are consistent across `gpif.py` (Tasks 2/4/5), `select.py` (3), `walk.py` (5), `pitch.py` (7), `pipeline.py` (10). `select_track(...) -> (Track|None, reason)`, `export_tab(...) -> str`, `project_seconds(t_ref, warp)`, `write_tab_notes(...)`/`write_excluded(...)` signatures match between definition and call sites. The `MasterBar.has_repeat` field added in Task 5 Step 1 is consumed only in `walk_track`. ✓

**Note on real-file risk:** the synthetic fixture exercises the parser's control flow, but the real GPIF (Task 13) is the true test of timing/technique-tag coverage. Task 13 explicitly routes any real-file surprise back through TDD (failing test → fix → re-run), which is the intended catch.
