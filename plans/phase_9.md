# Phase 9 — Product: upload mp3 → get tabs

> Expanded from [the roadmap](../docs/roadmap.md#phase-9--product-upload-mp3--get-tabs)
> in the 2026-07-06 planning session. Decisions here are **binding**; there is
> no later phase — the consumers are users and the Phase 7 background loop.
>
> **Genre note (Phase 8's genre, deliberately):** Phase 9's evidence — per-window
> decode latency (Phase 6 M5 / the Phase 7 exit report), the overlap inference
> factor (Phase 8's rig), the stem verdict (Phase 6 M2) — does not exist yet,
> but the serving machinery is **checkpoint-agnostic**: Phase 8 made its driver
> runnable oracle-backed, so the entire service can be built and end-to-end
> tested with no model at all. The plan is therefore a **concrete, oracle-first
> design** with **sizing as a named evidence slot**: GPU concurrency, cost, and
> any cloud decision are read off measured numbers when they exist, never
> invented. The roadmap's "~real-time or better per song" becomes a measured
> report against that slot, not a requirement gate.

## Goal & scope

Everything between "a CLI on our machine turns an audio file into a `.gp`"
and "a person uploads an mp3 and gets tabs back":

- **`serve-py/`** — a new house-pattern service: FastAPI + SQLite job queue +
  one GPU worker (the scraper's proven skeleton, with the GPU as the scarce
  serialized resource instead of the browser). Upload or YouTube URL in;
  job id out; poll; download/view the result.
- **The v1 product surface**: any-ffmpeg-format upload + YouTube URL
  ingestion, an optional tuning/capo declaration (Phase 8's forced-header
  path as a user feature), stage-level progress, and a result page with
  in-browser alphaTab rendering + playback, `.gp` download, and an honest
  diagnostics panel from `assembly_meta.json`.
- **The operational envelope**: time-shared GPU on the training box (lease
  serializing against Phase 7 background runs), tunnel + shared-token
  access, job retention with full provenance, a flag-bad-result feedback
  feed into the Phase 7 loop.
- **The deferral ledger with triggers**: a defined public-exposure gate
  behind which accounts, pricing, rate limiting, and hosting decisions live
  — "the model earns them" made into a checkable predicate.

**v1 audience (locked): private tool with public-ready seams.** It serves
the operator + invited testers over LAN/tunnel. No accounts, no billing, no
public URL. But no "it's just me" shortcuts either: the API is stateless
job-based, auth is a replaceable layer, and the legal review is an explicit
gate before any public exposure — the architecture doesn't need a rewrite
to go public, only the gate's checklist.

**Out of scope:** accounts, pricing, payment, batch/API-key product tiers
(behind the public gate); building the warm predictor, a dedicated
inference GPU, or cloud serving before their measured triggers fire;
model/decode iteration (Phase 7 — the service runs pinned checkpoints
only); stitching/assembly logic (Phase 8 — the service treats
`stitch-py transcribe` as opaque); Demucs as a *user* choice (whether
separation is in the path at all is Phase 6's stem verdict, a pipeline
config); calibrated confidence scores (no calibration data exists);
MusicXML (Phase 8 deferred it to demand).

**Sequencing note:** buildable oracle-backed as soon as Phase 8's driver
exists — no checkpoint, no GPU needed for development and tests. Real
serving additionally needs the first pinned checkpoint from the Phase 7
registry (interim pins allowed, see locked decisions) and fills the sizing
evidence slot at that moment.

## Inputs / outputs

**Consumes:**

- `stitch-py transcribe <audio>` (Phase 8): the single entry point —
  subprocess per job; produces `song.gp` + `assembly_meta.json`; owns
  escalation orchestration; supports the forced-header prefix (the
  tuning/capo feature's mechanism) and reports stage timings (the
  cold-start measurement comes free).
- Phase 7 registry: pinned checkpoints + decode configs; the exit report's
  per-window decode latency and Phase 8's overlap factor (the sizing
  slot's inputs); the background loop that consumes this phase's feedback
  feed.
- Phase 6: the stem-ablation verdict (whether Demucs sits in the inference
  path); M5 latency statistics (first sizing-slot input).
- Repo conventions: the scraper's FastAPI + SQLite + single-worker pattern;
  the enricher's yt-dlp usage pattern (URL ingestion); `ffmpeg` (already a
  pipeline dependency) for input normalization; alphaTab (already the
  Phase 5/8 review-bundle renderer) for the result page.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| The service | `serve-py/` | Code — FastAPI + SQLite queue + one GPU worker + static frontend; subprocess-orchestrates `stitch-py transcribe`; no in-process ML deps |
| Job records | `serve-py/data/jobs/<job_id>/` | Derived — input audio, normalized audio, `song/` output, `job.json` (state, provenance, timings) |
| Feedback feed | `manifest/requests/feedback.jsonl` | Contract — flag-bad-result records appended by the service, consumed by Phase 7 cycles (same directory contract as Phase 7's `requests.jsonl`) |
| Sizing readout | `docs/serve-py/sizing.md` | The evidence-slot fill: measured per-song wall-clock breakdown (queue wait, cold start, decode, assembly) at the first pinned checkpoint; updated per release |
| Public-exposure gate | `docs/serve-py/public-gate.md` | The deferral ledger: the four gate conditions and the decisions parked behind them |

**Later consumers:** users (the point); the Phase 7 background loop (feedback
feed + per-job provenance as qualitative error-analysis material — uploads
have no ground truth, so this is diagnostic, not eval data); a future
post-gate productization session (accounts/pricing/hosting — deliberately
not pre-planned here).

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Plan genre | **Concrete, oracle-first + sizing evidence slots**: full service design locked and buildable checkpoint-free; GPU sizing/concurrency/cloud read from M5 / Phase 7-exit latency × the overlap factor when they exist | Evidence-contingent framework (Phase 7 style — but the design questions here are mostly *not* evidence-dependent); defer the session until Phase 7 exits (forfeits the buildable checkpoint-agnostic 90 %) | Same reasoning as Phase 8: the machinery is testable today (oracle-backed driver); only sizing needs real numbers, and the job model absorbs any latency outcome without redesign. |
| v1 audience | **Private tool with public-ready seams**: operator + invited testers; no accounts/billing; stateless job API + replaceable auth so going public is a gate checklist, not a rewrite | Public free demo (forces legal review, abuse handling, cloud cost into the critical path before the model is presentable); commercial foundation (contradicts the roadmap's locked deferral) | Matches "product decisions deferred until the model earns them" while refusing to bake in private-only shortcuts that would make earning them expensive. |
| Service architecture | **Async job queue on the scraper house pattern**: FastAPI + SQLite queue + one worker; `POST /jobs` → id → poll → download; queued/running/succeeded/failed state machine | Synchronous request-response (bets on fast inference — a number we refuse to invent; forfeits progress UX; dies on timeouts); external task queue (Celery/Redis — broker infrastructure the SQLite/filesystem pattern deliberately avoids, unjustified at one-GPU concurrency) | The repo already validated this exact shape for long jobs against a scarce serialized resource (browser there, GPU here); it is the only option that is latency-outcome-proof. |
| GPU process model | **Subprocess per job (Phase 8 driver unchanged) + pre-designed warm-predictor escalation**: `model-py serve --spool <dir>` (persistent warm checkpoint, filesystem-mediated) + a driver `--predictor spool:<dir>` flag — built only when the measured cold-start fraction is significant at real latencies | Warm model server from day one (daemon lifecycle, CUDA-leak, restart-policy surface before evidence it matters); model in-process in the worker (drags torch/checkpoint conventions into the product service — the coupling Phase 8 rejected) | Cold-start fraction is measured for free from the driver's own stage timings; on a private tool, ~20 s overhead on a minutes-long job costs nobody anything; the escalation is additive to Phase 8's contract (a new predictor backend, not a redesign). |
| Input surface | **Any ffmpeg-decodable upload + YouTube URL** (enricher's yt-dlp pattern); everything normalized internally to the pipeline's 24 kHz mono; upload size/duration caps in config; **the URL path is flagged for mandatory re-review at the public gate** | Upload only (avoids yt-dlp in the service but makes daily use clunky); mp3 only (artificially narrow — our own corpus audio is webm/opus and normalization is internal anyway) | Normalization is one ffmpeg call the pipeline already needs; paste-a-link is the biggest private-tool UX win; the legal exposure is contained by the gate flag. |
| Result delivery | **`.gp` download + in-browser alphaTab rendering with its built-in synth playback** | `.gp` download only (every evaluation requires desktop Guitar Pro — weakest feedback loop for the phase whose point is *using* the model) | Reuses the Phase 5/8 review-bundle renderer; "see and hear it in the browser" is the difference between a file converter and a judgeable product. |
| User controls | **Tuning/capo forcing only** (optional field → Phase 8's forced-decode-prefix path, header voting skipped); separation is pipeline config per Phase 6's stem verdict, never user-facing; decode/window config pinned to the release | No controls (wastes the mechanism Phase 8 built specifically as this feature; mis-voted tuning is the one failure a user can trivially fix); advanced panel (knobs invite unpinned configurations Phase 7's discipline forbids) | The guitarist knows their tuning; the mechanism already exists; everything else stays pinned. |
| Quality signaling | **Diagnostics panel from `assembly_meta.json`**: repairs, seam disagreements, header vote splits, coverage — readable, honest, no invented score | Single confidence badge (the mapping would be an invented constant with zero calibration data); nothing (hides failure modes the pipeline already knows — bad for a tool whose v1 audience debugs the model) | Calibrated confidence is deferred until real-use evidence exists to calibrate against; the raw facts are available today. |
| Hosting | **The training box, time-shared GPU**: an advisory GPU lease (lock file) serializes inference jobs against Phase 7 background training; jobs queue while training holds the lease; queue-wait is measured per job | Dedicated inference GPU (hardware spend against unmeasured contention); cloud per job (cold starts + $/job for sporadic private use; contradicts local-first — stays as the pre-designed public-scale escalation) | Zero new hardware/ops; the job model already absorbs waiting; the sizing slot later says whether contention actually hurts. |
| Access control | **LAN/tunnel + one shared bearer token** | Open on tunnel (a leaked URL becomes a free public GPU + arbitrary-content uploads); accounts (locked out of v1) | One secret, trivially implemented, trivially replaced by real auth at the public gate. |
| Public-exposure gate | **All four, together**: (i) legal review — training-data posture, serving outputs of UG-trained weights, the YouTube-URL path; (ii) abuse controls — rate limits, upload caps, real auth; (iii) the measured sizing/cost readout; (iv) Phase 7's human floor (median blinded recognizability ≥ 4/5). Accounts/pricing/batch decisions live behind it | Legal review only (exposes an uncosted GPU service and possibly an indefensible model); undefined gate ("we'll know when we're ready" — the vibes-based exit every phase refused) | Makes the roadmap's "until the model earns them" a checkable predicate; each condition is cheap to check and expensive to skip. |
| Retention & feedback | **Jobs retained by default** (input audio + outputs + meta, full provenance) + per-job delete + a **flag-bad-result** control appending to `manifest/requests/feedback.jsonl` (consumed by Phase 7 cycles); retention re-decided at the public gate where it becomes a privacy matter | Auto-expire (throws away the only real-usage failure signal the model gets, for a privacy concern a private tool doesn't yet have) | Real uploads + real failures are exactly the "production feeds error analysis" material the Phase 7 background loop wants — qualitative, since uploads have no ground truth. |
| Checkpoint policy | **Any explicitly pinned checkpoint from the Phase 7 registry** (interim pins allowed); swap = config change + service restart; every job records checkpoint id, decode config, `gpscore`/vocab versions | Release-grade (≥ 4/5 floor) only (delays the tool's diagnostic purpose until late Phase 7 — the tool is partly how we judge the model) | The public gate separately demands the floor before strangers see output; privately, judging interim checkpoints is the point. |
| Project & frontend | **`serve-py/`** (house naming), Python ≥ 3.13; **static single-page frontend** (vanilla HTML/JS + vendored alphaTab, served by FastAPI, no build system) | Frontend framework + build pipeline (a second toolchain for one page); folding the service into `stitch-py` (couples product lifecycle to assembly library; violates one-project-one-contract) | The page is an upload box, a progress view, and a result view — a build system would outweigh the application. |

## Design

### `serve-py/` — the service

House-pattern project: FastAPI app + SQLite queue + one async worker +
static frontend. In-process dependencies: FastAPI/uvicorn + stdlib — **no
torch, no gpscore, no tabeval**; the ML stack is reached only through the
`stitch-py transcribe` subprocess (filesystem contract, like every project
boundary in the repo). External tools: `ffmpeg` (normalization), `yt-dlp`
(URL ingestion), both already repo dependencies via the enricher.

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings: data dir, checkpoint pin + decode config (passed through to the driver), auth token, upload caps (size/duration), yt-dlp policy, GPU-lease path, driver command + timeout. |
| `app/api.py` | Routes: `POST /jobs` (multipart upload or `{url}`, optional `{tuning, capo}`), `GET /jobs/{id}` (state + progress + diagnostics summary), `GET /jobs/{id}/song.gp`, `GET /jobs/{id}/meta` (assembly_meta), `DELETE /jobs/{id}`, `POST /jobs/{id}/flag` (feedback), `GET /healthz`, `GET /version` (checkpoint pin + git SHA). Bearer-token middleware. |
| `app/repo.py` | The only module issuing SQL (scraper convention): job table, state machine, queue queries, injectable clock. |
| `app/worker.py` | The single worker loop: claim job → prepare (fetch/normalize) → acquire GPU lease → run driver → finalize. Stage transitions + timings written to the job record; subprocess management + timeout kill. |
| `app/prepare.py` | Input handling: upload persistence; yt-dlp fetch (URL jobs); ffmpeg probe + normalize to the pipeline input format; cap enforcement (reject oversize/overlong before queueing GPU time). |
| `app/lease.py` | The GPU lease: advisory lock file at a configured path; blocking acquire with heartbeat; **the same convention `model-py` run wrappers acquire** (the one cross-project touch — documented in both projects' docs). |
| `app/feedback.py` | Flag-bad-result records → `manifest/requests/feedback.jsonl` (job id, checkpoint pin, user note, diagnostics snapshot). |
| `app/static/` | The page: upload/URL form + tuning/capo field → progress view (stage + window k/N) → result view (alphaTab render + playback, `.gp` download, diagnostics panel, delete, flag). Vendored alphaTab. |

### Job lifecycle

State machine (scraper-style, owned by `repo.py`):

```
queued → preparing → transcribing → assembling → done
              └──────────┴──────────┴────────→ failed (error class + message)
```

- **preparing**: URL fetch (if URL job) → ffmpeg normalize → caps enforced.
- **transcribing**: GPU lease acquired; `stitch-py transcribe` runs; window
  `k/N` progress parsed from the driver's progress output; queue-wait
  (lease acquisition time) recorded.
- **assembling**: the driver's assembly stage (fast, symbolic); then output
  moved into the job dir.
- **done**: `song.gp` + `assembly_meta.json` available; diagnostics summary
  extracted into the job record.

`job.json` provenance per job: input kind + original filename/URL, audio
duration, forced tuning/capo (if any), checkpoint id, decode config,
`gpscore`/vocab versions, driver version, per-stage wall-clock (queue wait,
cold start, decode, assembly — the sizing slot's instrumentation), error
class on failure. Progress is served by polling `GET /jobs/{id}` (SSE is a
deferred nicety; polling a SQLite-backed endpoint is the house pattern).

### The pipeline per job

```
upload / YouTube URL
  → prepare: yt-dlp (URL only) → ffmpeg normalize (24 kHz mono, caps)
  → [Demucs — present only if Phase 6's stem verdict put stems in the
     inference path; a pipeline config inside the driver invocation,
     never a user choice]
  → stitch-py transcribe (subprocess; forced-header prefix if the user
     declared tuning/capo; pinned checkpoint + decode config)
  → song.gp + assembly_meta.json → job dir → result page
```

### The sizing evidence slot

Filled when the first pinned checkpoint serves; published as
`docs/serve-py/sizing.md` and refreshed per release pin:

- **Inputs**: per-window decode latency (M5 / Phase 7 exit report, for the
  *chosen* decode config), Phase 8's overlap factor, measured cold-start
  fraction, measured queue-wait distribution, Demucs cost (if in path).
- **Outputs**: per-song wall-clock breakdown vs song duration (the
  roadmap's "~real-time or better" reported, not presumed); the verdicts
  for the named escalations below.
- **Escalation triggers read from it**: cold-start fraction significant at
  real latencies → build the warm predictor; queue-wait or throughput
  unacceptable for actual usage → dedicated GPU / cloud review; both are
  reviews with the numbers on the table, not automatic builds.

### Escalation contracts (pre-designed, evidence-triggered)

- **Warm predictor** (trigger: measured cold-start fraction): `model-py`
  gains `serve --spool <dir>` — a long-lived process holding the warm
  checkpoint, watching a spool directory for request dirs and writing
  `predictions.jsonl` per the song-mode contract; the Phase 8 driver gains
  `--predictor spool:<dir>` (additive flag; subprocess mode remains the
  default and the oracle/test path). `stitch-py` stays model-free; the
  boundary stays filesystem-mediated. The worker then manages the predictor
  process's lifecycle (start on service boot, restart on failure).
- **Dedicated / cloud GPU** (trigger: sizing readout or the public gate):
  the job model, API, and driver invocation are unchanged — the worker and
  the GPU move. No design work now beyond noting the seam is clean.

### Testing & the oracle-first path

Unit tests (deterministic, network/GPU-free — repo convention): state
machine + queue (injectable clock), API schemas + auth, cap enforcement,
lease behavior (held-lock contention), feedback records, provenance
completeness — the driver is faked with a stub subprocess that emits
driver-shaped progress and outputs. Integration tests (marker): real
ffmpeg/yt-dlp; end-to-end `POST /jobs` → `done` against the **oracle-backed
driver** (no checkpoint, no GPU) — the same trick Phase 8 built on, which
also gives frontend development a fast, model-free backend.

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| Cold start dominates per-song latency once real decode is fast | Measured per job from day one (driver stage timings); the warm-predictor escalation is pre-designed and additive; the trigger is a readout, not a guess. |
| GPU contention with Phase 7 background training makes the tool unusable | The lease makes contention safe (never concurrent OOM); queue-wait is measured per job; persistent pain is the dedicated-GPU review's evidence. |
| yt-dlp breakage / YouTube ToS exposure | Same dependency posture as the enricher (already accepted for research use); URL path is config-disableable and flagged for mandatory legal re-review at the public gate. |
| Untrusted uploads (hostile files, disk fill) | ffmpeg probe + normalize as the only parser touching input; size/duration caps enforced before GPU time; uploads live only under the service data dir. |
| Shared token leaks | Token rotation is a config change; caps bound the damage; the tunnel is not a public URL; real auth is a gate item. |
| Silent quality drift across checkpoint swaps | Every job pins its checkpoint id — outputs are always attributable; swaps are explicit config changes, never automatic. |
| alphaTab renders differently from Guitar Pro | Already the accepted risk of the Phase 5/8 review bundles; the `.gp` download is the ground truth artifact; discrepancies feed the flag-bad channel. |
| Disk growth from retained jobs | Retention is deliberate (error-analysis value); per-job delete exists; a df check in `healthz` makes growth visible; policy revisits at the public gate. |
| Scope creep toward product features before the gate | The deferral ledger is explicit and trigger-gated; accounts/pricing/batch are named as parked, not forgotten. |
| Service built against invented sizing assumptions | The genre decision: nothing in the job model depends on latency; sizing is a slot filled by measurement; the two escalations are the absorbers. |

## Acceptance criteria

- **Oracle-backed end-to-end**: `POST /jobs` with a corpus audio file →
  `done` → `song.gp` downloads and the result page renders + plays it in
  alphaTab — with no checkpoint and no GPU (stubbed/oracle driver), in CI
  behind the integration marker.
- The tuning/capo forcing path exercised end-to-end (declared tuning →
  forced-header driver invocation → header reflected in the output).
- URL-ingestion path works (integration marker) and is disableable by
  config.
- Job records carry complete provenance (pins, versions, per-stage timings
  incl. queue-wait and cold-start fraction) — asserted by test.
- GPU lease honored: contention test (held lock → job waits, records
  queue-wait); the lease convention documented in `serve-py` **and**
  `model-py` docs.
- Diagnostics panel surfaces repairs / seam disagreements / header votes /
  coverage from `assembly_meta.json`; flag-bad-result appends a valid
  record to `manifest/requests/feedback.jsonl`.
- Unit tests deterministic and network/GPU-free by default; ffmpeg, yt-dlp,
  driver, and end-to-end paths behind the `integration` marker (repo
  convention).
- **First real serving milestone** (needs a pinned checkpoint): a real
  song transcribed through the service by an invited tester on the tunnel;
  the sizing readout (`docs/serve-py/sizing.md`) published from measured
  job timings — the evidence slot filled.
- `docs/serve-py/public-gate.md` written: the four gate conditions and the
  parked decisions; no public exposure occurs within this phase.
- Docs current per CLAUDE.md: `docs/serve-py/overview.md` written,
  `OVERVIEW.md` map + roadmap updated (including the stale "quantization"
  trim).

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Accounts, pricing, rate limiting, batch/API product tiers, hosting choice | Parked behind the public-exposure gate with a defined predicate; the stateless job API + replaceable auth keep them additive. |
| Building the warm predictor (`model-py serve --spool` + driver flag) | Pre-designed, additive contract; trigger = measured cold-start fraction at real latencies. |
| Dedicated inference GPU / cloud serving | Trigger = sizing readout or the public gate; the seam (worker + GPU move, API unchanged) is clean by construction. |
| Calibrated confidence score | No calibration data exists; the diagnostics panel carries the honest facts meanwhile; flag-bad + retained jobs are the future calibration set. |
| Progressive/streaming results (bars appearing as windows decode) | Phase 8's assembler is batch by design and fast; stage + window-count progress covers the UX; revisit only if user feedback demands it. |
| SSE/websocket progress push | Polling a SQLite-backed endpoint is the house pattern and sufficient at private-tool concurrency. |
| Retention policy revision (quotas, expiry, privacy terms) | Becomes a privacy matter exactly at the public gate, where it is a named checklist item. |
| Multi-user job isolation / per-user namespaces | Meaningless before accounts exist; job ids are already the unit of isolation. |

## Open questions for later phases

- **Phase 7 (background loop)**: consume `manifest/requests/feedback.jsonl`
  in error-analysis cycles (qualitative material — no ground truth on
  uploads); publish per-window decode latency + decode config with every
  registry pin so the sizing readout can refresh per release without a
  special request.
- **Phase 8 (on trigger)**: the warm-predictor amendment (driver
  `--predictor spool:<dir>`) executes there when Phase 9's cold-start
  readout fires — an additive flag on the driver contract, recorded as a
  documented amendment to `plans/phase_8.md`.
- **Phase 6 (M5)**: no new obligation — per-window latency reporting is
  already promised; it doubles as the sizing slot's first input.
- **Post-gate productization session**: if/when the public-exposure gate is
  deliberately opened, a new planning session scopes accounts, pricing,
  hosting, batch tiers, and the retention/privacy policy — deliberately
  not pre-planned here.
