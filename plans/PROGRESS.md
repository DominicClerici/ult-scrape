# Implementation progress

> Living status document for implementing the [roadmap](../docs/roadmap.md).
> One phase is implemented per session, driven by [`PROMPT.md`](./PROMPT.md).
> The orchestrator agent owns this file: update it at every step boundary,
> at every checkpoint, and at session end. Deviations from the phase plans are
> **not** recorded here — they go in [`CAVEATS.md`](./CAVEATS.md).

## How to read this file

- **Status board** — one-line truth per phase. Statuses:
  `not started` / `in progress` / `blocked` / `complete`.
- **Per-phase sections** — filled in by the session that implements the phase:
  the step-1 task breakdown (the working plan), a running checklist, and a
  session log. A phase is `complete` only when every item in its plan's
  *Acceptance criteria* section is checked off and the final review gate
  (PROMPT.md step 3) has passed.
- **Resuming**: if a phase is `in progress`, its *Checkpoint* block is the
  authoritative statement of where work stopped and what to do next. A fresh
  session invoked with `@PROMPT.md - Phase N` must trust the checkpoint over
  re-deriving state from the diff.

## Status board

| Phase | Title | Status | Depends on | Notes |
|---|---|---|---|---|
| 0 | Corpus audit & hygiene (`score-py/` structural + `audit-py/`) | not started | — | First to implement |
| 1 | Scale the corpus (continuous ops) | not started | 0 (manifest exists) | Background/continuous once started |
| 2 | Score model 1.0 (`gpscore`) + alignment (`aligner-py/`) | not started | 0 | Highest-risk phase; likely multi-session |
| 3 | Tokenizer + dataset builder (`gpscore.tokens`, `dataset-py/`) | not started | 2 | |
| 4 | Synthetic data engine (`render-py/`) | not started | 2a, 3 | |
| 5 | Evaluation harness (`eval-py/` + `tabeval`) | not started | 3 | |
| 6 | Baseline model (`model-py/`) | not started | 3, 4, 5 | Likely multi-session |
| 7 | Scale & iterate (cycle process over `model-py`/`eval-py`) | not started | 6 | Becomes a background loop after exit |
| 8 | Stitching & export (`stitch-py/`) | not started | 3 (oracle-first); M5 for real checkpoints | Buildable any time after Phase 3 |
| 9 | Product (`serve-py/`) | not started | 8 (driver) | Real serving waits for first pinned checkpoint |

**Implementation order:** 0 → 2 → 3 → 4 → 5 → 6 → 7, with Phase 1 started
after Phase 0 and run continuously, and Phases 8–9 slotted in per the
dependency column.

## Phase sections

Template for the session that starts a phase (copy, fill in, keep updated):

```markdown
## Phase N — <title>

**Status:** in progress · **Started:** YYYY-MM-DD · **Plan:** plans/phase_N.md

### Task breakdown (step-1 output)
| # | Task | File scope | Model | Status | Reviewed |
|---|---|---|---|---|---|
| 1 | … | `proj/src/…` | opus/sonnet | pending/done | ✅/❌ |

### Acceptance criteria checklist
Mirror of the plan's Acceptance criteria section, checked off as satisfied:
- [ ] …

### Checkpoint
<Only while in progress: exactly where work stopped, what is verified-green,
what the next action is, and any in-flight state a resuming session needs.>

### Session log
- **YYYY-MM-DD (session 1):** <what was accomplished, commits made, step
  reached, caveats filed (link by ID)>
```

---

*No phases have been started yet. Scaffolding created 2026-07-06.*
