# Team finance subsystem (lights up the dormant owner-mood *money* factor)

**Status:** Accepted (FIN-01, 2026-06-16)

## Context

CAR-02 ([ADR-0026](0026-manager-firing-owner-mood.md)) shipped a ZenGM-faithful
owner-mood firing model with three cumulative factors — *wins*, *playoffs*, and
*money* — but left **money dormant by design**: `OwnerEvaluation.money_delta` /
`money_total` were persisted-but-always-`0.0` columns, exactly as ZenGM returns
`money = 0` when its `budget` feature is disabled. The seam was deliberately
shaped (`_ensure_owner_evaluations` hardcoding `money_delta = 0.0` /
`money_total = 0.0`) so a later slice could light it up **without an
`OwnerEvaluation` migration**. CAR-02's own scope-out named that slice
**FIN-01** and rejected "build the finance subsystem now" because salaries +
team budget + profit accounting is a whole epic.

FIN-01 is that epic. During the FIN-01 grilling session (2026-06-16) the
maintainer confirmed the finance model is modeled faithfully on **ZenGM**
(Basketball/Football/Hockey GM) and supplied five reference docs
(`Screenshots_and_video_examples/finance_system/`): budget **levels** (1–100,
`levelToAmount`), the per-game revenue/expense engine (`writeTeamStats` —
attendance ← hype/pop/price/facilities, then ticket/TV/sponsor/merch revenue
minus payroll + four budget expenses), the salary cap + luxury-tax / min-payroll
machinery, and the owner-mood money axis
(`money = (profit − expectedProfit) / (100 * salaryCapFactor)`,
`expectedProfit = 15 * salaryCapFactor`). The ZenGM engine **contradicts this
codebase's shape** in three ways that this ADR resolves: ZenGM runs finances
**per game** (we batch-sim, no per-game stream), pays players via **multi-year
contracts** (we have no contract model), and varies revenue by **market
population / popRank / per-game gauss noise** (we have no market model and the
finance path must stay outside the RNG seed chain). This ADR records the
decisions that adapt ZenGM's finance model to the batch-sim, season-level,
career-mode world FIN-01 ships into.

## Decisions

1. **Season-level accounting, not per-game.** ZenGM's `writeTeamStats` runs once
   per simulated game; this app batch-sims a whole Season with no per-game money
   hook. FIN-01 computes the entire revenue/expense/profit picture **once per
   completed Season** at the `next_season` rollover, persisting one immutable
   per-`(Team, Season)` `TeamSeasonFinance` snapshot (the `PlayerSeasonRating` /
   `OwnerEvaluation` precedent). ZenGM's **hype** loop is kept (`winp`-driven,
   carried across seasons), but the revenue model is collapsed from a per-game
   attendance stream to a season-level figure.

2. **Salary derived from `overall_rating`, not contracts.** A Player's salary is
   a deterministic cap-scaled function of their current `overall_rating`
   (`finance.salary_for_overall`), recomputed in place on a new
   `Player.salary` field at the same write sites LG-05 recomputes `potential`
   (`_write_baseline_ratings` + `_develop_league_for_new_season`) — mirroring
   LG-04/LG-05's "develop mutates in place; the season row is the audit trail."
   **Payroll** is the sum of a Team's active-roster (6-slot) salaries. There is
   **no contract model, no free agency, no cap space, no multi-year obligation**
   — those stay blocked (`players_trade` / `players_trading_block` keep their
   "needs the salary/contract model" blocker copy; FIN-01 adds salary but not
   contracts).

3. **A single fixed market, deterministic, no RNG.** ZenGM scales attendance by
   market `pop` / `popRank` and adds per-game `gauss` noise. FIN-01 uses a
   **fixed single market** (no population / popRank variance) and the finance
   path **consumes no RNG** — revenue/expense/profit are a pure deterministic
   function of hype, ticket price, budget levels, and payroll. Finances are
   **outside the SIM-07/08 seed chain** and, because no simulation mechanic
   changes, there is **NO Score Calibration re-baseline**. The pure math lives in
   a Django-free `matches/finance.py` (frozen import allowlist
   `dataclasses`/`typing`/`math`/`collections` — no `random`), defended by a
   `TestNoDjangoImportsLeaked` subprocess check (the `owner_mood.py` /
   `development.py` / `season_awards.py` precedent).

4. **Per-League finance toggle (the ZenGM `budget` master switch).** A new
   `League.finance_enabled` BooleanField (default `False`), set at create time,
   gates the whole subsystem **on top of** CAR-03's `_is_career_league` mode
   gate. OFF ⇒ the subsystem is **inert**: `_ensure_team_finances` writes zero
   rows, `Player.salary` stays `None`, every sim team runs on neutral budget
   defaults, and the money axis stays `0.0` — so a finance-OFF League is
   **byte-identical to today** (wins + playoffs sentiment only), and LG-04 /
   LG-05 develop output is unperturbed. This is the load-bearing inertness
   guarantee.

5. **Cost-only budgets this slice (facilities additionally feeds revenue).** The
   three budgets — **scouting / coaching / facilities** (1–100 ZenGM levels,
   `finance.DEFAULT_LEVEL = 34` neutral; **health deferred to FIN-04**) — are
   pure **expense** line-items feeding profit. **Facilities additionally feeds
   the revenue/attendance side** (ZenGM's facilities→attendance link). Scouting &
   coaching buy **no gameplay edge yet** — wiring the coaching budget into LG-04
   development and the scouting budget into LG-05 potential is deferred to
   **FIN-02 / FIN-03** (which promote LG-04's fixed-0 coaching knob and LG-05's
   fixed `DEFAULT_SCOUTING_BUDGET` constant to read these per-team levels).

6. **Luxury tax + min-payroll penalty ship; redistribution + challenge-fire do
   not.** The luxury tax (over a threshold payroll) and the min-payroll penalty
   (under a floor) ship as **expense lines** on `TeamSeasonFinance` and feed
   profit. **Tax redistribution is skipped** (ZenGM splits luxury tax among
   under-cap teams; FIN-01 charges the tax but does not pay it back out). The
   **luxury-tax challenge-mode firing** CAR-02 deferred (precisely because it
   needs an expenses model) **stays deferred to FIN-05** — the term is written,
   no toggle ships.

7. **Locked-but-tunable coefficients.** The locked decisions pin the **money
   formula** (`money_delta = (profit − 15*scf) / (100*scf)`,
   `scf = salary_cap / BASELINE_SALARY_CAP`) and the structural revenue/expense
   lines, but not the concrete dollar coefficients (ticket / national_tv /
   local_tv / sponsor / merch), the `LUXURY_PAYROLL` / `MIN_PAYROLL` magnitudes,
   or the `overall_rating → salary` curve. These are **locked-but-tunable magic
   constants in `finance.py`** (the LG-04 age-curve / LG-05 `POTENTIAL_MAX_SD`
   precedent — invented-by-analogy, calibration-deferred), sized so a typical
   Season's profit lands near the `EXPECTED_PROFIT_BASE = 15` anchor;
   `salaryCapFactor == 1.0` this slice (`SALARY_CAP == BASELINE_SALARY_CAP`).

8. **`cash` carries across seasons.** Like ZenGM's persistent `teamSeason.cash`,
   `Team.cash` accumulates each Season's profit (`team.cash += profit` at the
   finance-ensure pass) and persists across the rollover; `ticket_price` carries
   a non-zero default constant. (`cash` is a bank balance for display + future
   use; it is not yet a spending constraint this slice.)

## Considered options

- **Per-game money stream (ZenGM `writeTeamStats` faithfully).** Rejected: this
  app batch-sims a Season headless with no per-game hook, and a per-game stream
  would have to consume per-game gauss attendance noise — pulling finances into
  the RNG seed chain and forcing a Score Calibration re-baseline. Season-level
  accounting gives the same profit signal the owner-mood axis needs without
  touching the sim.

- **A contract / free-agency salary system.** Rejected: multi-year contracts +
  cap space + free agency + re-signing is a whole epic of its own (the
  `players_trade` / `players_trading_block` blocker copy names it). Salary
  derived deterministically from `overall_rating` gives a payroll figure — all
  the money axis needs — with one nullable field and zero new lifecycle.

- **Full ZenGM economy with population / hype / popRank variance + per-game
  gauss.** Rejected: there is no market model in the schema (no `pop` /
  `popRank`), and the per-game gauss noise would make finances RNG-dependent and
  non-deterministic for a given standings fixture. A **fixed single market +
  deterministic season math** keeps the hype loop (the part that matters for the
  money signal) while staying testable against a fixed profit fixture and
  outside SIM-07/08.

## Consequences

- **New models + migrations.** `teams.Player.salary` + five `teams.Team` finance
  fields (`budget_scouting` / `budget_coaching` / `budget_facilities` neutral
  `34`, `ticket_price`, `cash`) in `teams/migrations/0013_player_salary_team_finance.py`
  (dep `0012_player_potential`); `matches.League.finance_enabled` +
  `matches.TeamSeasonFinance` (one immutable per-`(Team, Season)` snapshot,
  `uniq_team_season_finance`, `team` SET_NULL / `season` CASCADE) in
  `matches/migrations/0050_league_finance_teamseasonfinance.py` (dep
  `0049_ownerevaluation` + the cross-app `teams 0013`). **CreateModel/AddField
  only — NO `RunPython` / backfill** (ADR-0004 disposable-data posture, the
  `0029` / `0048` / `0049` precedent); existing Leagues get no historical rows,
  the lazy writer fills them in Season order on first reach.

- **The money seam wires up without an `OwnerEvaluation` migration.** As CAR-02
  designed, `_ensure_owner_evaluations` simply starts feeding a non-zero
  `money_delta = finance.money_delta(profit)` /
  `money_total = owner_mood.cap_cumulative(running_money, money_delta)` at the
  two hardcoded-`0.0` sites when `league.finance_enabled` — reading the managed
  Team's `TeamSeasonFinance.profit`. The verdict already sums `money` in
  `MoodTotals` / `MoodDeltas`; **no `owner_mood` change**. The rollover order is
  locked: `_ensure_team_finances` runs **before** `_ensure_owner_evaluations`
  (finance rows feed the money axis).

- **FIN-02..05 follow-ups.** Wiring the **coaching** budget into LG-04
  development (FIN-02) and the **scouting** budget into LG-05 potential (FIN-03)
  promotes those slices' fixed knobs to read the per-team levels this ADR ships;
  the **health** budget + injury/availability system (FIN-04) adds the fourth
  ZenGM budget; the **luxury-tax challenge-mode firing** (FIN-05) lights up the
  challenge-fire CAR-02 + this ADR both defer.

- **No Score Calibration re-baseline.** Finances mutate no simulation mechanic
  and consume no RNG; LG-04 / LG-05 develop output is byte-identical with the
  toggle ON or OFF.

- **No CONTEXT.md term churn.** The finance glossary (the `### Finance` section
  — Salary / Budget / Profit / Luxury tax) and the two "money dormant" caveat
  edits were finalised inline at the grill (the CAR-02 precedent), so this slice
  needs no new term.

Decision recorded for FIN-01; CAR-02's [ADR-0026](0026-manager-firing-owner-mood.md)
stays Accepted (the dormant-money column it shipped is now fed, not changed).
