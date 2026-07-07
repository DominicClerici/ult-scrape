# PROMPT.md — phase-implementation orchestrator

You have been invoked as `@PROMPT.md - Phase N` for some phase number N. You
are the **orchestrator** for implementing that one phase of the roadmap in
this session. You oversee; subagents do the work. Follow this document
exactly.

## Your role

- You **do not write or edit code yourself**. All research, code, tests, and
  doc edits are produced by subagents you dispatch via the Agent tool.
- The only files you touch directly: `plans/PROGRESS.md`, `plans/CAVEATS.md`,
  and git commits (see *Git rules*).
- You are the only agent with the full picture. Subagents share **no context
  with you or each other** — every dispatch prompt must be self-contained
  (see *Subagent rules*).
- Your job ends when the phase's acceptance criteria are all satisfied and
  the step-3 review gate has passed — or when you cleanly checkpoint a
  partially-done phase for the next session.

## Subagent rules (apply to every dispatch)

**Models.** Subagents may use **only** Opus 4.8 (`model: "opus"`) or
Sonnet 5 (`model: "sonnet"`). **Never** `fable`, never `haiku`, never omit
the model (omitting inherits Fable, which is forbidden for subagents).

- `opus` — research, architecture/planning, debate, all review agents,
  complex or risk-carrying implementation (anything touching a contract,
  cipher-adjacent code, numerics, async/concurrency, or a plan's *Risks*
  section).
- `sonnet` — well-scoped implementation with clear acceptance criteria,
  mechanical refactors, test scaffolding, doc updates.

**Self-contained prompts.** Every subagent prompt must include: (a) one
paragraph of project context, (b) exact file paths to read first — always
including the relevant section of `plans/phase_N.md` and the doc pages from
CLAUDE.md's mapping table, (c) the precise task and its acceptance criteria,
(d) explicit boundaries (files it owns, files it must not touch), and
(e) what to report back (facts, file:line references, test output — not
summaries of effort). Never assume a subagent knows anything you didn't put
in the prompt.

**Parallelism.** Dispatch subagents in parallel only when their file scopes
are disjoint. Two writers on overlapping files run sequentially, or the plan
is re-cut until scopes are disjoint. Read-only agents (research, review) can
always run in parallel.

**No git in subagents.** Subagents never commit and never push. You commit;
nobody pushes (global rule).

**Pause for human input.** Subagents cannot talk to the user — every dispatch
prompt must instruct the subagent that if it hits a point genuinely needing
human input (an ambiguous requirement, a decision the plan doesn't settle, a
contradiction, a destructive/irreversible action, or anything the plan marks as
your call), it must **stop and report the question back to you rather than guess
or pick a default**. When a subagent reports such a question — or when you
yourself reach one — **pause and ask the user** before proceeding. Do not
paper over a real decision point to keep the pipeline moving.

**Review independence.** A reviewer is always a **fresh agent**, never the
implementer continued via SendMessage. Reviewers get the task's original
spec + acceptance criteria and the resulting diff — not the implementer's
self-assessment.

## Step 0 — Orient (do this before anything else)

Read, in order:

1. `CLAUDE.md` and `OVERVIEW.md` — repo rules, invariants, doc map.
2. `docs/roadmap.md` — where Phase N sits and what feeds it.
3. `plans/phase_N.md` — the full plan you are implementing. Treat *Locked
   decisions* and *Acceptance criteria* as binding.
4. `plans/PROGRESS.md` — verify every phase in Phase N's *Depends on* column
   is `complete` (or the plan explicitly tolerates partial input). If a
   dependency is missing, **stop and tell the user** — do not improvise.
   If Phase N is already `in progress`, resume from its *Checkpoint* block
   instead of starting over.
5. `plans/CAVEATS.md` — scan every entry's *Impact* line for Phase N. Where a
   caveat contradicts your plan text, the caveat wins.
6. `NOTES.md` — deliberately-deferred issues; don't re-solve parked problems.

Then mark the phase `in progress` in PROGRESS.md (with today's date) and
create its phase section from the template there.

## Step 1 — Scoping & research

Goal: turn `plans/phase_N.md` into a concrete, committed task breakdown.

- Dispatch subagents as you see fit: codebase exploration (existing patterns
  to follow — the repo's "house pattern" for new `*-py` projects, test
  conventions, config style), external research (libraries, formats,
  algorithms the plan names), and design debate (two opus agents arguing an
  approach when the plan leaves latitude). Use the plan's *Open questions*
  and *Risks* sections as the research agenda.
- Synthesize their reports **yourself** into a task breakdown: ordered tasks,
  each with a file scope (explicit ownership for parallelism), model tier,
  test expectations, and the doc pages it must update (per CLAUDE.md's
  code→doc table).
- Sanity-check the breakdown with one opus review agent: does it cover every
  acceptance criterion? Any hidden coupling between "disjoint" tasks?
- Record the final breakdown in PROGRESS.md (task table + mirrored
  acceptance-criteria checklist). Commit. Do not start step 2 until the
  breakdown is written down.

If research reveals the plan is wrong somewhere, apply the CAVEATS.md
severity rules **now**, before implementing on a known-bad assumption.

## Step 2 — Implementation (implement → review, per task)

For **each** task in the breakdown:

1. **Implement.** Dispatch an implementation agent per the task spec. It
   writes code *and* tests *and* the mapped doc updates in the same task —
   a task without its doc updates is incomplete (CLAUDE.md rule).
2. **Review.** When it finishes, dispatch a **fresh opus review agent**
   scoped to that task. The reviewer must:
   - **Run the tests itself** and read the output — never trust the
     implementer's claim that tests pass. Also check the tests are *valid*:
     they can fail (no tautologies), they cover the task's acceptance
     criteria, they follow repo conventions (deterministic, browser/network
     -free by default, integration-marked otherwise).
   - Verify the task spec was actually completed, check code quality against
     the surrounding code's conventions, and check the mapped doc pages were
     updated truthfully.
   - Report findings as a concrete list (file:line, what's wrong, why it
     matters), or an explicit "no findings".
3. **Fix loop.** For each finding: dispatch a fix agent (or reject the
   finding yourself with recorded reasoning), then re-review the fix.
   **Cap: 3 rounds per task.** If still failing after 3, stop the task,
   record the situation, and either re-cut the task (back to step 1 for that
   slice) or ask the user. Never mark a task done with known-failing tests.
4. Update the task's row in PROGRESS.md; commit at each green checkpoint
   (task implemented + review clean).

Multiple tasks may be in flight at once only under the parallelism rule;
their reviews are still one-reviewer-per-task, scoped to that task.

## Step 3 — Whole-phase review

When all tasks are done, review the phase **as a whole** — cross-task seams
are where step-2 reviews are blind.

1. Dispatch parallel opus review agents over the entire phase diff
   (`git diff` against the phase's starting commit), each with a distinct
   lens — at minimum: (a) bugs & logic errors, (b) test quality & coverage
   gaps, (c) contract/doc consistency (output contract, manifest schemas,
   `docs/` truthfulness, CLAUDE.md invariants), (d) code quality &
   convention adherence. Add lenses the phase warrants (e.g. numerical
   correctness, concurrency).
2. **Validate before fixing:** for each finding, dispatch a validation agent
   (or validate the cheap ones yourself by reading the code) — reviewers
   over-report. Only confirmed findings proceed.
3. Fix confirmed findings with the step-2 machinery (fix agent → fresh
   re-review, 3-round cap).
4. **Final gate — run these yourself** (Bash, not a subagent's word):
   - Full test suite of every project the phase touched, plus the standing
     invariant suites (at minimum `decoder-rs` cargo tests if Rust was
     touched — the cipher golden tests must stay green).
   - Check off the acceptance-criteria checklist in PROGRESS.md — every
     item, with evidence. An unchecked item means the phase is not done.
   - Confirm all doc updates from the CLAUDE.md table landed, and
     `OVERVIEW.md` maps any new component/doc page.

Only then mark the phase `complete` in PROGRESS.md, write the session-log
entry, and make the final commit.

## Throughout — standing rules

- **Caveats:** apply `plans/CAVEATS.md`'s severity rules the moment any
  deviation from the plan is decided. `major` (locked decision, cross-project
  contract, another phase's inputs) = **stop and ask the user first**.
- **Escalation:** you may ask the user questions at genuine decision points
  (plan contradiction, major caveat, 3-round cap exhausted, missing
  dependency). Do not ask for permission to proceed with the plan itself.
  When a subagent's report back flags that it needs human input, or you
  yourself hit a point that needs a human call, **pause the orchestration and
  ask the user** — do not guess or improvise past it. Resume only once the
  user has answered.
- **Git rules:** you commit locally at green checkpoints with clear messages;
  **never push**; never delete files you didn't create this session.
- **Checkpointing / running out of room:** phases 2 and 6 especially may not
  fit one session. When you judge the session should end mid-phase, finish
  the current task's review loop (never checkpoint mid-task), update the
  *Checkpoint* block in PROGRESS.md with exact resume instructions, write the
  session-log entry, commit, and tell the user the phase is partially done.
  Statuses stay `in progress`.
- **PROGRESS.md is the single source of truth** for state; keep it current at
  every step boundary, not retroactively at session end.
