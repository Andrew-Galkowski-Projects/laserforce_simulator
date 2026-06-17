# FIN-04 · Health budget + injury/availability — LOCKED seam contract

The fourth ZenGM budget. A per-Team **health budget** (cost line + ratings edge),
plus an **injury/availability system** that rolls injuries OUTSIDE the tick loop,
resolves rosters in-memory at fixture time (auto_sub or play_hurt), and decrements
a per-Player availability counter once per fixture. Gated on
`_is_career_league(league) AND league.finance_enabled` — OFF ⇒ no rolls, byte-identical
to today. Simulator byte-untouched ⇒ **NO Score Calibration re-baseline**.

Domain terms already written: **Injury** / **Health edge** / **Injury policy**
(`CONTEXT.md`) and `docs/adr/0028-health-budget-injury-availability.md`.

This contract LOCKS names/signatures/insertion points so the Code/Tests/Docs agents
cannot disagree. NO code bodies.

---

## 1. Model fields + migrations (AddField-only, NO RunPython)

### `teams.Player` (`teams/models.py`)
- **`games_unavailable`** — `models.PositiveSmallIntegerField(default=0)`.
  Declared after `salary` (the FIN-01 field). Availability counter: `> 0` ⇒ the
  player is out; decremented 1 per fixture the player's team plays; **reset to 0 at
  `next_season` rollover**. No injury-type taxonomy.

### `teams.Team` (`teams/models.py`)
- **`budget_health`** — `models.PositiveSmallIntegerField(default=34)` (the
  `finance.DEFAULT_LEVEL` neutral level; mirrors `budget_scouting/coaching/facilities`).
  Declared with the other four FIN-01 budget fields.
- **`injury_policy`** — `models.CharField(max_length=16, choices=INJURY_POLICY_CHOICES,
  default="auto_sub")`. Class attr `INJURY_POLICY_CHOICES = (("auto_sub", "Auto-substitute"),
  ("play_hurt", "Play hurt"))`. Manager-editable; AI teams stay `"auto_sub"`.

### `matches.TeamSeasonFinance` (`matches/models.py`)
- **`health_cost`** — `models.FloatField(default=0.0)`. Declared in the expense-line
  block, immediately **after `min_payroll_penalty`** (before the derived `revenue`).

### Migrations
- **`teams/migrations/0014_player_team_health_injury.py`** — dep `0013_player_salary_team_finance`.
  Three `AddField`: `Player.games_unavailable`, `Team.budget_health`, `Team.injury_policy`.
  AddField-only, NO `RunPython`, no backfill (ADR-0004 disposable-data posture).
- **`matches/migrations/0052_teamseasonfinance_health_cost.py`** — dep
  `0051_teamseasonfinance_budget_levels` (+ the cross-app `teams 0014_*` dep is implicit
  via no FK; declare only the matches dep). One `AddField`: `TeamSeasonFinance.health_cost`.
  AddField-only, NO `RunPython`.

---

## 2. Pure module `matches/injury.py` (Django-free)

**Frozen import allowlist:** `dataclasses`, `typing`, `random`, `collections` ONLY —
NO `django.*`, NO ORM, NO `datetime`, NO `math`, NO I/O, NO logging, NO `teams`/`matches`
imports. `random` is allowlisted because the draws consume an **INJECTED** `random.Random`
(production builds a fresh one per fixture, never the SIM-07/08 seed chain). The
level→float **`health_effect`** map lives in `finance.py` (§3), NOT here — `injury.py`
CONSUMES the float. Defended by `matches/tests/test_injury.py::TestNoDjangoImportsLeaked`
(subprocess fresh-import + `sys.modules` walk; the `test_finance.py` / `test_development.py`
precedent — a `SimpleTestCase`).

### Constants (LOCKED-but-tunable; calibration-deferred — invented-by-analogy, the
LG-04 age-curve / FIN-01 magic-number precedent)
- `BASE_INJURY_RATE: float = 0.04` — flat per-fixture base probability a healthy starter
  who fields is injured (before the age factor).
- `AGE_FACTOR_PIVOT: int = 27` — age at which the age factor is 1.0.
- `AGE_FACTOR_PER_YEAR: float = 0.04` — linear age-factor slope per year past the pivot
  (older ⇒ more injury-prone; bounded ≥ a small floor so a very young player is not 0).
- `AGE_FACTOR_MIN: float = 0.5` / `AGE_FACTOR_MAX: float = 2.5` — age-factor clamp.
- `DURATION_BASE_GAMES: float = 3.0` — mean drawn duration (matchdays) before the
  health-edge scale and age have applied.
- `DURATION_MIN_GAMES: int = 1` / `DURATION_MAX_GAMES: int = 12` — drawn-duration clamp.
- `PLAY_HURT_STAT_PENALTY: int = 12` — flat magnitude (points) subtracted from each of the
  injured Player's 19 stat fields when fielded hurt (clamped to `[0, 100]` by the caller).

### Public functions (every signature + return type LOCKED)
- `age_factor(age: int) -> float`
  — `1.0` at `AGE_FACTOR_PIVOT`, linear `AGE_FACTOR_PER_YEAR` each side, clamped
  `[AGE_FACTOR_MIN, AGE_FACTOR_MAX]`. A `None` age never reaches it (the caller coalesces
  `None → 25`, the LG-04 convention). Consumes NO RNG.
- `injury_probability(age: int) -> float`
  — `BASE_INJURY_RATE * age_factor(age)`. **No Stat input** (flat base × age factor only).
  Consumes NO RNG.
- `roll_injury(age: int, rng: random.Random) -> bool`
  — `rng.random() < injury_probability(age)`. Exactly ONE `rng.random()` draw.
- `draw_duration(health_effect: float, age: int, rng: random.Random) -> int`
  — draws a duration in matchdays, scales it DOWN by `health_effect` (frequency is fixed —
  only the duration is health-scaled), clamps to `[DURATION_MIN_GAMES, DURATION_MAX_GAMES]`,
  returns an int ≥ 1. Exactly ONE RNG draw. (`health_effect` is the sign-flipped ZenGM
  analogue float from `finance.health_effect`; a positive edge shortens the draw.)
- `play_hurt_penalty() -> int`
  — returns `PLAY_HURT_STAT_PENALTY` (the per-stat magnitude). Consumes NO RNG.

RNG-consumption order across one starter roll is PINNED: `roll_injury` (1 draw) THEN, only
if injured, `draw_duration` (1 draw) — so seeded tests are deterministic.

---

## 3. `finance.py` additions

- **`health_effect(level: int) -> float`** — NEW pure fn. Sign-flipped ZenGM `healthEffect`
  analogue: the `DEFAULT_LEVEL` (34) neutral → `0.0`, `MAX_LEVEL` (100) → `MAX_HEALTH_EFFECT`,
  level 1 → a negative effect. Reuses `_bound` + `DEFAULT_LEVEL` / `MAX_LEVEL`. Constant
  **`MAX_HEALTH_EFFECT: float = 0.5`** (LOCKED-but-tunable, calibration-deferred). Mirrors
  `coaching_effect` (dual-slope neutral pivot). The level→float mapping lives HERE (the
  FIN-02/03 rule); `injury.py` consumes the float. `finance.py`'s frozen allowlist
  (`dataclasses`/`typing`/`math`/`collections`) is unaffected — plain arithmetic over `_bound`.

- **`ExpenseLines`** (frozen dataclass) gains a trailing **`health: float`** field, appended
  LAST after `min_payroll_penalty` (pinned field order: `payroll, scouting, coaching,
  facilities, luxury_tax, min_payroll_penalty, health`).

- **`season_expenses(...)`** gains a keyword-only `health_level: int` parameter (appended after
  `facilities_level`, before `salary_cap=SALARY_CAP`) and sets
  `health=level_to_amount(health_level, salary_cap)` in the returned `ExpenseLines`.

- **`compute_team_finance(...)`** gains a keyword-only `health_level: int` parameter (appended
  after `facilities_level`), threads it into the `season_expenses(...)` call, and adds
  `expense_lines.health` to the `expenses` sum (the seventh expense line).

- **`TeamFinanceResult`** is UNCHANGED in shape (it already carries `expense_lines`); the new
  `health` expense flows through `expenses` / `profit` automatically.

`finance.level_to_amount(budget_health)` is the health-budget cost (the same per-level dollar
map as the other three budgets). This feeds profit→money like the other budgets — **no money
formula change**.

---

## 4. Play-loop seam — per-fixture injury resolution

### Module-level helper (LOCKED) — `matches/league_views.py`
- **`resolve_injuries_for_fixture(season, team_red, team_blue) -> dict`** —
  the single per-fixture injury resolver. Called by every play path BEFORE
  `simulate_scheduled_round(...)`. First-line guard:
  `if not (_is_career_league(season.league) and season.league.finance_enabled): return {}`
  ⇒ no-op, byte-identical OFF. Returns a **restore token** (the data needed to undo the
  in-memory mutations) — an opaque dict the caller passes to `restore_after_fixture(token)`.
  Operates on the two in-memory Team objects the play loop already holds.

- **`restore_after_fixture(token) -> None`** — undoes every in-memory mutation
  `resolve_injuries_for_fixture` applied (Team `slot_*` FKs and injured-Player 19 stat
  fields), restoring the pre-fixture in-memory state. **NEVER `.save()`** the temporary
  state — the only persisted writes are the `games_unavailable` decrement/set (below),
  done explicitly with `update_fields`.

### Per-fixture sequence (LOCKED order)
For each of the two Teams, per fixture:
1. **GATE** — return `{}` immediately when not `_is_career_league AND finance_enabled`.
2. **Subjects = the 6 active-roster STARTERS only** — the `slot_*` players as they stood
   BEFORE substitution (`team.active_roster` snapshot). Fill-ins (bench / free-agent) are
   NEVER injury subjects (never roll, never tracked) — avoids orphan injuries.
3. **DECREMENT (unavailable starters)** — a starter with `games_unavailable > 0` decrements
   by 1 (whether sub'd out or playing hurt) and does **NOT** re-roll. Persisted via
   `Player.objects.bulk_update(..., ["games_unavailable"])`.
4. **ROLL (healthy starters who FIELD)** — a healthy starter (`games_unavailable == 0`) that
   actually fields rolls a NEW injury via `injury.roll_injury(age, rng)`; on a hit, draws
   `N = injury.draw_duration(health_effect, age, rng)` and sets `games_unavailable = N`
   (persisted). `rng` is a FRESH `random.Random()` per fixture (never the SIM seed chain).
   `health_effect = finance.health_effect(team.budget_health)` read from the **LIVE current
   `Team.budget_health`** at fixture time (NOT the games-weighted ≤3-Season smoothing FIN-02/03
   use).
5. **RESOLVE roster to a valid 6** — for each now-unavailable starter (`games_unavailable > 0`
   after steps 3–4), apply the Team's `injury_policy`:
   - **`auto_sub`** — rewrite the in-memory Team `slot_*` FK to a substitute. Source priority:
     bench (`Team.bench_players`) → League free-agent pool (`League.free_agent_pool`).
     Available = `games_unavailable == 0`. **`play_hurt` is the universal no-sub fallback** ⇒
     when no available sub exists, the injured starter plays hurt (see below) so the injury
     ALWAYS resolves to a valid 6-roster.
   - **`play_hurt`** — rewrite the injured in-memory Player's 19 stat fields down by
     `injury.play_hurt_penalty()` (clamp `[0, 100]`); the starter stays in its slot.
6. The two restore lists (mutated Team `slot_*` FKs; mutated Player stat fields + original
   values) are returned in the token. The caller runs `simulate_scheduled_round(...)` against
   the mutated in-memory state, then `restore_after_fixture(token)`.

**Roster-resolution lives IN THE PLAY LOOP via in-memory mutate-then-restore** — NO simulator
signature change, NO new `before_round_hook` on `simulate_scheduled_round`. `_simulate_and_flush_round`
reads `list(team.active_roster)` off the passed-in Team (confirmed at
`matches/simulation/entrypoints.py:580-581`), so the in-memory `slot_*` / stat rewrites take
effect for that round's sim and are restored after.

### Call sites (LOCKED)
Wrap each `simulate_scheduled_round(...)` call in these THREE paths with
`token = resolve_injuries_for_fixture(season, team_a/red, team_b/blue)` before, and
`restore_after_fixture(token)` after (in a `finally`):
- **`matches/tasks.py::play_season_task`** — the per-fixture loop (~L236).
- **`matches/league_views.py::play_week`** — the per-fixture loop (~L2538).
- **LG-01i live path `matches/league_views.py::play_week_live`** — the RR branch
  `simulate_scheduled_round(...)` call (~L2765). The playoff branch (`play_specific_node` →
  `simulate_match`, tournament) is **UNTOUCHED** (tournaments/playoffs are scope-out).

### `next_season` reset pass (LOCKED)
In **`matches/league_views.py::_develop_league_for_new_season`** (the develop loop already
inside `_run_season_rollover`'s `@transaction.atomic`, over the developing set), each developing
Player gets `player.games_unavailable = 0`, and `"games_unavailable"` is appended to the existing
`Player.objects.bulk_update(players, [...])` field list. Reset is the developing set
(active + bench + free-agent-pool players), inside the existing atomic block.

---

## 5. Effective-level read

Injury frequency/duration reads `finance.health_effect(Team.budget_health)` from the **LIVE
current `Team.budget_health`** at fixture time — **NOT** the games-weighted ≤3-Season smoothing
that FIN-02 (`_coaching_effect_by_team`) and FIN-03 (`_scouting_budget_by_team`) use. (Health
responds to the manager's current setting immediately.) Frequency is a fixed base rate × age;
`health_effect` scales the drawn DURATION down only.

---

## 6. Gating (byte-identical OFF)

`resolve_injuries_for_fixture` AND `_ensure_team_finances`-side health-cost are gated FIRST-LINE
on `_is_career_league(season.league) and season.league.finance_enabled`. OFF (or sandbox /
multiplayer mode) ⇒ no rolls, no roster mutation, no `games_unavailable` change ⇒ byte-identical
to today. The `_ensure_team_finances` writer already early-returns when
`not _is_career_league(league) or not league.finance_enabled`, so the new `health_level=` thread
into `compute_team_finance(...)` + the `"health_cost"` snapshot default are inside that gate.

### `_ensure_team_finances` change (`matches/league_views.py`)
- The `compute_team_finance(...)` call gains `health_level=team.budget_health`.
- The `TeamSeasonFinance.objects.get_or_create(... defaults={...})` block gains
  `"health_cost": result.expense_lines.health`.

---

## 7. UI — Team Finances screen

`matches/league_screens/team_finances.py::team_finances` + `templates/leagues/team_finances.html`
(both already FIN-01-live). Additions:

### Budget-edit POST handler (`team_finances` view)
- Read + clamp `request.POST.get("budget_health")` via the existing `_coerce_level(...)` into
  `team.budget_health`; read `request.POST.get("injury_policy")` (accept only `"auto_sub"` /
  `"play_hurt"`, else keep current) into `team.injury_policy`; add `"budget_health"` and
  `"injury_policy"` to the `team.save(update_fields=[...])` list. Inert when finance OFF or no Team
  (existing branch).
- Context: add `budget_costs["health"] = finance.level_to_amount(team.budget_health)` to the
  existing `budget_costs` dict; add `injury_policy=team.injury_policy`; add `availability` — a
  `list[{name, games_remaining}]` of the Team's players with `games_unavailable > 0`
  (newest-first / by `games_unavailable` desc; `[]` when none).

### Template (`templates/leagues/team_finances.html`) — LOCKED DOM ids
- `team-finances-budget-health` — the `<input>` for `budget_health` (in
  `team-finances-budget-form`, alongside the existing scouting/coaching/facilities inputs).
- `team-finances-injury-policy` — the `<select name="injury_policy">` toggle
  (`auto_sub` / `play_hurt`), in `team-finances-budget-form`.
- `team-finances-availability` — the availability-display container (which players are
  unavailable + games remaining; rendered from `availability`), with a
  `team-finances-availability-empty` notice when the list is empty.

Manager-editable (FIN-01 pattern). The existing FIN-01 DOM ids
(`team-finances-budget-form` / `-budget-scouting` / `-budget-coaching` / `-budget-facilities` /
`-ticket-price` / `-budget-save` / `-disabled-notice` / `-empty-notice` / `-table` /
`-salaries-table` / `-metrics`) are PRESERVED unchanged.

---

## 8. Test boundary

### Pure-unit (no DB)
- `matches/tests/test_injury.py` — `injury.py`: `age_factor` boundaries + clamp;
  `injury_probability` = base × age (no Stat input); `roll_injury` deterministic under a
  seeded `random.Random` (exactly 1 draw); `draw_duration` health-edge-scaled + clamped (exactly
  1 draw); `play_hurt_penalty` returns the magnitude; RNG-consumption order pinned; plus
  `TestNoDjangoImportsLeaked` (subprocess fresh-import, `SimpleTestCase`).
- `matches/tests/test_finance.py` (EXTEND) — `health_effect` mapping (`0.0` at 34,
  `MAX_HEALTH_EFFECT` at 100, negative at 1, `MAX_HEALTH_EFFECT`); `ExpenseLines.health` field;
  `season_expenses` / `compute_team_finance` thread `health_level` and add the seventh expense to
  `expenses` / `profit`.

### DB tests (Django `TestCase`)
- `matches/tests/test_injury_resolution.py` (NEW) — `resolve_injuries_for_fixture`:
  starter-only subject rule (bench/free-agent never roll); roll sets `games_unavailable`; decrement
  1/fixture for an already-unavailable starter (no re-roll); auto_sub rewrites in-memory `slot_*`
  (bench → free-agent priority) and restores after; play_hurt rewrites the 19 stat fields and
  restores after; the universal no-sub → play_hurt fallback always yields a valid 6-roster;
  **never `.save()` the temporary roster** (post-fixture Team `slot_*` + Player stats unchanged in
  DB, only `games_unavailable` persisted); `next_season` rollover reset (`games_unavailable = 0`
  over the developing set); **byte-identical OFF** (finance disabled / sandbox ⇒ zero mutation, zero
  `games_unavailable` change); the health expense line appears in `TeamSeasonFinance.health_cost`
  and flows into `profit`.
- `matches/tests/test_finance_screens.py` (EXTEND) — the `budget_health` + `injury_policy`
  POST writes; the `team-finances-budget-health` / `-injury-policy` / `-availability` DOM ids;
  the availability display lists unavailable players.

**Internal (not asserted-against):** the restore-token dict shape; the in-memory sub-source walk
order beyond the bench→free-agent priority; exact `random.Random()` instance identity.

**Assertion discipline:** schema-level outcomes (roster validity, `games_unavailable` values, DOM
ids, expense-line presence, byte-identical-OFF) — NEVER raw simulated point totals.

---

## 9. Scope-out (verbatim)

tournaments/playoffs untouched, no injury-type taxonomy, no in-sim injuries, no
frequency-from-health, no Stat-driven probability, FIN-05 deferred, NO Score Calibration
re-baseline.
