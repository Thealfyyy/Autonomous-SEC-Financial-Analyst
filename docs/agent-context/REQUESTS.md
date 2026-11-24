---
project: Autonomous SEC Financial Analyst
purpose: intake for new feature/change requests, written by the human, triaged into TASKS.md by the agent
last_updated: 2026-11-20
---

# Feature & Change Requests

This file is where **you** write down new ideas, features, or changes as
they occur to you, instead of typing them straight into an agent's chat
box. Write as much or as little structure as you want.

**Not append-only** (unlike `PROGRESS_LOG.md`) — entries move through
statuses as the agent triages them. The agent should never rewrite your
`Description` or `Why` fields — only `Status` and its own `Triage note`.

## How this feeds into the rest of the system

1. You add a new entry here, `Status: New`.
2. At the start of a session, the agent reads this file and either
   turns each `New` entry into task(s) in `TASKS.md` (setting this
   entry to `Queued` with a note on which task(s)), or leaves it `New`
   with a `Triage note` asking a specific question if it's unclear or
   conflicts with current scope in `BRIEF.md`.
3. Once the linked task is done, the agent marks this entry `Done`.

## Entry template (copy this for each new request)

```
## REQ-<n> — <short title> — <date written>

**Status:** New

**Type:** Feature | Change | Bug fix | Question / investigation

**Description:**
<what you want, in their own words>

**Why:**
<what problem this solves, or why it matters now>

**Acceptance criteria (optional):**
- <how you'll know this is actually done>

**Notes (optional):**
<constraints, things to avoid, related existing code/areas>

**Triage note (agent-editable, doesn't touch the fields above):**
<e.g. "Queued as a task" or "Unclear — confirm X vs Y" or
"Conflicts with BRIEF.md §5, confirm before building">
```

---

<!-- Start writing requests below this line. Number them REQ-1, REQ-2, etc. -->

## REQ-1 — Add Project to CV After Final Live Testing — 2026-11-20

**Status:** Queued

**Type:** Change

**Description:**
Add the Autonomous SEC Financial Analyst project to the CV (`F:\CV\Adham El-Alfy - AI & LLM Systems Engineer (CV).pdf`) once final live re-ingestion (`AMZN`, `INTC`, `JPM`, optional `10-Q`s) and full 250-prompt live testing are complete.

**Why:**
Wait for final verified corpus counts, full `N/250` benchmark numbers, and GitHub link before generating the updated CV file.

**Acceptance criteria (optional):**
- Every number and claim is 100% verified from actual eval/test outputs (zero guesses).
- Save to a **new file** without overwriting `F:\CV\Adham El-Alfy - AI & LLM Systems Engineer (CV).pdf`.
- Show final text for confirmation before writing the new CV copy.

**Notes (optional):**
- Base on the approved 5-bullet Featured Project draft from Session 9, updating Bullet 1 (final corpus counts) and Bullet 4 (final `N/250` live accuracy).

**Triage note (agent-editable, doesn't touch the fields above):**
Queued to execute immediately after final live ingestion & 250-prompt evaluation run.
