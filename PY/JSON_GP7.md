# UG XTZ JSON → Guitar Pro 7 (`.gp`) conversion plan

This doc hands off the next step of the pipeline. The previous step
(`PY/wasm_runner.py`) drives Ultimate Guitar's authenticated Pro-tab page so
their proprietary "XTZ" WASM player parses the tab and emits the score as
JSON. Your job is to take that JSON and produce a real Guitar Pro 7+ `.gp`
file that opens in Guitar Pro 7/8 and any GP-compatible viewer.

## How we got here (1-minute recap)

- The "encrypted .gp" we capture from `tabs.ultimate-guitar.com/tab/download/file?ssid=...`
  is **not** a Guitar Pro file. It's UG's own binary container (magic `XTZ\0`,
  high entropy from compression, not encryption). Only their WASM player
  understands it.
- The player is an Emscripten module: ~MBs of compiled C++/Rust with a JS
  glue layer. It exposes a `Load(ptr, len, opts)` ccall and emits the parsed
  score back to JS via an `onScoreLoaded(trackIdx, jsonString)` callback
  (see chunk `8811.b309ffc58bc44f37a67e4821d597f86a.js` @ offset 84158).
- `PY/wasm_runner.py` instruments `JSON.parse`, `WebAssembly.instantiate`,
  and `fetch` *before* page scripts run, navigates the authenticated session
  to a Pro tab URL, and saves the score JSON plus everything around it.

## What you have to work with

Run the runner once for the song you want, e.g.:

```powershell
cd C:\Users\dcler\Desktop\Coding\ult-scrape\PY
python wasm_runner.py --route eagles/hotel-california-official-1910943
```

Output is at `PY/captures/<ts>-<route-slug>-json/`:

| File | What it is |
| --- | --- |
| `manifest.json` | Run summary (counts, score flag, route, UA) |
| `score.json` | **The prize.** First `JSON.parse` whose result has a `parts` array. Almost certainly the full score. |
| `score-meta.json` | Where in the parse stream it landed (idx, inputLen, ts) |
| `parses/NNNNN-<keys>.json` | Every JSON.parse capture (≥64 chars, object/array). Useful when `score.json` detection misses the prize, and for downstream callback payloads (canvas size, instruments, playable parts, playing notes, lyrics). |
| `blobs/` | Raw XTZ download (`.gp`), WASM modules (`.wasm`), notation style (`.mss`). `index.jsonl` lists URL/status/content-type. |
| `wasm-modules.json` | Import surface + export name list per WASM. Useful if you later want to call `Load`/`malloc`/`free` directly. |
| `ugapp-store.json` | Page state: artist, song name, tab id, version, capo, tonality, tuning, difficulty, contributor, etc. **Most non-musical metadata lives here, not in `score.json`.** |
| `page-snapshot.html` | DOM at exit, for sanity / further string mining |
| `errors.json` | Console/page errors (only if any) |

## Step 0: verify what's actually in `score.json`

**Don't skip this.** The runner picks the first `parts`-bearing object as the
score. Open `score.json` and confirm. The shape we expect from JS code-reading:

```jsonc
{
  "parts": [
    {
      "partName": "Acoustic Guitar",
      "instrumentId": 25,          // MIDI program (or UG-internal id)
      "isPercussion": false,
      "pan": 0.0,
      "volume": 0.7,
      "stringsCount": 6,
      // ...likely tuning, capo, midi channel
    }
  ],
  "lyrics": [ /* ... */ ],
  "startTempoBpm": 75,
  "duration": 392.4,                // seconds
  "initialTrack": [ /* ... */ ]
  // ...likely: bars, beats, notes, key/time signatures, repeats
}
```

The fields above are the only ones the JS glue *consumes by name*. The actual
JSON likely contains far more (the WASM has no reason to selectively serialize
only what JS reads — it almost certainly emits the full parsed score). Confirm
this by inspecting `score.json` and the larger `parses/*.json` files. Write up
the actual schema as `score.schema.md` before you start mapping.

**If `score.json` is missing note-level data** (only part summaries, lyrics,
duration), see "If the JSON is shallower than expected" below.

## Target format: Guitar Pro 7+ (`.gp`)

`.gp` is a ZIP container. Inside, the layout:

```
Content/
  score.gpif              # the score, as XML (UTF-8). This is the file.
  BinaryStaffPositioning  # optional, layout cache
  LayoutConfiguration     # optional
  PartConfiguration       # optional
  VERSION                 # optional, plain text version like "7.6.0"
```

All meaningful musical data lives in `score.gpif` (the rest are renderer
caches Guitar Pro will regenerate). The XML schema is partially documented but
not officially published; the most reliable reference is the AlphaTab project's
GP7 importer, which round-trips real GP7 files:

- AlphaTab GP7 importer:
  https://github.com/CoderLine/alphaTab/tree/develop/src/importer
  (`Gp7To8Importer.ts`, `GpifParser.ts`)
- AlphaTab GP7 exporter:
  https://github.com/CoderLine/alphaTab/tree/develop/src/exporter
  (`Gp7Exporter.ts`)
- AlphaTab can both READ and WRITE `.gp`. **Use it.** Don't author GPIF XML
  by hand unless you have to — the schema has hundreds of element types
  (bends, harmonics, palm mutes, ghost notes, whammies, slap/pop, tap/hammer,
  ornaments, time signatures, key signatures, repeats, alternate endings,
  brackets, chord diagrams, fingerings, beam grouping, automations, dynamics,
  free time, custom tunings, drum kit mapping, ...) and a hand-rolled writer
  will miss at least one and produce a file that GP refuses to open.

GPIF XML top-level structure (abridged):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <GPVersion>7</GPVersion>
  <GPRevision>7</GPRevision>
  <Score>
    <Title/>           <Artist/>          <Album/>
    <SubTitle/>        <Words/>           <Music/>
    <Copyright/>       <Tabber/>          <Instructions/>
    <Notices/>         <FirstPageHeader/> <PageHeader/>
    <PageFooter/>      <FirstPageFooter/> <ScoreSystemsLayout/>
    <PageSetup/>       <MultiVoice/>      <ScoreSystemsDefaultLayout/>
  </Score>
  <MasterTrack>
    <Tracks>0 1 2</Tracks>
    <Automations>
      <Automation>
        <Type>Tempo</Type>
        <Linear>false</Linear>
        <Bar>0</Bar>
        <Position>0</Position>
        <Visible>true</Visible>
        <Value>75 2</Value>
      </Automation>
    </Automations>
  </MasterTrack>
  <Tracks>
    <Track id="0">
      <Name>Acoustic Guitar</Name>
      <Color>...</Color>
      <SystemsLayout>...</SystemsLayout>
      <PlaybackState>Default</PlaybackState>
      <PartSounding>...</PartSounding>
      <Instrument ref="acoustic-guitar-steel"/>
      <PlaybackSound ref="..."/>
      <Sounds><Sound>...</Sound></Sounds>
      <PlayingStyle>Default</PlayingStyle>
      <Staves>
        <Staff>
          <Properties>
            <Property name="CapoFret"><Fret>0</Fret></Property>
            <Property name="FretCount"><Number>24</Number></Property>
            <Property name="PartialCapoFret"><Fret>0</Fret></Property>
            <Property name="PartialCapoStringFlags"><Bitset>...</Bitset></Property>
            <Property name="Tuning">
              <Pitches>40 45 50 55 59 64</Pitches>
              <Flat/><Label/><LabelVisible>false</LabelVisible>
            </Property>
            <Property name="DiagramCollection">...</Property>
            <Property name="DiagramWorkingSet">...</Property>
          </Properties>
        </Staff>
      </Staves>
      <Transpose>...</Transpose>
      <RSE>...</RSE>
    </Track>
  </Tracks>
  <MasterBars>
    <MasterBar>
      <Time>4/4</Time>
      <Bars>0 1 2</Bars>
      <Repeat start="true" end="false" count="0"/>
      <Section><Letter>A</Letter><Text>Verse</Text></Section>
      <Directions>...</Directions>
      <Fermatas>...</Fermatas>
      <Key><AccidentalCount>0</AccidentalCount><Mode>Major</Mode></Key>
    </MasterBar>
  </MasterBars>
  <Bars>
    <Bar id="0">
      <Voices>0 1 -1 -1</Voices>
      <Clef>G2</Clef>
      <Ottavia>Regular</Ottavia>
      <SimileMark>None</SimileMark>
    </Bar>
  </Bars>
  <Voices>
    <Voice id="0"><Beats>0 1 2 3</Beats></Voice>
  </Voices>
  <Beats>
    <Beat id="0">
      <Dynamic>F</Dynamic>
      <Rhythm ref="r0"/>
      <Notes>0 1 2</Notes>
      <Properties>...</Properties>  <!-- brush, arpeggio, etc -->
    </Beat>
  </Beats>
  <Notes>
    <Note id="0">
      <Properties>
        <Property name="String"><String>0</String></Property>
        <Property name="Fret"><Fret>3</Fret></Property>
        <Property name="Midi"><Number>40</Number></Property>
        <Property name="ConcertPitch"><Pitch>...</Pitch></Property>
        <Property name="HarmonicType"><HType>...</HType></Property>
        <!-- bend, slide, vibrato, palm-mute, ghost, accent, slap, pop, tap,
             let-ring, dead, hammer/pull, tie, tremolo, ornament, ... -->
      </Properties>
      <Tie origin="false" destination="false"/>
      <RightHandFinger>P</RightHandFinger>
      <LeftHandFinger>1</LeftHandFinger>
    </Note>
  </Notes>
  <Rhythms>
    <Rhythm id="r0">
      <NoteValue>Quarter</NoteValue>
      <AugmentationDot count="0"/>
      <PrimaryTuplet num="1" den="1"/>
    </Rhythm>
  </Rhythms>
  <Assets>...</Assets>
</GPIF>
```

Three things to note:

1. **Flat lookup tables.** Beats, Notes, Rhythms, Bars, Voices are all flat
   id-keyed lists referenced by space-separated id strings (`<Notes>0 1 2</Notes>`).
2. **MasterBars are global; Bars are per-track.** `MasterBar.Bars` lists one
   bar id per track in track order. Each Track has its own Staves; each Bar
   has its own Voices.
3. **Properties is the bag-of-flags pattern.** Almost every per-note feature
   (bend depth, vibrato width, palm-mute, ghost, slap, pop, tap, etc.) lives
   inside a `<Property name="...">...</Property>` child of `<Note>`. The
   property name list is large; plan to ignore unknown ones rather than
   crashing.

## Conceptual mapping

UG's score JSON → GPIF, conceptually:

| UG (JSON, expected) | GP7 (GPIF) |
| --- | --- |
| `parts[i].partName` | `Tracks/Track/Name` |
| `parts[i].instrumentId` (MIDI program) | `Tracks/Track/Instrument` (resolve to GP instrument ref) and `PartSounding`/`Sounds` |
| `parts[i].stringsCount` + tuning array | `Tracks/Track/Staves/Staff/Properties/Property[name="Tuning"]/Pitches` |
| `parts[i].pan` | `Tracks/Track/PartSounding` (RSE pan) |
| `parts[i].volume` | `Tracks/Track/PartSounding` (RSE volume) |
| `parts[i].isPercussion` | `Tracks/Track/Instrument ref="drumkit-..."`, drum-specific staff props |
| Capo (UGAPP store) | `Property[name="CapoFret"]/Fret` |
| Tonality (UGAPP store) | `MasterBars/MasterBar/Key` |
| `startTempoBpm` | `MasterTrack/Automations/Automation type="Tempo"` at bar 0 |
| Per-bar tempo changes (if present) | additional `Automation` entries |
| Time signatures (per bar) | `MasterBar/Time` |
| Repeats / sections | `MasterBar/Repeat`, `MasterBar/Section` |
| `lyrics` | `Tracks/Track/Lyrics` (line-keyed) |
| Notes: pitch / fret / string | `Note/Properties/Property[name="String"|"Fret"|"Midi"|"ConcertPitch"]` |
| Beat duration (rhythm) | `Beat/Rhythm ref` → `Rhythm/NoteValue` + `AugmentationDot` + `PrimaryTuplet` |
| Beat dynamic | `Beat/Dynamic` |
| Note effects (bend, vibrato, palm-mute, hammer, slide, tap, slap, pop, ghost, accent, harmonic, dead, let-ring, tie, tremolo, ornament) | one `Property[name=...]` each on `Note/Properties` |
| Tab metadata (artist, song, album, tabber) | `Score/{Artist,Title,Album,Tabber,Words,Music,Copyright}` |
| Tab id, version, source URL | put in `Score/Notices` or `Score/Instructions` so it's not lost |

`ugapp-store.json` is the right source for non-musical metadata — `score.json`
is unlikely to carry artist/album/contributor (those live in UG's API, not the
XTZ container).

## Recommended implementation path

The fast, correct path is **Node.js + AlphaTab**, in roughly this shape:

```
PY/captures/<ts>-<route>-json/   ← input (from wasm_runner)
TS/json_to_gp7/                  ← new directory you create
  package.json                   alphatab as dep
  src/
    load.ts                      read score.json + ugapp-store.json
    map_score.ts                 build alphaTab Score model from JSON
    map_track.ts                 per-track tuning, instrument, capo, RSE
    map_bars.ts                  master bars, time/key sigs, repeats, sections
    map_beats.ts                 beats + rhythms + voices
    map_notes.ts                 notes + properties (bag of effects)
    write_gp.ts                  alphaTab Gp7Exporter → bytes → write .gp
    cli.ts                       node cli.ts <input-dir> <output.gp>
```

Why Node + AlphaTab and not raw XML in Python:

- AlphaTab maintains the GP7/GP8 model (`alphatab.model.Score` and friends).
  You build that in-memory tree, hand it to `Gp7Exporter.export(score)`, and
  it produces ZIP bytes. The exporter handles every `<Property>` name,
  rhythm encoding, beat indexing, bar/voice/note id generation, repeats,
  brackets, and the rest. Re-implementing that in Python is weeks of work
  and you'll never catch every edge case GP cares about.
- AlphaTab is in active use (Songsterr-style web players use it) so the
  exporter is well-exercised on real GP files.

The existing `PY/` Python code stays as the capture layer; conversion lives
under a sibling `TS/` directory you create. Wire them together with a tiny
CLI: `npx tsx TS/json_to_gp7/src/cli.ts <input-dir> <output.gp>`.

If you'd rather stay in Python: `pyguitarpro` covers GP3–GP5 only. There is
no maintained Python GP7 writer. You would end up authoring GPIF XML by hand,
and GP7/8's parser is intolerant of malformed schemas (silent open-failure,
not error messages). Don't go this route unless AlphaTab is somehow off the
table.

## If the JSON is shallower than expected

If `score.json` has only part summaries / lyrics / tempo / duration and
**no per-bar / per-beat / per-note structure**, the WASM is keeping the score
internal and only emitting a summary to JS. Three follow-ups, easiest first:

1. **Inspect the full parse stream.** `parses/*.json` contains every
   `JSON.parse` call. Note-level data may arrive in a *different* callback
   (e.g. `onPlayingNotesInfo`, `onPartConfigChanged`). Look for parses with
   large `inputLen` and array-of-objects `result`s. Search the chunk
   `8811` JS for the callback names — there are many we didn't enumerate.

2. **Drive the player to emit more.** The runner currently waits for one
   `onScoreLoaded` and lingers briefly. Some callbacks fire only on user
   interaction (clicking a note, scrubbing playback, switching part). Extend
   `wasm_runner.py` to `page.evaluate()` a script that calls the public API
   methods (`Q.w.api.selectSinglePart(i)`, `setActiveInstrumentIndex`, play
   then pause, scroll through bars) and re-capture. Each call may surface
   additional JSON across the boundary.

3. **Call the WASM directly.** `wasm-modules.json` has the export-name list.
   If there's an export like `ExportScore`, `GetScoreJson`, `Serialize`,
   `Save`, `Encode`, `ToGpif`, etc., you can call it via `ccall` from
   `page.evaluate()`. Allocate output buffer, call, read `HEAPU8`, free.
   This is the highest-value lead if it exists. If no such export, you're
   reduced to either reverse-engineering the WASM (multi-week project,
   tools: `wasm-decompile`, `wasm2c`, Ghidra's WASM loader) or hooking
   the renderer to capture every `drawNote` / `drawBeat` call (laborious
   but yields enough to reconstruct).

Document whichever path you take in this file (append a "Findings" section
below) so a third agent doesn't have to re-derive it.

## Validation strategy

1. **Open in Guitar Pro 8.** First sanity check. If GP refuses to open the
   file, the error message will usually point at the failing element class.
2. **Round-trip via AlphaTab.** Read your generated `.gp` back with
   `alphaTab.importer.ScoreLoader.loadScoreFromBytes(...)` and compare against
   your in-memory model. Any drift indicates an exporter-side property name or
   value enum you mis-spelled.
3. **Listen.** GP playback is the harshest test: wrong rhythms, missing ties,
   missed bends are obvious aurally.
4. **Diff a known-good reference.** If UG also exposes the song under
   `/tab/download` (non-Pro path) and that returns a real `.gp`, use it as a
   gold reference. Note: per the user, community/non-Pro versions aren't
   acceptable as the *output* of this pipeline, but they're fine as
   *validation data*. (Skip if no such version exists for the song.)

## Quick reference: invocations

Capture (this layer, already implemented):

```powershell
cd C:\Users\dcler\Desktop\Coding\ult-scrape\PY
python wasm_runner.py --route eagles/hotel-california-official-1910943
```

Convert (your layer, to build):

```powershell
cd C:\Users\dcler\Desktop\Coding\ult-scrape
npx tsx TS\json_to_gp7\src\cli.ts `
  PY\captures\<ts>-eagles-hotel-california-official-1910943-json `
  out\hotel-california.gp
```

## Findings (append as you go)

<!-- The next agent should add notes here:
     - actual `score.json` schema (paste fields, types)
     - which parses/*.json carried which info
     - any WASM exports you ended up calling
     - any GPIF properties Guitar Pro rejected and how you fixed them
-->
