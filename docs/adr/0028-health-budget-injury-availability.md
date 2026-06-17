# Health budget + injury / availability system (lights up the fourth ZenGM budget)

**Status:** Accepted (FIN-04, 2026-06-17)

## Context

[ADR-0027](0027-team-finance-subsystem.md) (FIN-01) shipped three of ZenGM's four
budget categories — **scouting**, **coaching**, **facilities** — and deferred the
**health** category "entirely (FIN-04)", because health is the *one* category with
**no existing seam in our domain**: scouting maps onto LG-05 potential, coaching
onto LG-04 development, facilities onto attendance/revenue — but **we have no
injuries**, so the fourth budget needs a whole new subsystem before it can buy an
edge. FIN-02 (coaching→development) and FIN-03 (scouting→potential) then wired
their respective edges. FIN-04 is the deferred remainder: introduce a minimal
**injury / availability** model and wire the **health** budget level into it.

ZenGM's health model (`player/injury.ts`, `budgetLevels.ts::healthEffect`) rolls
injuries **mid-game** inside the simulation and benches the player for a number of
**future** games; `healthEffect(level)` (`+0.13 … −0.13`, sign-flipped) only
**shortens** injury duration, it does not reduce frequency. That engine
**contradicts this codebase's shape** the same way FIN-01's per-game money stream
did: this app **batch-simulates a 1800-tick Round headless** with no per-game
event stream, and its "game" is a **fixture** (a 2-Round `Match`), not a stream of
minutes — so a mid-game injury has nowhere meaningful to land, and rolling
injuries inside the tick loop would pull a new system into the **SIM-07/08 seed
chain** and force a **Score Calibration re-baseline** (the thing every finance /
development slice has been careful to avoid). This ADR records the decisions that
adapt ZenGM's injury model to the batch-sim, season-fixture, finance-gated world
FIN-04 ships into, resolved in the FIN-04 grilling session (2026-06-17).

A second domain reality drove the design: career-mode **Teams are generated with
exactly 6 Players and no bench** (`_generate_teams(num_teams, 6, …)`), and the
simulator fields `Team.active_roster` (the 6 `slot_*` FKs) with **no automatic
skip of an unavailable slotted Player**. So an injury only *means* anything if the
play loop actively intervenes at field time, and there is usually nobody on the
bench to intervene with.

## Decisions

1. **Injuries roll OUTSIDE the tick loop — no re-baseline.** The injury roll
   fires in the **play-loop orchestration** (`play_season_task` / `play_week` /
   the LG-01i live path), **once per regular-season fixture played**, on a
   **fresh `random.Random()`** that is never the simulator's seed chain. The
   `BatchSimulator` / `simulate_scheduled_round` tick loop is **byte-untouched**,
   so the SIM-07/08 internal-determinism contract holds in form and there is **NO
   Score Calibration re-baseline**. This is the structural mirror of FIN-01's
   "finances are outside the seed chain" posture. The ZenGM "injured during a
   specific game" flavour is the part that does not transfer; the "unavailable for
   the next N games" part — which is what matters — is a between-fixtures concept.

2. **Availability is a decrementing counter on `Player`, reset at rollover.** A
   new `Player.games_unavailable` (`PositiveSmallIntegerField(default=0)`) is the
   ZenGM `injury.gamesRemaining` analogue, and matches the live mutate-in-place
   `Player` field precedent (`salary` / `potential` / the 19 Stats). It is set to
   a drawn N when injured, **decremented by 1 each time the Player's Team plays a
   fixture**, gates slot eligibility while `> 0`, and is **reset to 0 at the
   `next_season` rollover** — injuries heal in the off-season and never accumulate
   across Seasons. This is the *one* place the project's "prefer transient,
   no-migration" default does not hold: the state must survive across the separate
   requests / Celery tasks that play a Season's fixtures, so it genuinely needs
   persistence. No injury-type taxonomy ships — just "out for N more games."

3. **The whole system is gated on `finance_enabled` (+ career `league` mode).**
   On top of CAR-03's `_is_career_league` gate, the injury roll fires only when
   `League.finance_enabled`. A finance-OFF League rolls no injuries,
   `games_unavailable` stays `0` forever, and the League is **byte-identical to
   today** — the load-bearing inertness guarantee shared with FIN-01/02/03. The
   health budget that tunes injuries is meaningless without finance anyway.

4. **An injury always resolves to a valid 6-Player Roster via the Injury policy.**
   A new per-Team `Team.injury_policy ∈ {"auto_sub" (default), "play_hurt"}`
   (`CharField`) chooses what happens when a slotted Player is unavailable at field
   time. **auto_sub** drops the best available replacement into the injured
   Player's slot *for that fixture only* (in-memory, restored after — the LG-02x-1
   `before_round_hook` pattern): the Team's **bench** first, then the **League
   free-agent pool**. **play_hurt** instead fields the injured Player with a
   transient **Stat penalty** (ZenGM's `playThroughInjuries` — "my hurt star still
   beats a healthy scrub"). The **Manager** sets their own Team's policy (on the
   Team Finances screen, the FIN-01 "manager edits own team" precedent); **AI
   Teams keep `auto_sub`** and never change it. **play_hurt is also the universal
   no-sub fallback**: when auto_sub finds no available body anywhere ("sometimes a
   team won't have a sub"), the injured Player fields hurt — so the simulator is
   *never* handed a short (< 6) roster, and the cost of an injury is either a
   weaker fill-in or a debuffed starter (the edge the health budget mitigates).

5. **Health shortens duration only, read from the live current level.** A new
   `finance.health_effect(level)` (the ZenGM `healthEffect` analogue, sign-flipped)
   **scales the drawn injury duration down**; injury **frequency** stays a fixed
   base rate. Unlike FIN-02/03 — which read the games-weighted ≤3-Season smoothed
   average of their budget level *at the rollover* — FIN-04 reads the **current
   live `Team.budget_health` level at fixture time**, because injuries are a
   per-fixture in-season event (a mid-Season budget bump helps immediately, and CPU
   levels are frozen at season start anyway). The level→float map lives in
   `finance.py` (the FIN-02/03 "budget mapping lives in finance, not the consuming
   module" rule); the injury probability / duration / age-curve / play-hurt math
   lives in a new Django-free pure module.

6. **Probability = flat base rate × age factor; no Stat input.** The per-fixture
   per-fielded-Player injury chance is a flat tunable base rate scaled by an **age
   factor** (older Players injure more — `Player.age` is already a first-class,
   LG-04-evolving attribute, giving injuries natural texture and a reason rosters
   age out). The 19 sim **Stats** deliberately do **not** feed it, keeping the
   "Stats are sim weights" line clean.

7. **Regular-season fixtures only; the tournament engine is untouched.** Injuries
   roll only on **regular-season RR-phase fixture play**. Tournaments and
   season-embedded playoffs drain through the tournament engine (kept VERBATIM by
   every Part2c slice), are already non-deterministic, carry `season=NULL`, and
   would heal at rollover anyway; standalone sandbox tournaments have no finance
   gate at all.

8. **The fourth budget lands fully.** Wiring a health *level* requires the level to
   exist, so FIN-04 adds `Team.budget_health` (default `34`, the neutral level) and
   its **health Season expense line** (`level_to_amount(budget_health)`) in
   `finance.py` / `ExpenseLines` / `TeamSeasonFinance`, feeding profit → the money
   axis like the other three budgets. All four ZenGM budgets are now live.

9. **Magic numbers are locked-but-tunable.** The base injury rate, the age curve,
   the duration draw range, the play-hurt Stat-penalty magnitude, and the
   `health_effect` slope are **invented-by-analogy, calibration-deferred** constants
   (the LG-04 age-curve / FIN-01 coefficient precedent) — not pinned by this ADR.

## Considered options

- **Roll injuries inside the tick loop (ZenGM-faithful mid-game).** Rejected: it
  consumes the seeded RNG and forces a Score Calibration re-baseline, and our
  1800-tick Round has no meaningful place for "injured at minute 14 of a season."
  A between-fixtures roll captures the only part that matters (future availability)
  while leaving the simulator byte-identical.

- **Play short (field 5).** Rejected as the cost model: the simulator's six-role
  assumptions (role pairing, MVP, etc.) are unverified for a 5-Player roster, and
  5v6 is a strange competitive model. Auto-sub / play-hurt always yield a valid 6.

- **Auto-substitute only (no play-hurt option).** Rejected: a Manager may rationally
  prefer a hurt star over a weak scrub (ZenGM's `playThroughInjuries`), and the
  no-bench/empty-pool case needs a graceful fallback anyway. Shipping both — with
  play-hurt as the universal fallback — covers every case with one mechanism reused.

- **Read the games-weighted ≤3-Season health level (FIN-02/03 contract).**
  Rejected for FIN-04: that smoothing is a *rollover-time* concept reflecting
  sustained investment for a once-per-Season computation. Injuries are per-fixture
  and in-season; the live current level is the responsive, intuitive input.

- **Injuries in finance-OFF Leagues (decouple from the budget).** Rejected: it
  would bench Players and change seeded season outcomes for Leagues that opted out
  of finance, breaking the FIN-01/02/03 "finance-OFF ⇒ byte-identical" invariant,
  and the health budget that tunes injuries is meaningless without finance.

## Consequences

- **New fields + migrations.** `teams.Player.games_unavailable` +
  `teams.Team.budget_health` + `teams.Team.injury_policy` (one `teams` migration);
  `matches.TeamSeasonFinance.health_cost` (one `matches` migration). **AddField
  only — NO `RunPython` / backfill** (ADR-0004 disposable-data posture); existing
  rows take the defaults (`0` / `34` / `"auto_sub"` / `0.0`).

- **A new Django-free pure injury module** (probability / duration / age-curve /
  play-hurt math, frozen import allowlist, `TestNoDjangoImportsLeaked`), plus
  `finance.health_effect(level)`; `finance.ExpenseLines` / `season_expenses` /
  `compute_team_finance` grow one health line.

- **Play-loop changes only — the simulator entry point is structurally unchanged.**
  The per-fixture roll + decrement + the field-time roster resolution (in-memory
  slot rewrite for auto_sub, in-memory Stat debuff for play_hurt, both restored
  after, never `.save()`d) live in the play loop and a field-time helper. The
  `next_season` rollover gains a `games_unavailable = 0` reset pass.

- **The fourth budget changes finance-ON profit.** Adding the health expense line
  lowers profit for finance-ON Teams (the 4th budget finally costs money), feeding
  the money axis like the other three — expected and consistent.

- **No Score Calibration re-baseline.** Injuries consume no simulator RNG and
  change no simulation mechanic; finance-OFF Leagues are byte-identical.

- **FIN-05 follow-up.** The luxury-tax challenge-mode firing remains deferred.

Decision recorded for FIN-04; [ADR-0027](0027-team-finance-subsystem.md) stays
Accepted (its "health deferred to FIN-04" note is now fulfilled).
