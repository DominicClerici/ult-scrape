# dataset-py — guitar tab note-event export (Phase 0) — design

> Spec for **Phase 0** of the audio→guitar-tab modeling effort (see
> [`PLAN_PHASES.md`](../../../PLAN_PHASES.md)). Produced via brainstorming on
> 2026-06-29. Status: **approved design, pending spec review**.

## 1. Context

`ult-scrape` produces, per tab, inside the frozen
[output contract](../../output-contract.md):

- a decoded Guitar Pro file (`.gp` + extracted `.gpif` XML),
- best-available source audio (`audio.<ext>` + `audio.json`), and
- (newly) `align.json` — a time **warp** mapping the MuseScore-rendered reference
  timeline → the real YouTube-audio timeline, plus confidence metrics.

The goal of the modeling effort is **full-mix song audio → guitar tablature**.
Phase 0 turns the aligned artifacts into the canonical, model-agnostic training
record: per tab, the guitar's notes expressed as `(onset, string, fret,
duration, …)` **in real-audio seconds**, paired with the audio. Tokenization and
windowing are explicitly deferred downstream (Phase 1/2).

This is a new decoupled stage. It reads the `output/` contract and writes one new
sidecar artifact (`tab_notes.json`) plus debug overlays. Per repo architecture, it
**shares no code** with the other projects — the filesystem is the only interface.

### Findings that drove the design (measured on the current 509-tab corpus)

- **Guitar-track count is high and variable.** Real guitar tracks per song (MIDI
  program 24–31, excluding UG `@$…$@` helper tracks): 0→26 songs, **1→75, 2→113**,
  3→108, 4+→191. Strict single-guitar is only 15% of data; **1–2 guitars = 188
  songs**.
- **The `Tuning/Instrument` label is useless** for instrument identity — Vocal,
  Brass, Sax, Strings, and Synth tracks all carry a "Guitar"-labeled 6-string
  tuning template. The authoritative signal is the per-track **`<MIDI><Program>`**
  (GM patch). `@$Strumming$@` / `@$Chords$@` are program-25 *helper* tracks, not
  playable guitar, and must be excluded by name.
- **No repeats anywhere.** 0/509 songs use repeat barlines or alternate endings —
  UG official tabs are written out fully linear. This removes the hardest part of
  GP timing (repeat expansion).
- **Mostly constant tempo.** 400/509 (79%) single-tempo; 109 have step tempo
  changes (mostly 2–3 markers). Onset = linear bar accumulation + a simple tempo
  map.
- **Techniques are common** (corpus-wide, coarse): slides 459 songs, HOPO 330,
  dead/muted 353, bends 204, harmonics 64; tapping negligible. Non-standard
  tunings and capos are widespread (capo on ≥77 songs).

## 2. Decisions (locked via brainstorming)

| # | Decision | Choice |
|---|---|---|
| Q1 | Which guitar track(s) are the target | **1–2 guitar songs qualify; when 2, pick the primary** (188-song set). Selector is a swappable policy. |
| Q1a | Primary tiebreak (2 guitars) | **Most notes, tie-break lowest track index.** Swappable. |
| Q2 | Where onsets + string/fret come from | **Self-parse the `.gpif`** (own timing via linear bars + tempo map; string/fret/technique native). No repeats → low risk. |
| Q3 | Vocabulary scope | **Export-rich / tokenize-lean.** Parse & store everything (string, fret, duration, slide/bend/hopo/dead/harmonic, tuning, capo); v1 *target* = `(onset, string, fret, duration)`. Keep all tunings. |
| Q4 | Representation | **Model-agnostic note events** in real-audio seconds, **whole-song**. Tokenization + windowing are downstream adapters, not baked into the export. |
| Placement | Code location | **New project `dataset-py/`** (matches the decoupled four-project architecture). |

## 3. Goals / non-goals

**Goals (Phase 0):**

- A new decoupled `dataset-py` project that reads the `output/` tree and writes a
  `tab_notes.json` sidecar per qualifying tab.
- Deterministic guitar-track selection (GM program + helper exclusion + 1–2-guitar
  policy + primary tiebreak).
- Self-parsed gpif → note events with onsets/durations in **reference seconds**,
  projected through `align.json`'s warp into **real-audio seconds**.
- A round-trip validation tool (listen + look) proving onsets land on the real
  guitar.
- Update the output contract and docs in the same change.

**Non-goals (deferred):** any model tokenization / windowing / vocab; technique
tokens in the predicted target; 3+-guitar handling and multi-guitar merging;
tuning/key normalization; restricting by tuning; per-note model conditioning;
a SQLite work queue (a filesystem scan like `aligner-py` is enough for now).

## 4. Architecture

A new decoupled project mirroring `aligner-py` / `enricher-py` conventions:

- Python ≥ 3.13, installed as a CLI (`dataset`).
- Subcommands: `dataset scan` (enumerate qualifying tabs), `dataset run [tab_id…]`
  (export), `dataset status` (counts by `tab_notes.json` status),
  `dataset inspect <tab_id>` (round-trip overlays).
- No shared code with other projects; reads/writes only via the output contract.

### Module sketch

- `select.py` — guitar-track discovery + selection policy.
- `gpif.py` — gpif timing + note/tab/technique parser (the core).
- `project.py` — piecewise-linear warp interpolation (ref seconds → real seconds).
- `output.py` — `tab_notes.json` writer (atomic; schema-versioned).
- `inspect.py` — round-trip overlay + plot.
- `cli.py` / `pipeline.py` — scan/run/status/inspect orchestration.
- `config.py` — paths, thresholds, selection policy knobs.

## 5. Track selection (`select.py`)

1. Enumerate `<Track>` elements; a **guitar track** = per-track
   `<MIDI><Program> ∈ 24–31`, **excluding** `@$…$@` helper tracks and tracks with
   no notes.
2. **0 or ≥3 guitars → skip**, writing `status:"excluded"` + `reason`.
3. **1 guitar → select it.**
4. **2 guitars → primary** = guitar track with the **most notes**, tie-break by
   lowest track index.

Policy knobs (max-guitars, tiebreak) live in `config.py` so the set can later widen
to "primary of any song" without re-architecting.

## 6. Parse → project pipeline

Single pass over the selected track:

1. **Timing.** Walk bars in order; **assert-fail if any repeat / alternate-ending
   appears** (guards the linear assumption against future scrapes). Accumulate
   beat durations per voice (rhythm value + dots + tuplets) → onset/duration in
   **reference seconds**, applying the gpif tempo map (constant for 79%; piecewise
   step for the rest).
2. **Tab data.** Per note: `string`, `fret`, `midi_pitch = tuning[string] + capo +
   fret`, technique flags (`slide/bend/hopo/dead/harmonic`).
3. **Pitch sanity check.** Assert written pitch == `tuning + capo + fret`;
   log/flag mismatches (validates parse + capo handling together).
4. **Projection.** `onset_real_s = piecewise_linear_interp(warp, onset_ref_s)`
   (and offsets). Notes outside the warp domain are clamped/flagged.

### Gating

Process a tab only when `metadata.json` exists (commit marker), a guitar track
qualifies, and `align.json` `status == "ok"`. Carry `align.json` confidence into
the output so Phase 1 can filter; Phase 0 does not itself threshold on confidence.

## 7. Output schema — `tab_notes.json`

Written atomically alongside `align.json`. Whole-song, notes in real-audio seconds,
model-agnostic.

```jsonc
{
  "schema_version": 1,
  "exporter_version": "0.1.0",
  "status": "ok",                    // or "excluded"
  "reason": null,                    // set when excluded (e.g. "3+ guitars")
  "source": {
    "gpif": "tab-download-ssid-3206441.gpif",
    "audio": "audio.webm",
    "align": { "warp_ref": "align.json", "tempo_ratio": 1.20, "confidence": { "fit_cost": 0.13, "path_deviation": 0.06 } }
  },
  "selected_track": {
    "index": 1, "name": "Rhythm Guitar (Clean)", "program": 27,
    "tuning": [40, 45, 50, 55, 59, 64], "capo": 0
  },
  "notes": [
    { "onset_s": 12.84, "duration_s": 0.25, "string": 4, "fret": 2, "midi_pitch": 52,
      "tech": { "slide": false, "bend": false, "hopo": false, "dead": false, "harmonic": false } }
  ]
}
```

`string` indexing convention (0 = low-E vs. 0 = high-E) is fixed in `gpif.py` and
documented in the schema; the pitch sanity check pins it down empirically.

## 8. Round-trip validation (`dataset inspect`)

Mirrors the aligner's `align_overlay.wav` / `align_plot.png` deliverables:

- Render the exported notes (MIDI → FluidSynth) onto the **real-audio timeline**,
  mix with the real audio at low gain → `dataset_overlay.wav`.
- Plot the real-audio spectrogram with note-onset markers → `dataset_overlay.png`.

This is the empirical proof that onsets land on the real guitar. Validate on
`the-1975/if-youre-too-shy-let-me-know-official-3206441` first (the only currently
aligned tab that qualifies); expand as alignment completes across the 188-song set.

## 9. Testing

Deterministic and tool-free by default (repo convention):

- **Unit:** gpif timing parser (durations, dots, tuplets, tempo map); track
  selector (all branches: 0/1/2/3+ guitars, helper exclusion, empty-track
  exclusion, primary tiebreak); warp interpolation (incl. out-of-domain);
  pitch sanity; schema/writer (atomic, versioned). Tiny synthetic gpif fixtures.
- **Integration (gated):** full export + round-trip on a real aligned tab; needs
  `mscore` + `fluidsynth`.

## 10. Docs to update in the same change

- `docs/output-contract.md` — new `tab_notes.json` artifact + `dataset_overlay.*`.
- `OVERVIEW.md` — new project + doc page in the maps.
- `docs/dataset-py/overview.md` — new page.
- `CLAUDE.md` — "What this repo is", commands, and the code→doc table.
- `PLAN_PHASES.md` — Phase 0 status log.

## 11. Open questions

None — all design decisions resolved during brainstorming.
