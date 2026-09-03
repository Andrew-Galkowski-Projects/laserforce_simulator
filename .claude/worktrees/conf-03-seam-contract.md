# CONF-03 seam contract — Worlds qualification + the Last-chance qualifier

The **single source of truth** for every name, signature, field, choice value,
context key and DOM id the Code, Tests and Docs agents share on branch
`conf-03-worlds-qualification`. Every decision below was locked at the CONF-03
grill (2026-09-03) and is recorded with its rationale and rejected alternatives
in
[ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md).
The domain language is already written in CONTEXT.md under **Worlds**, **Worlds
qualifier**, **Last-chance qualifier**, **Conference**, **Regional playoff**,
**Conference champion** and **Standings**. The CONF-01/CONF-02 foundation this
builds on is [ADR-0034](../../docs/adr/0034-conference-partition.md),
[ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md)
and [`.claude/worktrees/conf-02-seam-contract.md`](conf-02-seam-contract.md).

Nothing in this contract is open for renegotiation by an implementing agent. If
a name here turns out to be impossible, stop and report it rather than
inventing a substitute — a silent rename is exactly the failure mode this
document exists to prevent.

---

## 1. Scope

**Ship:** in a Season with **two or more** Conferences, the **final**
`tournament` `SeasonPhase` (the highest-ordinal one) decides **who goes to
Worlds**. Each Conference sends 1, 2 or 3 **Worlds qualifiers** as a function of
its **activation-snapshot size**. The third slot, where it exists, is decided by
a second per-Conference bracket — the **Last-chance qualifier** — created
eagerly and **unseeded** at phase activation and seeded once its Conference's
Regional playoff has crowned a champion. The unioned field is ordered tier-first
then by regular-season **rate**, and is **derived on demand, never persisted**.

**Byte-identical:**

- a Season with **zero or one** Conference is untouched in rows, reads, champion
  stamping and every rendered DOM id;
- a Conference of **8 or fewer** Teams gets **no** Last-chance bracket and is
  byte-identical to CONF-02 in rows and DOM ids;
- `Season.champion_team` **stays NULL** for a >= 2-Conference Season. **This
  slice crowns nothing.** CONF-04 crowns it.

**Do NOT build (CONF-04 and later):** the cross-Conference Worlds `Tournament`
row or phase; any bye / play-in handling for a non-power-of-two field; any
placement / elimination-depth ranking API on `Tournament`; per-Conference map
pools; a persisted qualifier table.

**No Score Calibration re-baseline.** No simulation mechanic changes — the only
shifts are one new discriminator column, one extra `Tournament` row per large
Conference, and a pure derivation module.

---

## 2. Domain rule — transcribed, not re-litigated

### 2.1 Qualifier count is a function of Conference SIZE

Size is `len(conference.starting_team_ids_json or [])` — the **activation
snapshot** written by `Season.start_season()`. It is **NOT** read from the
Regional playoff's participant count, and it is **NOT** affected by
`SeasonPhase.tournament_cut`.

| Conference size | Qualifiers | Slot provenances, in tier order |
| --- | --- | --- |
| 0 or 1 | **0** | (degenerate; CONF-01 forbids composing one) |
| 2-4 | 1 | Conference champion |
| 5-8 | 2 | Conference champion; best regular-season finisher not already qualified |
| 9 or more | 3 | the above two; plus the Last-chance qualifier bracket's winner |

### 2.2 Slot provenance, in tier order

1. **Tier 1 — Conference champion.** That Conference's Regional playoff
   `Tournament.champion`.
2. **Tier 2 — regular-season qualifier.** The Conference's best regular-season
   **Standings** finisher not already qualified: rank 1, or rank 2 when rank 1
   won the Regional playoff.
3. **Tier 3 — last-chance winner.** The `champion` of the Conference's
   Last-chance qualifier bracket.

### 2.3 The no-Regional-playoff fallback — LOCKED

A Conference of the final tournament phase can legitimately end up with **no
Regional playoff `Tournament` row** in **two** ways. Both are covered by the
same fallback:

| # | How it happens | Where it is skipped |
| --- | --- | --- |
| 1 | The Conference has **2-3 Teams** — fewer than `MIN_BRACKET_PARTICIPANTS` (= 4, models.py:18) — so its seed order is too short to build. | `_build_tournament_for_phase`'s `len(order) < MIN_BRACKET_PARTICIPANTS` regional guard (models.py:1448) |
| 2 | The phase's **`tournament_cut` is set below 4** (e.g. a cut of 2), truncating even a LARGE Conference's seed order below the floor. | the same guard, applied AFTER the `order = order[: phase.tournament_cut]` slice |

In both cases the Conference still sends **exactly one** qualifier:

- team = that Conference's Standings **rank 1**;
- `provenance = PROVENANCE_REGULAR_SEASON` (it is a regular-season placing, not
  a bracket result);
- `tier = QUALIFIER_TIER_CHAMPION` (**tier 1**), so it seeds among the champions.

**No Conference is ever unrepresented at Worlds.**

> **Locked gating rule — the fallback is NOT `regional is None`.** A bare
> `regional is None` is ALSO true when the tournament phase **has not been built
> at all** — the regular season is still running and `phase.regional_tournaments`
> is empty for *every* Conference. Firing the fallback there would emit a
> complete, plausible-looking Worlds field **mid-regular-season, before a single
> playoff Match has been played**, and the Worlds panel would render it — exactly
> the premature-field failure §5.5's readiness rule exists to prevent. It bites
> hardest on a >= 2-Conference Season whose Conferences are ALL 8 Teams or fewer,
> because such a Season never reaches a tier-3 slot and so nothing else would
> return `[]`.
>
> The fallback therefore fires **only** when this Conference has no Regional
> playoff row **AND** the phase has been built at all, i.e.
> `phase.regional_tournaments.exists()` is `True`. See §5.5 step 4 for the
> three-branch form.
>
> `exists()` is the right predicate — rather than re-deriving the Conference's
> size against `MIN_BRACKET_PARTICIPANTS` — because one read covers **both** rows
> of the table above, including the `tournament_cut` case where size alone would
> wrongly predict a bracket.

### 2.4 The Last-chance field — LOCKED

The **4 highest-ranked Teams of that Conference's regular-season Standings that
are NOT already qualified**, i.e. excluding both the Conference champion and the
tier-2 regular-season qualifier. This is why the bracket is **strictly
sequential**: its field cannot be computed until the Regional playoff's champion
is known.

For a Conference of size 9 there are always at least `9 - 2 = 7` unqualified
Teams, so a short field is unreachable by construction. The seeder still handles
it (§5.4) rather than deadlocking the phase.

### 2.5 Cross-region seeding — LOCKED

**Tier first**, then within a tier by regular-season **rate**, computed from that
Team's **Conference-scoped** `StandingsRow`:

1. `tier` ASC (1 champion, 2 regular-season, 3 last-chance)
2. `league_points / matches_played` DESC
3. `round_wins / matches_played` DESC
4. `total_score / matches_played` DESC
5. `team_id` ASC

Rate, not raw totals, because Conferences differ in size and therefore play
different numbers of games.

> **Locked rule — `matches_played == 0`.** When `matches_played <= 0`, **all
> three rates are `0.0`**. No division is attempted, no `None`, no sentinel, no
> exception. Such a Team therefore sorts below every Team in its tier with any
> positive rate, and ties with every other zero-rate Team in its tier, falling
> through to the `team_id` ASC tiebreak. This is the *only* defined behaviour;
> do not invent a "hasn't played" branch.

### 2.6 Only the FINAL tournament phase qualifies

Only the **highest-ordinal** `tournament` `SeasonPhase` drives Worlds
qualification and gets Last-chance brackets. A mid-season `tournament` phase
still builds its regional brackets **exactly as CONF-02 does** — no
`last_chance` row, no qualification read, no new DOM id.

---

## 3. Model — `laserforce_simulator/matches/models.py`

### 3.1 CHANGED `Tournament` — ONE new discriminator field

Declared inside `class Tournament`, placed immediately **after** the CONF-02
`conference` FK and **before** the `final_series_length` block, so the
Conference-linkage columns read together.

The choices tuple is declared as a class attribute immediately above the field,
matching the house discriminator precedent
(`SeasonPhase.TOURNAMENT_MODE_CHOICES`, `Tournament.TEAM_ASSEMBLY_CHOICES`,
`Tournament.ROLE_ASSIGNMENT_CHOICES` — all `max_length=16`, `choices=`,
`default=` a literal):

```python
# CONF-03 — which qualification stage this bracket IS (ADR-0036). Blank for a
# sandbox Tournament AND for the Season-wide embedded bracket of a
# 0/1-Conference Season. ``regional_playoff`` = a Conference's Regional
# playoff; ``last_chance`` = its Last-chance qualifier bracket.
#
# READ RULE (no backfill — ADR-0004): every read tests ONLY
# ``qualifier_stage == "last_chance"``. Everything else — including the
# un-backfilled "" on a CONF-02 regional row created before migration 0059 —
# is a Regional playoff when ``conference_id`` is set, and not a qualifier
# bracket at all when it is NULL.
QUALIFIER_STAGE_CHOICES = (
    ("", "Not a qualifier bracket"),
    ("regional_playoff", "Regional playoff"),
    ("last_chance", "Last-chance qualifier"),
)
qualifier_stage = models.CharField(
    max_length=16,
    choices=QUALIFIER_STAGE_CHOICES,
    blank=True,
    default="",
)
```

`max_length=16` is exact: `"regional_playoff"` is 16 characters. Do not widen
it, do not shorten the value.

**`Tournament` still has no `class Meta`.** Do not add one. No index, no
constraint, no `unique_together` on `(season_phase, conference,
qualifier_stage)` — the build path is guarded by the idempotence checks in §5,
not by the database.

### 3.2 LOCKED READ RULE — the no-backfill table

Because migration 0059 has **no backfill**, an existing CONF-02 regional row
keeps `qualifier_stage == ""`. Every read in production code, tests and
templates must classify by this table and by nothing else:

| Row | `conference_id` | `qualifier_stage` | Classify as |
| --- | --- | --- | --- |
| Sandbox `Tournament` | NULL | `""` | not a qualifier bracket |
| Season-wide embed (0/1-Conference Season) | NULL | `""` | not a qualifier bracket |
| CONF-02 regional row, created **before** 0059 | set | `""` | **Regional playoff** |
| Regional row, created **after** 0059 | set | `"regional_playoff"` | **Regional playoff** |
| Last-chance row | set | `"last_chance"` | **Last-chance qualifier** |

The single permitted predicate:

```python
is_last_chance = tournament.qualifier_stage == "last_chance"
```

**Forbidden:** `qualifier_stage == "regional_playoff"` as a *positive* test for
"is this a regional playoff", `qualifier_stage != ""` as a test for "is this a
qualifier bracket", any `.exclude(qualifier_stage="")`, and any `RunPython`
that would make those safe. A regional playoff is
`conference_id is not None and qualifier_stage != "last_chance"`.

### 3.3 The migration — DESCRIBED ONLY, do not generate it here

| Property | Value |
| --- | --- |
| App | `matches` |
| File | `laserforce_simulator/matches/migrations/0059_tournament_qualifier_stage.py` |
| Depends on | `0058_tournament_regional_linkage` (verified as the current latest) |
| Operations | exactly **one** `migrations.AddField` |
| Field | `model_name="tournament"`, `name="qualifier_stage"`, `field=models.CharField(blank=True, choices=[("", "Not a qualifier bracket"), ("regional_playoff", "Regional playoff"), ("last_chance", "Last-chance qualifier")], default="", max_length=16)` |

**There is NO `RunPython`, NO backfill, NO data migration and no follow-up
migration** — per [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)'s
disposable-data posture, and because §3.2's read rule makes the un-backfilled
`""` classify correctly on its own. If Django auto-names the file differently,
rename it to `0059_tournament_qualifier_stage.py`; the name is part of this
contract so Docs can reference it.

---

## 4. NEW pure module — `laserforce_simulator/matches/worlds.py`

Follows the `matches/standings.py` / `matches/bracket.py` precedent: **no Django
imports, no ORM, plain values in and out, unit-testable with zero DB.**

**Frozen import allowlist** (the only modules this file may import):
`dataclasses`, `typing`. No Django, no `random`, no `datetime`, no I/O, no
logging. Enforced by a `TestNoDjangoImportsLeaked` subprocess class in the Tests
agent's `test_worlds_qualification.py`, matching the existing classes in
`test_bracket.py` / `test_standings.py` / `test_development.py`.

### 4.1 Constants — final names and values

```python
QUALIFIER_TIER_CHAMPION = 1
QUALIFIER_TIER_REGULAR_SEASON = 2
QUALIFIER_TIER_LAST_CHANCE = 3

PROVENANCE_CHAMPION = "conference_champion"
PROVENANCE_REGULAR_SEASON = "regular_season"
PROVENANCE_LAST_CHANCE = "last_chance"

PROVENANCE_LABELS = {
    PROVENANCE_CHAMPION: "Conference champion",
    PROVENANCE_REGULAR_SEASON: "Regular season",
    PROVENANCE_LAST_CHANCE: "Last-chance qualifier",
}

# The Last-chance qualifier bracket's fixed field size (ADR-0036).
LAST_CHANCE_FIELD_SIZE = 4
```

**Tier and provenance are separate axes on purpose.** The 2-3-Team fallback of
§2.3 is exactly the case where they disagree: `tier =
QUALIFIER_TIER_CHAMPION` with `provenance = PROVENANCE_REGULAR_SEASON`. Never
derive one from the other.

Note `PROVENANCE_LAST_CHANCE` and `Tournament.qualifier_stage`'s `"last_chance"`
happen to share a string value. They are **different axes** (one is a
qualifier's provenance, one is a bracket's stage) and neither may be used in
place of the other; `matches/worlds.py` must not import from `matches/models.py`
at all.

### 4.2 `WorldsQualifier` — the frozen dataclass

```python
@dataclass(frozen=True)
class WorldsQualifier:
    """One Team's place in the Worlds field, with the provenance that earned it."""

    team_id: int
    team_name: str
    conference_id: int
    conference_name: str
    tier: int              # QUALIFIER_TIER_* — the seeding tier
    provenance: str        # PROVENANCE_* — how the slot was earned
    matches_played: int    # regular-season, Conference-scoped
    league_points: int     # regular-season, Conference-scoped
    round_wins: int        # regular-season, Conference-scoped
    total_score: int       # regular-season, Conference-scoped
    seed: int = 0          # 1-based Worlds seed; 0 until order_worlds_qualifiers stamps it

    @property
    def provenance_label(self) -> str:
        """Human-readable provenance for display. Unknown value ⇒ ``""``."""
        return PROVENANCE_LABELS.get(self.provenance, "")
```

The four regular-season integers come **verbatim** off that Team's
Conference-scoped `StandingsRow` (`matches/standings.py`). They are the rate
*inputs*; no rate is stored. `seed` defaults to `0` and is stamped by
`order_worlds_qualifiers` via `dataclasses.replace`, exactly as
`compute_standings` / `rerank_round_robin` stamp `rank`.

### 4.3 `qualifier_count_for_size`

```python
def qualifier_count_for_size(team_count: int) -> int:
    """How many Worlds qualifiers a Conference of ``team_count`` Teams sends."""
```

| `team_count` | returns |
| --- | --- |
| `< 2` | `0` |
| `2`-`4` | `1` |
| `5`-`8` | `2` |
| `>= 9` | `3` |

Pure integer branch. A negative input returns `0`.

### 4.4 `first_unqualified`

```python
def first_unqualified(ranked_team_ids: list, qualified_team_ids) -> "int | None":
    """The first id of ``ranked_team_ids`` not present in ``qualified_team_ids``.

    ``ranked_team_ids`` is a Conference's Standings order, best first.
    ``qualified_team_ids`` is any container supporting ``in``. ``None`` when
    every ranked id is already qualified (or the list is empty).
    """
```

This is the tier-2 rule and the 2-3-Team fallback's rank-1 rule in one function.

### 4.5 `last_chance_field`

```python
def last_chance_field(ranked_team_ids: list, qualified_team_ids) -> list:
    """The Last-chance qualifier's field: UP TO ``LAST_CHANCE_FIELD_SIZE`` ids.

    The highest-ranked ids of ``ranked_team_ids`` not in
    ``qualified_team_ids``, in rank order (index 0 becomes bracket seed 1).
    Returns FEWER than ``LAST_CHANCE_FIELD_SIZE`` when there are not enough
    unqualified Teams; the caller decides what to do about that (§5.4). Never
    raises, never pads.
    """
```

### 4.6 `order_worlds_qualifiers` — the ordering function

```python
def order_worlds_qualifiers(qualifiers: list) -> list:
    """Order the unioned Worlds field into seeds 1..M and stamp ``seed``.

    Sort key (ADR-0036): tier ASC, then regular-season RATE within the tier —
    league_points/matches_played DESC, round_wins/matches_played DESC,
    total_score/matches_played DESC — then team_id ASC. ``matches_played <= 0``
    ⇒ every rate is 0.0 (no division attempted).

    Returns a NEW list of ``WorldsQualifier`` with ``seed`` set 1..M
    (``dataclasses.replace``); the input list is not mutated. Empty input ⇒ [].
    """
```

The rate helper is module-private:

```python
def _rate(numerator: float, matches_played: int) -> float:
    """``numerator / matches_played``, or ``0.0`` when ``matches_played <= 0``."""
```

`_rate` is **internal** — Tests must not import or assert on it (§9.3).

---

## 5. `Season` — new and changed methods

All of these live on `Season` in `laserforce_simulator/matches/models.py`.
Signatures below are **final**.

### 5.1 NEW `_final_tournament_phase` — which phase qualifies

```python
def _final_tournament_phase(self) -> "SeasonPhase | None":
```

Placed on `Season` immediately **after** `tournaments_for_phase`. Private.

- Walk `self.ordered_phases()`; return the **last** phase with
  `phase_type == "tournament"` (ordinal order is guaranteed by
  `SeasonPhase.Meta.ordering`), or `None` when there is none.
- A non-persisted implicit fallback phase (`pk is None`) is **never** returned —
  it is always `round_robin`.

### 5.2 CHANGED `_build_tournament_for_phase` — stamps `qualifier_stage`

**Signature unchanged:**

```python
def _build_tournament_for_phase(self, phase, conference=None) -> "Tournament | None":
```

The **only** change is inside the existing conditional kwargs block of the
`Tournament.objects.create(...)` call:

```python
**(
    {
        "season_phase": phase,
        "conference": conference,
        "qualifier_stage": "regional_playoff",
    }
    if conference is not None
    else {}
),
```

`conference is None` still leaves `qualifier_stage` at its `""` default, so the
Season-wide path is byte-identical. **No new parameter is added** — a regional
build is always a Regional playoff; the Last-chance row has its own builder
(§5.3) because it must be created *unseeded*.

Everything else in the method — the `tournament_cut` slice, the
`if not order: return None` no-op, the `len(order) < MIN_BRACKET_PARTICIPANTS`
regional guard, the em-dash naming, the participant loop, `lock_and_build()` —
is **verbatim unchanged**.

### 5.3 NEW `_build_last_chance_tournament` — the eager, UNSEEDED row

```python
def _build_last_chance_tournament(self, phase, conference) -> "Tournament | None":
```

Placed immediately **after** `_build_tournament_for_phase`. Private.
`conference` is **required** (no default) — a Last-chance bracket is always
Conference-scoped.

Contract:

1. `size = len(conference.starting_team_ids_json or [])`.
2. `if qualifier_count_for_size(size) < 3: return None` — a Conference of 8 or
   fewer Teams gets **no row at all**. (Deferred import:
   `from .worlds import qualifier_count_for_size`.)
3. Idempotence: `if phase.regional_tournaments.filter(conference=conference,
   qualifier_stage="last_chance").exists(): return None`.
4. Create the row and **return it immediately** — no participants, no
   `lock_and_build()`:

```python
tournament = Tournament.objects.create(
    name=f"{self.name} — {conference.name} Last Chance Qualifier",
    format=phase.tournament_format,
    team_assembly="preset",
    state="setup",
    final_series_length=phase.final_series_length,
    semifinal_series_length=phase.semifinal_series_length,
    quarterfinal_series_length=phase.quarterfinal_series_length,
    earlier_series_length=phase.earlier_series_length,
    wb_advancers=phase.wb_advancers,
    lb_advancers=phase.lb_advancers,
    swiss_rounds=phase.swiss_rounds,
    season_phase=phase,
    conference=conference,
    qualifier_stage="last_chance",
)
```

The separator is an **em-dash U+2014 surrounded by single spaces**, matching
`Conference.__str__`, `SeasonPhase.__str__` and CONF-02's regional naming. The
label is `Last Chance Qualifier` (three words, no hyphen) — it is a proper-noun
bracket name, distinct from CONTEXT.md's prose term *Last-chance qualifier*.

> **UNSEEDED is load-bearing — do NOT "fix" it.** An unseeded bracket means: the
> `Tournament` row exists with `state="setup"`, **zero** `TournamentParticipant`
> rows, **zero** `BracketNode` rows, and `lock_and_build()` **not yet called**.
> Two existing behaviours make this safe with no engine change:
>
> - `Season._tournament_phase_complete` requires `all(t.state == "completed")`,
>   and `"setup" != "completed"`, so **the phase already refuses to advance**
>   while a Last-chance bracket is unseeded. No new gate is needed.
> - `Tournament.find_next_playable_node()` (models.py:2666) delegates to
>   `bracket.find_next_node` over `self.nodes`; on a node-less bracket that list
>   is empty and it returns `None`. So `tournament_engine.play_next_node`
>   returns `None` and `play_next_bracket_round` returns `0` — **both drain
>   loops already skip an unseeded bracket harmlessly.**
>
> Do not add a `state="pending_seed"`, do not create placeholder participants,
> do not call `lock_and_build()` early, and do not filter unseeded rows out of
> `tournaments_for_phase`.

### 5.4 NEW `seed_pending_last_chance_brackets` — the public seeding seam

```python
@transaction.atomic
def seed_pending_last_chance_brackets(self, phase) -> int:
```

Placed immediately **after** `_build_last_chance_tournament`. **Public** (no
leading underscore): `matches/tasks.py`, `matches/league_views.py` and
`Season.activate_pending_tournament_phase` all call it, and Tests assert on it
directly (§9.2).

**Returns the number of brackets it seeded this call** (`0` when none were
ready). **Idempotent** — calling it repeatedly is safe and returns `0` once
everything is seeded.

Contract:

1. `if phase is None or phase.pk is None or phase.phase_type != "tournament":
   return 0`.
2. Candidates:
   `phase.regional_tournaments.filter(qualifier_stage="last_chance",
   state="setup").select_related("conference")`. (Filtering on the *positive*
   `"last_chance"` value is the one permitted positive test — see §3.2.)
3. For each candidate, in `conference__ordinal, id` order:
   - `regional = phase.regional_tournaments.filter(conference=candidate.conference)
     .exclude(qualifier_stage="last_chance").first()`
   - `if regional is None or regional.champion_id is None: continue` — not ready
     yet; leave it unseeded.
   - Compute `ranked = self._seed_order_for_phase(prior, conference=...)`? **No.**
     Compute the Conference's Standings order directly:
     `rows = self._final_standings_for_phase(self._preceding_phase(phase),
     conference=candidate.conference)` and `ranked = [r.team_id for r in rows]`.
   - `qualified = {regional.champion_id}`; add
     `first_unqualified(ranked, qualified)` when it is not `None`.
   - `field = last_chance_field(ranked, qualified)`.
   - `if len(field) < MIN_BRACKET_PARTICIPANTS:` **delete the row**
     (`candidate.delete()`) and do **not** count it as seeded. It has no
     participants and no nodes, so the delete is clean, and removing it lets the
     phase finish instead of deadlocking on a bracket that can never be built.
     **This branch is unreachable for a Conference of 9+** (at least 7 Teams are
     always unqualified); it exists only so admin-mangled data cannot brick a
     Season. Record it in a comment.
   - Otherwise: one `TournamentParticipant.objects.create(tournament=candidate,
     team_id=team_id, seed=position + 1)` per entry of `field` in order, then
     `candidate.lock_and_build()`, then increment the counter.
4. Return the counter.

Deferred imports at the top of the method:
`from .worlds import first_unqualified, last_chance_field`.

> **Note on deleting a candidate mid-drain.** Both drain loops cache
> `tournaments_for_phase(phase)` (§6). A deleted instance left in that cached
> list is harmless: `tournament.nodes` re-queries and returns empty, so
> `stage_progress([])` is `(0, 0)` and `find_next_playable_node()` is `None`.
> No caller re-reads a stale `.state` off it.

### 5.5 NEW `worlds_qualifiers` — the public derivation seam

```python
def worlds_qualifiers(self) -> "list[WorldsQualifier]":
```

Placed immediately **after** `seed_pending_last_chance_brackets`. **Public.**
Derived on demand; **nothing is persisted**.

Deferred import:
`from .worlds import (PROVENANCE_CHAMPION, PROVENANCE_LAST_CHANCE,
PROVENANCE_REGULAR_SEASON, QUALIFIER_TIER_CHAMPION, QUALIFIER_TIER_LAST_CHANCE,
QUALIFIER_TIER_REGULAR_SEASON, WorldsQualifier, first_unqualified,
order_worlds_qualifiers, qualifier_count_for_size)`.

Contract:

1. `conferences = self.ordered_conferences()`; `if len(conferences) < 2:
   return []`. **A 0/1-Conference Season has no Worlds** (CONTEXT.md: its
   season-ending playoff crowns the Season champion directly).
2. `phase = self._final_tournament_phase()`; `if phase is None: return []`.
3. `prior = self._preceding_phase(phase)`, and — computed **ONCE**, before the
   per-Conference loop, never per Conference —
   `phase_built = phase.regional_tournaments.exists()`.
4. For each Conference in ordinal order, gather:
   - `rows = self._final_standings_for_phase(prior, conference=conference)`, and
     `row_by_team = {r.team_id: r for r in rows}`, `ranked = [r.team_id for r in rows]`.
   - `count = qualifier_count_for_size(len(conference.starting_team_ids_json or []))`;
     `if count == 0: continue`.
   - `regional = phase.regional_tournaments.filter(conference=conference)
     .exclude(qualifier_stage="last_chance").first()`
   - **Tier 1 — THREE branches, evaluated in exactly this order.** A bare
     `regional is None` is NOT the fallback predicate (§2.3):
     1. **`regional is not None`** ⇒ `if regional.champion_id is None: return []`
        (not ready — see the readiness rule below); else the tier-1 team is
        `regional.champion_id` with `PROVENANCE_CHAMPION` and
        `QUALIFIER_TIER_CHAMPION`.
     2. **`regional is None` AND `phase_built` is `False`** ⇒ **`return []`**.
        The phase has not been built at all, so qualification is not merely
        incomplete — it has not *started*. Without this branch a
        >= 2-Conference Season whose Conferences are ALL 8 Teams or fewer never
        reaches a tier-3 slot, nothing else returns `[]`, and
        `worlds_qualifiers()` emits a complete, plausible-looking field
        mid-regular-season (§2.3's gating rule).
     3. **`regional is None` AND `phase_built` is `True`** ⇒ the genuine §2.3
        fallback: the tier-1 team is `ranked[0]` (skip the Conference entirely
        when `ranked` is empty) with `PROVENANCE_REGULAR_SEASON`, **still
        `tier = QUALIFIER_TIER_CHAMPION`**.
   - **Tier 2** (only when `count >= 2`): `first_unqualified(ranked, qualified)`
     with `QUALIFIER_TIER_REGULAR_SEASON` / `PROVENANCE_REGULAR_SEASON`. Skip
     the slot when it is `None`.
   - **Tier 3** (only when `count >= 3`): `last_chance =
     phase.regional_tournaments.filter(conference=conference,
     qualifier_stage="last_chance").first()`. `if last_chance is None or
     last_chance.champion_id is None: return []` (not ready). Else that champion
     with `QUALIFIER_TIER_LAST_CHANCE` / `PROVENANCE_LAST_CHANCE`.
   - Build each `WorldsQualifier` with `team_name` from a single bulk
     `Team.objects.filter(id__in=...).in_bulk()` (tolerant — a deleted Team id
     drops out, mirroring `_final_standings_for_phase`'s tolerant `id__in`), and
     the four regular-season integers off `row_by_team[team_id]`, defaulting to
     `0` when the Team has no Standings row.
5. `return order_worlds_qualifiers(collected)`.

> **LOCKED readiness rule — "empty list = not ready", never a partial list.**
> `worlds_qualifiers()` returns `[]` the moment ANY required bracket of the
> final tournament phase is missing its `champion`, or any Conference that
> should have a Last-chance row has none. It never returns a partially-filled
> field.
>
> Reason: CONF-04 will build the Worlds bracket straight off this list, and a
> partial list is indistinguishable from a complete one at the call site — a
> 5-of-7 field would silently build a wrong-size bracket. It also gives the UI
> exactly one branch ("render the panel, or don't") instead of two. A legacy
> Season predating migration 0059 has no Last-chance rows and so returns `[]`
> forever; that is accepted under ADR-0004's disposable-data posture.

### 5.6 CHANGED `activate_pending_tournament_phase` — build + recovery hook

**Signature unchanged** (`@transaction.atomic def
activate_pending_tournament_phase(self) -> None`). Everything up to and
including the mode gate is **unchanged**. The `len(conferences) >= 2` branch
becomes exactly:

```python
conferences = self.ordered_conferences()
if len(conferences) >= 2:
    # CONF-02 — regional playoffs: one Tournament per Conference (ADR-0035).
    if phase.regional_tournaments.exists():
        # CONF-03 — the brackets are already built, so the only work left is
        # to seed any Last-chance bracket whose Regional playoff has since
        # crowned its champion. This makes activation a RECOVERY hook: it is
        # called after every scheduled Round (entrypoints.py:955, :984), so a
        # Season can never be stranded with a permanently unseeded bracket.
        self.seed_pending_last_chance_brackets(phase)
        return
    for conference in conferences:
        self._build_tournament_for_phase(phase, conference=conference)
    # CONF-03 — only the FINAL tournament phase qualifies for Worlds, and only
    # it gets Last-chance brackets (ADR-0036). Created EAGERLY and UNSEEDED so
    # the cached tournaments_for_phase lists in the drain loops already contain
    # them (§5.3, §6).
    final_phase = self._final_tournament_phase()
    if final_phase is not None and final_phase.pk == phase.pk:
        for conference in conferences:
            self._build_last_chance_tournament(phase, conference)
    return

tournament = self._build_tournament_for_phase(phase)
...
```

Pinned details:

- The seeding call sits **inside** and **before** the CONF-02 idempotence
  guard's `return`, which is what turns re-activation into a recovery hook. Do
  not move it below the guard, and do not remove the guard.
- The 0/1-Conference path below the branch is **untouched**.
- `seed_pending_last_chance_brackets` is `@transaction.atomic` inside an already
  atomic method; that is a savepoint and is fine.
- On the first activation the seeding call is deliberately **not** made — no
  Regional playoff has a champion yet, so it would return `0`.

### 5.7 NOT changed

`tournaments_for_phase`, `_tournament_phase_complete`, `_phase_complete`,
`_stamp_champion_for_final_phase`, `complete_if_finished`, `_preceding_phase`,
`_final_standings_for_phase`, `_seed_order_for_phase`, `ordered_conferences`,
`ordered_phases`, `conference_by_team_id`, `start_season` — **all unchanged**,
signatures and bodies.

`_stamp_champion_for_final_phase` in particular already does the right thing:
its `regional` list now includes the Last-chance rows, so
`any(t.champion_id is None for t in regional)` blocks completion until every
Last-chance bracket has crowned its winner too, and it still never assigns
`self.champion_team`. **This slice crowns nothing.**

### 5.8 `tournaments_for_phase` — the resulting order (VERIFIED)

The method keeps its exact signature, body and ordering:
`phase.regional_tournaments.select_related("conference").order_by("conference__ordinal", "id")`.

For a final tournament phase it now returns **up to two rows per Conference**.
Verified against the code and the locked build order of §5.6: activation builds
**all** regional rows in loop 1 and **all** Last-chance rows in loop 2, so every
regional row's `id` is lower than every Last-chance row's. With
`order_by("conference__ordinal", "id")` the result is therefore:

```
[Conf1 regional, Conf1 last_chance, Conf2 regional, Conf2 last_chance, ...]
```

i.e. **within a Conference the Regional playoff always sorts before its
Last-chance sibling.** A Conference of 8 or fewer contributes exactly one row,
as in CONF-02. Tests may assert this order (§9.2).

### 5.9 Summary table of the `Season` seam

| Method | Status | Final signature |
| --- | --- | --- |
| `_final_tournament_phase` | **new** | `(self) -> "SeasonPhase \| None"` |
| `_build_last_chance_tournament` | **new** | `(self, phase, conference) -> "Tournament \| None"` |
| `seed_pending_last_chance_brackets` | **new, public** | `(self, phase) -> int` |
| `worlds_qualifiers` | **new, public** | `(self) -> "list[WorldsQualifier]"` |
| `_build_tournament_for_phase` | changed (one kwarg) | `(self, phase, conference=None) -> "Tournament \| None"` |
| `activate_pending_tournament_phase` | changed (branch body) | `(self) -> None` |
| `tournaments_for_phase` | **unchanged** | `(self, phase) -> "list[Tournament]"` |
| `_tournament_phase_complete` | **unchanged** | `(self, phase) -> bool` |
| `_stamp_champion_for_final_phase` | **unchanged** | `(self, final_phase) -> None` |
| `_final_standings_for_phase` | **unchanged** | `(self, phase, conference=None) -> "list[StandingsRow]"` |
| `_seed_order_for_phase` | **unchanged** | `(self, phase, conference=None) -> list[int]` |

---

## 6. The drain — four hook sites, seed-then-continue semantics

The bracket engine is **NOT CHANGED**: `play_next_node`, `play_specific_node`,
`play_next_bracket_round`, `Tournament.find_next_playable_node`,
`bracket.find_next_node`, `stage_progress`, `lock_and_build` — all verbatim.
Every change is in the callers.

> **THE HAZARD THIS DESIGN EXISTS TO AVOID — read before touching either task.**
> `tasks.play_playoffs_task` and `tasks.play_season_task` each resolve
> `season.tournaments_for_phase(phase)` **once** and cache the list, and both
> carry an existing comment warning that a cached instance's `.state` is stale
> and must not be re-read in the loop.
>
> The **eager-row** design (§5.3) means the list does **not** need re-resolving:
> the Last-chance row is already in it, created at activation. And the cached
> instances stay safe because the only things the loops do with them —
> `play_next_node` (via `find_next_playable_node`) and `_stage_counts` (via
> `tournament.nodes`) — **re-query `tournament.nodes` fresh on every call**. So
> a bracket that was node-less on iteration 1 and seeded on iteration 4 is
> played correctly by the very same cached instance.
>
> **Therefore: do NOT re-resolve `tournaments_for_phase` inside either loop, do
> NOT add an in-loop `t.state` check, and do NOT `refresh_from_db()` the cached
> instances.** This is the reason the row is created eagerly rather than lazily
> (ADR-0036, "How the sequential stage is represented").

### 6.1 `matches/tasks.py::play_playoffs_task`

The guard, `_stage_counts`, both PLAY-01 cancel checks, `complete_if_finished()`
and the `finally:` block are **unchanged**. Only the no-progress exit changes:

```python
progressed = False
for tournament in tournaments:
    if play_next_node(tournament) is not None:
        progressed = True
if not progressed:
    # CONF-03 — nothing was playable: a Regional playoff may have JUST crowned
    # its champion, making this Conference's Last-chance bracket seedable
    # (ADR-0036). Seed, then RETRY the loop once more rather than exiting.
    # ``tournaments`` is deliberately NOT re-resolved — the eager row is
    # already in it and its nodes are re-queried every call.
    if season.seed_pending_last_chance_brackets(phase) > 0:
        continue
    break
completed, total = _stage_counts()
self.update_state(...)
```

`continue` re-enters the `while True:` loop, so the PLAY-01 between-stage cancel
check runs again before the retry. Return shape is **unchanged**:
`{"completed": int, "total": int}` (STAGE counts), plus `"cancelled": True` on a
cancel.

### 6.2 `matches/tasks.py::play_season_task` — the tournament tail

The gate, `tournaments` resolution + its stale-state comment, `_stage_counts`,
`rr_weeks_played`, the budget arithmetic, the PROGRESS emissions and both loop
shapes (`while _drain_one_stage() > 0:` and `for _ in range(bracket_budget):`)
are **unchanged**. Only `_drain_one_stage` changes:

```python
def _drain_one_stage() -> int:
    # CONF-02 — ONE budget unit = one stage across ALL N brackets (the
    # parallel-overlay pacing rule). ``sum`` over a generator evaluates every
    # term; do NOT rewrite as a short-circuiting ``any(...)``.
    clinched = sum(play_next_bracket_round(t) for t in tournaments)
    if clinched == 0:
        # CONF-03 — seed-then-continue: no bracket progressed, so a Last-chance
        # bracket may have just become seedable. Seed, then retry the stage
        # ONCE. Because this fires only when nothing progressed, one budget
        # unit is still exactly one stage.
        if season.seed_pending_last_chance_brackets(phase) > 0:
            clinched = sum(play_next_bracket_round(t) for t in tournaments)
    return clinched
```

Keeping the retry **inside** `_drain_one_stage` is what leaves both the
unbounded and the budgeted loop bodies untouched. Do not lift it into the loops.

### 6.3 `matches/league_views.py::play_single_round`

The GET guard, the `get_object_or_404`, the session write, the
`tournaments_for_phase` resolution, the `if not tournaments:` 400 error render
and the 302 redirect are **unchanged**. The play body becomes:

```python
from matches.tournament_engine import play_next_node

progressed = False
for tournament in tournaments:
    if play_next_node(tournament) is not None:
        progressed = True
        break
# CONF-03 — seed UNCONDITIONALLY after the click (idempotent, returns 0 when
# nothing is ready). This is what stops "Play Single Round" from becoming a
# DEAD CLICK: the click that resolves a Regional playoff's final node leaves
# that bracket ``completed`` and its Last-chance sibling ``setup``, so the
# dashboard would read neither "active" nor "completed" and hide the control.
# Seeding here flips the sibling to ``active`` before the redirect renders.
seeded = season.seed_pending_last_chance_brackets(phase)
if not progressed and seeded:
    # Nothing was playable this click, but seeding just made something
    # playable — play one node so the click is never wasted.
    for tournament in tournaments:
        if play_next_node(tournament) is not None:
            break
season.complete_if_finished()
return redirect("season_dashboard", season_id=season.id)
```

`tournaments` was resolved **before** the seed and already contains the eager
row (§5.8), so the retry loop sees the newly-seeded bracket. Do not re-resolve.

### 6.4 `Season.activate_pending_tournament_phase`

The fourth hook site, specified in §5.6.

### 6.5 The Season dashboard — NOT changed, and why

`matches/league_views.py`'s playoff-controls helper (~2080-2100) reads through
`tournaments_for_phase` and sets `playoff_phase_active` from
`any(t.state == "active")` and `playoff_completed` from
`all(t.state == "completed")`. **It is NOT changed by this slice.**

There is exactly one transient it cannot describe: a phase whose Regional
playoffs are all `completed` while a Last-chance bracket is still `setup` reads
as *neither* active *nor* completed. The four hook sites of §6.1-6.4 close that
window on every path that can leave a user looking at the dashboard — the two
tasks seed and drain to completion before returning, `play_single_round` seeds
before its redirect, and activation seeds as a recovery hook after every
scheduled Round. Tests pin this (§9.4, case 24). **Do not "fix" the dashboard by
treating `setup` as active** — that would change 0/1-Conference behaviour and
break the byte-identity invariant.

`play_playoffs` (the async 409 guard) is likewise **unchanged**: it gates on
`not season.tournaments_for_phase(phase)`, which is non-empty throughout.

---

## 7. UI — Playoffs screen and template

### 7.1 `matches/league_screens/playoffs.py` — bracket entries

The phase queryset, the deferred `_build_rounds` import, `_coerce_view_season`,
the `displayed_season` fallback and the GET-only guard are **unchanged**. The
`brackets` entry shape gains **two keys** and changes **one**:

| Key | Pending stub | Season-wide bracket | Regional playoff | Last-chance |
| --- | --- | --- | --- | --- |
| `phase` | the `SeasonPhase` | same | same | same |
| `tournament` | `None` | the `Tournament` | same | same |
| `name` | `"Playoffs"` | `tournament.name` | same | same |
| `rounds` | `[]` | `_build_rounds(t)["winners"]` | same | same (`[]` while unseeded) |
| `champion` | `None` | `tournament.champion` | same | same |
| `pending` | `True` | `tournament.state == "setup"` | same | same |
| `conference` | `None` | `None` | the `Conference` | the `Conference` |
| `key` | `str(phase.ordinal)` | `str(phase.ordinal)` | `"<ord>-<conf ord>"` | `"<ord>-<conf ord>-lc"` |
| **`stage`** *(new)* | `""` | `""` | `"regional_playoff"` | `"last_chance"` |
| **`stage_label`** *(new)* | `""` | `""` | `""` | `"Last Chance Qualifier"` |

`pending` on a built entry changes from the literal `False` to
`tournament.state == "setup"`. **This is byte-identical for every pre-CONF-03
row**: `_build_tournament_for_phase` always calls `lock_and_build()`, which
flips `state` to `"active"` before the row is ever rendered, so a Season-wide or
regional bracket is never `"setup"`. Only an unseeded Last-chance row is, and it
correctly renders the pending alert instead of an empty bracket div.

Locked derivations:

```python
conference = tournament.conference
is_last_chance = tournament.qualifier_stage == "last_chance"   # §3.2 read rule
if conference is None:
    key = str(phase.ordinal)
    stage = ""
elif is_last_chance:
    key = f"{phase.ordinal}-{conference.ordinal}-lc"
    stage = "last_chance"
else:
    key = f"{phase.ordinal}-{conference.ordinal}"
    stage = "regional_playoff"
stage_label = "Last Chance Qualifier" if is_last_chance else ""
```

**The `-lc` suffix is the locked discriminator.** Every CONF-02 regional
`key` (`"<ord>-<conf ord>"`) and every 0/1-Conference `key`
(`str(phase.ordinal)`) is therefore **byte-identical**, and so is every DOM id
built from them. `stage_label` is deliberately empty for a regional bracket so
**no new element renders** on a CONF-02 Season.

The pending stub keeps `"conference": None, "key": str(phase.ordinal)` and gains
`"stage": "", "stage_label": ""`.

### 7.2 `matches/league_screens/playoffs.py` — the Worlds panel

One new context key:

| Key | Value |
| --- | --- |
| `worlds_qualifiers` | `view_season.worlds_qualifiers()` when `view_season is not None`, else `[]` |

The value is the raw `list[WorldsQualifier]` from §4.2 — seeded 1..M, or `[]`.
The template reads `q.seed`, `q.team_id`, `q.team_name`, `q.conference_name` and
`q.provenance_label` (the zero-arg property, which Django templates call
directly). **Do not** re-map it into dicts and **do not** add a `worlds_ready`
boolean — `{% if worlds_qualifiers %}` is the readiness test.

For a 0/1-Conference Season `worlds_qualifiers()` returns `[]` (§5.5), so the
panel is **absent entirely** — no section, no heading, no empty-state text, no
new DOM id. Every other context key (`league`, `displayed_season`,
`sidebar_links`, `sidebar_active`, `season_options`, `view_season`,
`selected_season_id`, `brackets`) is **unchanged**.

### 7.3 `templates/leagues/playoffs.html` — three changes, nothing else

**(a) The stage badge**, inserted immediately **after** the existing
`{% if bracket.conference %}` conference sub-heading block:

```html
{% if bracket.stage_label %}
<div class="badge bg-secondary mb-2" id="league-playoffs-stage-{{ bracket.key }}">{{ bracket.stage_label }}</div>
{% endif %}
```

Renders **only** for a Last-chance bracket, so CONF-02 markup is unchanged.
`id="league-playoffs-stage-<key>"` is the stable hook Tests assert on.

**(b) The pending alert gains a Last-chance message.** The existing
`{% if bracket.pending %}` alert body becomes:

```html
<div class="alert alert-secondary">
    {% if bracket.stage == "last_chance" %}
    The field is not set yet — this Conference's Regional playoff must crown its
    champion first.
    {% else %}
    The bracket is not seeded yet — the regular season is still
    in progress.
    {% endif %}
</div>
```

The `{% else %}` branch is the existing text, **verbatim**, so every existing
rendered stub is byte-identical.

**(c) The Worlds panel**, appended **after** the `{% endfor %}` of the brackets
loop and **inside** the `{% else %}` of the `{% if not brackets %}` guard — i.e.
it renders only when there is at least one bracket AND a ready field:

```html
{% if worlds_qualifiers %}
<section id="league-playoffs-worlds" class="mt-4">
    <h2 class="h4">Worlds qualification</h2>
    <table id="league-playoffs-worlds-table" class="table table-sm">
        <thead>
            <tr><th>Seed</th><th>Team</th><th>Conference</th><th>Provenance</th></tr>
        </thead>
        <tbody>
            {% for q in worlds_qualifiers %}
            <tr id="league-playoffs-worlds-row-{{ q.seed }}">
                <td>{{ q.seed }}</td>
                <td id="league-playoffs-worlds-team-{{ q.seed }}"><a href="{% url 'team_match_history' q.team_id %}">{{ q.team_name }}</a></td>
                <td id="league-playoffs-worlds-conference-{{ q.seed }}">{{ q.conference_name }}</td>
                <td id="league-playoffs-worlds-provenance-{{ q.seed }}">{{ q.provenance_label }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</section>
{% endif %}
```

**Locked DOM ids** (Tests assert on exactly these):

| Element | id |
| --- | --- |
| Panel section | `league-playoffs-worlds` |
| Table | `league-playoffs-worlds-table` |
| Row | `league-playoffs-worlds-row-<seed>` |
| Team cell | `league-playoffs-worlds-team-<seed>` |
| Conference cell | `league-playoffs-worlds-conference-<seed>` |
| Provenance cell | `league-playoffs-worlds-provenance-<seed>` |
| Last-chance stage badge | `league-playoffs-stage-<phase ordinal>-<conf ordinal>-lc` |

The panel is **read-only** — no form, no button, no link other than the existing
`team_match_history` one. Everything else in the template (the season filter
form, the empty-state notice, the section / champion / bracket / round / node /
node-score ids, the game links, the winner lines) is **unchanged**.

---

## 8. Worked example — a 3-Conference Season

Season *2028*, three Conferences: **Alpha** (ordinal 1, 10 Teams), **Beta**
(ordinal 2, 6 Teams), **Gamma** (ordinal 3, 3 Teams). Phase 1 `round_robin`,
phase 2 `tournament` (`standings`, `single_elimination`, `tournament_cut=0`) —
the highest-ordinal tournament phase, so it qualifies.

**Activation.** `activate_pending_tournament_phase` takes the `>= 2` branch.
Loop 1 builds regional playoffs: Alpha (10 participants) and Beta (6). Gamma has
3 < `MIN_BRACKET_PARTICIPANTS`, so `_build_tournament_for_phase` returns `None`
and **no Gamma row exists**. `_final_tournament_phase()` is this phase, so loop
2 runs `_build_last_chance_tournament` per Conference:
`qualifier_count_for_size(10) == 3` ⇒ an Alpha row is created **unseeded**;
`qualifier_count_for_size(6) == 2` and `qualifier_count_for_size(3) == 1` ⇒ Beta
and Gamma get **nothing**.

| Table | Count | Detail |
| --- | --- | --- |
| `Tournament` | **3** | `"2028 — Alpha Playoffs"` (`qualifier_stage="regional_playoff"`, `state="active"`), `"2028 — Beta Playoffs"` (same), `"2028 — Alpha Last Chance Qualifier"` (`qualifier_stage="last_chance"`, `state="setup"`) |
| `TournamentParticipant` | **16** | 10 Alpha + 6 Beta + **0** for the Last-chance row |
| `BracketNode` | Alpha + Beta trees only | **0** for the Last-chance row |
| `SeasonPhase.tournament_id` | — | NULL |
| `tournaments_for_phase(phase)` | 3 | `[Alpha regional, Alpha last_chance, Beta regional]` |
| `Season.champion_team` | — | NULL |

**Drain.** `play_playoffs_task` round-robins `play_next_node` across all three.
The Last-chance row returns `None` every time (no nodes). When Alpha and Beta
have both drained, one iteration makes no progress; the task calls
`seed_pending_last_chance_brackets(phase)`, which finds Alpha's Last-chance row
in `setup` with its Regional playoff's `champion_id` set, computes
`qualified = {Alpha champion, Alpha's best unqualified Standings finisher}`,
takes the next 4 unqualified Standings ids as seeds 1-4, calls
`lock_and_build()` and returns `1`. The task `continue`s and drains the
Last-chance bracket. `_tournament_phase_complete` is `False` throughout and
`True` only once all three are `completed`.

**Terminal state.** `season.state == "completed"`,
`season.champion_team is None`.

**`worlds_qualifiers()`** returns 6 entries — Alpha 3, Beta 2, Gamma 1 — ordered
tier-first:

| Seed | Team | Conference | tier | provenance |
| --- | --- | --- | --- | --- |
| 1-3 | the three tier-1 slots | Alpha / Beta / Gamma | 1 | Alpha and Beta `conference_champion`; **Gamma `regular_season`** (§2.3 fallback, still tier 1) |
| 4-5 | Alpha's + Beta's best unqualified finishers | Alpha / Beta | 2 | `regular_season` |
| 6 | Alpha's Last-chance winner | Alpha | 3 | `last_chance` |

Within each tier the order is the rate comparison of §2.5.

**The Playoffs screen** renders three sections —
`#league-playoffs-phase-2-1` (Alpha regional), `#league-playoffs-phase-2-1-lc`
(Alpha Last-chance, carrying `#league-playoffs-stage-2-1-lc` reading
"Last Chance Qualifier") and `#league-playoffs-phase-2-2` (Beta regional) —
followed by `#league-playoffs-worlds` with rows
`#league-playoffs-worlds-row-1` .. `-6`. Gamma renders **no** section (it has no
bracket) but **does** appear in the Worlds panel.

---

## 9. Test boundary

### 9.1 The no-simulation rule (carried forward from CONF-02 §9.1)

**Brackets are drained by driving the engine, never by running the simulator.**
Two permitted techniques: call
`matches.tournament_engine.play_next_bracket_round` / `play_next_node` directly
(under a small `ROUND_TICKS` patch) when exercising the drain; or stamp
`BracketNode.winner` / `Tournament.champion` / `Tournament.state = "completed"`
on the persisted rows when exercising a *gate* or a *derivation*. The second is
the preferred technique for every `worlds_qualifiers()` test — it isolates the
derivation from the engine entirely.

**Forbidden `mock.patch` targets:** `seed_pending_last_chance_brackets`,
`worlds_qualifiers`, `_build_last_chance_tournament`, `_final_tournament_phase`,
`qualifier_count_for_size`, `order_worlds_qualifiers`, `last_chance_field`,
`first_unqualified`, plus the CONF-02 list (`_seed_order_for_phase`,
`_build_tournament_for_phase`, `activate_pending_tournament_phase`,
`tournaments_for_phase`, `lock_and_build`, `play_next_bracket_round`,
`play_next_node`). Patching `ROUND_TICKS` for speed remains fine.

**Never assert on exact simulated point totals.** Assertions are schema-level:
row counts, ids, seeds, tiers, provenances, states, booleans, context keys, DOM
ids, status codes, return ints.

### 9.2 PUBLIC names — Tests MAY assert against these

`matches/worlds.py`:

- `WorldsQualifier` and every field: `team_id`, `team_name`, `conference_id`,
  `conference_name`, `tier`, `provenance`, `matches_played`, `league_points`,
  `round_wins`, `total_score`, `seed` — plus the `provenance_label` property
- `QUALIFIER_TIER_CHAMPION` / `_REGULAR_SEASON` / `_LAST_CHANCE`
- `PROVENANCE_CHAMPION` / `_REGULAR_SEASON` / `_LAST_CHANCE`,
  `PROVENANCE_LABELS`, `LAST_CHANCE_FIELD_SIZE`
- `qualifier_count_for_size`, `first_unqualified`, `last_chance_field`,
  `order_worlds_qualifiers`

`matches/models.py`:

- `Tournament.qualifier_stage` (values and the §3.2 read rule)
- `Season.seed_pending_last_chance_brackets(phase)` — the **returned int** and
  its effects
- `Season.worlds_qualifiers()` — contents and **order**
- `Season.tournaments_for_phase(phase)` — contents and **order** (§5.8)
- `Season.activate_pending_tournament_phase()` / `complete_if_finished()` —
  effects only
- `Season.state`, `Season.champion_team`, `Season.current_phase()`
- `SeasonPhase.tournament` / `tournament_id`,
  `SeasonPhase.regional_tournaments`, `Conference.tournaments`
- `Tournament.champion`, `Tournament.state`, `Tournament.name` (containment
  only), `TournamentParticipant.seed` / `team_id`, `Tournament.nodes` counts

Callers and UI:

- `play_playoffs_task` / `play_season_task` return dicts (`completed`, `total`,
  `cancelled`); `play_single_round`'s 302 / 400; `play_playoffs`'s 202 / 409
- the Playoffs-screen context keys of §7.1-7.2 and the DOM ids of §7.3
- the Season dashboard's `playoff_phase_active` / `playoff_completed` context
  values (for the dead-click pin)

### 9.3 INTERNAL detail — Tests must NOT assert on these

- `Season._final_tournament_phase`, `_build_last_chance_tournament`,
  `_build_tournament_for_phase`, `_tournament_phase_complete`,
  `_stamp_champion_for_final_phase`, `_phase_complete`, `_preceding_phase`,
  `_seed_order_for_phase`, `_final_standings_for_phase` — private. Assert their
  **observable effects** through the public surface above. (CONF-02's §9.4
  additive-signature pins already cover the last two and are not repeated here.)
- `matches.worlds._rate` — module-private.
- Query counts, SQL text, `select_related` / `prefetch_related` choices.
- The insertion order of `TournamentParticipant` rows (assert `seed` values).
- Tournament `name` strings beyond containment (`"Last Chance Qualifier" in
  t.name`) — never hard-code the em-dash into an equality assertion.
- The deletion branch of §5.4 for a Conference of 9+ (it is unreachable there);
  if it is covered at all, cover it through a deliberately mangled snapshot and
  say so in the test name.
- Team History behaviour (CONF-02 §11 already covers regional Tournaments;
  CONF-03 adds no Team-History rule and Tests must not assert one for
  Last-chance brackets in either direction).

### 9.4 Required coverage

**`matches/tests/test_worlds_qualification.py`** — pure unit over
`matches/worlds.py` (no DB) **plus** DB tests for `Season.worlds_qualifiers()`.

*Pure, no database:*

1. **Tier boundaries.** `qualifier_count_for_size` at `1 -> 0`, `2 -> 1`,
   `3 -> 1`, `4 -> 1`, `5 -> 2`, `8 -> 2`, `9 -> 3`, `20 -> 3`, `0 -> 0`.
2. **Rate ordering across unequal Conference sizes.** Within one tier, a Team
   with `league_points=8` over `4` matches outranks a Team with
   `league_points=15` over `11` matches (2.0 vs ~1.36) — proving rate, not raw
   totals. Then the same at equal points-rate, resolved by `round_wins` rate;
   then by `total_score` rate; then by `team_id` ASC.
3. **Tier beats rate.** A tier-1 qualifier with a terrible rate seeds ahead of a
   tier-2 qualifier with the best rate in the field.
4. **`matches_played == 0`.** Every rate is `0.0`; the Team sorts last within
   its tier among positive-rate Teams and ties with other zero-rate Teams,
   resolved by `team_id` ASC. No `ZeroDivisionError`.
5. **`order_worlds_qualifiers` stamps `seed` 1..M**, returns a NEW list, and
   does not mutate its input (assert the input entries still have `seed == 0`).
   Empty input ⇒ `[]`.
6. **`first_unqualified` / `last_chance_field`.** `first_unqualified` skips
   already-qualified ids and returns `None` when all are qualified;
   `last_chance_field` returns exactly 4 ids in rank order excluding both
   qualified ids, returns fewer than 4 (never padded, never raising) on a short
   list, and returns `[]` on an empty list.
7. **`provenance_label`** for all three constants, and `""` for an unknown
   value.
8. **`TestNoDjangoImportsLeaked`** — a subprocess import of `matches.worlds`
   pulls in no `django` module, mirroring the existing classes in
   `test_bracket.py` / `test_standings.py`.

*DB tests for `Season.worlds_qualifiers()`:*

9. **Champion IS rank 1.** A Conference whose Standings rank-1 Team also won the
   Regional playoff sends that Team as tier 1 and **rank 2** as tier 2 — the
   same Team never appears twice.
10. **Champion is NOT rank 1.** Rank 1 (who lost the bracket) takes the tier-2
    slot; the champion takes tier 1.
11. **The no-Regional-playoff fallback, alongside DRAINED peers.** A
    >= 2-Conference Season in which one Conference has **3 Teams** (so no
    Regional playoff is possible) and the others are normal-sized and **fully
    drained**. `worlds_qualifiers()` returns a **complete** field in which the
    3-Team Conference's Standings **rank-1** Team appears with
    `tier == QUALIFIER_TIER_CHAMPION` and
    `provenance == PROVENANCE_REGULAR_SEASON`. No Conference is missing from the
    field. (§2.3 row 1.)
12. **PREMATURE-FIELD REGRESSION GUARD — do not omit this test.** A
    >= 2-Conference Season in which **every** Conference has **5-8 Teams** (so no
    Conference ever reaches a tier-3 slot), with the cursor still on the
    **round-robin** phase and the tournament phase **NOT built**
    (`phase.regional_tournaments.exists()` is `False`):
    `worlds_qualifiers()` **MUST return `[]`**. This is the exact case where a
    bare `regional is None` fallback would emit a complete, plausible-looking
    Worlds field mid-regular-season, before a single playoff Match — nothing
    else in the derivation would return `[]` for this Season shape (§5.5 step 4
    branch 2). Assert the empty list, and assert the Worlds panel is absent from
    the rendered Playoffs screen for the same Season.
13. **Readiness is all-or-nothing.** With one Regional playoff drained and
    another not, `worlds_qualifiers()` returns `[]`. With every Regional playoff
    drained but a required Last-chance bracket still uncrowned, it returns `[]`.
    Once everything has a champion it returns the full field.
14. **0 and 1 Conference ⇒ `[]`.** Both, including a fully-drained
    single-bracket Season whose `champion_team` IS stamped.
15. **Nothing is persisted.** Calling `worlds_qualifiers()` creates no rows
    (assert `Tournament` / `TournamentParticipant` / `BracketNode` counts are
    unchanged) and leaves `season.champion_team is None`.

**`matches/tests/test_last_chance_qualifier.py`** — DB, the build + seed +
gate cycle.

16. **Eager unseeded row after activation.** A 9+-Team Conference on the final
    tournament phase has, right after `activate_pending_tournament_phase()`, a
    `Tournament` with `qualifier_stage == "last_chance"`, `state == "setup"`,
    **zero** participants and **zero** nodes — and its Regional playoff sibling
    is `active` with a full field.
17. **No row for 8 or fewer.** A 4-Team and an 8-Team Conference produce exactly
    one Tournament each; `phase.regional_tournaments.filter(
    qualifier_stage="last_chance").count() == 0`.
18. **No row on a mid-season tournament phase.** A Season with two tournament
    phases builds Last-chance rows on the higher-ordinal one only.
19. **The field excludes BOTH already-qualified Teams.** After seeding, the 4
    participants are the top-4 Standings ids excluding the Regional playoff
    champion and the tier-2 regular-season qualifier; seeds are `1..4`.
20. **The phase refuses to advance while the bracket is unseeded.** With both
    Regional playoffs drained and the Last-chance row still `setup`,
    `season.current_phase()` still returns the tournament phase and
    `season.state == "active"`. Same assertion with the bracket seeded but
    undrained.
21. **The seeding hook makes it playable within ONE `play_playoffs_task`
    invocation.** A single call drains the Regional playoffs, seeds the
    Last-chance bracket, drains it too, and leaves every Tournament of the phase
    `completed`, `season.state == "completed"` and `season.champion_team is
    None`.
22. **Idempotence.** `seed_pending_last_chance_brackets(phase)` returns `0`
    before its Regional playoff has a champion, a positive count on the call
    that seeds, and `0` on every subsequent call — creating no extra
    participants or nodes.
23. **`tournaments_for_phase` order.** Verifies §5.8: per Conference the
    Regional playoff precedes its Last-chance sibling, and Conferences appear in
    ordinal order.
24. **`play_single_round` is never a dead click.** Drive the phase click-by-click
    through the view. On the request that resolves the final Regional-playoff
    node, assert afterwards that the Last-chance bracket is `state == "active"`
    with 4 participants, and that the Season dashboard renders with
    `playoff_phase_active is True`. Then assert further clicks drain the
    Last-chance bracket to a champion.
25. **Byte-identity pin — 0/1 Conference.** No `last_chance` row is ever
    created; `seed_pending_last_chance_brackets` returns `0`; the single bracket
    still stamps `Season.champion_team`.
26. **Byte-identity pin — the read rule.** A regional row whose
    `qualifier_stage` is forced back to `""` (simulating an un-backfilled
    CONF-02 row) is still treated as the Regional playoff: it is the tier-1
    source in `worlds_qualifiers()`, it is the row
    `seed_pending_last_chance_brackets` reads the champion from, and it renders
    with the un-suffixed `key`.

**`matches/tests/test_regional_playoffs_drain.py`** — **append** one new
`TestCase` class (do not restructure the existing 464 lines).

27. `play_season_task`'s tournament tail with `max_matchdays=None` drains the
    Regional playoffs, seeds and drains the Last-chance bracket, and completes
    the Season.
28. The budgeted branch: **one budget unit is still one stage** — the
    seed-then-retry inside `_drain_one_stage` fires only on a zero-progress
    stage. Assert the per-call clinched deltas, not wall-clock behaviour.
29. `play_playoffs_task` returns aggregated `{"completed", "total"}` that
    include the Last-chance bracket's stages once it is seeded, and a
    `play_cancel` mid-drain still returns `"cancelled": True` with the resolved
    stages committed.

**`matches/tests/test_league_playoffs.py`** — **append** one new `TestCase`
class (do not restructure the existing 331 lines).

30. **N+1 labelled sections.** A Season with one 9+-Team Conference and one
    smaller Conference yields three `brackets` entries; the Last-chance entry
    has `conference` set, `stage == "last_chance"`, `stage_label == "Last Chance
    Qualifier"` and `key == "<ord>-<conf ord>-lc"`; the rendered HTML contains
    `id="league-playoffs-stage-<ord>-<conf ord>-lc"`.
31. **CONF-02 DOM ids are byte-identical.** On a 2-Conference Season with no
    9+-Team Conference, every `key` is exactly `"<ord>-<conf ord>"`, no
    `league-playoffs-stage-` element is present, and no `-lc` substring appears
    anywhere in the response.
32. **The unseeded Last-chance section renders the pending alert**, not an empty
    bracket div: its entry has `pending is True`, `rounds == []`, and the
    rendered text carries the Last-chance message.
33. **The Worlds panel.** Absent (`"worlds_qualifiers"` is `[]` and
    `id="league-playoffs-worlds"` is not in the HTML) for a 0/1-Conference
    Season and for an incomplete multi-Conference one; present with exactly M
    rows, correct seeds, team names, Conference names and provenance labels once
    qualification is ready.

### 9.5 Why these test-file placements

Per CLAUDE.md, model + view behaviour lives in the `matches` tests package, one
file per slice. `test_regional_playoffs.py` (1162 lines) and
`test_season_playoffs.py` (975), `test_season_phase.py` (1991),
`test_tournament_models.py` (2511) are all large merge hot-spots. CONF-03
therefore takes **two new files**, mirroring how CONF-01 took
`test_conference.py` and CONF-02 took `test_regional_playoffs*.py`. The two
append targets are the small, exactly-scoped ones:
`test_regional_playoffs_drain.py` (464 lines, and CONF-03's drain changes are
literally edits to the code it already covers) and `test_league_playoffs.py`
(331 lines, the only home for Playoffs-screen coverage — fragmenting it across a
third file would be worse).

---

## 10. Explicitly NOT changed

Touching anything on this list is a contract violation. If a change here looks
necessary, stop and report rather than making it.

| Surface | Location | Why it stays |
| --- | --- | --- |
| The bracket engine | `matches/tournament_engine.py`, `matches/bracket.py` | `play_next_node`, `play_specific_node`, `play_next_bracket_round`, `find_next_node`, `advance_winner`, `advance_loser`, `stage_progress`, `series_length_for_round` — reused **verbatim**. A node-less bracket already returns `None` / `0`. |
| `Tournament.lock_and_build` | models.py:2409 | Reused verbatim for the Last-chance bracket; its `>= 4` participant check is exactly the field size we seed. |
| `Tournament.find_next_playable_node` | models.py:2666 | Already returns `None` on a node-less bracket. Do not add a `state` guard. |
| `Season.tournaments_for_phase` | models.py | Signature, body and `("conference__ordinal", "id")` ordering unchanged; it simply returns more rows now (§5.8). |
| `Season._tournament_phase_complete` / `_phase_complete` | models.py | Already the right gate — `"setup" != "completed"` blocks the phase for free. |
| `Season._stamp_champion_for_final_phase` | models.py | Already blocks on any NULL `champion_id` across `regional_tournaments`, and already never assigns `champion_team` on the multi-Conference branch. |
| `Season._final_standings_for_phase` / `_seed_order_for_phase` | models.py | CONF-02's `conference=` parameter is reused as-is; not one line changes. |
| `compute_standings` and the rest of `matches/standings.py` | `matches/standings.py` | Unchanged. CONF-03 only reads `StandingsRow` fields. |
| `SeasonPhase` | models.py | No new field, no Meta change. The Worlds phase is CONF-04's problem. |
| `Conference` | models.py | No new field, no Meta change. Size is read off the existing `starting_team_ids_json`. |
| `Match.conference` stamping | models.py; `matches/simulation/entrypoints.py` | CONF-03 **reads** the discriminator; it never changes how it is written. |
| `matches/league_views.py::play_week`, `play_playoffs`, `season_standings`, the dashboard playoff-controls helper (~2080-2100) | `matches/league_views.py` | Unchanged (§6.5). `play_single_round` is the ONLY function in this file the Code agent may edit. |
| `matches/league_screens/team_history.py` | — | CONF-02 §11's two-term `Q` chain already picks up any Tournament with `season_phase__isnull=False`, which includes Last-chance brackets. No edit, and Tests assert nothing about it. |
| `matches/tournament_views.py` | — | An unseeded Last-chance row appearing in the `/tournaments/` list in `setup` state is **accepted** (ADR-0036 Consequences). No filter is added. |
| The simulator | `matches/simulation/*` | No mechanic changes. **No Score Calibration re-baseline.** |
| `Season.champion_team` for a >= 2-Conference Season | everywhere | **Stays NULL.** This slice crowns nothing. |
| 0/1-Conference behaviour | everywhere | One bracket on `SeasonPhase.tournament`, Season-wide seeding, `champion_team` stamped, identical DOM ids, no Worlds panel. |

---

## 11. File ownership — three parallel agents, zero collisions

Every file below is owned by exactly one agent. **Do not edit a file you do not
own**; if you believe a change is needed in someone else's file, report it
instead.

### Code agent — owns

- `laserforce_simulator/matches/worlds.py` (§4, **new file**)
- `laserforce_simulator/matches/models.py` (§3.1, §5)
- `laserforce_simulator/matches/migrations/0059_tournament_qualifier_stage.py`
  (§3.3, **new file**)
- `laserforce_simulator/matches/tasks.py` (§6.1, §6.2)
- `laserforce_simulator/matches/league_views.py` — **only** `play_single_round`
  (§6.3). Every other function in this file, the dashboard helper included, is
  off-limits.
- `laserforce_simulator/matches/league_screens/playoffs.py` (§7.1, §7.2)
- `laserforce_simulator/templates/leagues/playoffs.html` (§7.3)

Runs `python -m black laserforce_simulator` on its own files when done.

### Tests agent — owns

- `laserforce_simulator/matches/tests/test_worlds_qualification.py` (**new**)
- `laserforce_simulator/matches/tests/test_last_chance_qualifier.py` (**new**)
- `laserforce_simulator/matches/tests/test_regional_playoffs_drain.py`
  (existing, 464 lines — **append** one `TestCase` class; do not restructure)
- `laserforce_simulator/matches/tests/test_league_playoffs.py` (existing, 331
  lines — **append** one `TestCase` class; do not restructure)

Touches no production file.

### Docs agent — owns

- `PLAN.md` — flip the **CONF-03** bullet (line ~766) from `[NOT STARTED]` to
  done and retire the "top-N-per-Conference" wording in favour of the
  size-tiered rule of ADR-0036. Do not touch the CONF-02 or CONF-04 bullets'
  substance.
- `laserforce_simulator/matches/CLAUDE.md` — a **CONF-03** subsection covering
  `Tournament.qualifier_stage` and its no-backfill read rule, the eager-unseeded
  Last-chance row and why it is safe, the four seeding hook sites, the
  `worlds_qualifiers()` derivation and its "empty list = not ready" rule.
- `PLAN-completed.md` — if the house convention moves the finished bullet there.

Docs must **NOT** rewrite:

- `docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md` —
  already written and Accepted. Reference it; do not edit it.
- `docs/adr/0035-...` and `docs/adr/0034-...` — closed.
- `CONTEXT.md` — the **Worlds**, **Worlds qualifier**, **Last-chance
  qualifier**, **Conference**, **Regional playoff**, **Conference champion** and
  **Standings** terms are already written and already describe CONF-03's
  behaviour. Do not re-word them.
- This contract.

---

## 12. Invariants — state these prominently, prove them in tests

1. **A Season with 0 or 1 Conference is byte-identical** in rows, reads,
   champion stamping and rendered DOM ids. It builds no `last_chance` row,
   `seed_pending_last_chance_brackets` returns `0`, `worlds_qualifiers()`
   returns `[]`, and the Worlds panel is absent entirely.
2. **A Conference of 8 or fewer Teams gets no Last-chance bracket** and is
   byte-identical to CONF-02 in rows and DOM ids.
3. **Every CONF-02 DOM id is unchanged.** The `-lc` suffix appears only on a
   Last-chance entry; `stage_label` is empty for every other bracket so no new
   element renders.
4. **`Season.champion_team` stays NULL** for a >= 2-Conference Season. This
   slice crowns nothing.
5. **The phase never advances early.** An unseeded or undrained Last-chance
   bracket blocks `_tournament_phase_complete` for free, with no new gate.
6. **The Worlds field is never partial.** `worlds_qualifiers()` is all-or-`[]`.
7. **No Conference is unrepresented.** Every Conference of size >= 2 in the
   final tournament phase contributes at least one qualifier.
8. **No Score Calibration re-baseline.** No simulation mechanic changes.

---

## 13. Definition of done

- One migration, `0059_tournament_qualifier_stage`, exactly one `AddField`, no
  `RunPython`.
- `python laserforce_simulator/manage.py makemigrations --check --dry-run`
  reports no further changes.
- `python -m black laserforce_simulator` is clean.
- The full `pytest` suite passes, reported with **exact** pass/fail counts (e.g.
  "N passed, 0 failed"), not "tests pass".
- A 0-Conference and a 1-Conference Season behave identically to
  `conf-02-regional-playoffs` in rows, reads, champion stamping and rendered DOM
  ids; so does a 2-Conference Season whose Conferences are all 8 Teams or fewer.
