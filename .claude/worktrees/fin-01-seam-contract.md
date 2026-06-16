# FIN-01 — Team finance subsystem · seam contract

Lights up the dormant **money** mood factor in CAR-02's owner-mood firing by adding a per-League team finance economy. Today the money seam (`matches/league_views.py::_ensure_owner_evaluations`, ~L3050/3054) hardcodes `money_delta = 0.0` / `money_total = 0.0`; FIN-01 replaces those with a real computed value, gated by a per-League toggle. Reference model: `Screenshots_and_video_examples/finance_system/` (5 docs).

**Locked posture:** budgets are **cost-only this slice** (facilities additionally feeds revenue; scouting/coaching buy NO gameplay edge yet → FIN-02/03). Salary is **derived from `overall_rating`** (cap-scaled), NOT a contract. Revenue is **season-level + deterministic** (no per-game stream, no RNG/gauss). Toggle OFF ⇒ subsystem inert, money axis byte-identical to today. **Finances consume no RNG, are outside SIM-07/08, NO Score Calibration re-baseline.**

---

## Migration numbers (verified against the tree)

- **teams next = `0013`** — latest is `0012_player_potential.py` (dep `0011_team_is_draw_team`). FIN-01 teams migration: **`teams/migrations/0013_player_salary_team_finance.py`**, dep `("teams", "0012_player_potential")`, ops: `AddField(Player.salary)` then the 5 Team fields. No backfill.
- **matches next = `0050`** — latest is `0049_ownerevaluation.py` (dep `0048_playerseasonrating` + `0012_player_potential`). FIN-01 matches migration: **`matches/migrations/0050_league_finance_teamseasonfinance.py`**, dep `("matches", "0049_ownerevaluation")` + `("teams", "0013_player_salary_team_finance")` (cross-app — `TeamSeasonFinance.team` FKs `teams.Team`), ops: `AddField(League.finance_enabled)` then `CreateModel(TeamSeasonFinance)`. No backfill.

---

## 1 · `teams.Player.salary` (persisted)

Mirrors `Player.potential` exactly (`models.py:239`):

```python
# FIN-01 — derived from overall_rating (cap-scaled) at the finance-ensure
# pass; None for players outside any finance-enabled League flow.
salary = models.FloatField(null=True, blank=True, default=None)
```

Declared immediately after `potential` (line 239). **No backfill.** `Payroll` = sum of a Team's **active-roster** salaries (`Team.active_players`, the 6 slot FKs). Salary is **recomputed** by the finance writer, never edited via a form.

## 2 · `teams.Team` finance fields (persisted)

Five fields on `Team` (declared after `is_draw_team`, `models.py:90`), all neutral defaults so non-finance teams are unaffected:

```python
# FIN-01 — per-Team budget settings (cost-only this slice). Neutral
# defaults: DEFAULT_LEVEL=34, ticket_price/cash at their league baseline.
budget_scouting   = models.PositiveSmallIntegerField(default=34)
budget_coaching   = models.PositiveSmallIntegerField(default=34)
budget_facilities = models.PositiveSmallIntegerField(default=34)
ticket_price      = models.FloatField(default=0.0)
cash              = models.FloatField(default=0.0)
```

The three budgets are ZenGM 1–100 **levels**; `finance.DEFAULT_LEVEL = 34` is the neutral level. `ticket_price` / `cash` are dollar amounts. **No choices, no validators** (the finance toggle + AI defaults keep them in band; the form clamps user input).

> **FLAG (under-specified):** the locked decisions name `ticket_price` and `cash` as Team fields but never pin a non-zero **default** for either, nor a per-Team initial `cash`. Contract pins `default=0.0` for both (neutral) and defers any "starting cash" seeding to the finance-ensure pass (or leaves cash inert this slice — see §4 note on whether `cash` is written). Confirm whether `cash` should carry across seasons (ZenGM does) or stay inert in FIN-01.

## 3 · `matches.League.finance_enabled` + `matches.TeamSeasonFinance` + migration

**`League.finance_enabled`** (declared on `League`, the per-League toggle, set at create-League time):

```python
finance_enabled = models.BooleanField(default=False)
```

OFF ⇒ entire subsystem inert. The toggle is an **additional gate ON TOP of** `_is_career_league(league)` (`mode == "league"`).

**`TeamSeasonFinance`** — one immutable per-`(Team, Season)` snapshot, the persisted revenue/expense/profit/hype record the writer fills and the money axis reads. Declared in `matches/models.py` after `OwnerEvaluation` (it FKs `teams.Team` + `matches.Season`, both defined above it):

```python
class TeamSeasonFinance(models.Model):
    team   = models.ForeignKey("teams.Team",   null=True, blank=True,
                               on_delete=models.SET_NULL,  related_name="season_finances")
    season = models.ForeignKey("matches.Season",
                               on_delete=models.CASCADE,    related_name="team_finances")

    # revenue lines
    ticket      = models.FloatField(default=0.0)
    national_tv = models.FloatField(default=0.0)
    local_tv    = models.FloatField(default=0.0)
    sponsor     = models.FloatField(default=0.0)
    merch       = models.FloatField(default=0.0)
    # expense lines
    payroll             = models.FloatField(default=0.0)
    scouting_cost       = models.FloatField(default=0.0)
    coaching_cost       = models.FloatField(default=0.0)
    facilities_cost     = models.FloatField(default=0.0)
    luxury_tax          = models.FloatField(default=0.0)
    min_payroll_penalty = models.FloatField(default=0.0)
    # derived (persisted for the audit trail + money axis read)
    revenue = models.FloatField(default=0.0)
    expenses = models.FloatField(default=0.0)
    profit  = models.FloatField(default=0.0)
    # carried-across-seasons hype loop
    hype    = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["season_id", "team_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "season"], name="uniq_team_season_finance"
            )
        ]
```

- **`team` on_delete = `SET_NULL`** (recommended + locked) — a deleted Team keeps its finance history (mirrors `OwnerEvaluation.team_managed` / `Match.season_phase`).
- **`season` on_delete = `CASCADE`** (locked) — a deleted Season drops its finance rows (the LG-01 `PlayerSeasonRating` / `OwnerEvaluation` precedent).
- `unique(team, season)` named **`uniq_team_season_finance`**; `Meta.ordering = ["season_id", "team_id"]`.
- **No backfill** (ADR-0004 disposable-data posture; the `0029`/`0048`/`0049` precedent). Existing Leagues get no historical rows — the lazy writer fills them in Season order on first reach.

## 4 · Pure module `matches/finance.py`

**Frozen import allowlist** (the ONLY imports): `dataclasses`, `typing`, `math`, `collections`. NO Django / ORM / `random` / `datetime` / I/O / logging — defended by `matches/tests/test_finance.py::TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk, the `season_awards.py` / `development.py` / `owner_mood.py` precedent).

**Constants (locked-but-tunable magic numbers — the LG-04 age-curve / LG-05 `POTENTIAL_MAX_SD` precedent):**

```python
MAX_LEVEL          = 100
DEFAULT_LEVEL      = 34            # neutral level — facility cost mid-range, effect 0
BUDGET_LEVEL_SCALE = 1.1
BASELINE_SALARY_CAP = 90000.0     # frozen baseline — salaryCapFactor denominator
SALARY_CAP          = 90000.0     # the league cap (== baseline this slice; tunable)
EXPECTED_PROFIT_BASE = 15.0       # expected_profit = 15 * salaryCapFactor
MONEY_DELTA_DIVISOR  = 100.0      # money_delta = (profit - expected) / (100 * salaryCapFactor)
LUXURY_PAYROLL  = ...             # luxury-tax threshold (tunable)
LUXURY_TAX_RATE = 1.5
MIN_PAYROLL     = ...             # min-payroll floor (tunable)
# revenue/merch/sponsor/tv coefficients + caps (the doc-02 table, cap-scaled) — tunable
```

> **FLAG (under-specified):** the locked decisions pin the **money formula** and the structural revenue/expense lines but not the concrete dollar coefficients for ticket / national_tv / local_tv / sponsor / merch, nor the `LUXURY_PAYROLL` / `MIN_PAYROLL` magnitudes, nor the salary derivation curve (overall→dollars). Contract pins them as **locked-but-tunable constants in `finance.py`** (the LG-04 magic-number precedent) and defers exact values to implementation/calibration. Confirm the salary curve shape (linear `overall * k`? cap-fraction?).

**Frozen dataclasses (pinned field order):**

```python
@dataclass(frozen=True)
class RevenueLines:
    ticket: float
    national_tv: float
    local_tv: float
    sponsor: float
    merch: float

@dataclass(frozen=True)
class ExpenseLines:
    payroll: float
    scouting: float
    coaching: float
    facilities: float
    luxury_tax: float
    min_payroll_penalty: float

@dataclass(frozen=True)
class TeamFinanceResult:
    revenue_lines: RevenueLines
    expense_lines: ExpenseLines
    revenue: float       # sum of revenue_lines
    expenses: float      # sum of expense_lines
    profit: float        # revenue - expenses
    hype: float          # next-season hype (winp loop)
    money_delta: float   # the owner-mood money axis input
```

**Pure functions (signatures):**

```python
def level_to_amount(level: int, salary_cap: float = SALARY_CAP) -> float
def salary_for_overall(overall: float, salary_cap: float = SALARY_CAP) -> float
def compute_hype(prev_hype: float, winp: float, winp_old: float) -> float       # hype += 0.01*(winp-0.55) + 0.015*(winp-winp_old), bound [0,1]
def season_revenue(hype: float, ticket_price: float, facilities_level: int,
                   salary_cap: float = SALARY_CAP) -> RevenueLines
def season_expenses(payroll: float, scouting_level: int, coaching_level: int,
                    facilities_level: int, salary_cap: float = SALARY_CAP) -> ExpenseLines
def luxury_tax(payroll: float, salary_cap: float = SALARY_CAP) -> float
def min_payroll_penalty(payroll: float, salary_cap: float = SALARY_CAP) -> float
def season_profit(revenue: float, expenses: float) -> float                     # revenue - expenses
def money_delta(profit: float, salary_cap: float = SALARY_CAP) -> float          # (profit - 15*scf) / (100*scf), scf = cap/baseline

def compute_team_finance(*, payroll: float, scouting_level: int, coaching_level: int,
                         facilities_level: int, ticket_price: float, prev_hype: float,
                         winp: float, winp_old: float,
                         salary_cap: float = SALARY_CAP) -> TeamFinanceResult
```

**`compute_team_finance` is the single entry the writer calls.** The **flat inputs crossing the view↔pure seam are ints/floats/levels ONLY** — never a Django object: `payroll` (float, summed view-side from `Player.salary`), three budget **levels** (int), `ticket_price` (float), `prev_hype` / `winp` / `winp_old` (float), `salary_cap` (float). The pure module never sees a `Team` / `Season` / `League` / ORM type. Money formula (LOCKED): `salaryCapFactor = salary_cap / BASELINE_SALARY_CAP`; `expected_profit = 15 * salaryCapFactor`; `money_delta = (profit − expected_profit) / (100 * salaryCapFactor)`. `money_total` cap-chains through `owner_mood.MOOD_FACTOR_CAP = 1.0` / `cap_cumulative` in the writer (NOT in `finance.py`).

## 5 · Orchestration in `matches/league_views.py`

**NEW lazy+idempotent `_ensure_team_finances(league, up_to_season) -> None`** — the twin of `_ensure_owner_evaluations` (read that function fully, ~L2970–3087, to mirror its structure):
- **First line early-return** when `not _is_career_league(league) or not league.finance_enabled` (the toggle gate ON TOP of the existing mode gate). OFF ⇒ writes zero `TeamSeasonFinance` rows.
- Walks `league.seasons.filter(state="completed", id__lte=up_to_season.id).order_by("id")` oldest→newest (so hype carries across seasons correctly).
- Per Season, per enrolled Team: `get_or_create`-keyed on **`(team, season)`** (idempotent — a present row left untouched, no backfill). Reads the salaries in effect **for the completed Season** (the active-roster `Player.salary` values — recomputed/refreshed for that Team before summing payroll); reads `prev_hype` from the **prior** Season's `TeamSeasonFinance` for that Team (the carried hype), `winp` from `Season._final_standings_for_phase(...)` for that Team, `winp_old` from the prior-Season standings; computes `compute_team_finance(...)`; persists the row (revenue/expense lines, derived totals, `hype`, `profit`).
- **First-season hype seeding:** when no prior `TeamSeasonFinance` snapshot exists for that Team, `prev_hype = 0.0` and `winp_old = 0.5` (the ZenGM new-team default — neutral 0.500 baseline) so `compute_hype` doesn't reward/punish a phantom prior season.

**Salary-write additions** (recompute `Player.salary` from `overall_rating`):
- **`_write_baseline_ratings(season, players)`** (`league_views.py:576`) — append `p.salary = finance.salary_for_overall(p.overall_rating)` inside the existing per-player loop and add `"salary"` to the existing `Player.objects.bulk_update(players, ["potential"])` ⇒ `["potential", "salary"]`. **Gated**: only when `season.league.finance_enabled` (else leave `salary=None`, byte-identical to today).
- **`_develop_league_for_new_season(league, new_season, latest_completed)`** — after development mutates the 19 stats + potential, recompute `player.salary = finance.salary_for_overall(player.overall_rating)` (post-development overall) and append `"salary"` to the existing develop `bulk_update` field list. Same `finance_enabled` gate.

**`_ensure_owner_evaluations` change (the money seam):** at the `money_delta = 0.0` (L3050) and `money_total = 0.0` (L3054) sites, when `league.finance_enabled` read the managed team's `TeamSeasonFinance.profit` for that Season → `money_delta = finance.money_delta(profit)`, then `money_total = owner_mood.cap_cumulative(running_money, money_delta)` (the existing `running_money` thread, L3014/3031/3084, is already wired). When the toggle is OFF (or no finance row exists), keep `money_delta = 0.0` / `money_total = 0.0` exactly as today (byte-identical). The verdict already sums `money` in `MoodTotals` / `MoodDeltas` — no `owner_mood` change.

**Rollover ORDER (LOCKED) in `next_season`** (the verdict gate, `@transaction.atomic`):
1. **`_ensure_team_finances(league, latest_completed)`** (finance rows first — they feed the money axis)
2. **`_ensure_owner_evaluations(league, latest_completed)`** (now reads the finance profit)
3. read the verdict; if fired+unreassigned → redirect to `new_team_picker`; else **`_run_season_rollover`** (which calls `_develop_league_for_new_season` → develop+resalary)

**Call sites:**
- **`next_season`** — insert the `_ensure_team_finances` call immediately **before** the existing `_ensure_owner_evaluations(league, latest_completed)` line (after the 405/404/active-guard/`latest_completed`-resolve steps).
- **`league_create`** — at the existing `current_team` / `_write_baseline_ratings` insertion point (inside the `@transaction.atomic`), the baseline salary write rides on the gated `_write_baseline_ratings` change above; no separate `_ensure_team_finances` call at create (there is no completed Season yet).

> **FLAG (under-specified):** the locked decisions pin payroll = "salaries in effect for the completed Season" but the salaries are recomputed at rollover/develop time and mutated in place on `Player.salary`. There is no per-(Player, Season) salary snapshot, so a completed Season's payroll is read from the **current** `Player.salary` (post any intervening development). Contract accepts this (mirrors LG-04's "develop mutates in place; the `PlayerSeasonRating` row is the audit trail") — but flag that a long gap between sim and finance-ensure could read drifted salaries. If exact per-Season payroll fidelity is required, the `PlayerSeasonRating.overall_rating` snapshot column could derive a snapshot payroll instead (cheaper than a new salary column). Confirm.

## 6 · Create form

`matches.forms.CreateLeagueForm` gains **`finance_enabled`** (appended after the LG-01j `map_pool` field, so the 9-field block becomes 10):

```python
finance_enabled = forms.BooleanField(
    required=False, initial=False, label="Enable team finances",
    widget=forms.CheckboxInput(attrs={"id": "league-create-finance-enabled"}),
)
```

DOM id **`league-create-finance-enabled`**. `league_create` passes `finance_enabled=form.cleaned_data["finance_enabled"]` into `League.objects.create(...)` inside the existing `@transaction.atomic`. Template `templates/leagues/create.html` adds one field row rendering `{{ form.finance_enabled }}` with its label. `next_season` carries nothing forward here (the toggle lives on `League`, not `Season`).

## 7 · UI — Team Finances + League Finances screens (flip LG-01h placeholders live)

Follow the **LG-01z pattern** (`matches/league_screens/team_stats.py` shell; `matches/views.py::_FEATURE_REGISTRY`; `_build_league_sidebar_links`). Two NEW view modules:

- **`matches/league_screens/team_finances.py`** → `def team_finances(request, league_id) -> HttpResponse` — GET (+ a budget-edit POST branch). **Keyed on `current_team`** (the LG-01g resolver chain, default `league.current_team`, optional `?team_id=` override — same as `team_roster`/`team_stats`). Renders the managed Team's `TeamSeasonFinance` history + current budget levels + ticket price + cash + live luxury-tax/min-payroll/payroll figures. The **budget-edit POST handler** writes `Team.budget_scouting/coaching/facilities` + `ticket_price` (clamped `[1, MAX_LEVEL]` for levels) then redirects to the bare URL (the LG-01z `watch_list_toggle` POST-then-redirect precedent); finance OFF ⇒ the edit form is hidden/inert.
- **`matches/league_screens/league_finances.py`** → `def league_finances(request, league_id) -> HttpResponse` — GET-only, the league-wide table (revenue / profit / cash / payroll per enrolled Team, from `TeamSeasonFinance` of the `displayed_season`).

Both re-exported from `matches/league_screens/__init__.py` (`from .team_finances import team_finances`, `from .league_finances import league_finances`, `__all__` append). Both follow the shared LG-01z contract: 405 GET-guard first line, `get_object_or_404(League)`, `request.session["last_league_id"] = league.id`, `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()`, `sidebar_links = _build_league_sidebar_links(league, displayed_season, sidebar_active="finances"|"finances_team")`, empty-state notice when no Season. Finance-disabled League ⇒ a "Finances are disabled for this League" notice (DOM id `*-disabled-notice`) in place of the body.

**URL names + paths** (`matches/league_urls.py`, after the LG-01z live-screen block):
```python
path("<int:league_id>/finances/",      league_screens.league_finances, name="league_finances"),
path("<int:league_id>/team/finances/", league_screens.team_finances,   name="team_finances"),
```
(URL name `league_finances` replaces the placeholder `coming_soon_finances`; `team_finances` replaces `coming_soon_team_finances`.)

**`_FEATURE_REGISTRY` entries removed:** `"league_finances"` (L1870) and `"team_finances"` (L1880) — both flip live, so their blocker entries are deleted. Registry drops by 2 entries. `players_trade` / `players_trading_block` keep their "needs the salary/contract model" blocker copy (FIN-01 adds salary but NOT contracts/cap-space — those stay blocked).

**Sidebar/topbar repoint** (`_build_league_sidebar_links`): the LEAGUE > Finances entry (L1706) `_cs("coming_soon_finances")` → `_cs("league_finances")`; the TEAM > Finances entry (L1717) `_cs("coming_soon_team_finances")` → `_cs("team_finances")`. Because `_build_league_sidebar_links` is the single source for BOTH the LG-01f sidebar AND the LG-01k topbar, both surfaces flip live at once (the LG-01z precedent).

**Template filenames:** `templates/leagues/team_finances.html`, `templates/leagues/league_finances.html` (the `d-flex` + `_partials/league_sidebar.html` shell). **Locked DOM ids:** Team Finances — `team-finances-table` (history), `team-finances-budget-form` / `team-finances-budget-scouting` / `team-finances-budget-coaching` / `team-finances-budget-facilities` / `team-finances-ticket-price` / `team-finances-budget-save` (save button), `team-finances-empty-notice`, `team-finances-disabled-notice`; League Finances — `league-finances-table`, `league-finances-empty-notice`, `league-finances-disabled-notice`.

## 8 · Test boundary

**Pure-unit (against `matches/finance.py`, no DB):** `matches/tests/test_finance.py` — `level_to_amount` / `salary_for_overall` / `compute_hype` (winp loop + bounds) / `season_revenue` / `season_expenses` / `luxury_tax` / `min_payroll_penalty` / `season_profit` / `money_delta` (the locked formula: `(profit − 15*scf)/(100*scf)`), `compute_team_finance` aggregation (lines sum to revenue/expenses, profit = revenue−expenses), and `TestNoDjangoImportsLeaked` (subprocess fresh-import).

**DB / view:**
- `matches/tests/test_team_finance_model.py` — `TeamSeasonFinance` create / `uniq_team_season_finance` rejection / `team` SET_NULL / `season` CASCADE / `Meta.ordering`; `Player.salary` + the 5 `Team` finance field defaults.
- `matches/tests/test_team_finances_writer.py` — `_ensure_team_finances` writes one row per (Team, Season) in Season order, idempotent (re-run writes no duplicate), hype carries across seasons, first-season hype seed (`prev_hype=0.0`, `winp_old=0.5`); salary recompute in `_write_baseline_ratings` / `_develop_league_for_new_season`.
- `matches/tests/test_finance_money_axis.py` — the money-axis integration: with finance ON, `_ensure_owner_evaluations` reads `TeamSeasonFinance.profit` and the eval row's `money_delta` / `money_total` are non-zero and cap-chained; a profitable Season raises money mood, a loss lowers it.
- **Toggle on/off + byte-identical-when-OFF invariant** (`test_finance_toggle.py`): with `finance_enabled=False`, `_ensure_team_finances` writes **zero** rows, `Player.salary` stays `None`, and the `OwnerEvaluation` rows are **byte-identical** to a no-finance run (`money_delta == 0.0`, `money_total == 0.0`, identical `verdict` / `hot_seat_level`) — the load-bearing inertness guarantee.
- `matches/tests/test_create_form_finance.py` — `CreateLeagueForm` accepts `finance_enabled`; a checked POST persists `League.finance_enabled=True`; the `league-create-finance-enabled` DOM id renders.
- `matches/tests/test_finance_screens.py` — `team_finances` / `league_finances` 200 / 405 / 404 / session-write / empty-state / disabled-notice / DOM ids; the budget-edit POST writes `Team.budget_*` + `ticket_price` then redirects; sidebar Finances entries repoint live (no longer `coming_soon_*`); `_FEATURE_REGISTRY` no longer contains `league_finances` / `team_finances`.

Tests assert schema-level outcomes (row counts, field values, `money_delta` sign/magnitude, DOM ids, byte-identical-OFF) — **never** raw simulated point totals (finances consume no RNG but the upstream sims that produce `winp` are non-deterministic; assert on the finance math given a fixed standings/profit fixture).

---

## Under-specified flags (relayed for decision, not invented silently)

1. **`ticket_price` / `cash` defaults + carry-across-seasons** (§2) — pinned `0.0`; confirm whether `cash` should carry like ZenGM or stay inert this slice.
2. **Concrete revenue/expense coefficients + `LUXURY_PAYROLL` / `MIN_PAYROLL` magnitudes + the salary curve shape** (§4) — pinned as locked-but-tunable `finance.py` constants; confirm the overall→salary derivation.
3. **Per-Season payroll fidelity** (§5) — payroll reads current `Player.salary` (mutated in place); confirm whether a per-Season snapshot is required or the in-place value is acceptable (LG-04 precedent).
