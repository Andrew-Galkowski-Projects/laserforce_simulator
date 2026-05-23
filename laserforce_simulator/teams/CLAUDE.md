# teams/

Manages teams, players, and rosters. Serves as the homepage (`/`).

## Models (`teams/models.py`)

**`Team`**: Has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role.

**`Player`**: Belongs to a team and has an assigned role. Carries 19 numeric stats (0–100) used as weights by the simulator:

| Category | Fields |
|----------|--------|
| Awareness | `player_awareness`, `game_awareness`, `resource_awareness` |
| Decision-making | `decision_making` |
| Physical | `positioning`, `stamina`, `speed`, `flexibility`, `adaptability` |
| Team | `communication`, `teamwork` |
| Role | `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`, `resupply_synergy`, `resupply_efficiency`, `accuracy`, `survival`, `special_usage` |

`overall_rating` is a `@property` returning the unweighted mean of all 19 stats.

`stat_for_simulation(stat_name, role)` returns `min(int(raw_value * 1.2), 100)` when `role in self.preferred_roles`, otherwise the raw stat value. Invalid `stat_name` values raise `AttributeError` naturally (no explicit guard). Used by `PlayerRoundState` forwarding properties and `BatchSimulator._make_players` to apply the preferred-role boost at simulation time without affecting stored values or `overall_rating`.

`PlayerForm` exposes all 19 stat fields (default 50) with "Set All to Average (50)" and "Set All to Elite (90)" preset buttons. Profile fields (age, started_playing_age, total_games, home_site, height) are also on the form; when adding a new player, the view pre-fills them via `_random_player_profile()`.

`_random_player_profile()` (in `models.py`) returns a dict of randomised profile values drawn from `teams/constants.py`. Age is 16–50; started_playing_age is 16–age; total_games is 0–5000; height is 4'0"–6'10"; home_site is drawn from `LASERFORCE_SITES`; name is drawn from `PLAYER_NAMES`.

`ROLE_STATS` is imported from `matches.sim_helpers.role_constants` — the canonical source for all role-level constants (`ROLE_STATS`, `MAX_LIVES`, `MAX_SHOTS`, `SPECIAL_COST`). Both `teams/models.py` and `sim_helpers/player_state.py` import from there; the duplicate definition that previously lived in `player_state.py` has been removed.

## Constants (`teams/constants.py`)

Static name pools used by `_random_player_profile()`:

- `PLAYER_NAMES` — ~386 laser-tag codenames drawn from real venue scorecards.
- `LASERFORCE_SITES` — 12 real Laserforce venue locations.

These are imported into `models.py` and re-exported so existing code that does `from teams.models import PLAYER_NAMES` continues to work.

## REST API (`teams/serializers.py`, `teams/api_views.py`)

Read-only DRF endpoints registered under `/api/`:

| Endpoint | Serializer | Notes |
|----------|-----------|-------|
| `GET /api/teams/` | `TeamListSerializer` | Slim — nested players include id/name/preferred_roles only |
| `GET /api/teams/<id>/` | `TeamSerializer` | Full — nested players include all 19 stats |
| `GET /api/players/` | `PlayerSerializer` | Paginated, ordered by team then name |
| `GET /api/players/<id>/` | `PlayerSerializer` | Full player detail |

**Serializer split:** `TeamListSerializer` (list) nests `PlayerInlineSerializer` (id, name, preferred_roles) to keep list payloads small. `TeamSerializer` (detail) nests the full `PlayerSerializer` with explicit stat fields. Both share `_TEAM_BASE_FIELDS` and `_PLAYER_STAT_FIELDS` constants so the field lists are defined once.

**`PlayerInlineSerializer`** — minimal player representation (id, name, preferred_roles) for use anywhere a full player is not needed.

**`PlayerSerializer`** — all 19 stats; explicit field list guarding against accidental exposure of future model fields.

## URLs

```
/           → team list (homepage)
/teams/     → team CRUD, player management

/api/teams/         → TeamViewSet (list, detail)
/api/players/       → PlayerViewSet (list, detail)
```

## HX-01 career stats

A per-player career page aggregating `PlayerRoundState` across every round the player appears in, served at `GET /players/<int:player_id>/stats/` (URL name `player_career_stats`, view `teams/views.py::player_career_stats`). The URL deliberately sits at the flat `/players/<pid>/` root — **not** nested under `/teams/<id>/` — so a future cross-team-history feature does not need to break URLs even though every `Player.team` FK is single-CASCADE today.

**URL include.** A NEW URL file `teams/player_urls.py` (`app_name = None` — explicit; reverse stays the bare `'player_career_stats'`, no namespace prefix) is included from `laserforce_simulator/urls.py` as `path("players/", include("teams.player_urls"))` placed **above** the `path("", include("teams.urls"))` homepage catch-all. **Order matters** — Django resolves top-to-bottom, so the include must sit above the `""` catch-all or the homepage will shadow it.

**Pure aggregation module.** `teams/career_stats.py` is the algorithmic seam — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing`, `collections`, optional `math`, and `SPECIAL_COST` from `matches.sim_helpers.role_constants`). The Tests agent pins this with a "no Django imports leaked" defensive check mirroring the RES-04 / RV-03 precedent. The module's public surface is four functions:

- `summarize(rounds: Iterable[Mapping]) -> dict` — career totals across every round, returning **exactly** six keys `{games, avg_points, tag_ratio, avg_survival_ticks, avg_accuracy_pct, avg_sp_earned}` (empty input ⇒ `games=0` and every other key `0.0`, no division by zero).
- `summarize_by_role(rounds) -> list[dict]` — per-role breakdown, one entry per role **actually played**, in the locked order Commander/Heavy/Scout/Medic/Ammo (roles not played are omitted; empty input ⇒ `[]`).
- `points_trend(rounds, window=10) -> list[list]` — rolling-mean trend of `points_scored`, returning `[[round_idx, mean_points], …]` with `round_idx` 1-based, sorted ascending by `(date_played, game_round_id)` tiebreaker, partial trailing window for rounds 1..9 and full 10-window for rounds 10+. The `list[list]` (not `list[tuple]`) shape makes `json_script` serialisation trivial.
- `rolling_mean(values: list[float], window=10) -> list[float]` — the pure helper used internally by `points_trend`, exported so tests can pin it directly without depending on `points_trend` ordering.

**Formulas (frozen, sum/sum where statistically required).** `Tag ratio` = `sum(tags_made) / max(sum(times_tagged), 1)` — sum/sum, **not** mean-of-per-round-ratios; pinned by `test_tag_ratio_is_sum_over_sum_not_mean_of_ratios` against the deliberately-asymmetric `10/1` vs `0/100` two-round case where mean-of-ratios would yield `5.0` and sum/sum yields ≈ `0.099`. `Avg survival ticks` = `mean(min(was_eliminated_at, 1800))` — the cap is TIME-01's `TICKS_PER_ROUND = 1800`, so `SURVIVED_SENTINEL = 1801` contributes 1800 (the `÷2` tick → second conversion is applied at the **template** layer only via the existing `team_extras.div` filter, TIME-01). `Avg accuracy` = `sum(tags_made) / max(sum(tags_made + shots_missed), 1) × 100`. `Avg SP earned` = `mean(final_special + SPECIAL_COST.get(role, 0) × specials_used)` — the `.get` fallback contributes **0** for Heavy (which has no `SPECIAL_COST` entry), pinned by `test_avg_sp_earned_mixed_roles_includes_heavy_fallback`.

**Round-dict crossing the view ↔ pure-module seam.** The view assembles a list of plain-dict rounds and hands the list to the pure functions; each dict carries **exactly** ten frozen keys `{role, points_scored, tags_made, times_tagged, shots_missed, final_special, specials_used, was_eliminated_at, date_played, game_round_id}` — no extras, no aliases. The pure module never sees a Django model, `PlayerRoundState`, `GameRound`, `select_related`, or any ORM type.

**View.** `player_career_stats(request, player_id)` runs `get_object_or_404(Player, pk=player_id)` (→ 404 on missing) and **exactly one** ORM query — `PlayerRoundState.objects.filter(player=player).select_related("game_round").order_by("game_round__date_played", "game_round_id")` — then assembles the round-dict list, calls `summarize` / `summarize_by_role` / `points_trend`, and renders `templates/teams/player_career_stats.html` with **six** frozen context keys: `player`, `total_rounds`, `career`, `per_role`, `trend`, `has_rounds` (with `has_rounds = total_rounds > 0`). The view is read-only; no `@require_GET` decorator is contracted (Django views accept any method by default and the view is non-destructive).

**Template surface.** `templates/teams/player_career_stats.html` extends `base.html`, `{% load team_extras %}` for the `div` filter, and renders three surfaces gated on `has_rounds`: a 6-column career-totals row (DOM id `career-totals-table`), the per-role table (DOM id `career-per-role-table`, `|title`-cased role labels), and a Chart.js dashed-line rolling-10 trend chart (canvas DOM id `points-trend-chart`, json_script id `trend-data`, dataset label `"Avg points (rolling 10)"`, x-axis title `"Round number"`, y-axis title `"Avg points (rolling 10)"`, `pointRadius: 2`). The empty branch renders a notice (DOM id `career-no-rounds-notice`) containing the substring `"No rounds played yet"` in place of the three surfaces; tests pin via substring match. Formatting is locked: avg points `|floatformat:1`, tag ratio `|floatformat:2`, survival `|div:2|floatformat:0` + `s` suffix (the `div` filter is the same `teams/templatetags/team_extras.py::div` used elsewhere for tick → seconds at the template layer, TIME-01), accuracy `|floatformat:0` + `%`, SP earned `|floatformat:1`, role labels `|title`-cased so `"commander"` renders as `"Commander"`.

**Entry point.** A single `"Career stats"` anchor in `templates/teams/player_detail.html` reversing `{% url 'player_career_stats' player.id %}`. Tests pin via substring `"Career stats"` in the rendered `/teams/<team_id>/player/<player_id>/` response body.

**Determinism / scope.** **Read-only view** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. **No model change, no migration**, no ADR (reversible: pure read-only view + pure aggregation module), no CONTEXT.md edit (the **Tag ratio** term was added inline during the grilling session that produced this contract). Tests live in a NEW `teams/tests/test_career_stats.py` — a pure-unit class for the four pure functions (empty inputs, single-round happy path, sum/sum tag-ratio direction, Heavy `SPECIAL_COST` fallback, `was_eliminated_at=1801` capping to 1800, all-misses accuracy, role ordering Commander/Heavy/Scout/Medic/Ammo, role omission, `rolling_mean` partial-then-full window, `points_trend` `(date, game_round_id)` tiebreaker, and the "no Django imports leaked" defensive check) plus a Django `TestCase` class for the view (200 with rounds + all six context keys, 200 empty state with the `"No rounds played yet"` substring, 404 on missing `player_id`, and the `"Career stats"` link rendered on `/teams/<team_id>/player/<player_id>/`). Locked names (URL, URL name, pure module, public surface, DOM ids, json_script ids, template paths, context keys) are pinned by the seam contract at [`.claude/worktrees/hx-01-seam-contract.md`](../../.claude/worktrees/hx-01-seam-contract.md).

## Tests

`teams/tests/` package — split by concern:
- `test_models.py` — roster validation (FIX-01 coverage)
- `test_serializers.py` — PlayerSerializer, PlayerInlineSerializer, TeamSerializer, TeamListSerializer
- `test_apis.py` — HTTP-level tests for `/api/teams/` and `/api/players/`
- `test_forms.py` — `PlayerForm` stat field completeness (all 19 fields present, defaults to 50) and save behavior
- `test_models.py` — `_random_player_profile()` output validation (keys, value ranges, source lists); `stat_for_simulation` boost logic; roster validation
- `test_career_stats.py` — HX-01 pure-unit tests for `teams/career_stats.py` (4 public functions, empty inputs, sum/sum tag-ratio direction, Heavy `SPECIAL_COST` fallback, `was_eliminated_at=1801` capping to 1800, role ordering, `rolling_mean` partial-then-full window, `points_trend` `(date, game_round_id)` tiebreaker, "no Django imports leaked" defensive check) **plus** Django `TestCase` view tests for `/players/<player_id>/stats/` (200 + 6 context keys, 200 empty state, 404 missing player, "Career stats" link on player detail)