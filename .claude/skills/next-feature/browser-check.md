# next-feature: the browser check (Step 7)

Read by `SKILL.md` when anything a person sees changed, or for an API-only task's endpoint check.

Use Chrome DevTools MCP, following `CLAUDE.md`'s *Browser Sessions* rules (reuse the
existing tab, close what you open, close the browser when done). Server on 8001;
for the Angular SPA, `npm --prefix frontend run start:worktree` (4201, API on
8001) with Django started as `frontend/CLAUDE.md` **API and auth** says.

## Seed

Seed with `python laserforce_simulator/manage.py seed_browser_check` (two
Accounts, Teams, a Match, a Single Round, an unfinished Match, and a
full-fidelity Single Round on the configured "Browser Check Arena" map for
map features; add `--league` for League features: a career, finance-enabled
League with two completed Seasons and an active one at its playoffs (round
robin played, seeded bracket built, no bracket games yet), a three-Player
free-agent pool, a ratings snapshot per Player per Season, and settled finances
for both completed Seasons (cash, Season history, one injured Player), plus a
sandbox League whose draft Season splits four Teams into two Conferences
(ANG-06-18: editable Conferences, draft Standings); `--tournaments` for
Tournament features: one sandbox Tournament per format and stage; `--syracuse`
when the owner reviews real pathing: a full-fidelity Single Round on the committed
Syracuse map, with its Playback, event log and heatmap paths in the summary) and
remove it with `--clean` when done.

## Assert and screenshot

Assert on `innerText` or the API response body, not a status code. Wait with
`evaluate_script` (poll for a selector, return only the text you assert):
`wait_for` returns the whole page snapshot, 3-6k tokens on a League screen.
Take a viewport screenshot of each changed screen at 1280px and at 768px
(`resize_page`): a `fullPage` shot drops an inner box's scrollbar, so check
a box scrolls with `scrollWidth > clientWidth` (ANG-06-15's Feats table);
shoot transient UI (a snackbar lasts 5 s) in the same turn it appears,
right after the script that waits for it (ANG-06-11),
read it, and fix anything clipped or wrapped mid-word before review: specs
assert text, so only the picture shows layout (ANG-06c's winner chips).
After a style fix, read the changed property's computed value before
re-taking the screenshot: the dev server may not have reloaded it yet
(ANG-06-8). Fix by reflowing (a second line, a stacked cell) so every field the spec's
user stories show stays on screen (ANG-06e first hid the names). Check
from a second Account when ownership changed.

## API-only tasks

An API-only task checks the
endpoints in the browsable API or with the test client instead, and says
which. A test-client script runs through `manage.py shell -c
"exec(open(r'<path>').read())"` with `APIClient(SERVER_NAME="localhost")`:
outside pytest the default host `testserver` is not in `ALLOWED_HOSTS`.

Clean up any other test data the pass created.
