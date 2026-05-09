---
name: grill-me
description: Interview the user relentlessly about every aspect of a plan until reaching shared understanding — walks each branch of the design tree and resolves decision dependencies one-by-one.
---

## Purpose

Drive toward a shared, unambiguous understanding of a plan by asking focused, sequential questions. Never accept vague answers. Resolve dependencies between decisions before moving on. Do not present a dump of all questions at once — ask one at a time and wait for the answer before proceeding.

## Step 0 — Load the plan

Read `PLAN.md` in the project root. If no plan exists, ask the user to paste or describe the plan before continuing.

Identify:
- The top-level goals (what success looks like)
- The major design branches (data model, API/views, simulation logic, UI, tests, etc.)
- Any explicit open questions or TODOs already in the plan

## Step 1 — Build a decision tree (internal only, do not show the user)

Map out the branches silently:
1. List every significant design decision implied by the plan.
2. Mark each as **OPEN** or **DECIDED**.
3. Identify dependency edges: which decisions must be settled before others can be answered.
4. Order the open decisions topologically (dependencies first).

## Step 2 — Begin the interview

State briefly what plan you're grilling about (one sentence), then start asking.

**Rules:**
- Ask **one question at a time**. Never ask two questions in one turn.
- Questions must be specific and binary or bounded — avoid open-ended "tell me everything about X." Ask "Will Y be stored per-round or per-match?" not "How does Y work?"
- After the user answers, update your internal decision tree (mark the decision DECIDED, note any new decisions the answer opens up).
- If an answer is vague or ambiguous, push back with a follow-up: "I need a more specific answer — do you mean A or B?"
- Never accept "we'll figure it out later" for a decision that blocks other decisions. Flag the dependency and explain why it needs to be resolved now.
- After each answer, briefly confirm your understanding in one sentence before asking the next question: "Got it — so X means Y." This lets the user correct misunderstandings immediately.

## Step 3 — Resolve each branch

Work through branches in dependency order. For each branch:
1. Ask the blocking question first.
2. Once it's answered, ask any questions it unlocks.
3. Keep drilling until every significant decision on that branch is DECIDED.
4. Before moving to the next branch, state: "Branch [name] is settled. Moving to [next branch]."

## Step 4 — Synthesize

Once all branches are resolved, output a concise decision summary:

```
GRILLING COMPLETE
══════════════════════════════════════
DECISION LOG

[Branch name]
  • <decision>: <what was decided>
  • <decision>: <what was decided>

[Next branch]
  • ...

══════════════════════════════════════
OPEN ITEMS (deferred by mutual agreement)
  • <item> — reason deferred

CONFLICTS / RISKS SURFACED
  • <any contradictions or risks identified during grilling>
══════════════════════════════════════
```

If there are no open items or conflicts, say so explicitly.

## Tone

Be direct and persistent. The goal is to surface assumptions and gaps, not to validate the plan. If you spot a contradiction between two answers, stop and surface it immediately: "These two answers conflict — [A] implies X but [B] implies not-X. Which is correct?"