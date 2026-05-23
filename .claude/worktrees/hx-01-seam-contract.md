# HX-01 Seam Contract — Per-player career stats page

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in parallel.
Names below are frozen — do not rename, do not add fields. If reality contradicts
a name here, STOP and flag; do not silently drift.

Branch: `hx-01-player-career-stats` (already checked out).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

---

## 1. New public names (frozen)

| Kind | Name | Location |
|------|------|----------|
| Module | `teams/career_stats.py` (new) | `teams/career_stats.py` |
| Pure function | `summarize(rounds) -> dict` | `teams/career_stats.py` |
| Pure function | `summarize_by_role(rounds) -> list[dict]` | `teams/career_stats.py` |
| Pure function | `points_trend(rounds, window=10) -> list[list]` | `teams/career_stats.py` |
| Pure helper | `rolling_mean(values, window=10) -> list[float]` | `teams/career_stats.py` |
| View | `player_career_stats(request, player_id)` | `teams/views.py` |
| URL name | `player_career_stats` | `teams/player_urls.py` |
| URL include | `path("players/", include("teams.player_urls"))` | `laserforce_simulator/urls.py` |
| Template | `templates/teams/player_career_stats.html` (new) | `templates/teams/player_career_stats.html` |
| Template link | "Career stats" anchor → `{% url 'player_career_stats' player.id %}` | `templates/teams/player_detail.html` |

No model change, no migration, no ADR, no CONTEXT.md edit (Tag ratio was added
inline during the grill), no new dependency.

---

## 2. The pure module seam (MOST IMPORTANT — view ↔ pure-Python boundary)

`teams/career_stats.py` is **pure Python**. The test agent will assert this
explicitly (see §7).

### 2a. Import allowlist (frozen)

The module may import **only** from:

- `typing` (`Iterable`, `Mapping`, etc.)
- `collections` (e.g. `defaultdict`)
- `math` (if `ceil` is needed — optional)
- `matches.sim_helpers.role_constants` — **`SPECIAL_COST` only**

The module must **NOT** import:

- `django.*` (no models, no ORM, no settings, no template engine)
- `random` or any RNG source
- any I/O module (no file I/O, no network)
- any simulator entry point (no `matches.simulation`, no `sim_helpers.simulator`)

This is the same "pure" discipline as RES-04's `cell_occupancy.py` and
RV-03's `pdf_report.py`. The Tests agent pins it with a defensive import
check (see §7.1).

### 2b. Round-dict schema crossing the seam (frozen)

The view assembles a list of round-dicts and passes it to the pure functions.
Each round-dict carries **exactly** these keys (no extras, no aliases):

```python
round_dict = {
    "role":              str,    # PlayerRoundState.role (e.g. "commander", "heavy", "scout", "medic", "ammo")
    "points_scored":     int,    # PlayerRoundState.points_scored
    "tags_made":         int,    # PlayerRoundState.tags_made
    "times_tagged":      int,    # PlayerRoundState.times_tagged
    "shots_missed":      int,    # PlayerRoundState.shots_missed
    "final_special":     int,    # PlayerRoundState.final_special
    "specials_used":     int,    # PlayerRoundState.specials_used
    "was_eliminated_at": int,    # PlayerRoundState.was_eliminated_at (1801 = SURVIVED_SENTINEL)
    "date_played":       datetime | str,  # GameRound.date_played (used for ordering only — opaque to the pure module)
    "game_round_id":     int,    # GameRound.pk (tiebreaker for ordering)
}
```

The pure module **does not** know about Django models, `select_related`,
PlayerRoundState, GameRound, or any ORM type. It consumes plain dicts.

### 2c. `summarize(rounds) -> dict`

```python
def summarize(rounds: Iterable[Mapping]) -> dict:
    """Aggregate a player's career across all rounds.

    Empty input ⇒ {"games": 0, "avg_points": 0.0, "tag_ratio": 0.0,
                   "avg_survival_ticks": 0.0, "avg_accuracy_pct": 0.0,
                   "avg_sp_earned": 0.0}.
    """
```

Returns **exactly** these keys (no more, no fewer):

| Key                   | Type   | Formula |
|-----------------------|--------|---------|
| `games`               | `int`  | `len(rounds)` |
| `avg_points`          | `float`| `sum(points_scored) / games` (mean) |
| `tag_ratio`           | `float`| `sum(tags_made) / max(sum(times_tagged), 1)` (sum/sum, **not** mean of per-round ratios) |
| `avg_survival_ticks`  | `float`| `sum(min(was_eliminated_at, 1800)) / games` |
| `avg_accuracy_pct`    | `float`| `sum(tags_made) / max(sum(tags_made + shots_missed), 1) × 100` (sum/sum) |
| `avg_sp_earned`       | `float`| `sum(final_special + SPECIAL_COST.get(role, 0) × specials_used) / games` |

Edge cases:

- Empty input ⇒ `games=0` and every other key = `0.0` (no division by zero).
- `was_eliminated_at` is capped at **1800** in the mean (so `SURVIVED_SENTINEL = 1801`
  contributes 1800, not 1801). TIME-01's `TICKS_PER_ROUND = 1800` is the cap.
- `SPECIAL_COST.get(role, 0)` — Heavy has no SP cost entry; the fallback
  ensures Heavy contributes `final_special` only to `avg_sp_earned`.

### 2d. `summarize_by_role(rounds) -> list[dict]`

```python
def summarize_by_role(rounds: Iterable[Mapping]) -> list[dict]:
    """Per-role breakdown. One entry per role ACTUALLY PLAYED."""
```

Returns a list of dicts, one per role the player has actually played
(roles not played are omitted). Each dict has **exactly** these keys, in this
key order:

```python
{
    "role":               str,
    "games":              int,
    "avg_points":         float,
    "tag_ratio":          float,
    "avg_survival_ticks": float,
    "avg_accuracy_pct":   float,
    "avg_sp_earned":      float,
}
```

Formulas are the same as `summarize`, applied per-role subset.

**Ordering:** Commander, Heavy, Scout, Medic, Ammo. Roles not played by this
player are simply absent from the list. Empty input ⇒ `[]`.

The string values used for role comparison match `PlayerRoundState.role` —
lowercase: `"commander"`, `"heavy"`, `"scout"`, `"medic"`, `"ammo"`.

### 2e. `points_trend(rounds, window=10) -> list[list]`

```python
def points_trend(rounds: Iterable[Mapping], window: int = 10) -> list[list]:
    """Rolling-mean trend of points_scored over time.

    Returns [[round_idx, mean_points], ...] with round_idx 1-based.
    Rounds are sorted ascending by (date_played, game_round_id).
    Partial trailing window for rounds 1..window-1.
    """
```

- Output is a `list[list]` (not `list[tuple]`) so JSON-serialisation is trivial
  for the template `json_script` filter.
- Inner list: `[round_idx: int, mean_points: float]`.
- `round_idx` is **1-based**.
- Sort order: ascending by `date_played`, then ascending by `game_round_id`
  as tiebreaker. The Tests agent pins the tiebreaker (see §7.1).
- Rolling window: for round N, the mean is computed over rounds
  `1..min(N, window)`. So rounds 1..9 use a partial trailing window; rounds
  10+ use a full 10-round window.
- Empty input ⇒ `[]`.

### 2f. `rolling_mean(values, window=10) -> list[float]`

```python
def rolling_mean(values: list[float], window: int = 10) -> list[float]:
    """Trailing rolling mean with partial window for the first window-1 entries.

    Empty input ⇒ [].
    For 1 <= i < window: mean of values[:i+1] (partial).
    For i >= window:     mean of values[i-window+1 : i+1] (full).
    """
```

A pure helper used internally by `points_trend`. Exported so the Tests agent
can pin it directly without depending on `points_trend` ordering.

---

## 3. View contract (`player_career_stats` in `teams/views.py`)

```python
def player_career_stats(request, player_id: int):
    """Render a player's career stats page (HX-01)."""
    player = get_object_or_404(Player, pk=player_id)

    states = (
        PlayerRoundState.objects
        .filter(player=player)
        .select_related("game_round")
        .order_by("game_round__date_played", "game_round_id")
    )

    rounds = [
        {
            "role":              s.role,
            "points_scored":     s.points_scored,
            "tags_made":         s.tags_made,
            "times_tagged":      s.times_tagged,
            "shots_missed":      s.shots_missed,
            "final_special":     s.final_special,
            "specials_used":     s.specials_used,
            "was_eliminated_at": s.was_eliminated_at,
            "date_played":       s.game_round.date_played,
            "game_round_id":     s.game_round_id,
        }
        for s in states
    ]

    career   = summarize(rounds)
    per_role = summarize_by_role(rounds)
    trend    = points_trend(rounds)
    total_rounds = career["games"]

    context = {
        "player":       player,
        "total_rounds": total_rounds,
        "career":       career,
        "per_role":     per_role,
        "trend":        trend,
        "has_rounds":   total_rounds > 0,
    }
    return render(request, "teams/player_career_stats.html", context)
```

- `get_object_or_404(Player, pk=player_id)` → 404 on missing.
- Exactly **one** ORM query — `PlayerRoundState.objects.filter(player=player).select_related("game_round").order_by("game_round__date_played", "game_round_id")`.
- GET only is implicit; this is a read-only Django view — **no method check**
  is required (Django views accept any method by default; the contract here
  is "GET only" only in the sense that the view does nothing destructive).
- Context keys are **frozen**: `player`, `total_rounds`, `career`, `per_role`,
  `trend`, `has_rounds`. `has_rounds = total_rounds > 0`.
- The view owns the round-dict assembly. The pure module never sees a Django
  object.

---

## 4. URL contract

### 4.1. New file: `teams/player_urls.py`

```python
from django.urls import path

from . import views

app_name = None  # use the global url namespace; reverse via 'player_career_stats'

urlpatterns = [
    path(
        "<int:player_id>/stats/",
        views.player_career_stats,
        name="player_career_stats",
    ),
]
```

`app_name = None` is explicit — the contract reverses via `{% url 'player_career_stats' player.id %}`
without an `app_name:` prefix. Do **not** add `app_name = "players"` or similar.

### 4.2. Edit `laserforce_simulator/urls.py`

Add the `players/` include **ABOVE** the homepage catch-all
`path("", include("teams.urls"))` so the homepage shadow does not eat it.
The order matters — Django resolves top-to-bottom:

```python
urlpatterns = [
    # ... existing entries ...
    path("players/", include("teams.player_urls")),  # ← NEW: must be above the "" include
    path("",         include("teams.urls")),         # existing homepage catch-all
]
```

Full URL: `/players/<int:player_id>/stats/`.

---

## 5. Template contract (`templates/teams/player_career_stats.html`)

New template. The contract pins **DOM IDs, json_script ids, copy substrings,
and block structure**.

### 5.1. Wireframe

```django
{% extends "base.html" %}
{% load static %}
{% load team_extras %}  {# for the existing ÷2 tick→second filter (TIME-01) #}

{% block title %}Career Stats - {{ player.name }}{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>{{ player.name }} <small class="text-muted">— {{ player.team.name }}</small></h1>
    <p>Total rounds: <strong>{{ total_rounds }}</strong></p>

    {% if not has_rounds %}
        <div class="alert alert-info" id="career-no-rounds-notice">
            No rounds played yet.
        </div>
    {% else %}
        {# Career totals row — 6 metrics #}
        <table class="table" id="career-totals-table">
            <thead>
                <tr>
                    <th>Games</th>
                    <th>Avg points</th>
                    <th>Tag ratio</th>
                    <th>Avg survival</th>
                    <th>Avg accuracy</th>
                    <th>Avg SP earned</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>{{ career.games }}</td>
                    <td>{{ career.avg_points|floatformat:1 }}</td>
                    <td>{{ career.tag_ratio|floatformat:2 }}</td>
                    <td>{{ career.avg_survival_ticks|div:2|floatformat:0 }}s</td>
                    <td>{{ career.avg_accuracy_pct|floatformat:0 }}%</td>
                    <td>{{ career.avg_sp_earned|floatformat:1 }}</td>
                </tr>
            </tbody>
        </table>

        {# Per-role table — only roles actually played #}
        <table class="table" id="career-per-role-table">
            <thead>
                <tr>
                    <th>Role</th>
                    <th>Games</th>
                    <th>Avg points</th>
                    <th>Tag ratio</th>
                    <th>Avg survival</th>
                    <th>Avg accuracy</th>
                    <th>Avg SP earned</th>
                </tr>
            </thead>
            <tbody>
                {% for row in per_role %}
                <tr>
                    <td>{{ row.role|title }}</td>
                    <td>{{ row.games }}</td>
                    <td>{{ row.avg_points|floatformat:1 }}</td>
                    <td>{{ row.tag_ratio|floatformat:2 }}</td>
                    <td>{{ row.avg_survival_ticks|div:2|floatformat:0 }}s</td>
                    <td>{{ row.avg_accuracy_pct|floatformat:0 }}%</td>
                    <td>{{ row.avg_sp_earned|floatformat:1 }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        {# Trend chart (Chart.js) #}
        <canvas id="points-trend-chart"></canvas>
        {{ trend|json_script:"trend-data" }}
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            (function () {
                const trend = JSON.parse(document.getElementById("trend-data").textContent);
                const ctx = document.getElementById("points-trend-chart").getContext("2d");
                new Chart(ctx, {
                    type: "line",
                    data: {
                        labels:   trend.map(p => p[0]),
                        datasets: [{
                            label:       "Avg points (rolling 10)",
                            data:        trend.map(p => p[1]),
                            borderDash:  [6, 4],
                            pointRadius: 2,
                        }],
                    },
                    options: {
                        scales: {
                            x: { title: { display: true, text: "Round number" } },
                            y: { title: { display: true, text: "Avg points (rolling 10)" } },
                        },
                    },
                });
            })();
        </script>
    {% endif %}
</div>
{% endblock %}
```

### 5.2. Locked DOM IDs and copy substrings

| Element / data                              | Locked value                              |
|---------------------------------------------|-------------------------------------------|
| Trend chart canvas DOM id                   | `points-trend-chart`                      |
| Trend data `json_script` id                 | `trend-data`                              |
| Career totals table id (recommended)        | `career-totals-table`                     |
| Per-role table id (recommended)             | `career-per-role-table`                   |
| Empty-state notice id (recommended)         | `career-no-rounds-notice`                 |
| Empty-state copy (substring assertion)      | `"No rounds played yet"`                  |
| Trend-line dataset label (y-axis ties to it)| `"Avg points (rolling 10)"`               |
| X-axis title                                | `"Round number"`                          |
| Y-axis title                                | `"Avg points (rolling 10)"`               |
| Trend-line style                            | dashed, `pointRadius: 2`                  |

### 5.3. Formatting rules (locked)

- Survival cell: ticks are converted to seconds with the existing `div` filter from `teams/templatetags/team_extras.py` (`{{ avg_survival_ticks|div:2|floatformat:0 }}s`). The `div` filter is the same one used elsewhere in the codebase for tick → seconds conversion at the template layer (TIME-01). The `|floatformat:0` rounds the seconds value to an integer.
- SP earned: rounded to **1 decimal** (`|floatformat:1`).
- Tag ratio: rounded to **2 decimals** (`|floatformat:2`).
- Accuracy: integer percent (`|floatformat:0` then `%`).
- Avg points: rounded to 1 decimal (`|floatformat:1`).
- Role label: title-cased (`|title`) so `"commander"` renders as `"Commander"`.

### 5.4. Empty state

When `has_rounds is False`, the template renders the notice containing the
substring `"No rounds played yet"` **in place of** the totals table, per-role
table, and trend chart. The Tests agent pins via substring match.

---

## 6. Entry point edit — `templates/teams/player_detail.html`

Add a single anchor in the player-header block:

```django
<a href="{% url 'player_career_stats' player.id %}">Career stats</a>
```

The exact location within the header is the Code agent's call. The Tests
agent pins via substring `"Career stats"` in the rendered
`/teams/<id>/player/<pid>/` response body.

---

## 7. Test boundary (frozen — Tests agent reads this section)

All HX-01 tests live in a **single new file**:
`teams/tests/test_career_stats.py`.

This file contains both the pure-unit cases (no DB) and the DB/view cases
(Django `TestCase`). The pure-unit cases must not touch the DB and must not
import anything from `teams.views` or other Django modules in their assertion
paths — they import `teams.career_stats` only.

### 7.1. Pure-unit cases (no DB, hand-built dict fixtures)

Each gets its own method. Class name suggestion: `TestCareerStatsPure`.

1. **`test_summarize_empty_input`** — `summarize([])` returns
   `{"games": 0, "avg_points": 0.0, "tag_ratio": 0.0,
   "avg_survival_ticks": 0.0, "avg_accuracy_pct": 0.0, "avg_sp_earned": 0.0}`.
2. **`test_points_trend_empty_input`** — `points_trend([])` returns `[]`.
3. **`test_summarize_by_role_empty_input`** — `summarize_by_role([])`
   returns `[]`.
4. **`test_summarize_single_round_happy_path`** — one hand-built round-dict;
   assert every key in the returned dict matches the locked formula.
5. **`test_tag_ratio_is_sum_over_sum_not_mean_of_ratios`** — Two rounds:
   round A `tags_made=10, times_tagged=1`; round B `tags_made=0, times_tagged=100`.
   Correct sum/sum tag_ratio = `10 / 101 ≈ 0.099`. A mean-of-per-round-ratios
   computation would yield `(10/1 + 0/100) / 2 = 5.0`. **Assert the result is
   ≈ 0.099, NOT 5.0.** This pins the formula direction.
6. **`test_avg_sp_earned_mixed_roles_includes_heavy_fallback`** — Three
   rounds: one Scout (`SPECIAL_COST["scout"] > 0`, `specials_used > 0`,
   `final_special` set), one Heavy (`SPECIAL_COST.get("heavy", 0) == 0`,
   `specials_used > 0`, `final_special` set), one Medic. Heavy round must
   contribute `final_special` only (its `SPECIAL_COST` fallback is 0).
   Assert the result equals the manually-computed mean.
7. **`test_survival_capping_at_1800`** — A single round with
   `was_eliminated_at = 1801` (SURVIVED_SENTINEL). Assert
   `avg_survival_ticks == 1800.0` (capped, NOT 1801).
8. **`test_accuracy_with_all_misses`** — One round: `tags_made=0,
   shots_missed=10`. Assert `avg_accuracy_pct == 0.0` (the `max(..., 1)`
   denominator floor prevents NaN/inf but the numerator is 0).
9. **`test_summarize_by_role_order_commander_heavy_scout_medic_ammo`** —
   Five rounds, one per role, deliberately fed in a shuffled order. Assert
   the returned list's `[r["role"] for r in result]` equals
   `["commander", "heavy", "scout", "medic", "ammo"]`.
10. **`test_summarize_by_role_omits_roles_not_played`** — Two rounds (Scout,
    Medic). Assert the returned list has exactly 2 entries and the role keys
    are `["scout", "medic"]` (in that locked order — Scout comes before Medic).
11. **`test_rolling_mean_partial_window_for_first_nine`** —
    `rolling_mean([1, 2, 3], window=10)` returns `[1.0, 1.5, 2.0]` (partial
    trailing window for i=0,1,2).
12. **`test_rolling_mean_full_window_at_ten_plus`** — Construct a list of 12
    values; assert the 10th element of the result is the mean of values 0..9,
    the 11th is the mean of values 1..10, etc.
13. **`test_rolling_mean_empty_input`** — `rolling_mean([], window=10)`
    returns `[]`.
14. **`test_points_trend_ordering_ties_broken_by_game_round_id`** — Two
    rounds with **identical** `date_played` but `game_round_id=2` and
    `game_round_id=5`. Assert the result orders `(date, 2)` before
    `(date, 5)` — i.e. the lower `game_round_id` is round index 1.
15. **`test_no_django_imports_leaked`** (defensive — pins the "pure" contract
    mirroring RES-04 / RV-03):
    ```python
    import teams.career_stats as m
    assert not hasattr(m, "django")
    assert not hasattr(m, "models")
    # And the module imports cleanly without django.setup():
    import importlib, sys
    sys.modules.pop("teams.career_stats", None)
    importlib.import_module("teams.career_stats")
    ```
    The exact assertion shape is the Tests agent's call; the contract is
    "the module has no `django` / `models` attribute and imports cleanly
    without Django setup."

### 7.2. DB/view cases (full Django `TestCase`)

Same file (`teams/tests/test_career_stats.py`). Class name suggestion:
`TestPlayerCareerStatsView`.

1. **`test_player_career_stats_view_200_with_rounds`** — Create a Player
   with at least two `PlayerRoundState` rows on real `GameRound`s. GET
   `reverse("player_career_stats", args=[player.id])` → `200`. Assert all
   six context keys present: `player`, `total_rounds`, `career`, `per_role`,
   `trend`, `has_rounds`. Assert `has_rounds is True`.
2. **`test_player_career_stats_view_200_empty_state`** — Create a Player
   with zero rounds. GET → `200`, `has_rounds is False`, response body
   contains the substring `"No rounds played yet"`.
3. **`test_player_career_stats_view_404_for_missing_player`** — GET on a
   bogus `player_id` → `404`.
4. **`test_career_stats_link_rendered_on_player_detail_page`** — GET the
   existing `/teams/<team_id>/player/<player_id>/` page for a real player;
   assert response body contains the substring `"Career stats"`.

### 7.3. Files the Tests agent edits

| File | Action |
|------|--------|
| `teams/tests/test_career_stats.py` | NEW — both pure-unit and view cases |

No other test files are touched. `teams/tests.py` may exist but HX-01 tests
live in the new module.

---

## 8. File ownership (who edits what)

| File | Code | Tests | Docs |
|------|:----:|:-----:|:----:|
| `teams/career_stats.py` (new pure module) | OWN | — | — |
| `teams/views.py` (add `player_career_stats`) | OWN | — | — |
| `teams/player_urls.py` (new URL file) | OWN | — | — |
| `laserforce_simulator/urls.py` (one `include`, order matters) | OWN | — | — |
| `templates/teams/player_career_stats.html` (new) | OWN | — | — |
| `templates/teams/player_detail.html` (add one link) | OWN | — | — |
| `teams/tests/test_career_stats.py` (new) | — | OWN | — |
| `teams/CLAUDE.md` (HX-01 subsection / model surface notes) | — | — | OWN |
| `PLAN.md` (mark HX-01 done) | — | — | OWN |

Tests agent: the pure-unit test cases import **only** `teams.career_stats`
and stdlib. The view/DB test cases use Django's `TestCase` + the test
client.

---

## 9. Determinism / scope notes

- **Read-only view.** No RNG, no simulation, no `_flush_to_db` touch.
- **No SIM-07 / SIM-08 interaction.** HX-01 reads pre-computed
  `PlayerRoundState` rows; it does not run the simulator and does not
  consume any RNG.
- **No Score Calibration re-baseline.** HX-01 runs no simulation; existing
  Score Calibration baselines are unaffected.

---

## 10. Out of scope (do NOT add)

- ❌ No migration, no model change.
- ❌ No ADR.
- ❌ No CONTEXT.md edit (Tag ratio was added inline during the grilling
  session that produced this contract).
- ❌ No `is_simulated` toggle / filter.
- ❌ No per-Match (round 1 vs round 2) filter.
- ❌ No API / JSON endpoint (the view renders HTML; the Chart.js data is
  inlined via `json_script`).
- ❌ No JS unit tests (template behaviour is exercised via the Django view
  tests' substring checks).
- ❌ No rolling-window-size configuration (window is hard-coded to 10 with
  the `window=10` default).
- ❌ No backfill or data migration.
- ❌ No global `app_name` on `teams/player_urls.py` (kept `None` so reverse
  works with the bare `'player_career_stats'` name).

---

## 11. Quick-reference name table

| Slot                                | Name                                                         |
|-------------------------------------|--------------------------------------------------------------|
| Pure module                         | `teams/career_stats.py`                                      |
| Pure function — overall summary     | `summarize(rounds) -> dict`                                  |
| Pure function — per-role breakdown  | `summarize_by_role(rounds) -> list[dict]`                    |
| Pure function — trend series        | `points_trend(rounds, window=10) -> list[list]`              |
| Pure helper — rolling mean          | `rolling_mean(values, window=10) -> list[float]`             |
| Role-cost dict consumed             | `SPECIAL_COST` from `matches.sim_helpers.role_constants`     |
| Survival cap (ticks)                | 1800 (`TICKS_PER_ROUND`); 1801 = `SURVIVED_SENTINEL`         |
| View (Django)                       | `teams.views.player_career_stats`                            |
| URL name                            | `player_career_stats`                                        |
| URL path                            | `/players/<int:player_id>/stats/`                            |
| URL file (new)                      | `teams/player_urls.py`                                       |
| URL include in root urls.py         | `path("players/", include("teams.player_urls"))` (above `""`)|
| Template (new)                      | `templates/teams/player_career_stats.html`                   |
| Template link added                 | `templates/teams/player_detail.html` ("Career stats" anchor) |
| Trend canvas DOM id                 | `points-trend-chart`                                         |
| Trend `json_script` id              | `trend-data`                                                 |
| Empty-state copy (substring)        | `"No rounds played yet"`                                     |
| Header link copy (substring)        | `"Career stats"`                                             |
| Test file (new)                     | `teams/tests/test_career_stats.py`                           |
| Template filter library | `team_extras` (`{% load team_extras %}`); `div` filter for ÷2 |
