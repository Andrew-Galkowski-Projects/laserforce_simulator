# Manager firing via a ZenGM owner-mood model

CAR-02 ("performance-based firing") is implemented as a **ZenGM-faithful owner-mood
model**, not a single configurable threshold. The team **Owner** judges the **Manager**
(the implicit local user, identified by `League.current_team`) once per Season across
three cumulative **Mood factors** — *wins* (regular-season record vs a .500 baseline),
*playoffs* (missed / advanced / champion of the Season's embedded `tournament`
**Season phase** bracket; neutral when the Season has no playoff phase), and *money*
(season profit). Each factor's cumulative total is capped at a small positive ceiling
(you cannot win by maxing one factor, but you can lose by neglecting one); when the
summed mood falls to/below the firing threshold and the **Grace period** is over, the
Manager is **Fired** and must **Reassign** to one of the worst-performing eligible teams
(old team excluded) via a "New Team" picker, starting a fresh tenure + grace. Evaluation
runs at season-end, surfaced on an **Owner evaluation** screen shown after the Season
completes and before the pre-season rollover (`next_season`), and is browsable for past
Seasons. Source spec: `Screenshots_and_video_examples/firing_rules/firing_rules.md`.

## Considered options

- **Single configurable rank/win-rate threshold** (the PLAN's literal wording) — rejected:
  it can't express the "over-perform to bank goodwill / rebuilding teams forgiven"
  fuzziness the maintainer wanted. The cumulative-mood + grace-period model delivers that
  for free.
- **Team-strength expectation baseline** (judge vs a projected finish) — rejected: a new
  sub-model with uncalibrated magic weights (the LG-04/LG-05 problem). The cumulative bank
  + grace period already produce "better-than-expected stays longer / rebuilding forgiven."
- **Recompute mood from a single `tenure_start` marker** — rejected once browsing *past*
  Seasons' evaluations was required: a single marker can't reconstruct prior tenures across
  a firing. We persist an **immutable per-(League, Season) `OwnerEvaluation` snapshot**
  instead (the `PlayerSeasonRating` / LG-04 precedent): factor deltas, cumulative mood,
  verdict, and managed team — history is just reading rows.
- **Build the finance subsystem now** so *money* is live — rejected: salaries + team budget
  (house/coaches/analysts) + profit is a whole epic. *money* is **dormant** (always 0),
  exactly as ZenGM returns `money = 0` when the budget feature is disabled; a follow-up
  PLAN item lights it up later.
- **Auto-assign the new team / fire-only (`current_team = NULL`)** — rejected in favour of a
  user-picked "New Team" screen (the worst-N eligible teams), matching ZenGM's New Team page
  and avoiding a manager-less broken state.
- **Challenge-mode firings (miss-playoffs / luxury-tax) and voluntary rival-offer switching**
  — deferred (both default-off in ZenGM; luxury-tax needs the finance subsystem).

## Consequences

- A new `OwnerEvaluation` model + one `CreateModel` migration (no backfill, ADR-0004
  disposable-data posture). No new field on existing models is required for the decision
  itself (tenure boundaries + grace derive from the snapshot chain via the managed-team
  changes).
- A finance subsystem (player salary + team budget + season profit) is a **new PLAN.md
  follow-up**; until it ships the *money* factor is inert and the firing decision rests on
  *wins* + *playoffs* alone.
- CAR-03 will gate firing to single-player career (`league`) mode; in the deferred
  `multiplayer` mode each user is locked to their team (no firing), mirroring ZenGM's
  "skip the whole system in multi-team mode."
