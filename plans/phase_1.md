# Phase 1 — Scale the corpus (continuous, background)

> Expanded from [the roadmap](../docs/roadmap.md#phase-1--scale-the-corpus-continuous-background)
> in the 2026-07-06 planning session. Decisions here are **binding inputs** to
> later phases.

## Goal & scope

Grow the corpus from ~509 tabs toward the **full UG official catalogue
(~100k tabs)** using the already-built discovery → scraper → enricher
pipeline, run as a slow continuous background process — and keep the growing
corpus *consumable*: decoded, enriched, manifested, backed up, and
snapshot-able on demand. This is deliberately a **pure operations phase**:
policies plus a few `scripts/` additions. No new subsystems. One sanctioned
exception to "no pipeline changes" (amended 2026-07-06): the enricher gains a
small **additive per-tab requeue** — a `repo.py` method + CLI subcommand
(`enricher requeue <tab_id>…`) — because `retry-audio` needs targeted,
eval-split-first retries and the enricher's only existing reset paths are
global (`retry_terminal` resets *every* `no_match`/`failed` row;
`upsert_pending` is `ON CONFLICT DO NOTHING`, so a re-scan never re-queues a
terminal row). The scraper-side prerequisites (idempotent enqueue against
active jobs; discovery startable while the queue is busy, serialized in the
worker) were implemented ahead of this phase, so `top-up` and catalogue
refreshes need no further scraper changes.

This session **revised the roadmap's original framing**. The initial planning
session assumed a 2k–10k target; we now know the official catalogue is
~100k tabs, all of it will be scraped eventually, the scrape rate is fixed
and slow (it cannot be sped up), and model-phase work proceeds in parallel
against whatever the corpus holds at the time. Training runs are **manually
triggered** and train on everything available at each cut, until the corpus
is very large (≥50k tabs). So Phase 1's real levers are *ordering*,
*hygiene*, and *durability* — not target-picking or training automation.

**Out of scope:** alignment or wrong-audio *fixing* beyond re-running the
enricher (Phase 2 owns alignment); dataset building/windowing (Phase 3);
render cadence and render disk budget (Phase 4 implementation); any
automated training triggers (training is manual by decision); scraper
throughput work (rate is fixed).

## Inputs / outputs

**Consumes:**

- The discovery catalogue (`scraper-py/scraper.db` `tab_metadata`) — currently
  516 rows from a capped run; a full uncapped discovery sweep is an early
  Phase 1 action.
- Phase 0's manifest (`manifest/manifest.jsonl`): `bad`/`suspect` audio
  verdicts and split assignments drive re-enrichment priorities.
- Phase 2's `manifest/alignment.jsonl` (once it exists): pairs whose
  alignment failed for audio reasons feed the re-enrichment queue.
- Phase 7's `manifest/requests/requests.jsonl` (once it exists):
  `re-enrichment` records (specific `tab_id`s) and `discovery` records
  (strata descriptors) jump the respective queues.

**Produces:**

| Artifact | Path | Nature |
|---|---|---|
| Growing corpus | `output/` | Under the frozen [output contract](../docs/output-contract.md), unchanged |
| Corpus snapshots | git commits of `manifest/manifest.jsonl` | Manifest hash = snapshot ID; git history = retention |
| Backup mirror | external drive | `output/` + `scraper.db` + `enricher.db` |
| Ops scripts | `scripts/top-up.*`, `scripts/retry-audio.*`, `scripts/maintain.sh` | Documented in `docs/scripts.md` |

Later-phase consumers: everything downstream reads the corpus through the
manifest; training runs (manual) pin the committed manifest's hash;
`dataset-py` derives its own dataset snapshot ID from it (Phase 3).

## Locked decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Population & target | Scrape the **entire official catalogue (~100k)** eventually, at the pipeline's fixed slow rate; **no numeric milestone**; building/training proceeds in parallel on whatever exists | 2k–10k target (roadmap's original); curated selection down to a fixed N | The catalogue is finite and all-official (uniform quality), so selection adds nothing; with rate fixed, *order* is the only real lever. Revises the roadmap's "corpus size" locked decision. |
| Enqueue ordering | **Seeded random**: stable shuffle, order key `sha1(f"{SEED}:{tab_id}")`, `SEED` a constant committed in the script; priority records from `manifest/requests/` jump the queue | Stratified round-robin by genre facet (needs slice→tab attribution discovery doesn't persist); popularity-first by votes/rating (months of mainstream bias); catalogue order (unexamined bias) | At a fixed slow rate the queue's front *is* the corpus for years. Seeded random makes "everything we have" an **unbiased sample of the catalogue at every instant** — diversity in expectation, representative splits, no distribution shift — with zero new metadata; newly discovered tabs slot in deterministically. |
| Training integration | **Manual.** Operator triggers training runs; each trains on everything available at cut time; no automated integration policy | Scheduled retrains; snapshot-triggered runs | User decision. Phase 7's data-lever cycles still request cuts explicitly when they exist — the snapshot ritual below serves both. |
| Snapshot convention | Before each training run: quiesce (finish decode + enrich pass) → regenerate manifest → **git-commit it**. Manifest hash = corpus snapshot ID; git history = retention. No copies, no snapshot directory | `snapshots/` directory of manifest copies (redundant with git); log-only hash in run config (can't reconstruct membership if the file is regenerated) | Phase 0 made the manifest deterministic and single-file precisely so snapshot = file hash; git supplies storage, diffing, and archaeology for free. |
| Backup | **External drive**, `rsync` of `output/` + `scraper.db` + `enricher.db`, run as a step of every maintenance pass. Decoded `.gp`/`.gpif` ride along in `output/` but renders are **not** backed up (regenerable) | Cloud object storage (B2/S3); both; none | User decision. The irreplaceable classes are audio (throttled re-download; videos vanish) — ~1.6 GB now, ~300 GB at 100k — **and the raw `.xtz` + `metadata.json`** (re-scraping the catalogue takes years at the fixed rate); all ride in the `output/` rsync. The DBs hold the catalogue and match history. **Git holds code + the committed manifest only — no data, no LFS** (amended 2026-07-06: `output/` is gitignored and lives on the 6 TB data drive; the backup drive must be a separate physical disk from it). |
| Tooling home | New glue lives in **`scripts/`** (the existing operator layer), documented in `docs/scripts.md`; promote to an `ops-py/` project only if the routine outgrows scripts | New `ops-py/` house-pattern project | ~200 lines of glue today; `scripts/` is already the documented operator surface for driving the pipeline. |
| Cadence | **Manual, one command**: `scripts/maintain.sh` runs the full maintenance pass whenever the operator chooses (and always as step one of the snapshot ritual). No cron | Scheduled (cron/launchd) + manual before training | Fits manual-training reality; zero moving parts on a personal machine. Backup is folded into the same command so it cannot be separately forgotten. |

## Design

### The ops loop

Three tempos, all operator-driven:

- **Continuous** — the scraper service runs with its queue kept non-empty:
  `scripts/top-up` enqueues the next K catalogue entries in seeded-random
  order (skipping tabs already succeeded or queued), after first draining any
  priority entries from `manifest/requests/requests.jsonl`.
- **Per maintenance pass** (`scripts/maintain.sh`) — decode new arrivals
  (`decoder-rs`) → `enricher scan` + `enricher run` (new arrivals only, so
  YouTube load is naturally trickle-paced) → `audit run` (regenerate manifest)
  → `aligner-py scan` + `run` (new arrivals; once Phase 2 lands) →
  `render-py scan` + `run` (new arrivals, batch size / disk budget per
  Phase 4's policy; once Phase 4 lands) → `rsync` to the external drive →
  print a status summary (queue depth, corpus counts by verdict/split, disk
  free). Stages whose tool doesn't exist yet are skipped gracefully —
  `maintain.sh` is the standing **cadence owner for every derived tree**, so
  alignment and renders never silently go stale as the corpus grows (Phase 7's
  val growth assumes newly aligned val-split songs accumulate here).
- **Occasional** — re-run discovery (uncapped) to refresh the catalogue as UG
  adds tabs; the first such run, which sizes the real population, is the
  first Phase 1 action. Tabs that disappear from UG stay in the corpus.

**Snapshot ritual** (before every training run): run `maintain.sh` → confirm
queue quiescent for decode/enrich → commit the manifest's **explicit allowlist**
(`git add manifest/manifest.jsonl manifest/overrides.json manifest/report.md &&
git commit`), never `git add manifest/` — `.gitignore` already fences off the
derived/large sub-trees (Phase 2 alignment warps, `requests/`), but staging by
file keeps the boundary explicit (see plans/phase_0.md "Git-commit boundary").
The committed `manifest.jsonl`'s hash is the corpus snapshot ID recorded by the
training run and by `dataset-py`.

### `scripts/top-up`

Reads `tab_metadata`, subtracts tabs with succeeded/pending jobs, sorts the
remainder by `sha1(f"{SEED}:{tab_id}")`, prepends any actionable
`manifest/requests/` priority tabs, and enqueues the next K via the existing
`POST /discover/enqueue`. Must be idempotent (re-running never duplicates
jobs — the scraper's enqueue now short-circuits on both succeeded *and*
active (queued/running) jobs per the 2026-07-06 scraper amendment, so this
is mostly free) and must tolerate `manifest/requests/` not existing yet.

### `scripts/retry-audio`

Builds the re-enrichment list from three sources, **eval-split (val/test)
tabs first** (they're eval capacity — Phases 5/6's standing request):

1. Manifest verdicts: `bad`/`suspect` for audio reasons, plus enricher
   `no_match`/`failed` states.
2. `manifest/alignment.jsonl`: pairs Phase 2 grades as failing for audio
   reasons (wrong video despite a Phase 0 `ok`).
3. `manifest/requests/requests.jsonl` `re-enrichment` records.

It resets/re-queues those `tab_id`s via the sanctioned `enricher requeue`
subcommand (the additive per-tab reset described under Goal & scope — the
existing `--retry-failed` stays all-or-nothing) and reports what it did.
Sources 2–3 don't exist until Phases 2/7 run; the script treats missing files
as empty. Run occasionally, ideally after `yt-dlp` upgrades (matching often
improves) — matching is otherwise deterministic, so blind retries without a
tooling change mostly re-fail.

### Priority semantics for `manifest/requests/`

- `re-enrichment` records (specific `tab_id`s) → consumed mechanically by
  `retry-audio`.
- `discovery` records (strata descriptors: genre/tuning/timbre) → consumed
  by the **operator**: run a facet-scoped discovery (`POST /discover` with
  the matching explore filters), then `top-up` with those tabs prepended.
  No per-tab genre attribution is needed for this — the facet scoping does
  the targeting.

### Scale checkpoints (documented expectations, not automation)

| At | Expectation | Action |
|---|---|---|
| ~509 (now) | `output/` 2.2 GB (1.6 GB audio, 31 MB `.xtz`) | Baseline |
| ~10k | `output/` ~35 GB (audio ~30 GB, `.xtz` ~600 MB) | Confirm data-drive and backup-drive headroom |
| ~50k+ | `output/` ~150–450 GB | Backup drive sized ≥ 1 TB; renders (regenerable, not backed up) live on the 6 TB data drive on top of this — variant count is the disk knob per Phase 4 |
| any | first manifest dedup-invariant violation | Phase 0's winner policy applies; inspect, add `overrides.json` entries if needed — fuzzy-dedup machinery stays deferred until violations are real |

## Risks & mitigations

| Risk | Detection / mitigation |
|---|---|
| UG markup/Cloudflare drift breaks scraping (the known-brittle layer) | Queue failures accumulate visibly in `status.sh`/maintain summary; fix per [browser.md](../docs/scraper-py/browser.md); the queue preserves state so nothing is lost while broken. |
| YouTube throttles/blocks the enricher at 100k scale | Enrichment is trickle-paced by construction (new arrivals per pass); keep `yt-dlp` current; `no_match` is acceptable — Phase 4 makes audio-less tabs usable synthetic training material. |
| Disk exhaustion (audio + future renders) | Scale-checkpoint table above; maintain.sh prints disk free; operator owns the budget. |
| Backup goes stale / drive dies silently | Backup is a maintain.sh step, not a separate habit; acceptance includes a restore spot-check; note: the backup drive must be a separate physical disk from both the machine's internal storage and the 6 TB data drive (with `.xtz`/`metadata.json` no longer in git, `output/` + the DBs have no durability outside this backup). |
| Seeded order silently violated (ad-hoc enqueues) | Ad-hoc/priority enqueues are *allowed* (requests.jsonl, operator judgment) but logged by top-up; the corpus only needs to stay *approximately* unbiased, and the manifest records what's actually present regardless. |
| Manifest regeneration slow at 100k | `audit-py --jobs` parallelism (Phase 0 design); determinism unaffected; regeneration is per-pass, not per-tab. |

## Acceptance criteria

- A full **uncapped discovery run** has completed: the real catalogue size is
  known and `tab_metadata` holds it.
- `scripts/top-up`, `scripts/retry-audio`, `scripts/maintain.sh` exist, are
  idempotent, tolerate not-yet-existing feedback files, and are documented in
  `docs/scripts.md`.
- Seeded ordering is reproducible: same catalogue + same seed → same order
  (unit-testable pure function).
- One maintenance pass has run end-to-end on the live corpus (decode →
  enrich → manifest → align/render stages when their tools exist → backup)
  and the backup passed a restore spot-check.
- `enricher requeue <tab_id>…` exists (additive repo method + CLI subcommand)
  and `retry-audio` uses it; global `--retry-failed` behavior unchanged.
- The snapshot ritual has been exercised once: a committed manifest whose
  hash is recorded as a snapshot ID.
- The scraper queue has been continuously non-empty across at least one
  multi-day stretch (the "background" property actually holds).

## Deferred items

| Item | Why deferring is safe |
|---|---|
| Genre/slice→tab attribution in discovery | Seeded-random ordering needs no metadata; Phase 7 `discovery` requests are served by facet-*scoped* discovery runs instead. Additive change if ever needed (Phase 0's deferral stands). |
| Fuzzy dedup clustering | Unchanged from Phase 0: the invariant surfaces real collisions; machinery waits for evidence. |
| Render cadence + render disk budget | Phase 4 implementation scope; renders are regenerable and excluded from backup, so no durability coupling. The `maintain.sh` slot for the render stage already exists — Phase 4 fills in batch size and disk budget. |
| Cron/scheduled maintenance | One operator, manual training; revisit only if passes get forgotten in practice. |
| `ops-py/` promotion | `scripts/` suffices at current glue size; promoting later is mechanical. |
| Exact K (top-up batch size) and pass frequency | Pure operator knobs; nothing downstream depends on them. |

## Open questions for later phases

- **Phase 2:** `alignment.jsonl` records for audio-reason failures should
  carry `tab_id` + a machine-readable reason so `retry-audio` can consume
  them mechanically (Phase 2's plan already commits to defining this loop —
  this names its consumer).
- **Phase 7:** `discovery` request records should carry the UG explore facet
  parameters (or enough to derive them) so the operator can launch the
  facet-scoped discovery run directly from the record.
- **Phase 6/7:** the user's stated intent is that "massive corpus" training
  (≥50k tabs) is a milestone years out; interim runs train on everything
  available — Phase 7's snapshot-bump data-lever cycles are the formal
  version of this and need no additional Phase 1 machinery.
