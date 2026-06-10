# LG-03 — SEAM CONTRACT (Season-end awards)

**Season-end awards** — a per-Season awards page (6 category awards + 2 headline
awards), the two headline awards (**Season MVP** / **Finals MVP**), the two LG-01f
League-History columns, and the LG-06h player-page badge. All awards computed over
the Season's **completed regular-season Rounds** (the playoff is a separate phase;
only Finals MVP reads the playoff). Read-only over completed data; **no simulator
change, no re-baseline, no ADR**. Branch `lg-03-season-end-awards`.

**Single source of truth** for three parallel agents (code / tests / docs). Pins
every name / signature / dict-key / DOM-id / URL / migration they share.

Constraints (locked, whole slice):
- **ONE migration:** a single `AddField(Season.season_awards_json)`, NO `RunPython`,
  NO backfill (ADR-0004 disposable-data posture).
- **NO simulator / engine change** — awards read persisted `PlayerRoundState` +
  `GameEvent` rows; consumes **no RNG**.
- **NO Score Calibration re-baseline.**
- **NO ADR** (JSONField cache follows the `highlights_json` / `starting_team_ids_json`
  precedent; fully reversible). **NO CONTEXT.md edit** — the glossary already carries
  `### Awards` (**Season award** / **Season MVP** / **Finals MVP**); stay consistent.
- **NO SIM-13 work** — the dead PRS nuke counters
  (`medic_lives_removed_from_nuke` / `lives_lost_to_nukes`) are NOT touched; Most
  Efficient Nuke is derived from the `GameEvent` log.
- **NO sidebar / topbar entry** — do NOT touch `_build_league_sidebar_links` or any
  LG-01f/h/k nav machinery. Entry points are the Season dashboard + League History row
  only.
- **NO global career-page badge** — only the LG-06h `league-player-awards-stub`.

---

## 0. Design sub-decisions — MADE & LOCKED (with one-line justification)

1. **`games` floor definition** = **the max Rounds any single player played in the
   Season's completed regular-season Rounds** (`max(row.games for row in agg)`, `0`
   when no rounds). Justification: it is derivable from the SAME per-round dict corpus
   the awards already aggregate (no extra query / no schedule re-derivation), and it
   is the natural "a full participant played this many games" denominator; `⌈games/2⌉`
   is then the half-season eligibility bar. Empty corpus ⇒ `games == 0` ⇒
   `⌈0/2⌉ == 0` ⇒ floor admits everyone (vacuous — there are no players anyway).
2. **Eager warm = NO (lazy-only).** Awards compute on first render of a `completed`
   Season (cache miss → compute → write → save). `Season.complete_if_finished` is
   NOT modified. Justification: avoids coupling the completion path to the awards
   module + keeps the simulator/play path RNG-free and untouched; the first dashboard
   / history / player-page view that needs awards warms the cache.
3. **Cached JSON shape = a dict keyed by category** (round-trips `AwardWinner`), see §3.
   Justification: keyed-by-category makes the per-category template lookup + the
   headline `season_mvp` / `finals_mvp` reads O(1) and self-documenting vs a flat list.
4. **`season_awards` view location = a new module
   `matches/league_screens/season_awards.py`**, re-exported from
   `matches/league_screens/__init__.py` and routed from `matches/season_urls.py`.
   Justification: the LG-01z `league_screens` package is the established home for
   read-only screen views; matches the prevailing pattern.
5. **Cache read/write site = a `Season` method `Season.get_or_compute_awards()`** (the
   ORM/I-O wrapper) that the view calls. Justification: keeps the ORM scan + the
   pure-module call + the JSON serialise/deserialise + the cache write in ONE place
   reused by the awards view, the LG-01f history row, AND the LG-06h badge — three
   consumers, one chokepoint. It consumes **no RNG**.

---

## 1. Pure module `matches/season_awards.py` (NEW)

**Frozen import allowlist:** `dataclasses`, `typing`, `math`, `collections` ONLY —
**NO** Django, NO ORM, NO `random`, NO `datetime`, NO I/O, NO logging. Defended by
`TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk, the
`matches/standings.py` / `matches/stat_feats.py` precedent).

### 1.1 Category vocabulary (LOCKED string keys + labels)

```python
AWARD_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("most_points",      "Most Points"),
    ("tag_ratio",        "Highest Tag Ratio by Role"),
    ("most_resupplies",  "Most Resupplies"),
    ("longest_survival", "Longest Survival"),
    ("most_efficient_nuke", "Most Efficient Nuke"),
    ("best_accuracy",    "Best Accuracy"),
)
# headline category keys (NOT in AWARD_CATEGORIES — rendered in their own slots):
HEADLINE_SEASON_MVP = "season_mvp"
HEADLINE_FINALS_MVP = "finals_mvp"
```

The 5 per-Role Tag-Ratio sub-winners share `category == "tag_ratio"` and are
distinguished by the `role` field (`"commander"` / `"heavy"` / `"scout"` / `"medic"`
/ `"ammo"`).

### 1.2 `AwardWinner` frozen dataclass

```python
@dataclass(frozen=True)
class AwardWinner:
    category: str            # an AWARD_CATEGORIES key, or "season_mvp" / "finals_mvp"
    label: str              # the human label (from AWARD_CATEGORIES or "Season MVP"/"Finals MVP")
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    value: float            # the winning metric value (the category's measure)
    role: Optional[str] = None  # set ONLY for the 5 tag_ratio per-role winners; None otherwise
```

`value` semantics per category (the number the winner led on): `most_points` ⇒
summed `points_scored` (float); `tag_ratio` ⇒ `sum(tags_made)/max(sum(times_tagged),1)`;
`most_resupplies` ⇒ summed `resupplies_given`; `longest_survival` ⇒ mean alive-seconds
per Round; `most_efficient_nuke` ⇒ nuke-elimination count; `best_accuracy` ⇒ mean
`get_accuracy` per Round; `season_mvp` ⇒ summed `get_mvp`; `finals_mvp` ⇒ summed
`get_mvp` over the deciding playoff node's Rounds (champion-team players only).

### 1.3 Eligibility floor + tiebreak (LOCKED)

- **Rate/avg awards** (`tag_ratio`, `longest_survival`, `best_accuracy`): a player is
  eligible iff `player_games >= ceil(games / 2)` where `games = max(games over all
  players)` (per §0.1). `most_points` / `most_resupplies` / `most_efficient_nuke` are
  **count awards — NO floor**. Headline `season_mvp` (summed) + `finals_mvp` (summed)
  are **self-flooring — NO games floor**.
- **Tiebreak** (every award): `value` DESC → `player_id` ASC (mirror
  `matches/stat_feats.py::_season_best_keys`). For the 5 per-role tag-ratio winners
  the tiebreak applies within each role bucket.
- **Empty input** ⇒ that award's winner is **absent** (no `AwardWinner` emitted). A
  count/rate award with zero eligible players ⇒ absent. `most_efficient_nuke` with an
  empty nuke map ⇒ absent. **Finals MVP absent** when `finals_rounds == []` (RR-only
  Season, or a playoff not played to a champion) or `champion_team_id is None`.

### 1.4 `compute_season_awards` signature + input seams

```python
def compute_season_awards(
    regular_rounds: list[dict],          # per-PlayerRoundState dicts, regular-season completed Rounds
    nuke_elims_by_player: dict[int, int],# {player_id: nuke-elimination count} over those Rounds
    finals_rounds: list[dict],           # per-PlayerRoundState dicts, deciding playoff node's Rounds ([] if none)
    champion_team_id: Optional[int],     # the Season champion team id (None if no playoff champion)
) -> dict[str, object]:
```

**`regular_rounds` / `finals_rounds` dict shape** — REUSE the `_build_round_dicts`
output VERBATIM (`matches/league_screens/player_stats.py::_build_round_dicts`). It
already supplies every field the awards need EXCEPT nuke elims:
`player_id / player_name / team_id / team_name / role / points_scored / mvp /
tags_made / times_tagged / accuracy / survival_seconds / final_lives /
resupplies_given / missiles_landed / specials_used / follow_up_shots /
reaction_shots / combo_resupply_count`. The pure module reads:
`points_scored` (Most Points sum), `tags_made` / `times_tagged` (Tag Ratio per role),
`resupplies_given` (Most Resupplies sum), `survival_seconds` (Longest Survival mean —
`survival_seconds` is already `min(was_eliminated_at, 1800)/2` per round),
`accuracy` (Best Accuracy mean), `mvp` (Season MVP / Finals MVP **summed**), `role`,
identity fields. The **nuke-elimination count** is NOT in the dict ⇒ supplied
separately as `nuke_elims_by_player`.

**`nuke_elims_by_player`** — `{player_id: int}` keyed by the eliminating Commander's
`player_id`, summing `GameEvent` rows with `event_type="elimination"` whose
`metadata["elimination_action"] == "nuke"` over the regular-season completed Rounds
(the view builds it — see §4.3). Commander-only by nature.

**Return value** — a dict keyed by category (the cached JSON shape, §3): each
`AWARD_CATEGORIES` key maps to either an `AwardWinner` (1 winner) or, for
`"tag_ratio"`, a `list[AwardWinner]` (the ≤5 per-role winners in
`commander/heavy/scout/medic/ammo` order, absent roles skipped); plus
`"season_mvp"` ⇒ `AwardWinner | None` and `"finals_mvp"` ⇒ `AwardWinner | None`.
A category whose winner is absent maps to `None` (or `[]` for `tag_ratio`). The
**pure module returns `AwardWinner` instances**; the SERIALISATION to plain dicts for
the JSON cache happens at the `Season` method boundary (§3), NOT inside the pure
module.

### 1.5 Internal aggregation (pinned behaviour, not asserted on body)

The module re-aggregates summed MVP itself (`aggregate_player_stats` AVERAGES mvp and
does NOT expose summed mvp) — it sums `mvp` per `player_id` over `regular_rounds` for
Season MVP and over `finals_rounds` (filtered to `team_id == champion_team_id`) for
Finals MVP. It sums `resupplies_given`, `points_scored`, `tags_made`, `times_tagged`
per player; means `survival_seconds` and `accuracy` per player over their rounds; and
buckets tag-ratio by `role`. It does NOT call `aggregate_player_stats` (that module
averages mvp + omits nuke elims) — it owns its own pure accumulation so the summed-MVP
requirement is met. Tests assert OUTPUT (winners / values / floor / tiebreak), not the
loop body.

### 1.6 Purity test

`TestNoDjangoImportsLeaked` in `matches/tests/test_season_awards.py` (subprocess
fresh-import `matches.season_awards`, walk `sys.modules`, assert no `django*`).

---

## 2. Model + migration

### 2.1 Field (`matches/models.py`, on `Season`)

```python
season_awards_json = models.JSONField(null=True, blank=True, default=None)
```

Declared alongside the existing `highlights_json` / `starting_team_ids_json`
JSONField-cache precedent on `Season`. `None` = not yet computed (cache miss); a dict
(the §3 shape) = warmed cache. An in-progress / draft Season is NEVER written (it
shows the "not yet awarded" empty state).

### 2.2 Migration (LOCKED filename)

`matches/migrations/0048_season_season_awards_json.py` — dependency
`("matches", "0047_seasonphase_tournament_subconfig")` (verified the highest-numbered
tracked `matches` migration; c-3f shipped NO migration). **A SINGLE `AddField`, NO
`RunPython`, NO `RunSQL`, NO backfill** (ADR-0004 posture — existing completed Seasons
take `default=None` and warm lazily on first awards render).

---

## 3. Cache semantics (LOCKED)

### 3.1 Cached JSON shape (`Season.season_awards_json`)

A dict keyed by category. Each `AwardWinner` round-trips as a plain dict with its 8
fields (`category, label, player_id, player_name, team_id, team_name, value, role`;
`role` is `null` except for tag-ratio per-role winners). Shape:

```json
{
  "most_points":      {<award dict> | null},
  "tag_ratio":        [<award dict>, ...],   // 0..5 per-role winners
  "most_resupplies":  {<award dict> | null},
  "longest_survival": {<award dict> | null},
  "most_efficient_nuke": {<award dict> | null},
  "best_accuracy":    {<award dict> | null},
  "season_mvp":       {<award dict> | null},
  "finals_mvp":       {<award dict> | null}   // null for RR-only / no-champion Season
}
```

### 3.2 `Season.get_or_compute_awards()` (`matches/models.py`)

```python
def get_or_compute_awards(self) -> dict:
```

- **In-progress / draft / non-`completed` Season** ⇒ returns the empty sentinel
  `{}` **WITHOUT** computing or writing the cache (the caller renders "not yet
  awarded"). Pin: only a `completed` Season ever computes/caches.
- **Completed Season, cache hit** (`season_awards_json is not None`) ⇒ returns the
  cached dict verbatim (deserialised JSON; values are plain award dicts).
- **Completed Season, cache miss** ⇒ build the three pure-module inputs from the ORM
  (the regular-season per-round dicts via `_build_round_dicts`, the nuke-elim map via
  the `GameEvent` scan, the finals per-round dicts + `champion_team_id` via the
  playoff FK-chain — see §4.3/§4.4), call `compute_season_awards(...)`, **serialise**
  the returned `AwardWinner`s to plain dicts (the §3.1 shape), write
  `self.season_awards_json = <dict>` + `self.save(update_fields=["season_awards_json"])`,
  and return the dict. **Consumes no RNG.** It is the SINGLE read/write chokepoint —
  the awards view, the LG-01f history row, and the LG-06h badge all call it.
- Serialisation helper (private, e.g. `_award_to_json` / `_awards_to_json`) lives on
  the `Season` method side (NOT in the pure module) — Code agent discretion on the
  exact private name; only `get_or_compute_awards` is the locked public surface.

The "completed regular-season Rounds" corpus for the regular-season inputs is the
Season's `GameRound`s whose `Match.season == self` and `Match.is_completed` (playoff
Matches keep `season=NULL` — Part2c-1 #3 — so they are naturally excluded; the
`_build_round_dicts` filter is `{"game_round__match__season": self}`).

---

## 4. View / URL / template

### 4.1 URL (`matches/season_urls.py`)

Insert, mirroring the bare-named GET-only family:

```python
path("<int:season_id>/awards/", league_views.season_awards, name="season_awards"),
```

…re-exported from `matches/league_screens` and bound on `league_views` the same way
the other LG-01z screens are reached (`league_views.season_awards =
league_screens.season_awards` re-export, OR `from . import league_views` referencing
the re-exported name — match the prevailing wiring). Insert the route adjacent to the
`standings/` / `schedule/` entries. URL name **`season_awards`** (bare, no `app_name`).

### 4.2 View (`matches/league_screens/season_awards.py`, re-exported)

```python
def season_awards(request: HttpRequest, season_id: int) -> HttpResponse:
```

- GET-only — `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` as
  the FIRST body line (the season-URL-family guard precedent).
- `season = get_object_or_404(Season, pk=season_id)`.
- `request.session["last_league_id"] = season.league_id` (the LG-01f session-write
  contract — int, after the 404 guard, before render).
- `displayed_season = season` (this IS the Season). `sidebar_links =
  _build_league_sidebar_links(season.league, <displayed_season for the league>,
  sidebar_active=None)` — NO sidebar entry matches the awards page, so every entry
  renders inactive (do NOT add a sidebar key).
- `awards = season.get_or_compute_awards()` (the §3.2 chokepoint — `{}` for a
  non-completed Season ⇒ the not-yet-awarded empty state).
- Context keys (frozen): `season`, `league` (= `season.league`), `sidebar_links`,
  `sidebar_active` (= `None`), `awards` (the §3.1 dict or `{}`), `award_categories`
  (= `AWARD_CATEGORIES`, for the template loop), `is_awarded` (bool — `season.state ==
  "completed"` AND `awards` is non-empty).
- `render(request, "seasons/awards.html", context)`.

### 4.3 GameEvent nuke scan (view → pure-module seam helper)

The view (or `get_or_compute_awards`) builds `nuke_elims_by_player` by counting, over
the Season's completed regular-season `GameRound`s:

```python
GameEvent.objects.filter(
    game_round__match__season=season,
    game_round__match__is_completed=True,
    event_type="elimination",
    metadata__elimination_action="nuke",
).values("actor_id").annotate(n=Count("id"))
```

⇒ `{row["actor_id"]: row["n"]}`. The eliminating Commander is the event **actor**
(`ctx.events.elimination(player, opp, second, action="nuke")` — `player` is the
Commander; persisted as `event_type="elimination"`, `metadata["elimination_action"] ==
"nuke"`). Verified emit site: `matches/sim_helpers/event_log.py::elimination`
(`metadata=_build_meta(attacker, defender, elimination_action=action)`) + the
`_complete_nuke` call at `matches/simulation/entrypoints.py:~1890`. (If the JSON-field
`metadata__elimination_action` lookup is awkward on the backend, the view MAY scan
`event_type="elimination"` rows and filter `ev.metadata.get("elimination_action") ==
"nuke"` in Python — equivalent; the COUNT-by-actor result is the seam, not the query
plan.)

### 4.4 Finals FK-chain resolution (deciding playoff node → champion-only Rounds)

The view (or `get_or_compute_awards`) resolves the Finals MVP corpus:

1. **The Season's playoff is the embedded tournament** reached via
   `SeasonPhase.tournament` where `phase.phase_type == "tournament"` and the phase is
   the Season's deciding phase. The season-embedded discriminator is the FK chain
   `Match.series_match → BracketNode → Tournament` where `Tournament.season_phases` is
   non-empty (study `matches/league_screens/team_history.py::_build_overall_context`
   for the exact `.distinct()` usage and the
   `match__series_match__node__tournament__season_phases__isnull=False` filter).
2. **The deciding Bracket node** is the championship-decider: the `BracketNode` whose
   `advances_to is None` (single-elim final / DE GF2). Resolve it off the Season's
   playoff Tournament (`tournament.nodes.filter(advances_to__isnull=True)`); for a
   single-elim bracket that is the final node, for DE it is GF2.
3. **The deciding node's Rounds** = the `GameRound`s of the Matches in that node's
   `SeriesMatch` rows (`GameRound.objects.filter(match__series_match__node=<node>)`,
   `.distinct()`).
4. **`champion_team_id` = `Season.champion_team_id`** (the resolved champion).
5. `finals_rounds = _build_round_dicts({"game_round__match__series_match__node":
   <node>})` (the SAME `_build_round_dicts` reuse; the pure module then filters to
   `team_id == champion_team_id`). When there is no embedded playoff Tournament, no
   `advances_to is None` node, or no champion ⇒ `finals_rounds = []` AND/OR
   `champion_team_id = None` ⇒ Finals MVP **absent**.

(Pin: the awards module / view MUST work out the deciding-node resolution off the
embedded playoff Tournament via this FK chain — mirror the c-3f
`season_phases__isnull=False` discriminator + `.distinct()`. The champion Team is
`Season.champion_team`. `Season.activate_pending_tournament_phase` /
`_stamp_champion_for_final_phase` are the references for how the playoff resolves.)

### 4.5 Template `templates/seasons/awards.html` (NEW) + LOCKED DOM ids

Extends `base.html`, `{% block title %}{{ season.league.name }} — {{ season.name }}
Awards{% endblock %}` (em-dash U+2014). Uses the `d-flex` +
`{% include "_partials/league_sidebar.html" %}` shell like the other
`templates/seasons/*` / `templates/leagues/*` screens. Locked DOM ids:

- `season-awards` (root container).
- `season-awards-table` (the 6-category award table; one row per `AWARD_CATEGORIES`
  entry, the `tag_ratio` row expanding to its ≤5 per-role winners).
- per-category container `season-awards-category-{key}` for each of the 6 keys
  (`most_points` / `tag_ratio` / `most_resupplies` / `longest_survival` /
  `most_efficient_nuke` / `best_accuracy`).
- `season-awards-mvp` (the Season MVP headline slot).
- `season-awards-finals-mvp` (the Finals MVP headline slot — renders `—` / null when
  `awards["finals_mvp"]` is `None`).
- `season-awards-not-yet` (the "not yet awarded" empty state for a non-completed /
  un-warmed Season; shown when `is_awarded` is False).
- `season-awards-empty` (the per-category "—" placeholder for a category whose winner
  is absent on an otherwise-awarded Season).

Player-name cells link to `league_player_detail` (the LG-06h in-League page) via
`{% url 'league_player_detail' season.league_id <player_id> %}` (the prevailing
league-screen player link). Render `value` with `|floatformat` where a decimal reads
better (tag ratio / survival / accuracy / mvp).

---

## 5. LG-01f extension — two League-History columns

### 5.1 `_build_history_row` (`matches/league_views.py`) — +2 keys (11 → 13)

The frozen row dict gains exactly two keys, appended:

- `season_mvp` — value shape: **a plain award dict (the §3.1 `AwardWinner`-as-dict
  shape) or `None`** (the cached `awards["season_mvp"]`). NOT a `Player`, NOT a bare
  name string — so the template can render the player name + link without an extra
  query. `None` ⇒ render `—`.
- `finals_mvp` — same value shape; the cached `awards["finals_mvp"]` (`None` for an
  RR-only / no-champion Season).

For an in-progress / draft row (`is_in_progress=True`) both are **`None`** (awards are
only computed for completed Seasons; the in-progress row never warms the cache).

### 5.2 `league_history` (`matches/league_views.py`) — source per completed Season

For each **completed** Season row, `_build_history_row` reads
`season.get_or_compute_awards()` (the §3.2 chokepoint — cache-hit fast path; a
cache-miss warms it once). Pin: the existing 3-query history view picks up at most one
extra cached read per completed row on the page (and a one-time compute on first
render). The in-progress row passes `None` for both new keys (no compute). The
`_build_history_row` signature is unchanged (it already receives `season` +
`teams_by_id`); it gains the `get_or_compute_awards()` call internally for completed
seasons only (gate on `not is_in_progress`).

### 5.3 `templates/leagues/history.html` — +2 columns

Add **Season MVP** and **Finals MVP** `<th>` columns (after the existing Champion /
Runner-Up / Tournament Champion cluster, Code-agent discretion on exact position) and
the matching per-row `<td>` cells rendering `row.season_mvp` / `row.finals_mvp`
(player name linked to `league_player_detail`, `—` when `None`). DOM ids:
`league-history-th-season-mvp` / `league-history-th-finals-mvp` on the headers; the
existing per-row id `league-history-row-{season_id}` is unchanged (the new cells live
inside it). The in-progress row (`league-history-in-progress-row`) renders `—` for
both.

---

## 6. LG-06h extension — fill `league-player-awards-stub`

### 6.1 View (`matches/league_screens/player_detail.py::player_detail`) — new context

`player_detail` gains ONE new context key: **`player_awards`** — a
`list[dict]` of the per-Season awards THIS Player won in THIS League, each entry shaped
`{"season_id": int, "season_name": str, "category": str, "label": str, "role": str |
None, "value": float}`. Built by iterating `league.seasons.filter(state="completed")`,
calling `season.get_or_compute_awards()` per completed Season (the §3.2 chokepoint),
and collecting every `AwardWinner`-dict whose `player_id == player.id` across all 8
category/headline slots (including each of the ≤5 tag-ratio per-role winners and the
two headline awards). Order: newest Season first, then `AWARD_CATEGORIES` order then
headline order within a Season. Empty list when the player won nothing in any of this
League's completed Seasons. Consumes no RNG. The view's existing 9 frozen context keys
are UNCHANGED; `player_awards` is the 10th.

### 6.2 Template `templates/leagues/player_detail.html` — fill the stub

Replace the placeholder body inside the existing `<div id="league-player-awards-stub"
class="card mt-3">` with the awards list: when `player_awards` is non-empty, render one
row per entry (`{{ entry.season_name }} — {{ entry.label }}` + the per-role suffix +
`{{ entry.value|floatformat }}`); when empty, render a `"No awards yet"` substring. The
stub DOM id `league-player-awards-stub` is **PRESERVED** (the other four stubs —
`league-player-playoffs-stub` / `-ratings-history-stub` / `-salaries-stub` /
`-transactions-stub` — are UNTOUCHED). New inner DOM id `league-player-awards-list`
(the list container) + `league-player-awards-empty` (the empty-state). The global HX-01
career page (`/players/<id>/stats/`) is **NOT touched**.

---

## 7. Entry-point links

### 7.1 Season dashboard (`templates/seasons/dashboard.html`)

Add a **"View Awards"** link `{% url 'season_awards' season.id %}` with DOM id
`season-dashboard-awards-link`. Render it for a `completed` Season (Code-agent
discretion: it MAY render always and the page itself shows the not-yet-awarded state,
but the link only earns awards on a completed Season — recommend gating on
`season_mode == "completed"` to match the dashboard's existing cursor conditionals).
Place it near the existing `season-dashboard-view-bracket-link` slot. **NO** League
dashboard link required (the awards page is per-Season; League History is the league-
level entry).

### 7.2 League History row (`templates/leagues/history.html`)

Each **completed-Season** row's Season-name cell (or a dedicated cell) carries a link
`{% url 'season_awards' row.season_id %}` with DOM id
`league-history-awards-link-{season_id}`. The in-progress row gets NO awards link.

(NO sidebar / topbar entry — §0 constraint.)

---

## 8. Test boundary

Test files (NEW + EXTENDED), and what each asserts vs what is internal.

### 8.1 `matches/tests/test_season_awards.py` (NEW — pure-unit + purity)
- `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk).
- Per-category winner correctness from hand-built `regular_rounds` dict lists: Most
  Points (summed), Tag Ratio **per role** (5 buckets, `sum/sum` not mean-of-ratios,
  the `max(...,1)` denominator clamp), Most Resupplies (summed, an Ammo CAN win),
  Longest Survival (mean `survival_seconds`), Most Efficient Nuke (from
  `nuke_elims_by_player`, Commander-only, absent on empty map), Best Accuracy (mean).
- Floor: a rate award excludes a player with `games < ceil(games/2)`; count awards
  apply NO floor; `season_mvp` / `finals_mvp` apply NO games floor.
- Tiebreak `value desc → player_id asc` (incl. within a tag-ratio role bucket).
- Empty `regular_rounds` ⇒ every category absent; `finals_rounds == []` OR
  `champion_team_id is None` ⇒ Finals MVP absent; Finals MVP filters to
  `team_id == champion_team_id`; Season MVP is **summed** mvp not mean.
- The returned dict shape (keyed by category; `tag_ratio` ⇒ list; headlines ⇒
  `AwardWinner | None`).
- **Tests assert on the PURE FUNCTION output** — never on simulated point totals.

### 8.2 The awards view test (NEW `matches/tests/test_season_awards_view.py`, or
EXTEND an existing season-view test file)
- 200 on a completed Season; the locked DOM ids present (`season-awards-table`,
  per-category `season-awards-category-{key}`, `season-awards-mvp`,
  `season-awards-finals-mvp`); `season-awards-not-yet` shown for a draft/active Season;
  405 on POST; 404 on a missing id; session-write of `last_league_id`.
- The **cache**: first GET on a completed Season warms `season_awards_json` (was
  `None`, now a dict); a second GET reads the cache (no recompute — assert via a
  cache-already-populated fixture or a spy on the compute path). A draft/active Season
  GET does NOT write the cache.
- The **GameEvent nuke scan**: hand-build a completed Season with a
  `GameEvent(event_type="elimination", actor=<commander>,
  metadata={"elimination_action": "nuke"})` and assert that Commander is the Most
  Efficient Nuke winner.
- The **Finals FK-chain resolution**: hand-build a Season with an embedded single-elim
  playoff via the FULL FK chain (`SeasonPhase(tournament=…) → Tournament(
  season_phases non-empty) → BracketNode(advances_to=None) → SeriesMatch → Match(
  season=NULL) → GameRound`), a stamped `champion_team`, and assert Finals MVP is the
  champion-team player with the highest summed mvp over the deciding node's Rounds, and
  is **absent** for an RR-only Season.
- **Tests must NEVER assert exact simulated point totals** (playoff sims are
  non-deterministic) — assert winner IDENTITY / category / DOM ids / cache state /
  absence, hand-building deterministic `PlayerRoundState` + `GameEvent` rows.

### 8.3 LG-01f extension (EXTEND the existing `matches/tests/test_league_history.py`)
- A completed-Season row carries `season_mvp` / `finals_mvp` (the award-dict-or-`None`
  shape); the two new `<th>` ids (`league-history-th-season-mvp` /
  `-finals-mvp`) and the per-row cells render; the in-progress row renders `—` for both
  and does NOT warm the cache; the awards link `league-history-awards-link-{id}` on
  completed rows only.

### 8.4 LG-06h extension (EXTEND the existing
`matches/tests/test_league_player_detail.py`)
- `player_awards` context key present; the `league-player-awards-stub` body filled with
  the player's wins across this League's completed Seasons (`league-player-awards-list`
  populated) and `league-player-awards-empty` for a player who won nothing; the global
  career page is unaffected.

### 8.5 Internal (NOT asserted)
- The exact accumulation loop inside `compute_season_awards` (assert OUTPUT).
- The exact SQL of the nuke scan / the FK-chain query (assert the resulting winner /
  count, not the query plan).
- The private award→JSON serialiser name on the `Season` side.

---

## 9. Locked-names quick index

- **Pure module:** `matches/season_awards.py` — `AWARD_CATEGORIES` (6 `(key,label)`
  pairs: `most_points` / `tag_ratio` / `most_resupplies` / `longest_survival` /
  `most_efficient_nuke` / `best_accuracy`), `HEADLINE_SEASON_MVP = "season_mvp"`,
  `HEADLINE_FINALS_MVP = "finals_mvp"`; `AwardWinner(category, label, player_id,
  player_name, team_id, team_name, value, role=None)`; `compute_season_awards(
  regular_rounds, nuke_elims_by_player, finals_rounds, champion_team_id) -> dict`;
  `TestNoDjangoImportsLeaked`.
- **Model + migration:** `Season.season_awards_json = models.JSONField(null=True,
  blank=True, default=None)`; `Season.get_or_compute_awards() -> dict` (cache
  chokepoint — completed-only, lazy, no RNG); migration
  `matches/migrations/0048_season_season_awards_json.py` (dep
  `0047_seasonphase_tournament_subconfig`, single `AddField`, no `RunPython`).
- **Floor:** `games = max(games over all players)`; rate awards (`tag_ratio` /
  `longest_survival` / `best_accuracy`) require `player_games >= ceil(games/2)`; count
  awards (`most_points` / `most_resupplies` / `most_efficient_nuke`) + headline MVPs
  have NO floor. **Tiebreak:** `value desc → player_id asc`.
- **Nuke seam:** `nuke_elims_by_player = {actor_id: count}` from
  `GameEvent(event_type="elimination", metadata["elimination_action"]=="nuke")`
  (actor = Commander).
- **Finals seam:** deciding node = `tournament.nodes.filter(advances_to__isnull=True)`
  off the embedded playoff Tournament (`...node__tournament__season_phases__isnull=
  False`); `champion_team_id = Season.champion_team_id`; `finals_rounds` from
  `_build_round_dicts({"game_round__match__series_match__node": node})`, filtered to
  champion-team players; absent ⇒ Finals MVP null.
- **View / URL / template:** URL name `season_awards`, path
  `<int:season_id>/awards/` in `matches/season_urls.py`; view
  `matches/league_screens/season_awards.py::season_awards(request, season_id)`
  (GET-guard first line); template `templates/seasons/awards.html`; DOM ids
  `season-awards` / `season-awards-table` / `season-awards-category-{key}` /
  `season-awards-mvp` / `season-awards-finals-mvp` / `season-awards-not-yet` /
  `season-awards-empty`.
- **LG-01f:** `_build_history_row` +2 keys `season_mvp` / `finals_mvp` (award-dict or
  `None`); `league_history` sources them via `season.get_or_compute_awards()` for
  completed rows; `history.html` +2 columns (`league-history-th-season-mvp` /
  `-finals-mvp`) + `league-history-awards-link-{season_id}`.
- **LG-06h:** `player_detail` context key `player_awards` (list of award dicts for
  THIS player in THIS league's completed Seasons); fills
  `league-player-awards-stub` (inner ids `league-player-awards-list` /
  `league-player-awards-empty`).
- **Entry points:** `season-dashboard-awards-link` (`templates/seasons/dashboard.html`)
  + `league-history-awards-link-{season_id}` (`templates/leagues/history.html`).
- **Test files:** `matches/tests/test_season_awards.py` (NEW pure + purity), the awards
  view test (NEW `test_season_awards_view.py` OR existing season-view file), EXTEND
  `matches/tests/test_league_history.py` (the +2 columns), EXTEND
  `matches/tests/test_league_player_detail.py` (the badge).

---

## 10. Scope-out (LOCKED — do NOT build here)

- **NO sidebar / topbar entry** — `_build_league_sidebar_links` + all LG-01f/h/k nav
  machinery untouched.
- **NO global career-page badge** — only `league-player-awards-stub`;
  `/players/<id>/stats/` is not touched.
- **NO ADR** (JSONField cache follows precedent, reversible). **NO CONTEXT.md edit**
  (the `### Awards` glossary already carries Season award / Season MVP / Finals MVP).
- **NO re-baseline, NO simulator / engine / RNG change.**
- **NO eager warm** in `Season.complete_if_finished` (lazy-only).
- **NO SIM-13 work** — the dead PRS nuke counters stay dead; nuke elims come from the
  `GameEvent` log.
- **NO new `Match` / `GameRound` / `GameEvent` field, NO Match migration** — the only
  migration is the single `Season.season_awards_json` `AddField`.
- **NO reuse of `aggregate_player_stats` for the awards module** — it averages mvp and
  omits nuke elims; the awards module owns its own summed-mvp accumulation (but DOES
  reuse `_build_round_dicts` for the per-round input shape).
