# FIN-03 seam contract — wire the scouting budget into player potential

Wires a Team's effective **scouting** budget level into LG-05's `compute_potential`
scouting-noise band (better scouting tightens the potential estimate; neglect widens
it) and strength-seeds AI+manager team budgets at League create. Structural **MIRROR**
of the shipped FIN-02 (coaching→development). Finance-OFF leagues keep LG-05's default
`scouting_budget=50` ⇒ **byte-identical to LG-05**. **No migration, no simulator change,
no Score Calibration re-baseline.**

## Invariants (state these up front)

- **NO migration** — `TeamSeasonFinance.budget_scouting` + `.games_played` (models.py
  ~L1696/1699) and `Team.budget_scouting/coaching/facilities` (teams/models.py
  ~L94–96) already exist.
- **`compute_potential` always consumes EXACTLY ONE `rng.gauss` draw** regardless of
  `scouting_budget` (the band width never perturbs the `pot_rng` stream — already true
  in LG-05; FIN-03 only changes the band *value*).
- **Finance-OFF ⇒ byte-identical to LG-05**: `_scouting_budget_by_team` returns `{}` ⇒
  every `.get(tid, development.DEFAULT_SCOUTING_BUDGET)` yields 50 ⇒ unchanged.
- `finance.py` MUST NOT import `development` — `NEUTRAL_SCOUTING_BUDGET = 50.0` just
  *equals* LG-05's `DEFAULT_SCOUTING_BUDGET=50` by value. The frozen import allowlist
  (`dataclasses` / `typing` / `math` / `collections`, **NO django**) is unchanged.
- **Decision record:** extend **ADR-0027** (finance subsystem) with a FIN-03
  consequences note (AI strength-seeding + scouting→potential). **NO new ADR.**
- **CONTEXT.md already updated this session** (Potential pointer CAR-01→FIN-03,
  Budget/Budget-level avoid-lines, new "Scouting estimate edge" term) — Docs agent must
  **NOT** re-edit those.

## Pure mapping — `matches/finance.py`

**NEW** module-level constants (beside FIN-02's `MAX_COACHING_EFFECT` at ~L46):

```python
NEUTRAL_SCOUTING_BUDGET = 50.0
MAX_SCOUTING_BUDGET = 100.0
```

**NEW** pure fn (mirrors `coaching_effect`'s clamp style, **single-slope** — anchored at
`DEFAULT_LEVEL`→neutral and `MAX_LEVEL`→max, NOT FIN-02's dual-slope neutral-pivot):

```python
def scouting_budget(level: int) -> float:
    lvl = _bound(float(level), 1.0, float(MAX_LEVEL))
    return NEUTRAL_SCOUTING_BUDGET + (MAX_SCOUTING_BUDGET - NEUTRAL_SCOUTING_BUDGET) * (
        lvl - DEFAULT_LEVEL
    ) / (MAX_LEVEL - DEFAULT_LEVEL)
```

Reuses FIN-01's `_bound`, `DEFAULT_LEVEL` (34), `MAX_LEVEL` (100). Final value bounded to
`[0, 100]` (the formula already lands inside; clamp inputs via `_bound` on `level`).
Yields **level 1 → 25.0, 34 → 50.0, 100 → 100.0**. **`finance.py` must NOT import
`development`.**

## `compute_potential` — UNCHANGED (LG-05)

`development.compute_potential(stats, age, rng, *, scouting_budget=DEFAULT_SCOUTING_BUDGET)`
(development.py ~L294) and `DEFAULT_SCOUTING_BUDGET=50` (~L250) / `POTENTIAL_MAX_SD=8.0`
are consumed **verbatim**. The band is `sd = POTENTIAL_MAX_SD * (1 - scouting_budget/100)`
(budget 100 ⇒ sd 0 ⇒ tightest; budget 0 ⇒ widest). FIN-03 only *threads a different float*
into the existing keyword arg. **One `rng.gauss` draw, always** (already guaranteed).

## DB helper — `matches/league_views.py`

**NEW** `_scouting_budget_by_team(league, latest_completed) -> dict[int, float]` — the
twin of `_coaching_effect_by_team` (~L622): first-line gate `if not
league.finance_enabled: return {}`; per developing Team (from
`latest_completed.starting_team_ids_json`), the games-weighted mean of `budget_scouting`
over the last ≤3 completed-Season `TeamSeasonFinance` rows
(`sum(row.budget_scouting * row.games_played) / sum(games_played)`; weight 0 / no rows ⇒
the Team's current `Team.budget_scouting`), mapped via `finance.scouting_budget(level)`.
Returns `{team_id: scouting_budget_float}`.

## CHANGED — `_develop_league_for_new_season` (~L661)

Before the per-player loop (alongside the FIN-02 `coaching_by_team = ...` line):

```python
scouting_by_team = _scouting_budget_by_team(league, latest_completed)
```

In the LG-05 `compute_potential(...)` call (~L738), thread the keyword arg:

```python
scouting_budget=scouting_by_team.get(player.team_id, development.DEFAULT_SCOUTING_BUDGET),
```

Finance OFF ⇒ `{}` ⇒ default 50 ⇒ **byte-identical to LG-05**.

## CHANGED — `_write_baseline_ratings` (~L577)

Founding pass — **no completed Season exists**, so do **NOT** call
`_scouting_budget_by_team`. Instead, when `season.league.finance_enabled`, build a
per-team **current-level** band map over the founding teams
`{tid: finance.scouting_budget(team.budget_scouting)}`, and thread
`scouting_budget=band_map.get(p.team_id, development.DEFAULT_SCOUTING_BUDGET)` into the
baseline `compute_potential(...)` call (~L597). Pool players / finance OFF ⇒ default 50.

## NEW — `_seed_team_budgets_by_strength(teams) -> None` (`matches/league_views.py`)

Rank the enrolled teams by mean active-roster `overall_rating` **desc** (tie-break
`team_id` **asc** — matches the existing talent ranking; uses `Team.active_players` +
`Player.overall_rating`). Assign a **rank-linear** budget level across the band
**`[SEED_BUDGET_MIN, SEED_BUDGET_MAX] = [20, 90]`** (rank 0 strongest → 90, rank N-1
weakest → 20; **single team ⇒ `SEED_BUDGET_SINGLE = 55`**; round to int). Set the **SAME**
level on all three `budget_scouting` / `budget_coaching` / `budget_facilities`. Persist via
`Team.objects.bulk_update(teams, ["budget_scouting", "budget_coaching",
"budget_facilities"])`. Seeds **EVERY enrolled team INCLUDING `League.current_team`** (the
manager edits theirs later).

**Call site:** `league_create`, **finance-ON only**, **BEFORE `_write_baseline_ratings`**
(so the baseline reads seeded levels). Seeded **ONCE** at create, frozen forever —
`next_season` carries Team rows forward untouched (**NO re-seed call in next_season**).

Pin the band + single-team fallback as tunable module constants:
`SEED_BUDGET_MIN = 20`, `SEED_BUDGET_MAX = 90`, `SEED_BUDGET_SINGLE = 55`.

## CHANGED vs NEW summary

| Name | Module | Status |
|---|---|---|
| `NEUTRAL_SCOUTING_BUDGET = 50.0`, `MAX_SCOUTING_BUDGET = 100.0` | `matches/finance.py` | NEW const |
| `scouting_budget(level: int) -> float` | `matches/finance.py` | NEW pure fn |
| `_scouting_budget_by_team(league, latest_completed) -> dict[int, float]` | `matches/league_views.py` | NEW DB helper |
| `_seed_team_budgets_by_strength(teams) -> None` | `matches/league_views.py` | NEW DB helper |
| `SEED_BUDGET_MIN/MAX/SINGLE` (20/90/55) | `matches/league_views.py` | NEW const |
| `_develop_league_for_new_season` | `matches/league_views.py` | CHANGED (thread `scouting_budget`) |
| `_write_baseline_ratings` | `matches/league_views.py` | CHANGED (current-level band) |
| `league_create` | `matches/league_views.py` | CHANGED (seed call, finance-ON, before baseline) |
| `compute_potential`, `DEFAULT_SCOUTING_BUDGET`, `POTENTIAL_MAX_SD` | `matches/development.py` | UNCHANGED |

## Test boundary

**Tests assert against:**
- the **pure `finance.scouting_budget` mapping fn** (1→25.0, 34→50.0, 100→100.0; clamp
  below 1 / above 100; in `test_finance.py`).
- `_scouting_budget_by_team` — games-weighted mean over ≤3 completed Seasons + current-level
  fallback; **finance OFF ⇒ `{}`**.
- `_seed_team_budgets_by_strength` — rank-linear [20,90] desc by mean overall (team_id asc
  tiebreak), single-team→55, all three budget fields set equal, `current_team` included.
- **byte-identical-OFF** — finance OFF: no seed, baseline + develop potential rows
  byte-identical to an LG-05/no-finance run; pool players use default 50.
- **AI-budget-frozen-across-rollover** — `next_season` does NOT re-seed; carried Team
  budget rows unchanged.

**Internal (not a test boundary):** the exact `compute_potential` band math (LG-05 owns it),
the per-player loop wiring, the one-gauss-draw guarantee (LG-05 regression already covers it).
