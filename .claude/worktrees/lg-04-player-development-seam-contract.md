# LG-04 · Season-end stat updates — Seam Contract

**Task:** ZenGM-style player development (age-curve) + a per-Season ratings history.
**Branch:** `lg-04-player-development`. **ADR:** `docs/adr/0024-zengm-player-development-ratings-history.md` (already written — do not re-author).
**Status:** awaiting approval. Code / Tests / Docs agents implement against the locked names below.

This contract pins every model field, migration op, pure-function signature, the **complete 19-stat age-curve table**, the two view insertion points, the UI stub-fill, and the test boundary. Where a name or number is given, it is **locked** — the Code agent and the Tests agent must not disagree.

All paths below are relative to the nested Django project root `laserforce_simulator/laserforce_simulator/` unless they start with `.claude/` or `docs/` (repo root).

---

## 0. Locked decisions recap (from the completed grill — encoded below, not relitigated)

1. **Age-curve development, faithful to ZenGM.** Age drives ratings; games-played is NOT an input. Per-season change = `(baseChange(age) + perStatAgeModifier(age)) × uniform(0.4, 1.4)`, clamped to the per-stat change limits, added to the live value, then floored to `[0, 100]`. Coaching effect is fixed at **0** (pure age curve).
2. **Full per-stat age curves.** Each of the 19 `Player` stats gets its own `ageModifier(age)` + `changeLimits(age)`, mapped by analogy to a ZenGM archetype. The complete proposed table is §3.2 below — locked-but-tunable constants.
3. **Trigger = `next_season`** (the preseason analogue), inside its existing `@transaction.atomic`, AFTER the carry-forward. Develops every Player on the rolling League's snapshot Teams (active slots + bench) + `league.free_agent_pool` Players: age `+1` → develop (mutate live `Player` stat fields in place) → tick `total_games` → write one `PlayerSeasonRating` row for the NEW Season. League-isolated; NO cross-League guard.
4. **`total_games` is cosmetic** (never a develop input). Tick rule in §5.4.
5. **Baseline `PlayerSeasonRating` rows at `league_create`** (as-generated stats, current age, no development).
6. **New model `PlayerSeasonRating`** in `matches/models.py` — §1.
7. **Pure module `matches/development.py`** — Django-free, RNG injected, defended by a `TestNoDjangoImportsLeaked` subprocess check — §3.
8. **UI** fills the LG-06h `league-player-ratings-history-stub` — §6.
9. **No Score Calibration re-baseline** (no simulation mechanic change). No new CONTEXT.md term beyond the ADR's **Player development** / **Ratings history** (already written).

---

## 1. `PlayerSeasonRating` model (`matches/models.py`)

**Placement:** declared **immediately after the `Season` class and before `SeasonPhase`** in `matches/models.py`. (It FKs both `teams.Player` and `matches.Season`; `Season` is defined above it, `Player` is already imported at the top of the file via `from teams.models import Team, Player`.)

```python
class PlayerSeasonRating(models.Model):
    """LG-04 — an immutable per-Season snapshot of a Player's 19 stat ratings,
    age, and overall rating at the start of a Season (a baseline row at
    league_create, a developed row at each next_season rollover).

    Read-only audit trail: the live ``teams.Player`` stat fields remain the
    Simulator's source of truth; these rows are never read back by the engine.
    ``potential`` is reserved (always NULL until LG-05).
    """

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="season_ratings",
    )
    season = models.ForeignKey(
        "matches.Season",
        on_delete=models.CASCADE,
        related_name="player_ratings",
    )

    # Snapshot of the player's age at the time this row was written.
    age = models.IntegerField(null=True, blank=True)

    # Snapshot of all 19 stat ints — same names + the capital-O quirk as Player.
    player_awareness = models.IntegerField()
    game_awareness = models.IntegerField()
    resource_awareness = models.IntegerField()
    decision_making = models.IntegerField()
    positioning = models.IntegerField()
    stamina = models.IntegerField()
    speed = models.IntegerField()
    flexibility = models.IntegerField()
    adaptability = models.IntegerField()
    communication = models.IntegerField()
    teamwork = models.IntegerField()
    Offensive_synergy = models.IntegerField()
    defensive_synergy = models.IntegerField()
    midfield_synergy = models.IntegerField()
    resupply_synergy = models.IntegerField()
    resupply_efficiency = models.IntegerField()
    accuracy = models.IntegerField()
    survival = models.IntegerField()
    special_usage = models.IntegerField()

    # Snapshot of the unweighted-mean overall at write time (float — mirrors
    # Player.overall_rating's float return).
    overall_rating = models.FloatField()

    # Reserved for LG-05; always NULL in LG-04.
    potential = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["player_id", "season_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "season"],
                name="uniq_player_season_rating",
            )
        ]

    def __str__(self) -> str:
        return f"{self.player.name} — {self.season.name} (ovr {self.overall_rating:.1f})"
```

### Locked field facts
- **19 stat fields**, names byte-for-byte identical to `Player` — including the **intentional capital-O `Offensive_synergy`** (every other stat lowercase). Source of truth: `teams.player_generator._STAT_FIELDS` (19-tuple, canonical order) and the `Player` model. **No validators** on the snapshot ints (the writer always supplies clamped `[0,100]` values from the pure module; the live `Player` fields carry the validators).
- `age` is `IntegerField(null=True, blank=True)` — mirrors `Player.age` (which is nullable). A baseline row copies the live `Player.age`; a developed row stores the post-`+1` age.
- `overall_rating` is `FloatField` (matches `Player.overall_rating`'s `sum/len` float).
- `potential` is `FloatField(null=True, blank=True)` — **always written as `None` in LG-04**; reserved for LG-05's Monte-Carlo `pot`.
- FK on_delete: **CASCADE** on both (a deleted Player or Season drops its rating snapshots — disposable derived data, ADR-0004 posture). related_names: `Player.season_ratings`, `Season.player_ratings`.
- Constraint: `unique(player, season)` named **`uniq_player_season_rating`** (one row per player per season — re-running a rollover must not duplicate).
- `Meta.ordering = ["player_id", "season_id"]`.

---

## 2. Migration

- **Filename:** `matches/migrations/0048_playerseasonrating.py`.
- **Dependency:** `("matches", "0047_seasonphase_tournament_subconfig")` (the current latest matches migration — verified by inspecting `matches/migrations/`). It also implicitly depends on the latest `teams` migration via the `teams.Player` FK; let `makemigrations` resolve the `teams` swappable/initial dependency automatically rather than hard-coding it.
- **Operations:** exactly **one `CreateModel(PlayerSeasonRating)`** carrying all 21 fields + the `UniqueConstraint` + `Meta.ordering`. **NO `RunPython`, NO `RunSQL`, NO backfill** — existing Leagues/Seasons get no historical rows (ADR-0004 disposable-data posture, the `0029`/`0041`/`0042`/`0047` precedent).
- Generate via `python laserforce_simulator/manage.py makemigrations matches` and verify the filename/number, then `python laserforce_simulator/manage.py makemigrations --check --dry-run` is clean.

---

## 3. Pure module `matches/development.py`

**Frozen import allowlist (LOCKED):** `dataclasses`, `typing`, `random`, `collections` ONLY. **NO** `django`, NO ORM, NO `datetime`, NO `math` (not needed — all math is integer/float arithmetic), NO I/O, NO logging. The 19 stat field NAMES are **hand-rolled locally** (do NOT import from `teams`/Django) — mirroring `matches/draw.py`, `matches/bracket.py`, and `teams/roster_importer.py`. Defended by `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk; mirror `matches/tests/test_draw.py` / `test_bracket.py`).

`random` is allowlisted because the develop math consumes an **injected** `random.Random`. Production builds a **fresh `random.Random()`** per rollover; tests inject a seeded one. **No seed is stored** anywhere — the `PlayerSeasonRating` row IS the audit trail.

### 3.1 Module-level constants

```python
import random
from typing import Mapping

# The 19 Player stat field names, canonical order, hand-rolled locally so this
# module stays Django-free. MUST equal teams.player_generator._STAT_FIELDS
# byte-for-byte — incl. the intentional capital-O Offensive_synergy. Pinned by
# test_stat_fields_equals_player_generator (the ONE allowed teams import, in the
# pure-unit test file only).
STAT_FIELDS: tuple[str, ...] = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)

STAT_MIN: int = 0
STAT_MAX: int = 100
```

### 3.2 Age base-change table — `base_change(age: int) -> int`

Faithful to ZenGM's `calcBaseChange` (basketball), with `coachingLevel` fixed at **0** so the coaching multiplier is a no-op. The age → base-change table (LOCKED, tunable):

| Age band | Base change |
| --- | --- |
| ≤ 21 | **+2** |
| 22–25 | **+1** |
| 26–27 | **0** |
| 28–29 | **−1** |
| 30–31 | **−2** |
| 32–34 | **−3** |
| 35–40 | **−4** |
| 41–43 | **−5** |
| ≥ 44 | **−6** |

```python
def base_change(age: int) -> int:
    """ZenGM calcBaseChange age table (coaching fixed at 0). Pure, total."""
    if age <= 21:
        return 2
    if age <= 25:
        return 1
    if age <= 27:
        return 0
    if age <= 29:
        return -1
    if age <= 31:
        return -2
    if age <= 34:
        return -3
    if age <= 40:
        return -4
    if age <= 43:
        return -5
    return -6
```

A `None` age must never reach this function — the **view coalesces a `None` `Player.age` to a default before calling the developer** (see §5.3; the rule: treat `None` as `25`). `base_change` itself takes a plain `int`.

### 3.3 Noise — `base_change_noise(age: int, rng: random.Random) -> float`

ZenGM adds age-banded gaussian noise to the base change (young players are volatile, veterans predictable). LOCKED bands (`bound(x, lo, hi)` clamps `x` to `[lo, hi]`):

| Age band | Noise |
| --- | --- |
| ≤ 23 | `bound(rng.gauss(0, 5), -4, 20)` |
| 24–25 | `bound(rng.gauss(0, 5), -4, 10)` |
| ≥ 26 | `bound(rng.gauss(0, 3), -2, 4)` |

```python
def _bound(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def base_change_noise(age: int, rng: random.Random) -> float:
    if age <= 23:
        return _bound(rng.gauss(0, 5), -4.0, 20.0)
    if age <= 25:
        return _bound(rng.gauss(0, 5), -4.0, 10.0)
    return _bound(rng.gauss(0, 3), -2.0, 4.0)
```

The **per-player effective base change** is `base_change(age) + base_change_noise(age, rng)` — one noise draw per player per rollover, shared across all 19 stats (matching ZenGM, where the noisy base change is computed once then a per-rating modifier is added).

### 3.4 Per-stat age-curve table — the heart of the contract

Each of the 19 stats is assigned a ZenGM **archetype group**; the group fixes its `ageModifier(age)` and `changeLimits(age)`. No laser-tag regression data exists, so this mapping is **invented-by-analogy, locked-but-tunable**. The Code agent implements the five group functions below and the `_STAT_ARCHETYPE` mapping; the Tests agent pins each stat's group + the group boundary values.

#### Archetype → laser-tag stat mapping (LOCKED)

| Archetype group | ZenGM analogue | Laser-tag stats in this group | Rationale |
| --- | --- | --- | --- |
| **`athletic`** | speed / jump (athleticism fades earliest) | `speed`, `stamina`, `flexibility` | physical tools peak early, decline fast |
| **`skill`** | shooting touch (persists into 30s) | `accuracy`, `special_usage`, `resupply_efficiency` | trained mechanical skills hold late |
| **`awareness`** | basketball IQ (grows with experience, resists late decline) | `player_awareness`, `game_awareness`, `resource_awareness`, `decision_making`, `positioning` | cognitive/court-sense ratings keep climbing young, fade last |
| **`team`** | passing/IQ-adjacent (mild positive, modest limits) | `communication`, `teamwork`, `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`, `resupply_synergy` | coordination skills, slow steady curve |
| **`durable`** | strength (no age modifier, follows base change) | `survival` | grit/longevity stat — neutral curve |

That is **3 + 3 + 5 + 6 + 1 = 18**… wait — recount: athletic 3, skill 3, awareness 5, team 6, durable 1 = **18**. The 19th stat is **`adaptability`** → assign to **`awareness`** (so awareness = 6). Final per-stat assignment (LOCKED, exhaustive — all 19):

| Stat | Group |
| --- | --- |
| `player_awareness` | awareness |
| `game_awareness` | awareness |
| `resource_awareness` | awareness |
| `decision_making` | awareness |
| `positioning` | awareness |
| `adaptability` | awareness |
| `stamina` | athletic |
| `speed` | athletic |
| `flexibility` | athletic |
| `communication` | team |
| `teamwork` | team |
| `Offensive_synergy` | team |
| `defensive_synergy` | team |
| `midfield_synergy` | team |
| `resupply_synergy` | team |
| `resupply_efficiency` | skill |
| `accuracy` | skill |
| `special_usage` | skill |
| `survival` | durable |

Counts: awareness 6, athletic 3, team 6, skill 3, durable 1 = **19**. ✓

```python
_STAT_ARCHETYPE: dict[str, str] = {
    "player_awareness": "awareness",
    "game_awareness": "awareness",
    "resource_awareness": "awareness",
    "decision_making": "awareness",
    "positioning": "awareness",
    "adaptability": "awareness",
    "stamina": "athletic",
    "speed": "athletic",
    "flexibility": "athletic",
    "communication": "team",
    "teamwork": "team",
    "Offensive_synergy": "team",
    "defensive_synergy": "team",
    "midfield_synergy": "team",
    "resupply_synergy": "team",
    "resupply_efficiency": "skill",
    "accuracy": "skill",
    "special_usage": "skill",
    "survival": "durable",
}
```

#### Per-group `ageModifier(age)` — LOCKED

```python
def age_modifier(group: str, age: int) -> float:
    if group == "awareness":
        # Big positive when young (experience compounds), resists late decline.
        if age <= 21:
            return 4.0
        if age <= 23:
            return 3.0
        if age <= 27:
            return 1.0
        if age <= 31:
            return 0.0
        return 0.5  # mild offset against base-change decline (IQ persists)
    if group == "skill":
        # Flat young, positive past prime (touch/technique held into 30s).
        if age <= 27:
            return 0.0
        if age <= 31:
            return 0.5
        return 1.5
    if group == "athletic":
        # Increasingly negative with age (athleticism fades first).
        if age <= 23:
            return 0.0
        if age <= 27:
            return -0.5
        if age <= 31:
            return -2.0
        return -4.0
    if group == "team":
        # Mild positive young, gentle taper.
        if age <= 25:
            return 1.0
        if age <= 31:
            return 0.0
        return -0.5
    # "durable" — no age modifier; follows the base change + noise only.
    return 0.0
```

#### Per-group `changeLimits(age)` — LOCKED

Returns `(lo, hi)` clamping the per-season delta for a stat in that group **before** it is added to the live value.

```python
def change_limits(group: str, age: int) -> tuple[float, float]:
    if group == "awareness":
        # Young players can gain a lot; ZenGM-style widening upper bound.
        if age <= 24:
            return (-3.0, 7.0 + 5.0 * (24 - age))  # up to large early gains
        return (-3.0, 7.0)
    if group == "skill":
        return (-3.0, 13.0)
    if group == "athletic":
        return (-12.0, 2.0)  # can crash, barely improves
    if group == "team":
        return (-2.0, 5.0)
    # "durable" — unbounded (follows base change + noise only).
    return (-100.0, 100.0)
```

> Note the awareness widening: `7 + 5*(24 - age)` mirrors ZenGM's IQ formula (a 19-year-old's cap is `7 + 25 = 32`). The `durable` `(-100, 100)` is effectively unbounded across one `[0,100]` step.

### 3.5 The clamp — `_clamp_int(value: float) -> int`

```python
def _clamp_int(value: float) -> int:
    """Round to int and floor to [STAT_MIN, STAT_MAX]."""
    return max(STAT_MIN, min(STAT_MAX, round(value)))
```

`round()` is banker's rounding (Python default) — matching the `teams/career_stats.py` precedent.

### 3.6 The per-stat delta — `develop_stat(...)`

```python
def develop_stat(
    current: int,
    stat_name: str,
    age: int,
    effective_base_change: float,
    rng: random.Random,
) -> int:
    """One stat's developed value for one season.

    delta = (effective_base_change + ageModifier(group, age)) * uniform(0.4, 1.4)
    delta = bound(delta, *changeLimits(group, age))
    return clampInt(current + delta)
    """
    group = _STAT_ARCHETYPE[stat_name]
    raw = (effective_base_change + age_modifier(group, age)) * rng.uniform(0.4, 1.4)
    lo, hi = change_limits(group, age)
    delta = _bound(raw, lo, hi)
    return _clamp_int(current + delta)
```

**RNG consumption order is pinned** so seeded tests are deterministic: per player per rollover the noise draw (`base_change_noise`, one `gauss`) happens **first**, then the 19 `develop_stat` calls draw their `uniform(0.4, 1.4)` in `STAT_FIELDS` order.

### 3.7 The whole-player developer — `develop_player_stats(...)`

```python
def develop_player_stats(
    stats: Mapping[str, int],
    age: int,
    rng: random.Random,
) -> dict[str, int]:
    """Develop all 19 stats one season for a player of the given (already
    aged) ``age``. ``stats`` is a 19-key mapping (every STAT_FIELDS key).
    Returns a fresh 19-key dict of clamped [0,100] ints.

    Pure: receives the RNG; consumes exactly 1 gauss + 19 uniform draws,
    in (noise, then STAT_FIELDS order) sequence.
    """
    effective = base_change(age) + base_change_noise(age, rng)
    return {
        name: develop_stat(stats[name], name, age, effective, rng)
        for name in STAT_FIELDS
    }
```

> The view passes the **already-incremented** age (the develop step runs on age `+1`). The dict comprehension iterates `STAT_FIELDS` so RNG order is fixed.

### 3.8 The cosmetic `total_games` free-agent tick — `free_agent_games_tick(...)`

The active-player appearance count + the **median** are ORM-side (the view computes them — §5.4). The **only** pure piece is the free-agent random draw:

```python
def free_agent_games_tick(median_active: int, rng: random.Random) -> int:
    """Cosmetic total_games bump for a free-agent-pool player at rollover.

    Returns rng.randint(0, median_active // 2). Degenerate no-active case
    (median_active == 0) returns 0 deterministically (randint(0, 0) == 0).
    """
    return rng.randint(0, max(0, median_active) // 2)
```

### 3.9 Pure module public surface (LOCKED)

| Symbol | Kind | Signature / shape |
| --- | --- | --- |
| `STAT_FIELDS` | tuple | 19 names, == `teams.player_generator._STAT_FIELDS` |
| `STAT_MIN` / `STAT_MAX` | int | `0` / `100` |
| `base_change(age)` | fn | `int -> int` |
| `base_change_noise(age, rng)` | fn | `(int, Random) -> float` |
| `age_modifier(group, age)` | fn | `(str, int) -> float` |
| `change_limits(group, age)` | fn | `(str, int) -> tuple[float, float]` |
| `develop_stat(current, stat_name, age, effective_base_change, rng)` | fn | `... -> int` |
| `develop_player_stats(stats, age, rng)` | fn | `(Mapping[str,int], int, Random) -> dict[str,int]` |
| `free_agent_games_tick(median_active, rng)` | fn | `(int, Random) -> int` |
| `_STAT_ARCHETYPE` | dict | 19 entries (private but test-readable) |
| `_bound` / `_clamp_int` | fn | private helpers |

Pure vs ORM split: **everything in `development.py` is pure** (RNG injected). The view (§5) owns: the developing-set ORM query, the active-appearance-count + median ORM queries, the `Player` field mutation + `save`, and the `PlayerSeasonRating.objects.bulk_create`/`create`. The free-agent randint is pure (`free_agent_games_tick`); the median that feeds it is ORM-computed.

---

## 4. `league_create` change — baseline rows

**File:** `matches/league_views.py`, inside `league_create` (function starts line 533), inside the existing function-level `@transaction.atomic`.

**Insertion point:** AFTER the founding Teams + their Players exist and the `league.free_agent_pool` pool Players have been generated, and AFTER `season = Season.objects.create(...)` exists (we tag baseline rows to the founding draft Season). Concretely: after the `_generate_free_agents(...)` call and after the `Season.objects.create(...)` that the function already does. (Do NOT depend on the season being `active` — baseline rows tag to the founding draft Season's id.)

**Helper (new, private, module-level in `matches/league_views.py`):**

```python
def _write_baseline_ratings(season: "Season", players: "Iterable[Player]") -> None:
    """LG-04 — write an as-generated PlayerSeasonRating baseline row for each
    founding Player (current stats, current age, current overall_rating,
    potential=None). No development. Bulk-created in one query.
    """
```

- **Which Players get baseline rows:** every founding Player — i.e. every Player on the newly-created competitive Teams (active slots **and** bench) PLUS every Player in `league.free_agent_pool`. The gatherer is the same set-builder used by `next_season` (§5.2) — factor it as `_developing_players(league)` and reuse it here (at `league_create` time the snapshot Teams are the just-created Teams enrolled in the draft Season; the free-agent pool is `pool_team`). Simplest correct sourcing at create time: gather `created_teams`' players (`team.players.all()` per team) + `pool_team.players.all()`.
- **Each baseline row carries:** `player=p`, `season=season` (the founding draft Season), `age=p.age` (verbatim, may be `None`), the 19 stat fields copied straight off `p` via `getattr(p, name)` for `name in development.STAT_FIELDS`, `overall_rating=p.overall_rating`, `potential=None`.
- **No development, no age tick, no `total_games` tick** at baseline.
- Use `PlayerSeasonRating.objects.bulk_create([...])` for the whole founding set (one query).

---

## 5. `next_season` change — develop + persist loop

**File:** `matches/league_views.py`, inside `next_season` (function starts line 2366, decorated `@transaction.atomic`).

**Insertion point:** at the **END** of the existing body, INSIDE the `@transaction.atomic`, **AFTER** the team carry-forward (`new_season.teams.add(...)`), the map-pool rehydrate, and the phase-composition copy loop, and **BEFORE** the final `return redirect("season_dashboard", season_id=new_season.id)`. Develop rows tag to **`new_season`** (the NEW Season).

### 5.1 The orchestration helper (new, private, module-level)

```python
def _develop_league_for_new_season(league: "League", new_season: "Season") -> None:
    """LG-04 — age + develop every Player in the rolling League's developing
    set, tick total_games, and write one PlayerSeasonRating row tagged to
    new_season. Called inside next_season's atomic block, after carry-forward.

    Builds a fresh random.Random() (no stored seed). League-isolated; NO
    cross-League guard.
    """
```

It does, in order:
1. `rng = random.Random()` — fresh OS entropy (production). (Tests call the lower-level pieces with a seeded `Random`; see §7.)
2. `players = _developing_players(league)` — the developing set (§5.2).
3. Compute the completed-Season appearance counts + median (§5.4) once, scoped to `latest_completed` (the just-completed Season the rollover is from).
4. Partition `players` into active-roster players vs free-agent-pool players (§5.4).
5. For each player: age `+1` → `develop_player_stats` → mutate the 19 live fields + `age` → tick `total_games` → recompute `overall_rating` → stage a `PlayerSeasonRating` row.
6. `Player.objects.bulk_update(players, fields=[*STAT_FIELDS, "age", "total_games"])` and `PlayerSeasonRating.objects.bulk_create(rows)` — two bulk queries.

### 5.2 Developing-set gatherer — `_developing_players(league)`

```python
def _developing_players(league: "League") -> "list[Player]":
    """LG-04 — the rolling League's snapshot Teams' players (active slots +
    bench) plus the league.free_agent_pool players. De-duplicated by pk.
    """
```

- **Snapshot Teams:** the Teams enrolled in the just-completed Season's snapshot. Source the team ids from `latest_completed.starting_team_ids_json` (the frozen snapshot — the same source `next_season` uses for `team_ids` carry-forward); resolve `Team.objects.filter(id__in=team_ids)`.
- **Per Team:** every `team.players.all()` — this is `Player.team` membership, which already includes both active-slot players and bench players (bench = on the Team but not in a `slot_*` FK; `Player.team` covers both). **Do NOT** gather only `team.active_players` — bench players develop too.
- **Free agents:** `league.free_agent_pool.players.all()` when `free_agent_pool` is not None.
- De-dup by pk (a player should never be in two of these sets within one League, but guard defensively).

> `_develop_league_for_new_season` needs `latest_completed`. `next_season` already computes `latest_completed` (line 2400). Pass it through, or recompute inside the helper from `league` — pinned choice: **pass `latest_completed` in** as a third arg (`_develop_league_for_new_season(league, new_season, latest_completed)`) so the helper does not re-query.

### 5.3 Per-player develop sequence (LOCKED order)

For each `player` in the developing set:
1. **Resolve effective age for development:** `raw_age = player.age if player.age is not None else 25`. (A `None` age is coalesced to `25` — the peak band — only for the develop math; see §3.2. This is a defensive guard; generated players always have an int age.)
2. **Age tick:** `player.age = raw_age + 1` (write the incremented age back onto the live Player — even if it was `None`, it becomes `26`). The develop math uses the **incremented** age.
3. **Develop:** `new_stats = development.develop_player_stats({name: getattr(player, name) for name in STAT_FIELDS}, player.age, rng)`.
4. **Mutate live fields:** `for name, val in new_stats.items(): setattr(player, name, val)`.
5. **`total_games` tick:** per §5.4.
6. **Stage the rating row:** `PlayerSeasonRating(player=player, season=new_season, age=player.age, **new_stats, overall_rating=<recomputed>, potential=None)`.

**`overall_rating` recompute:** the row's `overall_rating` is the unweighted mean of the **developed** 19 stats — i.e. `sum(new_stats.values()) / 19` (equivalently `player.overall_rating` read AFTER the live fields are mutated, since `Player.overall_rating` is the same mean). Pin the row value to `sum(new_stats.values()) / len(STAT_FIELDS)` to avoid an extra property call ordering hazard.

### 5.4 `total_games` tick (cosmetic — never a develop input)

Computed AFTER the develop math (order-independent, but keep it in the same loop):

- **Active-Team players:** `total_games += <their real regular-season appearance count in latest_completed>`.
- **Free-agent-pool players:** `total_games += development.free_agent_games_tick(median_active, rng)`.

**Appearance counts (ORM, scoped to `latest_completed`):** count `PlayerRoundState` rows where `game_round__match__season == latest_completed`, grouped by `player_id`. Playoff rounds carry `match.season = NULL` (Part2c-1 #3) so they are **naturally excluded** — only regular-season appearances count. Implementation:

```python
from django.db.models import Count
appearances = dict(
    PlayerRoundState.objects.filter(game_round__match__season=latest_completed)
    .values("player_id")
    .annotate(n=Count("id"))
    .values_list("player_id", "n")
)
```
A player with no rows ⇒ `appearances.get(pk, 0)`.

**`median_active`:** the median of the **active players' season appearance counts** — i.e. the median over the active-Team players (NOT free agents) of `appearances.get(pk, 0)`. Use a plain median (sorted middle / mean of two middles). **Degenerate no-active case ⇒ `median_active = 0`** (and `free_agent_games_tick(0, rng) == 0`).

**Active vs free-agent partition:** a player is "active" iff their pk is in the snapshot-Teams' player-id set; "free agent" iff in the `free_agent_pool` set. Build both id-sets in `_developing_players` (or recompute in the orchestrator) so the tick branch is a cheap membership test.

### 5.5 Persistence

- `Player.objects.bulk_update(players, fields=[*development.STAT_FIELDS, "age", "total_games"])` — one query for all live-field mutations.
- `PlayerSeasonRating.objects.bulk_create(rating_rows)` — one query for the new-Season snapshots.
- All inside the existing `next_season` `@transaction.atomic`, so a failure rolls back the whole rollover (new Season + carry-forward + development) atomically.

---

## 6. View + template (LG-06h stub fill)

**Goal:** fill the existing `league-player-ratings-history-stub` block in `templates/leagues/player_detail.html` with a read-only overall-rating-over-time trend chart (Chart.js, the HX-01 precedent) + a per-Season stat table, driven by the player's `PlayerSeasonRating` rows scoped to this League.

### 6.1 View change — `matches/league_screens/player_detail.py`

Add one context key to `player_detail(request, league_id, player_id)`:

- **`ratings_history: list[dict]`** — one entry per `PlayerSeasonRating` row for this Player whose Season belongs to this League, **oldest-first** (ascending by `season_id`, so the trend reads left-to-right in chronological order). Query:

```python
psr_qs = (
    PlayerSeasonRating.objects.filter(player=player, season__league=league)
    .select_related("season")
    .order_by("season_id")
)
ratings_history = [
    {
        "season_id": r.season_id,
        "season_name": r.season.name,
        "age": r.age,
        "overall_rating": r.overall_rating,
        "potential": r.potential,  # always None in LG-04 → renders "—"
        "stats": {name: getattr(r, name) for name in development.STAT_FIELDS},
    }
    for r in psr_qs
]
```

- Import: `from matches.models import PlayerSeasonRating` and `from matches import development` (for `STAT_FIELDS`) at the top of `player_detail.py`.
- The context-key name `ratings_history` is **added to the frozen-keys docstring** of `player_detail` (the existing docstring lists the seam keys).
- The trend series for the chart is `[[i, overall_rating], …]` 0-based index along x, OR `[[season_name, overall], …]` — pinned choice: the template builds a `json_script` list of `[label, value]` pairs **in the template** from `ratings_history` (label = `season_name`, value = `overall_rating`), mirroring HX-01's `trend|json_script` shape. No extra view-side list needed.

### 6.2 Template change — `templates/leagues/player_detail.html`

**Replace** the stub block (currently DOM id `league-player-ratings-history-stub`, lines 207–212 in the current file) with a live block. **The DOM id changes from the `-stub` suffix to the live id `league-player-ratings-history`** (mirroring how LG-03's awards block dropped its `-stub` suffix to `league-player-awards`). Tests assert the live id is present and the old `-stub` id is absent.

Live block contents:
- A `<canvas id="league-player-ratings-history-chart">` + a `{{ <trend-list>|json_script:"league-player-ratings-history-data" }}` + a Chart.js `<script>` (the HX-01 pattern: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>` then an IIFE building the line chart). Dataset label `"Overall rating"`, x-axis title `"Season"`, y-axis title `"Overall rating"`, `pointRadius: 2`. (HX-01 uses a dashed rolling line; LG-04's trend is a solid per-Season line — `borderDash` omitted.)
- A per-Season stat table `<table id="league-player-ratings-history-table">` with a header row `Season | Age | Ovr | Pot | <19 stat labels>` and one `<tbody>` row per `ratings_history` entry (oldest-first). `Pot` renders the em-dash `—` when `potential is None` (always, this slice). The 19 stat cells render in `STAT_FIELDS` order; because Django can't dict-lookup by a loop variable, render each known key explicitly (the `player_detail.html` RS-table precedent at lines 149–168).
- An empty-state: when `ratings_history` is empty, render a notice inside `league-player-ratings-history` with DOM id `league-player-ratings-history-empty` (substring e.g. `"No ratings history"`).

> Chart.js availability: HX-01 includes the CDN `<script>` inline on its own page. Mirror that — include the CDN `<script>` inside the new block (or once near the bottom of `player_detail.html`). Do not assume `base.html` provides Chart.js.

### 6.3 What stays untouched
- The global HX-01 career page (`/players/<id>/stats/`, `player_career_stats`) is **not** touched.
- The LG-03 `league-player-awards` block, the `league-player-playoffs-stub`, `league-player-salaries-stub`, `league-player-transactions-stub` are unchanged.
- `league-player-potential` (the Potential card, still `—` until LG-05) is unchanged.

---

## 7. Test boundary

### 7.1 Pure-module unit tests — `matches/tests/test_development.py`

No Django DB; build hand-rolled stat dicts + a seeded `random.Random(42)`.

- `TestStatFields` — `development.STAT_FIELDS == teams.player_generator._STAT_FIELDS` (the ONE allowed `teams` import in this file), 19 entries, capital-O present.
- `TestBaseChange` — every age-band boundary in §3.2 (21/22/25/26/27/28/29/30/31/32/34/35/40/41/43/44) returns the locked value; monotone non-increasing across age.
- `TestBaseChangeNoise` — under a seeded RNG, draws fall inside the band bounds for each age band; deterministic for a fixed seed.
- `TestAgeModifier` — each of the 5 groups × its age boundaries returns the locked value.
- `TestChangeLimits` — each group's `(lo, hi)` at boundary ages; the awareness widening `7 + 5*(24-age)` for `age <= 24`.
- `TestDevelopStat` — clamps to the change limits (a huge effective base change still can't exceed `hi`); floors to `[0,100]` (`develop_stat(99, "accuracy", 20, 50, rng)` ≤ 100; `develop_stat(1, "speed", 40, -50, rng)` ≥ 0).
- `TestDevelopPlayerStats` — returns exactly 19 keys; **monotone direction** under a seeded RNG with a young player (most stats trend up at age 20) vs an old player (most trend down at age 40) — assert the **direction of the sum-of-deltas**, NOT exact values; 0/100 clamp at extremes; deterministic for a fixed seed (call twice with fresh `Random(42)` → identical dicts).
- `TestFreeAgentGamesTick` — bounds `0 <= tick <= median_active // 2`; `free_agent_games_tick(0, rng) == 0`; deterministic for a fixed seed.
- `TestNoDjangoImportsLeaked` — subprocess fresh-import `matches.development`, walk `sys.modules`, assert no module name starts with `django` (mirror `test_draw.py` / `test_bracket.py`).

**Assertion discipline:** schema-level outcomes + seeded-RNG determinism + clamp/limit boundaries. NEVER assert exact unseeded stat values.

### 7.2 Model + migration tests — `matches/tests/test_player_season_rating.py`

Django `TestCase`.

- Field shape: 19 stat fields present (incl. `Offensive_synergy`), `age` nullable, `overall_rating` float, `potential` nullable; `unique(player, season)` rejects a duplicate but allows the same player across different seasons; CASCADE on Player delete and Season delete; `Meta.ordering == ["player_id", "season_id"]`; related_names `Player.season_ratings` / `Season.player_ratings`.

### 7.3 `league_create` baseline tests — `matches/tests/test_league_create.py` (EXTEND)

- A `league_create` POST writes exactly one baseline `PlayerSeasonRating` per founding Player — every competitive-Team player (active + bench) AND every free-agent-pool player — tagged to the founding draft Season, with `potential is None`, `age == player.age`, and the 19 stat fields equal to the as-generated live values (no development applied: row stats == live stats).

### 7.4 `next_season` development tests — `matches/tests/test_league_next_season.py` (EXTEND)

Hand-build a completed Season with snapshot Teams + a free-agent pool + some `PlayerRoundState` rows for regular-season appearances. Then POST `next_season` and assert:

- Every developing-set Player's `age` is incremented by exactly 1 (vs pre-rollover).
- Every developing-set Player's 19 live stat fields changed only within `[0,100]` (no out-of-range), and the change direction is consistent with the player's age band under the (production fresh) RNG — assert range/clamp invariants, NOT exact values.
- `total_games` ticks: an active-Team player's `total_games` rises by their exact regular-season appearance count in the just-completed Season; a free-agent-pool player's `total_games` rises by a value in `[0, median_active // 2]`.
- Exactly one `PlayerSeasonRating` row per developed Player tagged to the **NEW** Season, with `age == post-tick age`, `overall_rating == mean of the developed stats`, `potential is None`.
- **League isolation:** a Player in a *different* League is NOT developed and gets NO new-Season row (no cross-League guard needed — the developing set is league-scoped by construction; pin it with a two-League fixture).
- Playoff appearances (`PlayerRoundState` on a `season=NULL` playoff round) do NOT count toward the `total_games` tick (only regular-season `match__season=latest_completed` rounds count).

> For the develop assertions, the production view builds a fresh `random.Random()`. Tests that need determinism on the develop math itself should unit-test the pure module (§7.1); the `next_season` integration tests assert schema-level outcomes (age `+1`, one row per player, tick bounds, isolation), NOT exact developed stat values.

### 7.5 View + template tests — `matches/tests/test_league_player_detail.py` (EXTEND)

- The `player_detail` context carries `ratings_history` — a list of dicts (oldest-first) for this Player's `PlayerSeasonRating` rows scoped to this League; a Player with rows in another League does NOT see them.
- The rendered page contains the live DOM id `league-player-ratings-history` and the chart canvas `league-player-ratings-history-chart`, the `json_script` id `league-player-ratings-history-data`, and the per-Season table id `league-player-ratings-history-table`; the old `league-player-ratings-history-stub` id is ABSENT.
- Empty-history Player renders `league-player-ratings-history-empty` (substring `"No ratings history"`).
- A Potential cell renders `—` for every row (always `None` in LG-04).

---

## 8. Locked names — quick index

| Concept | Locked name |
| --- | --- |
| **Model** | `matches.models.PlayerSeasonRating` |
| Model fields | 19 stat fields (== `Player`, incl. `Offensive_synergy`) + `age` (nullable int) + `overall_rating` (float) + `potential` (nullable float, always `None`) + FKs `player` / `season` |
| FK on_delete / related_name | both `CASCADE`; `Player.season_ratings`, `Season.player_ratings` |
| Constraint | `uniq_player_season_rating` (`unique(player, season)`) |
| Meta.ordering | `["player_id", "season_id"]` |
| Declared in | `matches/models.py`, after `Season`, before `SeasonPhase` |
| **Migration** | `matches/migrations/0048_playerseasonrating.py`, dep `("matches", "0047_seasonphase_tournament_subconfig")`, one `CreateModel`, NO `RunPython` |
| **Pure module** | `matches/development.py` (allowlist `dataclasses`, `typing`, `random`, `collections`) |
| Pure constants | `STAT_FIELDS` (19), `STAT_MIN=0`, `STAT_MAX=100`, `_STAT_ARCHETYPE` (19) |
| Pure functions | `base_change(age)`, `base_change_noise(age, rng)`, `age_modifier(group, age)`, `change_limits(group, age)`, `develop_stat(current, stat_name, age, effective_base_change, rng)`, `develop_player_stats(stats, age, rng)`, `free_agent_games_tick(median_active, rng)`, `_bound`, `_clamp_int` |
| Archetype groups | `awareness` (6) / `athletic` (3) / `team` (6) / `skill` (3) / `durable` (1) |
| **`league_create` helper** | `matches.league_views._write_baseline_ratings(season, players)` (+ reuse `_developing_players` for the founding set; bulk_create) |
| **`next_season` helpers** | `matches.league_views._develop_league_for_new_season(league, new_season, latest_completed)`, `_developing_players(league)` |
| Develop trigger | inside `next_season`'s `@transaction.atomic`, after carry-forward, before redirect; rows tag to `new_season` |
| Age coalesce | `None` age → `25` for the develop math; live `age` written as `raw_age + 1` |
| Appearance count ORM | `PlayerRoundState.objects.filter(game_round__match__season=latest_completed).values("player_id").annotate(n=Count("id"))` |
| `total_games` tick | active player `+= appearances`; free agent `+= free_agent_games_tick(median_active, rng)` |
| Persistence | `Player.objects.bulk_update(..., [*STAT_FIELDS, "age", "total_games"])` + `PlayerSeasonRating.objects.bulk_create(...)` |
| **View context key** | `player_detail` gains `ratings_history: list[dict]` (oldest-first, league-scoped) |
| **DOM ids (UI)** | `league-player-ratings-history` (block, replaces `…-stub`), `league-player-ratings-history-chart` (canvas), `league-player-ratings-history-data` (json_script), `league-player-ratings-history-table`, `league-player-ratings-history-empty` |
| Chart labels | dataset `"Overall rating"`, x-axis `"Season"`, y-axis `"Overall rating"`, `pointRadius: 2` |
| **Test files** | `matches/tests/test_development.py` (NEW, pure), `matches/tests/test_player_season_rating.py` (NEW, model), EXTEND `test_league_create.py` / `test_league_next_season.py` / `test_league_player_detail.py` |
| Test discipline | schema-level outcomes + seeded-RNG determinism + clamp/limit boundaries; NEVER exact unseeded stat values |
| Determinism / scope | fresh `random.Random()` per rollover, no stored seed; no SIM-07/08 interaction; **no Score Calibration re-baseline**; no new CONTEXT.md term (ADR-0024 carries **Player development** / **Ratings history**) |
