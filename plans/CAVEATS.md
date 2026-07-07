# Caveats — deviations from the phase plans

> The phase plans in this directory were locked during the planning sessions
> (see [`docs/roadmap.md`](../docs/roadmap.md)). Implementation will sometimes
> have to deviate — an assumption turns out false, an API doesn't exist, a
> design detail doesn't survive contact with the code. **Every deviation is
> recorded here**, so later phases (which were planned against the original
> text) can discover what actually shipped and assess the impact.
>
> Status/progress tracking does **not** belong here — that's
> [`PROGRESS.md`](./PROGRESS.md). This file is only for "we did something
> different from what the plan says, and here is why and what it affects."

## Rules

1. **File a caveat the moment the deviation is decided**, not at session end.
2. **Severity gates who decides:**
   - `minor` — implementation detail differs, no locked decision or
     cross-project contract touched, no other phase's inputs change.
     The orchestrator records it and continues.
   - `major` — touches a locked decision (roadmap table or a plan's *Locked
     decisions* section), a contract (`docs/output-contract.md`, manifest
     schema, `gpscore` API, dataset/eval formats), or another phase's
     documented inputs. The orchestrator **stops and asks the user** before
     proceeding; the entry records the user's decision.
3. **Update the affected docs in the same session** — a caveat entry is a
   pointer and impact record, not a substitute for keeping `docs/` and the
   phase plans truthful. When a plan file itself is amended, note it in the
   entry.
4. Later-phase orchestrators **must read this file during orientation** and
   check the *Impact* column for their phase before trusting their plan text.

## Entry format

```markdown
### CAV-NNN — <one-line summary>
- **Date:** YYYY-MM-DD · **Phase:** N · **Severity:** minor | major
- **Plan said:** <what the plan/roadmap specifies, with file+section ref>
- **We did:** <what was actually implemented>
- **Why:** <the reason the plan didn't survive>
- **Impact:** <which phases/plans/docs/contracts are affected and how;
  "none beyond Phase N" is a valid answer>
- **Follow-ups:** <doc updates made; anything a later phase must do; user
  decision if severity was major>
```

IDs are sequential across the whole project (CAV-001, CAV-002, …), never
reused, entries never deleted — if a caveat is later reversed, file a new
entry that references the old one.

---

## Entries

*None yet.*
