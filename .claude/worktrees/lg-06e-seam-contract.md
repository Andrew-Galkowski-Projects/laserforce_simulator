# LG-06e Seam Contract — Statistical Feats as a per-game feed

**Task:** Reshape the Statistical Feats league screen from ~9 "category-best"
entries (one row = the single best of each feat kind) into ZenGM's model: **one
sortable row per (Player, Round) that achieved a feat**, showing that round's
box-score line + Opp / Result / Season, deep-linking to the Round.

**Read-only / derived.** NO model change. NO migration. NO RNG. NO Score
Calibration re-baseline. NO simulation. This reshapes the OUTPUT SHAPE of the
pure module `matches/stat_feats.py` + the view + the template only.

**Domain term:** aligns with the new CONTEXT.md **Statistical feat** entry
(`### Analytics and review`) — one (Player, Round) performance, qualifying by
crossing a per-game bar OR being a season-best, with box-score line + Opp +
per-Round Result + Season, comeback as a separate Team-feats section.

---

## 0. Scope-out (locked — do NOT do these)

- **No model change, no migration** — `matches/models.py` read-only.
- **No RNG, no simulation, no `_flush_to_db` touch** — outside SIM-07/SIM-08;
  no Score Calibration re-baseline.
- **No CONTEXT.md edit** — the **Statistical feat** term already exists.
- **No ADR** — reversible (a pure-module output reshape + view/template rewrite).
- **comeback_win stays a SEPARATE "Team feats" section** below the main
  per-player feed — NOT a per-player row, NO box-score line.
- **No new pure module** — reshape the existing `matches/stat_feats.py`.
- **No URL change** — the route `stats_statistical_feats` and the view
  `matches.league_screens.statistical_feats.statistical_feats` keep their names.
- **No change to the LG-06b `_coerce_team_id` / LG-06d `_resolve_season_scope`
  / LG-06a `_coerce_per_page` / `_coerce_page` / LG-06c `_coerce_sort_key`
  shared helpers** — consumed verbatim from `matches.league_views`.
- **No re-baseline of the per-game threshold constants** — they ship at the
  conservative starting values below; calibration is explicitly deferred.

---

## 1. Owning module per new name

| New name | Owning module |
|---|---|
| `FeatRow` (frozen dataclass) | `matches/stat_feats.py` |
| `TeamFeatRecord` (frozen dataclass, comeback) | `matches/stat_feats.py` |
| `FEAT_KINDS` (vocabulary tuple) | `matches/stat_feats.py` |
| All threshold constants | `matches/stat_feats.py` |
| `scan_feats(...)` (reshaped) | `matches/stat_feats.py` |
| `find_comeback_win(...)` (retained, reshaped return) | `matches/stat_feats.py` |
| `SEASON_BEST_STATS` (tuple) | `matches/stat_feats.py` |
| `BOX_SCORE_KEYS` (tuple) | `matches/stat_feats.py` |
| view extended-seam build + sort + paginate | `matches/league_screens/statistical_feats.py` |
| `_FEATS_SORT_KEYS` (expanded), `_FEATS_SORT_KEYS_DISPLAY` | `matches/league_screens/statistical_feats.py` |
| `_feat_row_sort_value(...)` | `matches/league_screens/statistical_feats.py` |
| template (rewrite) | `templates/leagues/statistical_feats.html` |

---

## 2. Pure module `matches/stat_feats.py` — new public surface

The module stays **PURE**: import allowlist `dataclasses`, `typing`,
`collections` ONLY. NO Django, NO ORM, NO RNG, NO I/O. The
`TestNoDjangoImportsLeaked` subprocess check must keep passing.

### 2.1 Box-score keys (per-round values carried on each row)

The 12 `STAT_KEYS` from `matches/season_player_stats.py` (PER-ROUND values, NOT
aggregated) PLUS `nuke_detonations`. Pinned tuple, this exact order:

```python
BOX_SCORE_KEYS: tuple[str, ...] = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
    "nuke_detonations",
)
```

Each `FeatRow` carries these as a `stats: dict[str, float]` mapping (every key
present; `mvp` / `accuracy` are floats, the rest ints stored as numbers). The
view supplies all of them per (player, round) — see §3.

### 2.2 Feat-kind vocabulary — REPRESENTATION (locked: ONE representation)

**Chosen representation:** each badge a row carries is a `FeatBadge` frozen
dataclass with a stable `kind` key, a human `label`, and an `is_season_best`
flag. This is the ONE pinned representation (NOT distinct `high_tags` vs
`season_best_tags` kind strings — the same `kind` is reused, the flag
distinguishes threshold-cross from season-best).

```python
@dataclass(frozen=True)
class FeatBadge:
    kind: str            # stable key, drives the per-badge DOM id stat-feat-badge-{kind}
    label: str           # human-readable badge text
    is_season_best: bool  # True ⇒ "season best" tag; False ⇒ threshold crossing
```

**`FEAT_KINDS`** — the stable vocabulary, a tuple of `(kind, label)` pairs
(single source of truth for labels; the threshold-cross label is the base, the
season-best label is rendered by the template as `"<label> (season best)"` when
`is_season_best`). Pinned:

```python
FEAT_KINDS: tuple[tuple[str, str], ...] = (
    ("triple_nuke",    "Triple Nuke"),
    ("medic_shutout",  "Medic Shutout"),
    ("perfect_heavy",  "Perfect Heavy"),
    ("high_tags",      "Tags"),
    ("high_points",    "Points"),
    ("high_mvp",       "MVP"),
    ("high_resupplies","Resupplies"),
    ("high_missiles",  "Missiles"),
)
```

Notes:
- The 5 count-stat feats use kind keys `high_tags` / `high_points` / `high_mvp`
  / `high_resupplies` / `high_missiles`. A given (player, round) carries the
  SAME kind whether it qualified by threshold OR by season-best — the
  `is_season_best` flag on the `FeatBadge` disambiguates.
- `triple_nuke` / `medic_shutout` / `perfect_heavy` are threshold-only feats
  (no season-best variant) — they only ever appear with `is_season_best=False`.
- A row can carry BOTH a threshold-cross badge and a season-best badge for the
  SAME kind (e.g. a round that is both ≥ 20 tags AND the season's most tags).
  In that case the row carries TWO badges of kind `high_tags` — one
  `is_season_best=False`, one `is_season_best=True`. (Implementations MAY
  de-dup to a single badge with `is_season_best=True` taking precedence; the
  contract permits either, but tests assert the row is tagged "season best"
  when it is the season leader.) **Locked choice: collapse to ONE badge per
  kind per row, `is_season_best=True` winning** when a row both crosses the
  threshold and is the season leader for that kind.

### 2.3 Threshold constants (conservative starting values; tunable; calibration deferred)

Module-level constants, named exactly:

```python
TRIPLE_NUKE_THRESHOLD = 3          # nuke_detonations >= 3 (RETAINED from current module)
HIGH_TAGS_THRESHOLD = 20           # tags_made >= 20
HIGH_POINTS_THRESHOLD = 12000      # points_scored >= 12000
HIGH_MVP_THRESHOLD = 15            # mvp >= 15
HIGH_RESUPPLIES_THRESHOLD = 20     # resupplies_given >= 20
HIGH_MISSILES_THRESHOLD = 8        # missiles_landed >= 8
```

Boolean feats (no numeric constant — predicates pinned in §2.5):
- `medic_shutout`: `role == "medic"` AND `times_tagged == 0` (participation
  proven by the row's presence — same rule as today).
- `perfect_heavy`: `role == "heavy"` AND `shots_missed == 0` AND `tags_made > 0`.

### 2.4 Season-best stats

```python
SEASON_BEST_STATS: tuple[str, ...] = (
    "mvp",
    "points_scored",
    "tags_made",
    "resupplies_given",
    "missiles_landed",
)
```

Mapping season-best stat → feat kind (pinned): `mvp → high_mvp`,
`points_scored → high_points`, `tags_made → high_tags`,
`resupplies_given → high_resupplies`, `missiles_landed → high_missiles`.

**"Best" = the single highest value of that stat within the current scope**
(the whole `player_rounds` list as ONE pool — the view has already restricted
the pool to the selected Season, or to the whole Career pool for `?season=career`).
Each of the 5 stats yields **exactly one** guaranteed season-best row (the row
that holds the highest value), **always listed** even if below threshold, tagged
`is_season_best=True`.

**Tiebreak (deterministic, pinned):** among rows tied on the stat value, pick:
1. highest stat value (the max),
2. then highest `round_id` (most recent),
3. then lowest `player_id`.

(Documented: "highest value, then highest round_id, then lowest player_id".)
A stat whose maximum across the pool is `0` (e.g. nobody landed a missile) still
produces a season-best row for the top holder — but implementations MAY skip the
season-best badge when the max value is `0` for that stat to avoid a vacuous
"season best: 0 missiles" badge. **Locked choice: skip the season-best badge
when the stat's pool maximum is `0`** (an all-zero stat has no meaningful
leader). Threshold feats are unaffected by this rule.

### 2.5 Qualification (hybrid) — a (player, round) row is INCLUDED iff EITHER:

(a) it crosses ANY threshold feat:
- `nuke_detonations >= TRIPLE_NUKE_THRESHOLD`, OR
- `role == "medic" and times_tagged == 0`, OR
- `role == "heavy" and shots_missed == 0 and tags_made > 0`, OR
- `tags_made >= HIGH_TAGS_THRESHOLD`, OR
- `points_scored >= HIGH_POINTS_THRESHOLD`, OR
- `mvp >= HIGH_MVP_THRESHOLD`, OR
- `resupplies_given >= HIGH_RESUPPLIES_THRESHOLD`, OR
- `missiles_landed >= HIGH_MISSILES_THRESHOLD`;

OR (b) it is the season-best leader for any of the 5 `SEASON_BEST_STATS`
(per §2.4 selection + tiebreak), always listed even below threshold.

One row per (player_id, round_id). A single round can carry several badges for
the same player → still ONE row, badges stack on `FeatRow.feats`.

### 2.6 `FeatRow` dataclass — EXACT field list / order / types

```python
@dataclass(frozen=True)
class FeatRow:
    # --- identity / deep-link ---
    player_id: int
    player_name: str
    role: str
    team_id: Optional[int]       # the row's own team that round; None when unresolved
    team_name: str               # "" when unresolved
    round_id: int                # deep-link target (game_round_detail)
    # --- descriptor columns ---
    opp_team_name: str           # the OTHER team that Round; "" when unresolved
    result: str                  # "W" / "L" / "T" — per-ROUND, own vs opp points
    season_id: Optional[int]     # the Round's Match.season id; None for Career pool rows is allowed
    season_name: str             # "" when unresolved
    # --- box-score line (per-round values) ---
    stats: Mapping[str, float]   # every BOX_SCORE_KEYS key present
    # --- badges (stacked) ---
    feats: tuple[FeatBadge, ...] # >= 1 badge; the reason(s) this row qualified
```

Field order is pinned. `stats` carries all 13 `BOX_SCORE_KEYS`. `feats` is
non-empty for every emitted row (a row with zero badges is never emitted).

### 2.7 `TeamFeatRecord` dataclass (comeback — Team feats section)

Replaces the comeback half of the old `FeatRecord`. EXACT fields:

```python
@dataclass(frozen=True)
class TeamFeatRecord:
    kind: str                # "comeback_win" (the only kind today)
    label: str               # "Comeback win (won the match after losing round 1)"
    team_name: str           # the winning team's name; "" when unresolved
    round_id: Optional[int]  # round-2 GameRound id (deep-link); None when no anchor
```

### 2.8 `scan_feats(...)` — new signature + return type

```python
def scan_feats(
    player_rounds: list[dict],
    matches: list[dict],
) -> tuple[list[FeatRow], list[TeamFeatRecord]]:
    """Build the per-(player,round) feat feed + the team-feats list.

    Returns ``(feat_rows, team_feats)``:
      * feat_rows — one FeatRow per qualifying (player, round), in the module's
        guaranteed deterministic order (see §2.10). Badges stacked per row.
      * team_feats — the comeback-win record(s) for the separate Team feats
        section (today: zero or one record via find_comeback_win).
    """
```

The 9-finder + single-`FeatRecord` design is replaced. Internally `scan_feats`:
1. Computes, for each of the 5 `SEASON_BEST_STATS`, the season-best (player,
   round) per §2.4 (skipping all-zero-max stats per §2.4) → a set of
   `(round_id, player_id) → list[FeatBadge(is_season_best=True)]`.
2. Iterates `player_rounds`, for each row collecting its threshold badges
   (§2.5(a)) plus any season-best badges from step 1; collapses to one badge
   per kind (season-best wins, §2.2).
3. Emits a `FeatRow` for every (player, round) with ≥ 1 badge.
4. Calls `find_comeback_win(matches)` → the `team_feats` list.

### 2.9 `find_comeback_win(...)` — retained, reshaped return

```python
def find_comeback_win(matches: list[dict]) -> list[TeamFeatRecord]:
    """A team that won the Match after losing round 1.

    DEFINITION unchanged: the Match has a winner whose round-1 score was
    strictly LOWER than the opponent's. Returns the record(s) for the
    Team feats section. Today returns 0 or 1 record (the LAST / most-recent
    qualifying Match by input order — input is id-ascending). Returning a
    list (not Optional) keeps the section render uniform.
    """
```

(The detection logic — winner_team_id vs red/blue round-1 points — is unchanged
from the current `find_comeback_win`; only the return type changes from
`Optional[FeatRecord]` to `list[TeamFeatRecord]`.)

### 2.10 Deterministic ordering the pure module GUARANTEES on its output

`scan_feats` returns `feat_rows` in a **stable, deterministic** order so a
caller that does NOT re-sort still renders deterministically. Pinned default
order (most-recent first):

1. `round_id` DESC (most recent round first),
2. then `player_id` ASC,

i.e. `sorted(rows, key=lambda r: (-r.round_id, r.player_id))`. The view applies
the user-requested sort on top of this (see §3.4); but the module's own output
is this fixed order. `team_feats` is returned in input (id-ascending) order.

### 2.11 Per-(player,round) input seam-dict shape (every key + type)

The view materialises one dict per `PlayerRoundState` row and passes the list to
`scan_feats`. **Opp / Result / Season are computed VIEW-SIDE and passed in** so
the pure module stays Django-free (recommended + locked). Required keys:

```python
{
    # --- identity / deep-link ---
    "round_id": int,
    "match_id": int | None,
    "player_id": int,
    "player_name": str,
    "role": str,                 # PlayerRoundState.role
    "team_id": int | None,       # the row's own team that Round
    "team_name": str,            # "" when unresolved
    # --- descriptor columns (view-computed) ---
    "opp_team_name": str,        # the other team that Round; "" when unresolved
    "result": str,               # "W"/"L"/"T" per-ROUND (own vs opp points)
    "season_id": int | None,
    "season_name": str,
    # --- box-score line (13 BOX_SCORE_KEYS, per-round values) ---
    "points_scored": int,
    "mvp": float,                # PlayerRoundState.get_mvp (property)
    "tags_made": int,
    "times_tagged": int,
    "accuracy": float,           # PlayerRoundState.get_accuracy() (METHOD — call with ())
    "final_lives": int,
    "resupplies_given": int,
    "missiles_landed": int,
    "specials_used": int,
    "follow_up_shots": int,
    "reaction_shots": int,
    "combo_resupply_count": int,
    "nuke_detonations": int,     # per-round nuke-detonation count (event-derived)
}
```

The pure module reads `stats` from the 13 box-score keys, identity from the
identity keys, descriptors from the descriptor keys. It never recomputes opp /
result / season.

### 2.12 Per-Match input seam-dict shape (comeback) — UNCHANGED from today

```python
{
    "match_id": int,
    "round_id": int | None,         # round-2 GameRound id (deep-link)
    "winner_team_id": int | None,
    "winner_team_name": str,        # "" when unresolved / tie
    "red_team_id": int | None,
    "blue_team_id": int | None,
    "red_round1_points": int,
    "blue_round1_points": int,
}
```

---

## 3. View `matches/league_screens/statistical_feats.py`

Same shared LG-01z contract: GET-guard (`HttpResponseNotAllowed(["GET"])`, 405)
→ `get_object_or_404(League)` (404) → `request.session["last_league_id"] =
league.id` → displayed-Season pick → sidebar links
(`sidebar_active="statistical_feats"`) → season scope (LG-06d) → team filter
(LG-06b) → build extended seam dicts → `scan_feats` → sort → paginate → render.

### 3.1 Building the extended per-(player,round) seam dicts

ORM → dict-key mapping (extends the current build in
`league_screens/statistical_feats.py`):

- Reuse the existing nuke-detonation pass (`GameEvent` `event_type="special"`,
  `points_awarded=500`, `_is_nuke_detonation` filter) keyed
  `(game_round_id, actor_id)` → count → `nuke_detonations`.
- `PlayerRoundState` queryset with `select_related("player", "game_round",
  "game_round__match", "game_round__match__season", "game_round__team_red",
  "game_round__team_blue")`, `.order_by("id")`.
- Per `prs`:
  - `round_id = prs.game_round_id`
  - `match_id = prs.game_round.match_id`
  - `player_id = prs.player_id`, `player_name = prs.player.name`
  - `role = prs.role`
  - own team: `team_red` when `prs.team_color == "red"`, `team_blue` when
    `"blue"`, else `None` → `team_id` / `team_name`.
  - **opp team** (view-computed): the OTHER side of the Round —
    `team_blue` when `prs.team_color == "red"`, `team_red` when `"blue"`, else
    `None` → `opp_team_name` (`""` when unresolved).
  - **result** (view-computed, per-ROUND, NOT the Match outcome): compare the
    row's OWN-team points vs the OPPONENT points **in that `GameRound`**, using
    `GameRound.red_points` / `GameRound.blue_points`:
    - own points = `red_points` if `team_color == "red"` else `blue_points`;
    - opp points = `blue_points` if `team_color == "red"` else `red_points`;
    - `"W"` if own > opp, `"L"` if own < opp, `"T"` if equal.
  - **season** (view-computed): from `prs.game_round.match.season` —
    `season_id = match.season_id` (or `None`), `season_name = match.season.name`
    (or `""`).
  - box-score keys: `points_scored`, `tags_made`, `times_tagged`,
    `final_lives`, `resupplies_given`, `missiles_landed`, `specials_used`,
    `follow_up_shots`, `reaction_shots`, `combo_resupply_count` read directly;
    `mvp = float(prs.get_mvp)` (**property, no parens**);
    `accuracy = float(prs.get_accuracy())` (**method — CALL with `()`**);
    `nuke_detonations` from the detonation map.

> NOTE the asymmetry, pinned: `get_mvp` is a `@property` (no parens);
> `get_accuracy` is a plain method (call with `()`). The Player Stats screen
> uses `prs.get_accuracy` without parens because there it is a different
> property — but `matches/models.py::PlayerRoundState.get_accuracy` is defined
> as a METHOD `def get_accuracy(self)`. Use `prs.get_accuracy()` here. The Code
> agent MUST verify against `matches/models.py` and call accordingly; tests pin
> a non-zero accuracy value to catch a missing/extra call.

### 3.2 Season scope (LG-06d) + team filter (LG-06b)

- Season scope: call `_resolve_season_scope(request, league, displayed_season)`
  → `(seasons, selected_season, season_options, season_filter)`. `season_filter`
  is `{"match__season": <Season>}` (single) / `{"match__season__league":
  league}` (career) / `None` (empty). Re-point onto the PRS / GameEvent join as
  today: `prs_filter = {f"game_round__{k}": v for k, v in season_filter.items()}`
  and `match_filter = {k[len("match__"):]: v for k, v in season_filter.items()}`.
  When `season_filter is None` → render the empty-state (no Season) early.
- Team filter: `enrolled_teams = displayed_season.teams.order_by("name")`,
  `selected_team_id = _coerce_team_id(request.GET.get("team_id"),
  {t.id for t in enrolled_teams})`. Apply to the seam INPUTS before
  `scan_feats` (mirrors today): filter `player_rounds` by `team_id ==
  selected_team_id`; filter `matches` by `selected_team_id in {red_team_id,
  blue_team_id}`.

### 3.3 Pagination (LG-06a)

- `per_page = _coerce_per_page(request.GET.get("per_page"))` (whitelist
  `(10,25,50,100)`); `per_page_options = _LG01F_PER_PAGE_OPTIONS`.
- Sort the materialized `FeatRow` list FIRST (§3.4), THEN
  `Paginator(sorted_rows, per_page)`, `page_obj = paginator.get_page(
  _coerce_page(request.GET.get("page")))`.
- The Team-feats list is NOT paginated (it is the small separate section).

### 3.4 Sort (LG-06c) — expanded key set, default + tiebreak

- `sort = _coerce_sort_key(request.GET.get("sort"), _FEATS_SORT_KEYS,
  default="round")` and `direction = _coerce_dir(request.GET.get("dir"))`
  (`teams.views._coerce_dir` imported and reused verbatim;
  `matches.league_views._coerce_sort_key` for the key).
- **Default sort = most recent first**: `sort="round"`, `dir="desc"` (by
  Round recency).
- `_FEATS_SORT_KEYS` (frozenset) — EVERY column sortable. Pinned key set:
  ```
  {
    # descriptors / identity
    "name", "role", "team", "opp", "result", "season", "round", "feat",
    # box-score columns (13)
    "points_scored", "mvp", "tags_made", "times_tagged", "accuracy",
    "final_lives", "resupplies_given", "missiles_landed", "specials_used",
    "follow_up_shots", "reaction_shots", "combo_resupply_count",
    "nuke_detonations",
  }
  ```
- `_FEATS_SORT_KEYS_DISPLAY` — ordered `(key, label)` pairs for the header row
  (the template renders `statistical-feats-th-<key>` headers from this).
- Sort runs **view-side** over the materialized rows (the pure module already
  emitted them in its default order). A module-level helper
  `_feat_row_sort_value(row: FeatRow, key: str)` extracts the sort value:
  - `"name"` → `row.player_name.lower()`; `"role"` → `row.role`;
    `"team"` → `row.team_name.lower()`; `"opp"` → `row.opp_team_name.lower()`;
    `"result"` → `row.result`; `"season"` → `row.season_name.lower()`;
    `"round"` → `row.round_id`;
    `"feat"` → a stable join of the row's badge kinds, e.g.
    `",".join(sorted(b.kind for b in row.feats))`;
    any box-score key → `row.stats.get(key, 0.0)`.
- **Secondary tiebreak (always appended, deterministic):** `(round_id desc,
  player_id asc)` — i.e. `sorted(rows, key=lambda r: (_feat_row_sort_value(r,
  sort)), reverse=(dir=="desc"))` followed by a stable secondary; the cleanest
  pin is to sort by the secondary key first then by the primary with a stable
  sort, OR build a composite key. The Code agent MUST guarantee that two rows
  equal on the primary key always order by `round_id` desc then `player_id` asc.
  Numeric keys sort numerically; string keys case-insensitively; `result`
  sorts lexically ("L" < "T" < "W"). No key may raise on a `None` (descriptors
  are `""`-defaulted in the dataclass).

### 3.5 Comeback "Team feats" context key

`scan_feats` returns `(feat_rows, team_feats)`. The view passes `team_feats`
straight through as the context key `team_feats` (a `list[TeamFeatRecord]`).

### 3.6 ALL context keys (FROZEN list)

```
league
displayed_season
sidebar_links
sidebar_active            # "statistical_feats"
feat_rows                 # list[FeatRow] (the CURRENT page's rows: page_obj.object_list)
team_feats                # list[TeamFeatRecord]
box_score_columns         # the (key,label,is_float) display spec for the box-score <th>/<td>
sort                      # coerced sort key
dir                       # coerced direction
sort_keys                 # _FEATS_SORT_KEYS_DISPLAY (ordered (key,label) pairs)
per_page
per_page_options          # _LG01F_PER_PAGE_OPTIONS
page_obj                  # Paginator page (None in empty-state)
paginator                 # (None in empty-state)
season_options            # LG-06d picker options
selected_season           # "career" | int | None
enrolled_teams            # LG-06b picker options
selected_team_id          # int | None
querystring_without_sort  # for sort-header hrefs (carries season+team+per_page, pops sort/dir)
querystring_without_page  # for pagination links (carries season+team+sort/dir+per_page, pops page)
querystring_without_sort_dir_page  # for sort headers when also paginated (pops sort/dir/page)
```

(The empty-state render — no Season — supplies `feat_rows=[]`, `team_feats=[]`,
`page_obj=None`, `paginator=None`, the picker context keys, and empty
querystring strings, mirroring the Player Stats empty-state precedent.)

`box_score_columns` is a view-side display spec mirroring the Player Stats
`_PLAYER_STATS_COLUMNS` pattern: a tuple of `(key, label, is_float)` over the 13
`BOX_SCORE_KEYS` (mvp/accuracy `is_float=True`, the rest `False`). It is the
single source for both the box-score `<th>` headers and the per-row `<td>` cells.

### 3.7 Querystring-carry rules (which params each form/header carries; page reset)

- **Sort headers** carry: `season`, `team_id` (when set), `per_page`, and the
  new `sort`/`dir` for that column. They OMIT `page` (changing sort resets to
  page 1). Built from `querystring_without_sort_dir_page` (coerced values).
- **Pagination links** carry: `season`, `team_id` (when set), `sort`, `dir`,
  `per_page`; only `page` varies. Built from `querystring_without_page`.
- **Season `<select>` form** carries hidden inputs: `sort`, `dir`, `per_page`,
  and `team_id` (when set). It OMITS `page` (changing season resets to page 1).
- **Team `<select>` form** carries hidden inputs: `sort`, `dir`, `per_page`,
  `season`. It OMITS `page` (changing team resets to page 1).
- **Per-page `<select>` form** carries hidden inputs: `sort`, `dir`, `season`,
  and `team_id` (when set). It OMITS `page` (changing page size resets to page 1).
- All querystring helpers are built from the **COERCED** values (LG-00c
  precedent) so invalid params never survive into links.

---

## 4. Template `templates/leagues/statistical_feats.html`

Full rewrite from the current `<ul>`-of-feats into a sortable table + a separate
Team-feats section. Extends `base.html`; `d-flex` + `_partials/league_sidebar.html`
shell (sidebar renders even in the empty-state).

### 4.1 DOM-id list (locked)

Existing filter ids (PRESERVED — LG-06b / LG-06d already ship these):
- `statistical-feats-season-filter-form` / `statistical-feats-season-filter-select`
- `statistical-feats-team-filter-form` / `statistical-feats-team-filter-select`

NEW pagination ids (LG-06a):
- `statistical-feats-per-page-form`
- `statistical-feats-per-page-select`
- `statistical-feats-pagination` (rendered only when `paginator.num_pages > 1`)

Main table:
- `statistical-feats-table` (the outer per-player feed `<table>`, rendered only
  when `feat_rows` non-empty)
- Sort-header ids — one per sortable key: `statistical-feats-th-<key>` for every
  key in `_FEATS_SORT_KEYS_DISPLAY` (e.g. `statistical-feats-th-round`,
  `statistical-feats-th-name`, `statistical-feats-th-result`,
  `statistical-feats-th-points_scored`, …, `statistical-feats-th-feat`). The
  active header carries the ` ↑` (U+2191, asc) / ` ↓` (U+2193, desc) glyph.

Feat-badge rendering:
- Each row's `feats` rendered as badges; per-badge element class contains the
  substring `stat-feat-badge` and an id/class hook `stat-feat-badge-<kind>` so
  tests can assert a kind's presence (e.g. `stat-feat-badge-high_tags`,
  `stat-feat-badge-perfect_heavy`). A season-best badge additionally renders the
  text suffix `(season best)` (or a class substring `season-best`).

Team feats section:
- `statistical-feats-team-feats` (the section wrapper, rendered only when
  `team_feats` non-empty)
- per-record hook `stat-team-feat-<kind>` (e.g. `stat-team-feat-comeback_win`)
  with a deep-link to the Round when `round_id` is set.

Empty-state:
- `stat-feats-empty-notice` (PRESERVED id) — rendered when the League has no
  Season (substring `"No Season"`) OR when there are no feat rows AND no team
  feats (substring e.g. `"No statistical feats"`).

### 4.2 Columns rendered (per-player feed row, left→right)

Pinned column order:
1. **Player** (`player_name`) — links to the player career page is NOT required
   (out of scope); plain text.
2. **Role** (`role`).
3. **Team** (`team_name`).
4. **Opp** (`opp_team_name`).
5. **Result** (`result`, "W"/"L"/"T").
6. **Season** (`season_name`).
7. **Feats** (the stacked badges from `feats`).
8. **Box-score columns** — the 13 `box_score_columns` in order (Points, MVP,
   Tags, Tagged, Acc%, Lives, Resup, Missiles, Specials, Follow-up, Reaction,
   Combo Resup, Nukes), `mvp`/`accuracy` rendered with one decimal place.
9. **Round** — a deep-link "View round" anchor.

The header row exposes EVERY column as a sortable `statistical-feats-th-<key>`
header per §3.4 (the Feats column sorts on key `feat`, Round on key `round`).

### 4.3 Deep-link target

Each row's Round link uses `{% url 'game_round_detail' row.round_id %}`. The
Team-feats records link to `{% url 'game_round_detail' rec.round_id %}` when
`rec.round_id` is set.

---

## 5. Test boundary

### 5.1 Pure-module assertions (`matches/stat_feats.py`)

Test file: **`matches/tests/test_league_statistical_feats.py`** (EXTENDED — the
existing pure-unit + view classes are reshaped; the file is the single home for
both surfaces, as today).

Pure-unit (hand-built dict fixtures, no DB), asserting against `scan_feats`'s
new `(feat_rows, team_feats)` return + the dataclasses:

- **Qualification (hybrid):**
  - a row crossing each threshold (triple_nuke ≥3, medic_shutout, perfect_heavy,
    high_tags ≥20, high_points ≥12000, high_mvp ≥15, high_resupplies ≥20,
    high_missiles ≥8) is emitted with the right badge kind +
    `is_season_best=False`;
  - a row below ALL thresholds but holding a season-best is emitted with
    `is_season_best=True`;
  - a row below all thresholds AND not a season leader is NOT emitted.
- **Threshold edges:** value exactly at the constant qualifies (`>=`); one below
  does not.
- **Season-best selection + tiebreak:** the single highest value wins; ties
  resolved highest value → highest round_id → lowest player_id; all-zero-max
  stat produces NO season-best badge (§2.4 locked skip).
- **Badge stacking + collapse:** a round that is both ≥ threshold AND the season
  leader for the same kind carries ONE badge for that kind with
  `is_season_best=True` (§2.2 locked collapse); a round qualifying for multiple
  DIFFERENT kinds carries multiple badges, one per kind.
- **Feat-kind vocabulary:** every emitted badge's `kind` is in `FEAT_KINDS`;
  labels match `FEAT_KINDS`.
- **Deterministic order:** `feat_rows` returned in `round_id` DESC then
  `player_id` ASC; repeated calls equal.
- **Comeback:** `find_comeback_win` returns a `list[TeamFeatRecord]` —
  winner-lost-round-1 qualifies, winner-won-round-1 excluded, tie excluded,
  last-qualifier-chosen; the record's `team_name` / `round_id` correct.
- **`scan_feats` empty inputs** → `([], [])`.
- **`TestNoDjangoImportsLeaked`** subprocess purity check (RETAINED — must keep
  passing after the reshape).

### 5.2 View assertions (`matches.league_screens.statistical_feats`)

Django `TestCase` (hand-constructed `Match` / `GameRound` / `PlayerRoundState`
/ `GameEvent` rows — NO simulation), in the same test file:

- **Routing:** GET → 200; POST → 405; bad league id → 404
  (`get_object_or_404`); GET writes `request.session["last_league_id"]`.
- **Empty states:** no-Season League renders `stat-feats-empty-notice` +
  `"No Season"` + still renders `league-sidebar`; Season-with-no-feats renders
  `stat-feats-empty-notice`.
- **Per-player feed body:** a round crossing a threshold renders the
  `statistical-feats-table` with the row's `stat-feat-badge-<kind>` + the
  box-score cells + a `/matches/game-round/<id>/` deep link; the per-Round
  **result** is "W"/"L"/"T" derived from `GameRound.red_points`/`blue_points`
  (NOT the Match outcome) — assert a row whose Round result DIFFERS from the
  Match winner shows the per-Round result; **opp_team_name** shows the other
  team; **season_name** shows the Round's `Match.season.name`.
- **Accuracy call:** a PRS with `tags_made`/`shots_missed` producing a known
  non-zero `get_accuracy()` renders that value (catches a missing/extra `()`).
- **Season-best below threshold:** a round below all thresholds that is the
  season's top MVP/points/tags/resupplies/missiles is still listed with a
  "season best" badge.
- **Team feats section:** a comeback match renders `statistical-feats-team-feats`
  + `stat-team-feat-comeback_win` in the SEPARATE section (NOT a per-player row).
- **Pagination (LG-06a):** `statistical-feats-per-page-form` /
  `-per-page-select` present; `?per_page=10` paginates; the per-page form omits
  `page`; `statistical-feats-pagination` present only when > 1 page; sort runs
  BEFORE paginate (global top row leads on page 1).
- **Sort over ALL columns (LG-06c):** default order is most-recent
  (`round` desc); each key in `_FEATS_SORT_KEYS` sorts without 500 and
  asc/desc reverse over the same multiset; invalid `?sort=` / `?dir=` fall back
  to the default; `statistical-feats-th-<key>` ids present; active header glyph
  ↑/↓.
- **Filter coexistence:** `?season=` (LG-06d) + `?team_id=` (LG-06b) + `?sort=`
  + `?per_page=` honoured together; sort-header hrefs and the season/team/
  per-page forms carry the other params; changing season/team/sort/per-page
  omits `page` (resets to page 1). `?team_id=` narrows the feed to that team's
  rows (and the comeback to matches that team played).
- **`?season=career`** aggregates across all of THIS league's Seasons (the pool
  is the whole career list; season-best is league-wide).

### 5.3 What is INTERNAL (not asserted directly)

- The exact internal grouping/loop structure of `scan_feats`.
- The exact Bootstrap class names / cell markup (tests assert DOM ids, glyphs,
  substrings, and rendered values — not CSS classes beyond the locked
  `stat-feat-badge` / `season-best` substrings).
- The internal ordering of badges within a single row (only the SET of kinds +
  the `is_season_best` flag are asserted).

### 5.4 Test files (NEW + EXTENDED)

- **EXTENDED:** `matches/tests/test_league_statistical_feats.py` — the existing
  pure-unit classes (`TestFindTripleNukes`, `TestFindMedicShutout`,
  `TestFindPerfectHeavy`, `TestFindTopMvpAndScore`, `TestFindTagStreak`,
  `TestFindResuppliesAndMissiles`, `TestFindComebackWin`, `TestScanFeats`) are
  RESHAPED to the new dataclass / return shape; the view classes
  (`TestStatisticalFeatsRouting`, `…EmptyState`, `…Body`, `…TeamFilter`,
  `…ComebackFilter`, the `…Sort*` family) are RESHAPED to the table + badge +
  pagination surface. `TestNoDjangoImportsLeaked` retained verbatim.
- **NEW:** none required — the single existing test file covers both surfaces.
  (The Code/Tests agents MAY add a separate `test_stat_feats.py` pure-unit file
  if they prefer to split pure-module tests from view tests; if so its name is
  locked as `matches/tests/test_stat_feats.py`. The default is to keep both in
  the one existing file.)

---

## 6. Locked names (quick index)

- Pure module: `matches/stat_feats.py`.
- Dataclasses: `FeatRow` (12 fields, §2.6), `FeatBadge` (`kind`, `label`,
  `is_season_best`), `TeamFeatRecord` (`kind`, `label`, `team_name`,
  `round_id`).
- Tuples: `BOX_SCORE_KEYS` (13), `FEAT_KINDS` (8 `(kind,label)` pairs),
  `SEASON_BEST_STATS` (5).
- Constants: `TRIPLE_NUKE_THRESHOLD=3`, `HIGH_TAGS_THRESHOLD=20`,
  `HIGH_POINTS_THRESHOLD=12000`, `HIGH_MVP_THRESHOLD=15`,
  `HIGH_RESUPPLIES_THRESHOLD=20`, `HIGH_MISSILES_THRESHOLD=8`.
- Functions: `scan_feats(player_rounds, matches) ->
  tuple[list[FeatRow], list[TeamFeatRecord]]`,
  `find_comeback_win(matches) -> list[TeamFeatRecord]`.
- Feat kinds: `triple_nuke`, `medic_shutout`, `perfect_heavy`, `high_tags`,
  `high_points`, `high_mvp`, `high_resupplies`, `high_missiles`;
  team-feat kind `comeback_win`.
- View: `matches.league_screens.statistical_feats.statistical_feats`;
  `_FEATS_SORT_KEYS` (expanded frozenset), `_FEATS_SORT_KEYS_DISPLAY`,
  `_feat_row_sort_value`; default sort `("round", "desc")`.
- Reused helpers: `matches.league_views._resolve_season_scope`,
  `_season_param`, `_coerce_team_id`, `_coerce_per_page`, `_coerce_page`,
  `_coerce_sort_key`, `_build_league_sidebar_links`, `_LG01F_PER_PAGE_OPTIONS`;
  `teams.views._coerce_dir`.
- URL name: `stats_statistical_feats` (UNCHANGED).
- Template: `templates/leagues/statistical_feats.html`.
- DOM ids: `statistical-feats-season-filter-form/-select`,
  `statistical-feats-team-filter-form/-select`,
  `statistical-feats-per-page-form/-select`, `statistical-feats-pagination`,
  `statistical-feats-table`, `statistical-feats-th-<key>`,
  `stat-feat-badge-<kind>`, `statistical-feats-team-feats`,
  `stat-team-feat-<kind>`, `stat-feats-empty-notice`.
- Deep-link: `game_round_detail`.
- Context keys: §3.6 frozen list.
- Test file: `matches/tests/test_league_statistical_feats.py` (EXTENDED;
  optional split `matches/tests/test_stat_feats.py`).
- Model fields confirmed: `GameRound.red_points` / `blue_points` /
  `red_team_eliminated` / `blue_team_eliminated` / `round_number` / `date_played`
  / `team_red` / `team_blue` / `match`; `Match.season` / `winner` /
  `red_round1_points` / `blue_round1_points`; `Season.name`;
  `PlayerRoundState.get_mvp` (property), `get_accuracy()` (method — CALL with
  `()`), `team_color`, `role`, plus the box-score count fields.
