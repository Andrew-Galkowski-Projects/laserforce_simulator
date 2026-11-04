# next-feature: port and frontend tasks

Read by `SKILL.md` for a task that ports a Django screen to Angular or makes a frontend decision.

## Grill (Step 3)

A port task (a Django screen moving to Angular) maps every screen it replaces
to its `/api/` route before round 1: grep `api_urls.py` and the viewsets'
`@action`s. A missing or partial route is a settled fact in round 1, and a
backend ticket. PLAN.md said ANG-01 covered every ANG-06 item; ANG-06-9's
Team match history had no endpoint. Also grep `frontend/src` for each feature
PLAN.md lists: one already shipped is a settled fact, not a ticket (ANG-06-10's
Export PDF came with ANG-06d).

## Spec (Step 4)

Check each frontend decision against `CODING_STANDARDS.md`'s Angular section
and cite the rule it follows or the reason it departs: ANG-06c's grill left
the team pickers without `required`, and review sent them back.
A port or migration spec takes its behaviour from the source being ported
(the template and its script), not from the app `CLAUDE.md` summary: ANG-06h's
doc described a wall-height input the template never had. For each template
card, table row and page heading, list every value it renders and name the API
field behind it; a value with no field is a backend change in the spec
(ANG-06-9's Single Round cards showed Won / Lost / Tie, and the spec's response
shape had no `result`; ANG-06-14's History heading needed the League name, which
the History endpoint lacks, so every page change re-fetched the League). Quote
each screen's h1 and route `title` verbatim (ANG-06-17's spec named no title, so an
agent chose one) and list which controls each empty state hides:
templates differ screen by screen, and ANG-06-15's spec said only "copied word
for word", so an agent copied a sibling's "<League> — X" heading and kept
pickers the template hides. Check every "one of" the spec writes against the
template's `if` branches (ANG-06-15's Playoffs template shows the champion and
the bracket together). Quote headers and empty-state text from the template file
itself, never from a subagent's summary of it, and check each empty branch is
reachable from the API response: ANG-06-16's spec took "Survival(s)" from a
summary (the template says "Survival") and listed Team History's "No history
yet.", which renders only when no Team resolves.

## Verify (Step 7)

When `frontend/` changed, run `test:ci` as CI's 4-vCPU runner does, through the
PowerShell tool: `$env:CI='true'; cmd /c start '""' /b /wait /affinity F npm
--prefix frontend run test:ci`. The spec-to-worker layout decides which specs
share a jsdom, so a leak between specs shows only at CI's core count (ANG-06-13b
passed locally and failed CI twice). A spec that passes alone and fails there:
bisect with `docs/agents/ci.md` *Spec passes alone, fails in the suite*.
Run every suite on its own, never in the background beside another: any
`pytest -n auto` run (SQLite or Postgres) beside `test:ci` starves Vitest
into false timeouts (ANG-06c, ANG-06d).

Frontend code mutates with `--run -- npm --prefix frontend exec -- ng test
--watch=false --include=<spec>` in place of the pytest args.
