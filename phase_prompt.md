# Phase-expansion session prompt

> Reusable prompt. Start a session with this file plus the phase number, e.g.:
> "Read phase_prompt.md — this session is Phase 2."

---

## Context

This repo (`ult-scrape`) is the data pipeline for an automatic music
transcription (AMT) project: train a neural network that converts an mp3 into
Guitar Pro tabs. The pipeline (scraper → decoder → enricher) has already
produced a corpus of ~500 songs pairing trustworthy, professionally-transcribed
multi-track Guitar Pro files with their source audio, and will be scaled to
thousands.

Read these before doing anything else:

1. `docs/roadmap.md` — the high-level roadmap: locked decisions, the two hard
   problems (alignment, tab representation), the synthetic-data strategy, and
   all ten phases (0–9). This session expands exactly **one** of those phases.
2. `OVERVIEW.md` — the documentation map, for how the existing pipeline works.
   Follow links into `docs/` only as the phase demands.
3. `plans/` — any `phase_{n}.md` files that already exist. Earlier phases'
   locked decisions are **binding inputs** to this session; do not silently
   contradict them. If this phase genuinely forces revisiting an earlier
   decision, flag it explicitly and get my sign-off before proceeding.

## Your task

Brainstorm **with me** to expand the assigned phase from its roadmap summary
into a complete, decision-ready plan. This is a collaborative design session,
not a solo writing task:

- Start by restating the phase's goal, its inputs (what prior phases deliver
  to it), its outputs (what later phases consume from it), and its risks —
  then lay out the design questions that must be settled, roughly ordered by
  how much they constrain everything else.
- Work through the design questions **one topic at a time**. For each: present
  the realistic options, the trade-offs, relevant prior art (don't reinvent
  what MT3 / DadaGP / Demucs / mir_eval / etc. already solved), and your
  recommendation with reasoning — then ask for my decision before moving on.
  Use the AskUserQuestion tool for decision points.
- Be rigorous, not agreeable. If an option I favor has a real problem, argue
  it. If something needs a quick experiment or measurement on the actual
  corpus (`output/`) to decide honestly, say so and run it — grounding
  decisions in the real data beats speculation.
- No decision may be left implicit. "We'll figure it out during
  implementation" is only acceptable for details that genuinely don't
  constrain the design, and must be listed as such.

## The deliverable

When all decisions are settled, write `plans/phase_{n}.md` (create `plans/` if
it doesn't exist) containing:

- **Goal & scope** — what this phase delivers, and explicitly what is out of
  scope.
- **Inputs / outputs** — contracts with neighboring phases: what it consumes,
  what it produces, in what format.
- **Locked decisions** — a table: decision, choice, alternatives considered,
  rationale. This is the heart of the document.
- **Design** — the concrete plan: components, data formats/contracts,
  algorithms, directory/project layout (follow the repo's decoupled-projects
  pattern), tooling and library choices.
- **Risks & mitigations** — what could sink this phase and how we'll detect it
  early.
- **Acceptance criteria** — how we'll know the phase is done and good
  (measurable where possible).
- **Deferred items** — the explicitly-punted details, each with a note on why
  deferring is safe.
- **Open questions for later phases** — anything this session surfaced that a
  future phase must answer.

Finally, update `docs/roadmap.md` if this session changed the phase's shape
(scope, sequencing, or a locked decision), keeping the roadmap and the plan
consistent.

## Ground rules

- One phase per session. If we uncover work belonging to another phase, record
  it under "Open questions for later phases" and stay on target.
- Plans are the session's *result* — don't start implementing anything beyond
  small measurement/verification scripts needed to settle a decision.
- Follow `CLAUDE.md` (repo conventions, doc-currency rules, git safety).
