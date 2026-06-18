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

## FIN-05 addendum — luxury-tax challenge fire (a second, mood-independent trigger)

FIN-05 lights up the **luxury-tax challenge-mode firing** this ADR's "Considered options"
deferred (it needed the finance subsystem FIN-01 ships). The firing decision now has a
**second trigger**: an optional per-League rule (`League.challenge_fired_luxury_tax`,
default off, set at League creation, never edited mid-League) that fires the Manager
**outright any Season their Current team pays the luxury tax — independent of cumulative
owner mood**, the faithful analogue of ZenGM's `challengeFiredLuxuryTax`.

- **`decide_verdict` stays the single decider.** The mood-independent trigger is **not** a
  second decision path — `matches/owner_mood.py::decide_verdict` gains two keyword-only
  bools (`luxury_tax_paid`, `challenge_fired_luxury_tax`, both `default False`, so every
  existing caller is byte-unchanged) and one new branch. The pure module stays Django-free
  (plain bools, no new import) and the `Verdict` dataclass is unchanged (no `fired_reason`
  on the seam).
- **Checked first, but inside the same Grace-period gate.** The luxury-tax branch is the
  FIRST check *inside* the existing `past_grace` block — so it **takes precedence over the
  mood verdict** yet still **respects the Grace period** (no luxury firing during grace),
  exactly as mood firing does. It does not bypass grace.
- **The reason is persisted immutably.** Consistent with this ADR's rejection of recomputing
  mood from current state, the firing *reason* is **stamped on the immutable per-(League,
  Season) `OwnerEvaluation` row at write time** (`OwnerEvaluation.fired_reason`,
  `""`/`"owner_mood"`/`"luxury_tax"`) and read back verbatim on the owner-evaluation screen —
  never re-derived. Legacy pre-FIN-05 fired rows default `""` and render as the mood message.
- **Mood is still recorded normally on a challenge fire.** The writer still computes the
  *wins*/*playoffs*/*money* deltas and cap-chains the cumulative totals exactly as before;
  the challenge only flips the verdict to *fired* and the reason to luxury-tax. `next_season`
  is unchanged — a challenge fire is a `verdict == "fired"` and routes to the New Team picker
  by the existing reason-independent gate.
- **Inert finance-OFF / non-career.** With team finances OFF there is no `TeamSeasonFinance`
  row, so `luxury_tax_paid` is `False` and the branch never fires (a toggle on a non-finance
  League is silently harmless — no cross-field validation). Outside career mode the writer
  early-returns and writes no evaluation at all (the CAR-03 posture). No simulator change ⇒
  **no Score Calibration re-baseline**. One migration (two `AddField`s, no backfill — the
  ADR-0004 disposable-data posture); **no new ADR** (this addendum records the consequence).
