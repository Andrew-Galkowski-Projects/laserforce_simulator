# next-feature: tickets that run in parallel

Read by `SKILL.md` for a breakdown of more than one ticket, at the spec (Step 4) and again at
implement (Step 6).

## Spec (Step 4)

When tickets will run in parallel, write each behaviour they share (input
during a save, retry after a failed load, the dirty flag) once as a spec
invariant, and cite it in every such ticket's acceptance criteria: ANG-06h's
Sight Lines ignored canvas edits during a save and Zones & Bases lost them.
An invariant that reloads or refreshes after an action names each landing
case: the screen already open, a screen the action navigates to, and a screen
that keeps its state. Each case gets a spec that counts the screen's GETs.
ANG-06-19's I2 said "after every success … `changed` ticks", and a navigating
action re-created the screen it had just landed on, so every GET went out
twice. Only the Spec review caught it, and the fix pass cost 204k tokens.
Also when tickets will run in parallel, give each parallel ticket its own
module(s) at the seam (e.g. a package with one file per ticket) so
implementers never append to the same file. Shared files caused every merge
conflict in the last run.

## Start implementers (Step 6)

Implementers run in their own worktrees branched from
the integration branch, started one at a time: a worktree created while
another agent is still committing can branch from a stale tip, so confirm
each one's base before starting the next. The agent moves its own worktree
off `origin/main` as its first step, sometimes onto a branch it names itself;
wait for its worktree, not its branch, with one background call through the
PowerShell tool (the Bash worktree guard refuses an `until` loop over `git`):
`while (-not ((git worktree list) -match 'agent-<id>\s+<integration sha>')) {
Start-Sleep 5 }` (ANG-06-16: a `rev-parse` wait on the agent's branch spun on a
renamed one). Point each one at the spec issue and its ticket issue rather than
pasting them.

## Prepare the integration tip (Step 6)

Before starting tickets that run in parallel, prepare the integration tip yourself:

- Grep it for every helper two of them will need, and move each into a neutral
  service module; every new action goes in its own ticket's file. Also list in
  each ticket the existing shared UI helpers its screens need
  (`shared/_alerts.scss`, `_badges.scss`, `api-message.ts`, `notify.ts`,
  `confirm-dialog.ts`), and create any missing one on the integration tip
  first, so agents reuse it instead of copying (ANG-06-8's badge, error box and
  load-error code each came back twice). When two tickets each need a card or
  flow an existing screen already has (an import card, a picker with its load
  error), extract the whole piece, its state included, not only its pure
  functions: ANG-06-13a moved `classifyImportErrors` alone, both agents copied
  roster-import's chooser, template download and error lists, and review paid
  for a shared `CsvImport`.
- When a foundation ticket ships a **worked-example screen** for parallel
  tickets to copy, move every helper it defines on the component into the kit
  before they start: agents copy the example, helpers included (ANG-06-15's
  Team Stats defined `arrow`, `sortParams` and `pickSeason`; six screens copied
  them, and the review fix pass cost 184k tokens). Its load + Retry pipeline
  counts as a helper: the foundation ticket ships it as an `inject*Load` kit
  function, and the spec names it at the seam (ANG-06-18: four screens copied
  the dashboard's constructor chain, and the fix pass cost 150k). A kit whose output feeds each
  ticket's service call gets a compile-time assignment test against every
  target params type (`const p: PlayerStatsParams = statsApiParams(...)`):
  ANG-06-15's header comment claimed assignability it lacked, and three agents
  worked around it two ways.
- For each existing kit helper a ticket will call (a screen loader, a picker),
  check it against the spec's invariants and that ticket's screen, and add any
  missing input on the integration tip first: a nav gate, a Retry, an option the
  screen must hide. ANG-06-16's `injectStatsScreen` loaded before the nav and had
  no Retry, and `TeamPicker` always offered "All Teams": five screens
  hand-rolled around them, and the review fix pass cost 285k tokens.
- For each new public seam method, grep what it calls (an `ApiClient` verb, a
  model method). Add anything missing to its shared module first, or the ticket
  that is barred from that file stops half-done (ANG-06a: `ApiClient.put`).
- When an earlier ticket left **stubs** for later tickets to replace, check its
  specs against the real screen: they assert *which* component routed (not
  the stub's text), and a spec that opens another ticket's screen releases
  that screen's requests before `verify()`. Otherwise every parallel ticket
  breaks the earlier specs. A kit helper that releases a stubbed screen's loads
  matches every GET the screen may add later (the method, or the API prefix),
  never an exact URL list: ANG-06-18's `releaseSeasonScreenLoads` missed
  Standings' League GET, and five layout specs broke at the merge. Also check each stub's **inputs** against the
  ticket that replaces it: every acceptance criterion must be computable from
  them (ANG-06f: the map stub could not dim deactivated players). A slot for
  later work needs a caller that can fill it; a routed component gets no
  projected content (ANG-06f's RV-04 `<ng-content>`). When an early ticket
  registers **placeholder routes**, give their block in `app.routes.spec.ts`
  an `afterEach(() => http.match(() => true))`, so the screen tickets never
  edit that spec as their screens start loading data (ANG-06-9).
- When a ticket adds a request to a layout or the Shell that every screen
  under it makes (a toolbar or nav GET), add its release to the test kit on the
  integration tip first, and call it from the existing `release*Loads`
  helpers, so no screen spec changes. This holds for tickets that run one
  after another too. ANG-06-19a's toolbar `play-controls/` GET took a
  hand-written release in 25 League specs, and review moved them all into
  `releaseLeagueChrome()`.
- When a ticket adds a **required** field to a shared API model type, add the
  field to every shared spec fixture that builds that type first. Otherwise
  the ticket must edit fixtures another ticket owns (ANG-06d: `scoreboard`
  on `GameRound` broke `match-fixtures.ts`).

## Merge each ticket (Step 6)

Merge each finished ticket branch yourself. Into the branch checked out in your
worktree, use `git merge`. Into a branch checked out nowhere (a
`prototype/<task>` branch), use `python scripts/merge_into_branch.py <target>
<ticket branch>`. It fast-forwards when it can, otherwise writes the merge
commit, and leaves the target alone on a conflict. After each merge of a
ticket that ran in parallel, run `npm --prefix frontend run test:ci` once
(about 15 s, with no agent running Vitest), so a spec that opens a stub
another ticket replaced fails at the merge that broke it: ANG-06-13a's list
spec clicked into Create and left the real screen's Teams GET open. After
every ticket merge that touches `frontend/`, run
`npm --prefix frontend run format:check` too (about 5 s): ANG-06-19c's
unformatted `playback.spec.ts` was caught only at `/pr-ready`. After merging a
ticket that deletes tests, run `python scripts/deleted_tests.py <tip before the
merge>` and match every line it prints to a spec verdict or a ported test; an
unmatched one is a port before the next deletion ticket starts (ANG-08's
`views_tests.py` lost 11 League play behaviours this way).

## Implementer-only constraints

Add these to each implementer prompt, beside `SKILL.md`'s shared constraints:

- **First step:** confirm your worktree is based on the integration branch,
  else `git merge --ff-only <integration branch>` in your own worktree (the
  auto-mode classifier refuses `git reset --hard`). Agent worktrees branch from
  `origin/main` by default. Then copy
  `laserforce_simulator/.env` from the main checkout into your worktree (Django
  needs its `SECRET_KEY`); it stays uncommitted.
- Run only your own test files; the full suite is the orchestrator's job.
- When tickets run in parallel, they share one MCP Chrome. Open your own page
  with `new_page`, close only that page, and leave `close_mcp_browser.ps1` to
  the orchestrator, who runs it once after the last agent reports. This
  overrides the reuse-the-tab rule in `CLAUDE.md`.
- Commit only on your ticket branch, and commit each green slice as you go: the
  PR is squash-merged, so WIP commits cost nothing, and uncommitted work is lost
  if your session ends. Merge the integration tip into it before reporting done.
  Comment the commit hash on your ticket issue.

## An implementer that stops reporting

When an agent's result is a network error (`ENOTFOUND`, a dropped connection),
first `SendMessage` it "continue": it resumes with its context intact (CAL-04's
three drops all resumed this way). Restart only if that fails.

An agent's session can end with no report and no commit (CAL-02b finished every
criterion and died before `git commit`). Before restarting a ticket, run
`python scripts/worktree_status.py` (read-only: every worktree's branch, last
commit, commits not in `origin/main` and uncommitted files), then read the
ticket's last comment for the checkpoint it left. The guard refuses
`git -C <worktree> status`, and CAL-03c's resume found its uncommitted
acceptance fix only by hashing each file against its commit.
Bring finished work over with `python scripts/copy_staged.py <worktree> --worktree`:
it takes everything uncommitted and prints any path that also changed on the
integration branch as `CHANGED ON BOTH SIDES`, for you to merge by hand. Re-run the
ticket's tests there and commit it yourself.
