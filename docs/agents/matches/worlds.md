# matches: Worlds

CONF-03 Worlds qualification + last-chance qualifier, CONF-04 the Worlds phase.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## CONF-03 worlds qualification + the last-chance qualifier

The third slice of the **Conference** epic, and the one that settles **who goes to Worlds**. In a
Season with **two or more** Conferences, the **final** (highest-ordinal) `tournament` `SeasonPhase`
now decides qualification: each Conference sends **1, 2 or 3 Worlds qualifiers** as a function of its
**size**, and the third slot — where it exists — is decided by a *second* per-Conference bracket, the
**Last-chance qualifier**, created eagerly and **unseeded** at phase activation and seeded only once
that Conference's Regional playoff has crowned a champion. The unioned field is ordered tier-first,
then by regular-season **rate**, and is **derived on demand and never persisted**. See
[ADR-0036](../../adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md), the
GLOSSARY.md **Worlds** / **Worlds qualifier** / **Last-chance qualifier** terms, and the seam contract
[`.claude/worktrees/conf-03-seam-contract.md`](../../../.claude/worktrees/conf-03-seam-contract.md).
**`Season.champion_team` still stays NULL** for a `>= 2`-Conference Season — this slice decides who
plays for it, CONF-04 crowns it — and there is **no Score Calibration re-baseline** (no simulation
mechanic changes; the only shifts are one new discriminator column, one extra `Tournament` row per
large Conference, and a pure derivation module).

**Qualifier count is a function of Conference SIZE.** Size is `len(conference.starting_team_ids_json
or [])` — the **activation snapshot** written by `Season.start_season()`. It is **not** read from the
Regional playoff's participant count and it is **not** affected by `SeasonPhase.tournament_cut`, so a
top-N cut can shrink a region's bracket without changing how many Teams that region sends to Worlds.

| Conference size (activation snapshot) | Qualifiers | Slot provenances, in tier order |
| --- | --- | --- |
| 0 or 1 | 0 | degenerate; CONF-01/CONF-05 forbid composing one |
| 2-4 | 1 | Conference champion |
| 5-8 | 2 | Conference champion; best regular-season finisher not already qualified |
| 9 or more | 3 | the above two; plus the Last-chance qualifier bracket's winner |

The three provenances are fixed: **tier 1** is that Conference's Regional playoff
`Tournament.champion`; **tier 2** is the Conference's best **Standings** finisher not already
qualified (rank 1, or rank 2 when rank 1 also won the bracket, so the same Team never occupies two
slots); **tier 3** is the `champion` of that Conference's Last-chance qualifier.

**The no-Regional-playoff fallback, and its TWO causes.** A Conference of the final tournament phase
can legitimately end up with **no Regional playoff `Tournament` row** in two different ways, and both
land on the same fallback: (1) the Conference has **2-3 Teams**, fewer than `MIN_BRACKET_PARTICIPANTS`
(= 4), so its seed order is too short and `_build_tournament_for_phase`'s regional guard returns
`None`; or (2) the phase's **`tournament_cut` is set below 4** (a cut of 2, say), truncating even a
**large** Conference's seed order below the floor — the same guard, applied *after* the
`order = order[: phase.tournament_cut]` slice. In both cases the Conference still sends **exactly
one** qualifier: its Standings **rank 1**, recorded with `provenance = PROVENANCE_REGULAR_SEASON`
(it is a regular-season placing, not a bracket result) but seeded at `tier =
QUALIFIER_TIER_CHAMPION`, so it sorts among the champions. **No Conference is ever unrepresented at
Worlds.**

> **The fallback is NOT `regional is None` — this is the slice's sharpest trap.** A bare
> `regional is None` is *also* true when the tournament phase **has not been built at all**: the
> regular season is still running and `phase.regional_tournaments` is empty for *every* Conference.
> Firing the fallback there would emit a complete, plausible-looking Worlds field
> **mid-regular-season, before a single playoff Match had been played**, and the Worlds panel would
> render it. The fallback therefore fires **only** when this Conference has no regional row **AND**
> `phase.regional_tournaments.exists()` is `True`, which is why tier 1 is a **three-branch**
> decision — `regional is not None`; else not built (`return []`); else the genuine fallback.
> `exists()` is the right predicate rather than re-deriving size against `MIN_BRACKET_PARTICIPANTS`,
> because one read covers **both** causes above, including the `tournament_cut` case where size alone
> would wrongly predict a bracket. The bug bites hardest on a `>= 2`-Conference Season whose
> Conferences are **all 8 Teams or fewer**: such a Season never reaches a tier-3 slot, so nothing
> else in the derivation would ever return `[]`.

**Cross-region seeding — tier first, then RATE.** The unioned field sorts by `tier` ASC (1 champion,
2 regular-season, 3 last-chance), then within a tier by `league_points / matches_played` DESC, then
`round_wins / matches_played` DESC, then `total_score / matches_played` DESC, then `team_id` ASC —
every input taken off that Team's **Conference-scoped** `StandingsRow`. **Rate rather than raw
totals** because Conferences differ in size and therefore play different numbers of games: a 12-team
Conference's 11-game points total is not comparable to a 5-team Conference's 4-game total. Locked
degenerate rule: when `matches_played <= 0` **all three rates are `0.0`** — no division is attempted,
no `None`, no sentinel, no exception — so such a Team sorts below every positive-rate Team in its
tier and ties with other zero-rate Teams, falling through to the `team_id` tiebreak.

**CHANGED `Tournament` — ONE new discriminator column (`matches/models.py`, after the CONF-02
`conference` FK, before the `final_series_length` block).** `qualifier_stage =
models.CharField(max_length=16, choices=QUALIFIER_STAGE_CHOICES, blank=True, default="")`, with the
choices tuple declared as a class attribute immediately above it (the house precedent of
`SeasonPhase.TOURNAMENT_MODE_CHOICES` / `Tournament.TEAM_ASSEMBLY_CHOICES` /
`Tournament.ROLE_ASSIGNMENT_CHOICES`): `("", "Not a qualifier bracket")`, `("regional_playoff",
"Regional playoff")`, `("last_chance", "Last-chance qualifier")`. `max_length=16` is **exact** —
`"regional_playoff"` is 16 characters. `Tournament` still has **no `class Meta`** and none was added:
no index, no `unique_together` on `(season_phase, conference, qualifier_stage)`; the build path is
guarded by idempotence checks, not by the database.

> **LOCKED read rule — no backfill.** Migration `0059` has **no `RunPython`**, so an existing CONF-02
> regional row keeps `qualifier_stage == ""` forever. Every read in production code, tests and
> templates therefore tests **only** `tournament.qualifier_stage == "last_chance"`. Everything else
> — including that un-backfilled `""` — is a **Regional playoff** when `conference_id` is set, and
> not a qualifier bracket at all when it is NULL. **Forbidden:** `== "regional_playoff"` as a
> *positive* test for "is this a regional playoff", `!= ""` as a test for "is this a qualifier
> bracket", any `.exclude(qualifier_stage="")`, and any migration that would make those safe. A
> regional playoff is `conference_id is not None and qualifier_stage != "last_chance"`.

| Row | `conference_id` | `qualifier_stage` | Classify as |
| --- | --- | --- | --- |
| Sandbox `Tournament` | NULL | `""` | not a qualifier bracket |
| Season-wide embed (0/1-Conference Season) | NULL | `""` | not a qualifier bracket |
| CONF-02 regional row, created **before** 0059 | set | `""` | **Regional playoff** |
| Regional row, created **after** 0059 | set | `"regional_playoff"` | **Regional playoff** |
| Last-chance row | set | `"last_chance"` | **Last-chance qualifier** |

**Migration `0059_tournament_qualifier_stage.py`** (dep `0058_tournament_regional_linkage`): exactly
**one `AddField`**, no `RunPython`, no backfill, no data migration, no follow-up migration — per
[ADR-0004](../../adr/0004-simulation-data-is-disposable.md)'s disposable-data posture, and
because the read rule above makes the un-backfilled `""` classify correctly on its own.

**NEW pure module `matches/worlds.py`.** Follows the `matches/standings.py` / `matches/bracket.py`
precedent — **no Django imports, no ORM, plain values in and out, unit-testable with zero DB**, with
a frozen import allowlist of `dataclasses` + `typing` pinned by a `TestNoDjangoImportsLeaked`
subprocess class. Exports the tier constants `QUALIFIER_TIER_CHAMPION` (1) /
`QUALIFIER_TIER_REGULAR_SEASON` (2) / `QUALIFIER_TIER_LAST_CHANCE` (3); the provenance constants
`PROVENANCE_CHAMPION` (`"conference_champion"`) / `PROVENANCE_REGULAR_SEASON` (`"regular_season"`) /
`PROVENANCE_LAST_CHANCE` (`"last_chance"`) with their `PROVENANCE_LABELS` map; `LAST_CHANCE_FIELD_SIZE
= 4`; the frozen dataclass **`WorldsQualifier`** (`team_id`, `team_name`, `conference_id`,
`conference_name`, `tier`, `provenance`, `matches_played`, `league_points`, `round_wins`,
`total_score`, `seed = 0`, plus a `provenance_label` property returning `""` for an unknown value);
and four pure functions — `qualifier_count_for_size(team_count) -> int` (the size table above; a
negative input returns `0`), `first_unqualified(ranked_team_ids, qualified_team_ids) -> int | None`
(the tier-2 rule *and* the fallback's rank-1 rule in one function),
`last_chance_field(ranked_team_ids, qualified_team_ids) -> list` (**up to** `LAST_CHANCE_FIELD_SIZE`
ids in rank order — never pads, never raises), and `order_worlds_qualifiers(qualifiers) -> list`
(the sort above, returning a **new** list with `seed` stamped 1..M via `dataclasses.replace`, exactly
as `compute_standings` / `rerank_round_robin` stamp `rank`). The rate helper `_rate(numerator,
matches_played)` is module-private. The four regular-season integers on `WorldsQualifier` are the
rate *inputs*, taken verbatim off the Conference-scoped `StandingsRow`; **no rate is stored**.

> **Naming hazards — three, and all three have real bite.**
> - `Tournament.season_phase` (the CONF-02 **forward FK**, reverse-accessed as
>   `phase.regional_tournaments`) vs `Tournament.season_phases` (the **pre-existing reverse manager**
>   of `SeasonPhase.tournament`). One character apart, **opposite directions**. CONF-03 reads the
>   forward one everywhere.
> - **`PROVENANCE_LAST_CHANCE` and `Tournament.qualifier_stage`'s `"last_chance"` share a string
>   value while being different axes** — one is a *qualifier's provenance*, the other a *bracket's
>   stage*. Neither may be used in place of the other, and the coincidence must never become an
>   import.
> - **`matches/worlds.py` must never import from `matches/models.py`.** Tier and provenance are
>   deliberately **separate axes** for the same reason: the 2-3-Team fallback is exactly the case
>   where they disagree (`QUALIFIER_TIER_CHAMPION` with `PROVENANCE_REGULAR_SEASON`), so never derive
>   one from the other.

**NEW `Season._final_tournament_phase(self) -> SeasonPhase | None`** (after `tournaments_for_phase`,
private) — walks `ordered_phases()` and returns the **last** `phase_type == "tournament"` phase, or
`None`. A non-persisted implicit fallback phase (`pk is None`) is never returned. **Only the final
tournament phase qualifies for Worlds and only it gets Last-chance brackets**; a mid-season
`tournament` phase still builds its regional brackets exactly as CONF-02 does — no `last_chance` row,
no qualification read, no new DOM id.

**NEW `Season._build_last_chance_tournament(self, phase, conference) -> Tournament | None`** (after
`_build_tournament_for_phase`, private; `conference` is **required**) — returns `None` when
`qualifier_count_for_size(size) < 3` (so a Conference of 8 or fewer gets **no row at all**), returns
`None` when a `last_chance` row for that Conference already exists (idempotence), and otherwise
creates the row and **returns it immediately** — copying every per-format field off the phase exactly
as the regional builder does, plus `season_phase=phase`, `conference=conference`,
`qualifier_stage="last_chance"`, named `f"{season.name} — {conference.name} Last Chance Qualifier"`
(em-dash U+2014, matching `Conference.__str__` and CONF-02's regional naming; the label is the
three-word proper-noun bracket name, distinct from GLOSSARY.md's prose term *Last-chance qualifier*).
`_build_tournament_for_phase` itself changes by exactly **one kwarg** — its conditional
`season_phase` / `conference` block gains `"qualifier_stage": "regional_playoff"` — and takes **no
new parameter**; a regional build is always a Regional playoff.

> **UNSEEDED is load-bearing, not a bug — do not "fix" it.** The Last-chance row exists with
> `state="setup"`, **zero** `TournamentParticipant` rows, **zero** `BracketNode` rows and
> `lock_and_build()` not yet called. Two existing behaviours make that safe with **no engine change**:
> `_tournament_phase_complete` requires `all(t.state == "completed")` and `"setup" != "completed"`,
> so **the phase already refuses to advance** while a Last-chance bracket is unseeded (no new gate);
> and `Tournament.find_next_playable_node()` delegates to `bracket.find_next_node` over `self.nodes`,
> which is empty, so it returns `None` — `play_next_node` returns `None` and `play_next_bracket_round`
> returns `0`, and **both drain loops already skip an unseeded bracket harmlessly**. Do not add a
> `state="pending_seed"`, do not create placeholder participants, do not call `lock_and_build()`
> early, and do not filter unseeded rows out of `tournaments_for_phase`. The row is created
> **eagerly** precisely because both drain loops resolve `tournaments_for_phase(phase)` **once and
> cache the list**: a row created lazily mid-drain would be invisible to the loop that must play it,
> and `_tournament_phase_complete` would report the phase complete before the bracket existed.

**NEW public `Season.seed_pending_last_chance_brackets(self, phase) -> int`** (after
`_build_last_chance_tournament`; `@transaction.atomic`) — **the seeding seam**, returning the number
of brackets it seeded on that call and **idempotent** (`0` before a Regional playoff has a champion,
a positive count on the call that seeds, `0` forever after). It filters
`phase.regional_tournaments.filter(qualifier_stage="last_chance", state="setup")` — the *one*
permitted positive test of the discriminator — and for each candidate in `conference__ordinal, id`
order finds that Conference's regional sibling (`.exclude(qualifier_stage="last_chance").first()`),
skips it when the sibling is missing or has no `champion_id`, and otherwise computes the Conference's
Standings order via `_final_standings_for_phase(self._preceding_phase(phase), conference=...)`, builds
`qualified = {champion} | {first_unqualified(...)}`, takes `last_chance_field(ranked, qualified)`,
creates one `TournamentParticipant` per entry with `seed = position + 1`, and calls
`lock_and_build()`. A field shorter than `MIN_BRACKET_PARTICIPANTS` **deletes the row** rather than
deadlocking the phase on a bracket that can never be built — unreachable for a Conference of 9+
(at least `9 - 2 = 7` Teams are always unqualified) and present only so admin-mangled data cannot
brick a Season. Deleting a candidate mid-drain is safe: a deleted instance left in a cached
`tournaments_for_phase` list re-queries `nodes` to empty, so `stage_progress([])` is `(0, 0)` and
`find_next_playable_node()` is `None`.

**Four hook sites, seed-then-continue semantics — the engine is NOT changed.** `play_next_node`,
`play_specific_node`, `play_next_bracket_round`, `find_next_playable_node`, `find_next_node`,
`stage_progress` and `lock_and_build` are reused **verbatim**; every change is in the callers.
(1) `tasks.play_playoffs_task` — only its no-progress exit changes: when a full pass plays nothing it
calls `seed_pending_last_chance_brackets(phase)` and, if that returns `> 0`, **`continue`s** the
`while True:` loop (so the PLAY-01 between-stage cancel check runs again) instead of breaking.
(2) `tasks.play_season_task`'s tournament tail — the retry lives **inside** the local
`_drain_one_stage()`, which re-runs `sum(play_next_bracket_round(t) for t in tournaments)` once after
seeding when the first pass clinched `0`; because it fires only on a zero-progress stage, **one budget
unit is still exactly one stage**, and both loop bodies stay untouched. (3)
`league_views.play_single_round` — seeds **unconditionally** after the click (idempotent, `0` when
nothing is ready) and then plays one node if seeding just made something playable. This is what stops
"Play Single Round" becoming a **dead click**: the click that resolves a Regional playoff's final node
leaves that bracket `completed` and its Last-chance sibling `setup`, which the dashboard's
playoff-controls helper reads as *neither* `active` nor `completed` and so hides the control; seeding
before the redirect flips the sibling to `active`. (4)
`Season.activate_pending_tournament_phase` — the `>= 2`-Conference branch calls
`seed_pending_last_chance_brackets(phase)` **inside and before** the CONF-02 idempotence guard's
`return`, turning re-activation into a **recovery hook** (it runs after every scheduled Round, so a
Season can never be stranded with a permanently unseeded bracket); after the regional build loop it
runs a second loop of `_build_last_chance_tournament(phase, conference)`, gated on
`_final_tournament_phase()` being this phase. **In all four the cached `tournaments_for_phase` list is
deliberately NOT re-resolved**, no in-loop `t.state` check is added and no cached instance is
`refresh_from_db()`-ed — the eager row is already in the list and `tournament.nodes` is re-queried on
every call, so the very same cached instance that was node-less on iteration 1 plays correctly on
iteration 4.

> **The Season dashboard is deliberately NOT changed.** Its helper still sets
> `playoff_phase_active` from `any(t.state == "active")` and `playoff_completed` from
> `all(t.state == "completed")`. The one transient it cannot describe — all regionals `completed`
> while a Last-chance bracket is still `setup` — is closed by the four hook sites on every path that
> can leave a user looking at the dashboard. **Do not "fix" it by treating `setup` as active**: that
> would change 0/1-Conference behaviour and break the byte-identity invariant.

**NEW public `Season.worlds_qualifiers(self) -> list[WorldsQualifier]`** (after
`seed_pending_last_chance_brackets`) — the derivation seam, computed on demand with **nothing
persisted**. Returns `[]` immediately for a `< 2`-Conference Season (a 0/1-Conference Season has no
Worlds — its season-ending playoff crowns the Season champion directly) or when there is no
tournament phase. Otherwise it resolves `prior = self._preceding_phase(phase)` and — **once, before
the per-Conference loop, never per Conference** — `phase_built = phase.regional_tournaments.exists()`,
then walks Conferences in ordinal order gathering tier 1 (the three-branch decision above), tier 2
when `count >= 2` and tier 3 when `count >= 3`, resolving Team names through a single tolerant
`Team.objects.filter(id__in=...).in_bulk()` and the four regular-season integers off that
Conference's `StandingsRow` (defaulting to `0`), and finally returns `order_worlds_qualifiers(...)`.

> **LOCKED readiness rule — "empty list = not ready", never a partial list.** `worlds_qualifiers()`
> returns `[]` the moment **any** required bracket of the final tournament phase is missing its
> `champion`, or any Conference that should have a Last-chance row has none. It never returns a
> partially-filled field. Reason: CONF-04 will build the Worlds bracket straight off this list, and a
> partial list is **indistinguishable from a complete one at the call site** — a 5-of-7 field would
> silently build a wrong-size bracket. It also gives the UI exactly one branch ("render the panel, or
> don't") instead of two. A legacy Season predating 0059 has no Last-chance rows and so returns `[]`
> forever; accepted under ADR-0004.

**Playoffs screen (`matches/league_screens/playoffs.py` + `templates/leagues/playoffs.html`).** The
`brackets` entry shape gains two keys — **`stage`** (`""` / `"regional_playoff"` / `"last_chance"`)
and **`stage_label`** (`"Last Chance Qualifier"` for a Last-chance entry, `""` for everything else, so
**no new element renders** on a CONF-02 Season) — and changes one: `pending` becomes
`tournament.state == "setup"` instead of the literal `False`, which is byte-identical for every
pre-CONF-03 row because `_build_tournament_for_phase` always calls `lock_and_build()` and flips
`state` to `"active"` before the row is ever rendered. **The CONF-02 `key` discriminator gains a
`-lc` suffix on the Last-chance entry only** (`f"{phase.ordinal}-{conference.ordinal}-lc"`), so every
regional `key` (`"<ord>-<conf ord>"`) and every 0/1-Conference `key` (`str(phase.ordinal)`) — and
therefore every DOM id built from them — stays **byte-identical**. The template gains exactly three
things: a stage badge `id="league-playoffs-stage-{{ bracket.key }}"` rendered only when
`stage_label` is set; a Last-chance branch in the existing `{% if bracket.pending %}` alert (the
`{% else %}` keeps the old text **verbatim**, so every existing rendered stub is unchanged); and the
**Worlds panel**, one new context key `worlds_qualifiers` (the raw
`view_season.worlds_qualifiers()`, or `[]`) rendered under `{% if worlds_qualifiers %}` — which *is*
the readiness test, so there is deliberately **no `worlds_ready` boolean** and the list is **not**
re-mapped into dicts. Locked ids: `league-playoffs-worlds`, `league-playoffs-worlds-table`,
`league-playoffs-worlds-row-<seed>`, `league-playoffs-worlds-team-<seed>`,
`league-playoffs-worlds-conference-<seed>`, `league-playoffs-worlds-provenance-<seed>`. The panel is
**read-only** and, for a 0/1-Conference Season, **absent entirely** — no section, no heading, no
empty-state text, no new DOM id.

**Explicitly NOT changed.** The bracket engine and `Tournament.lock_and_build` (whose `>= 4`
participant check is exactly the field size we seed); `Tournament.find_next_playable_node` (no
`state` guard added); `Season.tournaments_for_phase` (same signature, body and
`("conference__ordinal", "id")` ordering — it simply returns **up to two rows per Conference** now,
and because activation builds all regional rows in loop 1 and all Last-chance rows in loop 2, every
regional `id` is lower than every Last-chance `id`, so **within a Conference the Regional playoff
always sorts before its Last-chance sibling**); `_tournament_phase_complete` / `_phase_complete`
(already the right gate); `_stamp_champion_for_final_phase` (its `regional` list now simply includes
the Last-chance rows, so `any(t.champion_id is None ...)` blocks completion until they too have
crowned a winner, and it still never assigns `champion_team`); `_final_standings_for_phase` /
`_seed_order_for_phase` / `start_season` / `ordered_conferences`; `compute_standings`; `SeasonPhase`
and `Conference` (no new field, no `Meta` change — size is read off the existing
`starting_team_ids_json`); `Match.conference` **stamping** (CONF-03 only reads it);
`league_views.play_week` / `play_playoffs` / `season_standings` and the dashboard helper
(`play_single_round` is the only function in that file this slice edits);
`league_screens/team_history.py` (CONF-02's two-term `Q` chain already picks up any Tournament with
`season_phase__isnull=False`, Last-chance brackets included, and CONF-03 asserts nothing about it in
either direction); `matches/tournament_views.py` (an unseeded Last-chance row appearing in the
`/tournaments/` list in `setup` state is **accepted**, per ADR-0036's Consequences); and the
simulator.

**Locked names (quick index).**
- **`matches/worlds.py`** (new, Django-free) — `WorldsQualifier` (+ `provenance_label`),
  `QUALIFIER_TIER_CHAMPION` / `_REGULAR_SEASON` / `_LAST_CHANCE`, `PROVENANCE_CHAMPION` /
  `_REGULAR_SEASON` / `_LAST_CHANCE`, `PROVENANCE_LABELS`, `LAST_CHANCE_FIELD_SIZE`,
  `qualifier_count_for_size`, `first_unqualified`, `last_chance_field`, `order_worlds_qualifiers`
  (private `_rate`). **Never imports `matches/models.py`.**
- **`Tournament`** NEW `qualifier_stage` (`CharField(max_length=16, blank=True, default="")` +
  `QUALIFIER_STAGE_CHOICES`); no `Meta`. Read rule: test **only** `== "last_chance"`.
- **`Season`** NEW `_final_tournament_phase()`, `_build_last_chance_tournament(phase, conference)`,
  **public** `seed_pending_last_chance_brackets(phase) -> int` and `worlds_qualifiers() ->
  list[WorldsQualifier]`; CHANGED `_build_tournament_for_phase` (one kwarg) and
  `activate_pending_tournament_phase` (branch body only).
- **Hook sites** `tasks.play_playoffs_task` (seed-then-`continue`), `tasks.play_season_task`'s
  `_drain_one_stage()` (seed-then-retry inside the helper), `league_views.play_single_round`
  (unconditional seed before the redirect), `Season.activate_pending_tournament_phase` (build +
  recovery).
- **Screens** `league_screens/playoffs.py` (new `stage` / `stage_label` bracket keys, `-lc` `key`
  suffix, `pending = state == "setup"`, new `worlds_qualifiers` context key) +
  `templates/leagues/playoffs.html` (`league-playoffs-stage-{key}`, the Last-chance pending message,
  the `league-playoffs-worlds*` panel).
- **Migration** `matches/migrations/0059_tournament_qualifier_stage.py` (dep `0058`, one `AddField`,
  no `RunPython` / backfill).
- **Tests** `matches/tests/test_worlds_qualification.py` (the pure module + `worlds_qualifiers()`,
  including the **premature-field regression guard**: an all-`5-8`-Team `>= 2`-Conference Season with
  the tournament phase unbuilt must return `[]`) + `matches/tests/test_last_chance_qualifier.py` (the
  build/seed/gate cycle, idempotence, the `tournaments_for_phase` order, the dead-click pin and both
  byte-identity pins) + appended classes in `matches/tests/test_regional_playoffs_drain.py` and
  `matches/tests/test_league_playoffs.py`. House rule unchanged: brackets are drained by **driving
  the engine or stamping node winners**, never by running the simulator, and never by patching the
  seam under test.
- **ADR / CONTEXT** —
  [ADR-0036](../../adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md) + the
  GLOSSARY.md **Worlds** / **Worlds qualifier** / **Last-chance qualifier** terms. Invariants: a
  **0/1-Conference Season is byte-identical**; a **Conference of 8 or fewer Teams is byte-identical
  to CONF-02** in rows and DOM ids (no Last-chance row at all); **every CONF-02 DOM id is unchanged**;
  **`Season.champion_team` stays NULL**; the phase never advances early; the Worlds field is
  **all-or-`[]`**; no Conference is unrepresented; and **no Score Calibration re-baseline**.
  **DEFERRED to CONF-04 and later:** the cross-Conference Worlds `Tournament` row and phase, any
  bye / play-in handling for a non-power-of-two field, any placement / elimination-depth ranking API
  on `Tournament`, per-Conference map pools (CONF-06), and a persisted qualifier table.

## CONF-04 the worlds tournament phase

The fourth slice of the **Conference** epic, and the one that closes it competitively: a Season with
**two or more** Conferences grows a **derived, never-authored fifth-mode `SeasonPhase`** — Worlds —
carrying a **single Season-wide bracket** built straight off CONF-03's
`Season.worlds_qualifiers()`, drained through the **untouched** bracket engine, and finally
**crowning the Season champion** that CONF-01, CONF-02 and CONF-03 each deliberately left NULL. See
[ADR-0037](../../adr/0037-worlds-is-a-derived-season-phase.md), the GLOSSARY.md **Worlds** /
**Worlds phase** / **Season phase** terms, and the seam contract
[`.claude/worktrees/conf-04-seam-contract.md`](../../../.claude/worktrees/conf-04-seam-contract.md).
**A 0/1-Conference Season is byte-identical** in rows, reads, champion stamping and rendered DOM ids
— it grows no Worlds phase at all — and there is **no Score Calibration re-baseline** (no simulation
mechanic changes; the only shifts are one choices-only `AlterField`, one derived `SeasonPhase` row
per `>= 2`-Conference Season, and one `Tournament` row per such Season).

**Worlds is a phase, not a third bracket on the regional phase.** The rejected alternative — hanging
a third `Tournament` off the *existing* final tournament phase alongside the regional and
Last-chance rows, discriminated by a new `qualifier_stage="worlds"` — would have landed the row
inside `phase.regional_tournaments`, which `tournaments_for_phase` orders by `conference__ordinal`;
the Worlds row's `conference` is NULL, and NULL sorts **first on SQLite and last on PostgreSQL**,
making the bracket's drain and render position **backend-dependent** in a project whose two
supported backends are exactly those
([ADR-0025](../../adr/0025-postgresql-canonical-sqlite-dev-only.md)). It would also have forced
a rewrite of `_stamp_champion_for_final_phase`, whose `if regional: … return` is the very line
encoding "a Conference champion is not a Season champion". A fourth `SeasonPhase.phase_type` value
was rejected as too broad — every site keyed on `phase_type == "tournament"` (phase completion,
phase activation, both entry points, both drain loops, the Playoffs screen and the dashboard helper)
would need a parallel branch, and each branch is a place for the two kinds of tournament phase to
drift apart. `tournament_mode` already **is** the "what flavour of tournament phase is this?" axis.

**NEW `SeasonPhase.TOURNAMENT_MODE_CHOICES` value — exactly one constant, appended fifth.**
`("worlds", "Worlds")`, after `("random_draw", "Mid-season: drawn pool -> RR->DE")`; the four
existing pairs, their order and their label strings are **byte-unchanged**. The stored value is
6 characters, well inside the existing `max_length=16`. **No other constant is added anywhere** — no
new `Tournament.QUALIFIER_STAGE_CHOICES` value, no new `SeasonPhase.PHASE_TYPE_CHOICES` value, no
new `owner_mood.py` constant, and `MIN_BRACKET_PARTICIPANTS` (`matches/models.py:18`, value `4`) is
**not changed and not re-pointed** — it keeps guarding `_build_tournament_for_phase` and
`seed_pending_last_chance_brackets` at 4.

**The phase is DERIVED, never authored.** There is no composer wire token, no create-form control
and no admin surface for it. **NEW `Season._ensure_worlds_phase(self) -> SeasonPhase | None`**
(private, idempotent) creates **nothing** unless all three of: `len(self.ordered_conferences()) >=
2`; at least one **persisted** phase with `phase_type == "tournament"` **and** `tournament_mode !=
"worlds"` exists; and `self._worlds_phase() is None`. When only the third fails it returns the
pre-existing row; when either of the first two fails it returns `None`. Nothing in production reads
the return value — both call sites discard it — but it is returned so tests have a direct handle
(the ADR gives it no public caller). Every column of the row is passed **explicitly and
exhaustively**, so it cannot drift if a model default later changes:

| Column | Value | Note |
| --- | --- | --- |
| `ordinal` | `max(p.ordinal for p in self.phases.all()) + 1` | always the Season's highest ordinal; gate 2 guarantees the `max` never sees an empty sequence, and the value always respects `uniq_season_phase_ordinal` |
| `phase_type` | `"tournament"` | **not** a new phase type — every `phase_type == "tournament"` site carries it for free |
| `schedule_format` | `None` | a tournament phase contributes no fixtures |
| `tournament` | `None` | the forward embed, filled later by `build_pending_worlds_bracket` |
| `tournament_mode` | `"worlds"` | the **only** discriminator |
| `tournament_format` | `"single_elimination"` | |
| `tournament_cut` | `0` | the Worlds field is never cut |
| `final_` / `semifinal_` / `quarterfinal_` / `earlier_series_length` | `1` | |
| `wb_advancers` / `lb_advancers` / `swiss_rounds` | `0` | |

**NEW `Season._worlds_phase(self) -> SeasonPhase | None`** — the **one** resolver, scanning
`ordered_phases()` for the first persisted phase with `tournament_mode == "worlds"`. A
non-persisted implicit fallback phase is never returned (it is always `round_robin`). At most one
Worlds phase can exist per Season — `_ensure_worlds_phase` is idempotent and the rollover skips it —
so the first match is the only match. `_ensure_worlds_phase`, `build_pending_worlds_bracket` and the
owner-mood classifier **all** go through it, so nobody re-implements the scan.

**Two creation hooks, and the rollover skip.** `Season.start_season()` calls
`_ensure_worlds_phase()` between its `self.save()` and its existing
`self.activate_pending_tournament_phase()` call — **Start Season is the earliest moment at which
every input is frozen** (the Conference partition, snapshotted immediately above; the phase
composition, authored at create). Creating the phase *lazily*, when qualification first resolves,
was rejected for a race: `complete_if_finished()` gates on `ordered_phases()[-1]`, so at the instant
the regional phase completes it *is* the final phase, the Season flips to `completed` with a NULL
champion, and Worlds would appear after the Season had already closed.
`activate_pending_tournament_phase()` calls it **again** as the first of its two new statements —
the same **recovery-hook** role CONF-03 gave that method for unseeded Last-chance brackets. Because
it runs after every scheduled Round and every input it reads is frozen at activation, a Season
**already active** when this slice shipped gains its Worlds phase there, with an identical row,
rather than through a data migration
([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)); a Season that had already
completed stays completed and championless. `league_views._run_season_rollover` gains one
`if src.tournament_mode == "worlds": continue` as the **first statement** of its phase-copy loop:
the rollover carries **no Conferences** forward, so a copied Worlds phase would land on a flat
Season whose `worlds_qualifiers()` returns `[]` forever and strand it at `active`. Because the
Worlds phase always holds the **highest** ordinal, skipping it leaves the copied ordinals contiguous
from 1 — **no renumbering is needed and none may be added**.

**CHANGED `Season._final_tournament_phase()` — narrowed to skip `worlds`-mode phases.** One
`continue` in the walk. **After this slice the method means "the final NON-Worlds tournament
phase"** — i.e. the Regional-playoff phase — which is exactly what both of its callers want
(`worlds_qualifiers()` and the Last-chance build gate inside `activate_pending_tournament_phase`).
Without that one line, appending Worlds silently redirects qualification at the **Worlds phase's own
bracket** — empty before the build, self-referential after it. `complete_if_finished` does **not**
call it (it uses `ordered_phases()[-1]`), so champion stamping is unaffected.

**NEW public `Season.build_pending_worlds_bracket(self) -> bool`** (`@transaction.atomic`) — the
idempotent build seam, returning `True` **if and only if** it built the bracket on that call and
`False` in every other case (no Worlds phase; already built; prior phase not complete;
qualification not ready). The gate is evaluated in exactly this order: `_worlds_phase()` resolves,
else `False`; `phase.tournament_id is not None` ⇒ `False`; `_preceding_phase(phase)` exists **and**
`_phase_complete(prior)`, else `False`; `worlds_qualifiers()` is non-empty **and** at least 2 long,
else `False`. It then creates the `Tournament`, one `TournamentParticipant` per qualifier, calls
`lock_and_build(minimum=2)`, wires `phase.tournament` with `save(update_fields=["tournament"])`, and
returns `True`. All of that is inside the method's own `@transaction.atomic`, so a failure at any
step leaves **no** partial Worlds bracket and the next hook-site call retries cleanly; nested inside
an already-atomic caller it is a savepoint — the CONF-03 `seed_pending_last_chance_brackets`
precedent.

> **The `>= 2` guard is defensive, not a live branch — do not delete it because it "cannot
> happen".** A legitimate `>= 2`-Conference Season always yields at least one qualifier per
> Conference (CONF-03: no Conference is ever unrepresented), so `M >= 2` by construction. The guard
> exists so admin-mangled data — a Conference snapshot emptied after activation, say — cannot reach
> `lock_and_build` with a one-participant field.

> **`seed=q.seed`, NOT `position + 1`.** `worlds_qualifiers()` returns the field already ordered and
> already stamped `1..M` by `order_worlds_qualifiers`. The enumerate index would silently work today
> (the list *is* in seed order) and break the instant anything filters or re-orders it. Use the
> stamped value.

**The Worlds `Tournament` row is deliberately FLAT — and that is what makes champion stamping work
unedited.** `name` is `f"{season.name} Worlds"` (**no em-dash**, no Conference qualifier — it mirrors
the flat `f"{season.name} Playoffs"` shape rather than CONF-02's `"… — <Conference> Playoffs"`),
`format` is the literal `"single_elimination"`, `team_assembly="preset"`, `state="setup"` until
`lock_and_build` flips it to `"active"` in the same transaction, and every per-format field is
copied off the phase. **Both CONF-02 linkage columns are LEFT UNSET** so they take their
`null=True` default, and `qualifier_stage` is left at its `""` default — structurally identical to
the closing playoff of a flat 0-Conference Season:

| Column | Worlds row | Why |
| --- | --- | --- |
| `season_phase` | **NOT PASSED** ⇒ NULL | it is **not** a regional row; it must never appear in any `phase.regional_tournaments` queryset |
| `conference` | **NOT PASSED** ⇒ NULL | it is Season-wide, not Conference-scoped |
| `qualifier_stage` | **NOT PASSED** ⇒ `""` | CONF-03's read rule stands — `== "last_chance"` is still the only permitted positive test |

Three existing behaviours then carry it with **no change at all**:
`tournaments_for_phase(worlds_phase)` finds `regional_tournaments` empty and returns
`[phase.tournament]` (or `[]` while unbuilt); `_tournament_phase_complete` requires that single
bracket be `"completed"`, and `bool([])` is `False`, so **the Season parks on an unbuilt Worlds
phase instead of completing championless**; and `_stamp_champion_for_final_phase(worlds_phase)` sees
an empty `regional` list, **skips** the CONF-02 "a Regional playoff crowns no Season champion" early
return, and runs the existing single-bracket branch —
`champion_team = worlds_phase.tournament.champion`, `state="completed"`. **Neither
`complete_if_finished` nor `_stamp_champion_for_final_phase` is edited.** That is the whole point of
the flat shape.

**CHANGED — the bracket floor becomes a keyword-only `minimum`, and only Worlds passes 2.**
`bracket.build_bracket(participants, *, minimum: int = 4)`,
`bracket.build_double_elim_bracket(participants, *, minimum: int = 4)` and
`Tournament.lock_and_build(self, *, minimum: int = 4)` each replace their hard-coded
`len(participants) < 4` check with `< minimum`. `minimum` is **keyword-only** in all three (a bare
`*`), so no positional call site can drift; `build_double_elim_bracket` **must forward
`minimum=minimum`** into its internal `build_bracket` call, and `lock_and_build` forwards it to the
elimination builders only (the `round_robin` / `round_robin_double_elim` and `swiss` early returns
never reach them). **The `ValueError` / `ValidationError` message text is unchanged — literally
`"A tournament requires at least 4 participants."` even when `minimum != 4`** — deliberately not
f-stringified, because existing tests and callers pin the string and the only non-4 caller's own
`>= 2` guard makes the message unreachable there. `bracket.build_rr_de_finals_bracket` keeps the
**default** and must **not** forward `minimum=`; an RR→DE finals build's `wb` is a power of two of
at least 4 by the create-form shape rule. Every other caller — `_build_tournament_for_phase`,
`seed_pending_last_chance_brackets`, `tournament_views` and the sandbox `/tournaments/` surface —
keeps the default and is byte-identical.

> **Why 2 is safe, and why the alternatives were rejected.** `M` can legitimately be 2: two
> Conferences of 2-4 Teams each send one qualifier apiece, and an 8-Team, 2-Conference League is
> exactly what CONF-05's create form produces by default. Skipping Worlds for such a Season would
> mean **the most common Conference setup a user can create silently never crowns anyone**, and
> declaring a bracket-less walkover would invent a second champion-stamping path outside
> `_stamp_champion_for_final_phase` and play no Worlds Match at all, leaving the Playoffs screen
> with a Worlds panel and nothing behind it. The pure builder is already correct below four: `n = 2`
> yields `size = 2`, one round, one node — **that node is the Worlds final**; `n = 3` yields
> `size = 4` with seed 1 taking a pre-resolved round-one bye. The `< 4` guard was inherited
> sandbox-form policy, not a property of the maths. Non-power-of-two fields (5, 7, 9) need no new
> mechanism — `build_bracket` has rounded up to the next power of two and given the top `size − N`
> seeds bye nodes since LG-02a, so the play-in round PLAN.md anticipated would have been a second,
> redundant mechanism.

**Five hook sites, and the ONE deliberate phase-boundary crossing.** Making Worlds its own phase
puts it behind a boundary the drain loops do not cross: `play_season_task` and `play_playoffs_task`
each resolve `season.current_phase()` and cache `tournaments_for_phase(phase)` **once**, and bracket
Matches run through `tournament_engine.play_next_node` rather than `simulate_scheduled_round`, so
the `activate_pending_tournament_phase` hook goes quiet the moment the regular season ends. Left
alone, a "Play whole season" run would stop with the regionals drained and Worlds unbuilt. We follow
CONF-03's seed-then-continue precedent rather than restructuring the loops (an outer per-phase loop
would fix this and any future multi-tournament-phase composition in one move, but it would re-thread
PLAY-01's cooperative cancel checks and `play_season_task`'s stage-budget arithmetic through a new
level of nesting, for a generality nothing yet needs):

| # | Site | Semantics |
| --- | --- | --- |
| A | `tasks.play_playoffs_task`, inside `if not progressed:` **after** the CONF-03 `seed_pending_last_chance_brackets` clause and **before** the `break` | build ⇒ **re-resolve** `phase` and `tournaments`, then `continue`; else `break`. `phase` / `tournaments` are plain locals of the function, so **no `nonlocal`** |
| B | `tasks.play_season_task._drain_one_stage`, after the CONF-03 seed-then-retry block, still **inside** the helper | build ⇒ `nonlocal phase, tournaments` re-resolve, then one more `sum(play_next_bracket_round(...))`. Keeping it inside `_drain_one_stage` is what leaves both loop bodies, the budget arithmetic, `rr_weeks_played` and the PROGRESS emissions untouched — **do not lift it out** |
| C | `league_views.play_single_round`, after the CONF-03 `seeded` block and **before** `season.complete_if_finished()` | fire-and-forget, return value discarded |
| D | `league_views.play_playoffs`, immediately after the `request.session[…]` write and **before** `phase = season.current_phase()` | fire-and-forget; **must precede the cursor read** |
| E | `Season.activate_pending_tournament_phase`, the **first two statements** of the method | `_ensure_worlds_phase()` then `build_pending_worlds_bracket()`, in that order and only that order — the build resolves the phase the ensure just created; both discarded |

Sites A and B are the **only** places the drain loops deliberately cross a phase boundary, because
unlike the Last-chance row the Worlds bracket lives on a **different** phase; because the build
fires only when nothing else progressed, **one budget unit is still exactly one stage**. C is not a
dead click: that click already played its regional node, and without the build the *next* click
would find the cursor on an unbuilt Worlds phase whose `tournaments_for_phase` is `[]` and 400 with
"No active playoff bracket to play." — the user could never start Worlds. Its `phase` /
`tournaments` locals were resolved **before** the build and nothing below re-reads them, so they are
deliberately **not** re-resolved. D must precede `phase = season.current_phase()` and not merely the
`if`, because `build_pending_worlds_bracket` writes `phase.tournament` on its **own** freshly-loaded
`SeasonPhase` instance: a `phase` object read before the build carries a stale
`tournament_id is None`, and `tournaments_for_phase` reads exactly that attribute, so a
build-after-read would still 409. Placing E's build before `phase = self.current_phase()` also
closes a structural hazard — were the cursor ever to reach the Worlds phase with the method running,
the `len(conferences) >= 2` branch below would fan regional brackets out onto it; building first
sets `phase.tournament_id`, so the existing `if phase.tournament_id is not None: return` guard fires
and the fan-out is unreachable. **Do not add a `tournament_mode` guard to that branch — the ordering
is the guard.** Everything else in `activate_pending_tournament_phase` (the four gates, the
`standings` / `strength` / `unseeded` mode split, the CONF-02 idempotence guard, the CONF-03
Last-chance loop and the 0/1-Conference tail) is **verbatim unchanged**.

> **The PROGRESS counts SHRINK at the phase boundary, and that is intended.** `_stage_counts()` in
> both tasks closes over the same `tournaments` name, so after the deliberate re-resolution it
> aggregates the **Worlds bracket alone** and the reported `{"completed", "total"}` drops the
> regional stages. The counts describe the **current** phase, matching CONF-02's "stage counts of
> the phase being drained" contract. Assert the **final** return (`completed == total`, `total > 0`)
> and the terminal DB state; **never** assert that the PROGRESS counts increase monotonically across
> a run.

**Owner mood — a TWO-TIER classification, and `matches/owner_mood.py` is NOT TOUCHED.**
`_classify_playoffs_for_team` picks the first `tournament` phase whose `tournament_id` is set; a
regional phase leaves that NULL, so a `>= 2`-Conference career League has always scored
`("none", 0, 0)` on the owner-mood playoff axis — and the Worlds phase, which **does** set it, would
have switched that axis on **by accident**, charging every Team that failed to qualify the full
`"missed"` penalty regardless of how far it got in its own region. The whole fix lives inside
`league_views._classify_playoffs_for_team`, whose signature, return triple
`(playoff_result, rounds_won, num_rounds)`, four result strings (`"champion"` / `"seeded"` /
`"missed"` / `"none"`) and single call site are **unchanged**. It gains one branch on
`len(season.ordered_conferences()) >= 2`; the `else` arm is **today's body, verbatim and
byte-identical** — including its `tournament_id is not None` scan, which is deliberately **not**
routed through `_worlds_phase()` because a flat Season never grows one, so the scan can never pick a
Worlds phase up. The `>= 2` arm resolves the Team's `conference` via `conference_by_team_id()`, its
Regional playoff via `season._final_tournament_phase()`'s `regional_tournaments`
`.filter(conference=…).exclude(qualifier_stage="last_chance")` (that `.exclude` **is** CONF-03's
read rule, and correctly picks up an un-backfilled `""` row), and the Worlds bracket via
`_worlds_phase().tournament`. Then:

| # | Condition (first match wins) | `playoff_result` | `rounds_won` | `num_rounds` |
| --- | --- | --- | --- | --- |
| 1 | `conference is None` (Team in no Conference — admin-mangled) | `"none"` | `0` | `0` |
| 2 | `regional is None and worlds is None` (no bracket at all) | `"none"` | `0` | `0` |
| 3 | `worlds.champion_id == team_id` | `"champion"` | `0` | two-bracket |
| 4 | Team is a participant of **either** bracket | `"seeded"` | two-bracket | two-bracket |
| 5 | otherwise (participant of neither) | `"missed"` | `0` | two-bracket |

> **Count per bracket, then ADD — never union the raw `bracket_round` integers.** `num_rounds` is
> `max(regional rounds) + max(worlds rounds)` and `rounds_won` is
> `len({distinct regional rounds won}) + len({distinct worlds rounds won})`. The two brackets number
> their rounds independently from 1, so a Team that won round 1 of its region **and** round 1 of
> Worlds has won **two** rounds; a set union would collapse them to one.

`num_rounds` is the same maximum path for every Team in a Conference, so the denominator is fair
within a region even when Conferences differ in size, and **the Last-chance bracket is excluded from
both the numerator and the denominator** — a Team cannot ride it past the maximum path its
Conference offers. `compute_playoffs_delta` is consumed verbatim: its `"seeded"` branch is already
depth-proportional (`(PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won`), so feeding it the longer
two-bracket path yields the intended ladder — Worlds champion above Worlds finalist above Conference
champion above regional finalist above first-round exit — **for free**. Inventing per-tier constants
was rejected because CAR-02's values are faithful to ZenGM's `updateOwnerMood` and a new ladder would
have no such source; summing two independent per-bracket deltas was rejected because it doubles the
axis range in both directions against a `FIRE_THRESHOLD` tuned for a single bracket. Rows 2 and 5
are what keep the pre-CONF-04 accident from returning, and row 1 returning `"none"` (the neutral
`0.0` delta) rather than `"missed"` (the `-0.2` penalty) is a **pinned defensive choice**: an
unpartitioned Team in a partitioned Season is broken data, and broken data must not fire a Manager.

**Playoffs screen (`matches/league_screens/playoffs.py`) — and `templates/leagues/playoffs.html` is
NOT EDITED.** Both bracket derivations (the built entry and the pending stub) gain a Worlds branch
evaluated **FIRST**, because the Worlds `Tournament`'s `conference` is `None` and would otherwise
fall into the Season-wide branch. `is_worlds` is read from **`phase.tournament_mode == "worlds"`**
— the PHASE flavour — never from anything on the Tournament row:

| Key | Season-wide (0/1-Conference) | Regional playoff | Last-chance | **Worlds** |
| --- | --- | --- | --- | --- |
| `key` | `str(phase.ordinal)` | `"<ord>-<conf ord>"` | `"<ord>-<conf ord>-lc"` | **`"<ord>-worlds"`** |
| `stage` | `""` | `"regional_playoff"` | `"last_chance"` | **`"worlds"`** |
| `stage_label` | `""` | `""` | `"Last Chance Qualifier"` | **`"Worlds"`** |
| `conference` | `None` | the `Conference` | the `Conference` | **`None`** |
| `champion` | `tournament.champion` | same | same | same — **the Season champion** |

The pending stub gets the same treatment (`name` `"Worlds"` instead of `"Playoffs"`, plus the same
`key` / `stage` / `stage_label`), so **a phase's DOM id is stable across the unbuilt → built
transition** and a `>= 2`-Conference Season shows a **"Worlds" pending section from the moment the
Season starts** — intended, since the phase is derived at `start_season` and the panel tells the
user Worlds is coming. Every id falls out of the existing `{{ bracket.key }}` interpolations and the
existing `{% if bracket.stage_label %}` badge block renders the Worlds badge for free:
`league-playoffs-phase-<ord>-worlds`, `-stage-`, `-champion-`, `-bracket-`,
`-round-<ord>-worlds-<bracket_round>`, `-node-…` and `-node-score-…`. The Conference sub-heading is
**absent** (`bracket.conference` is `None`). The Worlds pending stub reaches the existing
`{% if bracket.stage == "last_chance" %}` alert's `{% else %}` branch and renders that text
verbatim; that is accepted — **do not add a Worlds-specific alert branch**, the template stays
untouched this slice. The context dict is otherwise unchanged, CONF-03's `worlds_qualifiers` key
included.

> **DOM-id hazard.** `id="league-playoffs-worlds"` is **already taken** by CONF-03's Worlds
> *qualification* table (the seed / team / conference / provenance panel below the brackets). The
> CONF-04 bracket **section** is `league-playoffs-phase-<ord>-worlds`. They coexist on the same page
> for a fully-resolved Season and must never be conflated in an assertion.

**CHANGED `league_views._playoff_cursor_keys` — `following_tournament_is_final` generalised.** From
"the next tournament phase **is** the last phase" (`min(following_tournament_ordinals) ==
phases[-1].ordinal`) to "**nothing but tournament phases follows it**"
(`all(p.phase_type == "tournament" for p in phases if p.ordinal > next_tournament_ordinal)`).
Appending Worlds after the Regional-playoff phase would otherwise flip a season-ending playoff back
to the mid-season "Until Tournament" label. Verified to reproduce today's result on both pre-CONF-04
shapes: `RR(1) → t(2)` stays `True` (vacuously — no phase has a higher ordinal) and
`RR(1) → t(2) → RR(3) → t(4)` stays `False` (phase 3 is `round_robin`), while the new
`RR(1) → t(2) → worlds(3)` now correctly yields `True`. `has_following_tournament_phase` and every
other value the helper returns are **unchanged**.

**Migration `0060_alter_seasonphase_tournament_mode.py`** (dep `0059_tournament_qualifier_stage`):
exactly **one `AlterField`**, choices-only, with **no `RunPython`, no backfill, no data migration
and no second migration**. A choices-only `AlterField` has no database-level effect on PostgreSQL or
SQLite, and no column is added, dropped or re-typed anywhere.

**Explicitly NOT changed — the byte-identity invariants.** (1) **A Season with 0 or 1 Conference is
byte-identical** in rows, reads, champion stamping and rendered DOM ids: `_ensure_worlds_phase()`
returns `None`, `build_pending_worlds_bracket()` returns `False`, no `SeasonPhase` and no
`Tournament` row is added, and the rendered Playoffs screen contains **no `-worlds` substring**.
(2) **Every CONF-02 / CONF-03 DOM id is unchanged** — the `-worlds` suffix appears only on a Worlds
entry, `stage_label` stays `""` for Season-wide and regional brackets so no new element renders on a
CONF-02 Season, and `league-playoffs-worlds` continues to mean CONF-03's qualification table and
nothing else. (3) **Every existing caller of `build_bracket` / `build_double_elim_bracket` /
`lock_and_build` keeps `minimum=4`**, error message strings included. (4) **`matches/owner_mood.py`
is not touched** — no new constant, no new branch, no new `playoff_result` value. (5) **CONF-03's
`qualifier_stage` read rule survives**: every read still tests only `== "last_chance"`, and **there
is deliberately NO `qualifier_stage == "worlds"`** — the Worlds bracket is identified from
`phase.tournament_mode`. (6) **The bracket engine is untouched** — `play_next_node`,
`play_specific_node`, `play_next_bracket_round`, `find_next_node`, `advance_winner`,
`advance_loser`, `stage_progress`, `series_length_for_round`, `Tournament.find_next_playable_node`
and `_persist_elim_specs` are all reused verbatim. (7) **`complete_if_finished` and
`_stamp_champion_for_final_phase` are not edited.** (8) **`templates/leagues/playoffs.html` is not
edited.** (9) **`matches/worlds.py` is not edited** — CONF-03's pure module stays pure, frozen
import allowlist (`dataclasses`, `typing`), no Django, no ORM. (10) Exactly one migration,
choices-only. (11) **No Score Calibration re-baseline.** Also unchanged: `worlds_qualifiers()`,
`tournaments_for_phase()`, `_build_tournament_for_phase()` (the Worlds build does **not** go through
it), `seed_pending_last_chance_brackets()`, `_preceding_phase` / `_phase_complete` /
`_tournament_phase_complete` / `_final_standings_for_phase` / `_seed_order_for_phase` /
`ordered_phases` / `ordered_conferences` / `conference_by_team_id`, `matches/standings.py`,
`matches/tournament_views.py` and the simulator.

**Naming hazards, and every one of them has real bite.**
- **`tournament_mode == "worlds"` (the PHASE flavour) vs `qualifier_stage` (the BRACKET stage).**
  Different models, different questions. The Worlds `Tournament` carries `qualifier_stage == ""`;
  anything asking "is this the Worlds bracket?" asks the **phase**.
- **`_final_tournament_phase()` now means "the final NON-Worlds tournament phase"** — the
  Regional-playoff phase. For a `>= 2`-Conference Season it is **no longer** `ordered_phases()[-1]`.
  Use `_worlds_phase()` to reach the Worlds phase and `ordered_phases()[-1]` where "the last phase"
  is meant (`complete_if_finished` does exactly that, and is unchanged).
- **`minimum` (the builder / lock kwarg, default `4`) vs `MIN_BRACKET_PARTICIPANTS`
  (`matches/models.py:18`, value `4`).** They are **not** wired together — `matches/bracket.py` is a
  pure module and must not import `models.py`, so the builders' default is the literal `4`.
- **`Tournament.season_phase` vs `Tournament.season_phases`** (carried forward from CONF-02/CONF-03).
  The Worlds `Tournament` has `season_phase_id is None` **and** a non-empty `season_phases` whose
  `.first()` **is** the Worlds phase. Never use one to test for the other.
- **`worlds_qualifiers` (CONF-03's derived field, and the Playoffs-screen context key) vs the Worlds
  `Tournament`'s `participants`.** The first is a list of frozen `WorldsQualifier` dataclasses with
  a `seed` attribute; the second is a `TournamentParticipant` queryset. They carry the same team ids
  and the same seeds after the build, and are still different objects.
- **`build_pending_worlds_bracket` (returns `bool`) vs `seed_pending_last_chance_brackets` (returns
  `int`).** Both are idempotent "do it if ready" seams sitting side by side at four of the same hook
  sites, and their return types differ. `if season.build_pending_worlds_bracket():` and
  `if season.seed_pending_last_chance_brackets(phase) > 0:` are both correct as written — do not
  "harmonise" them.
- **`_ensure_worlds_phase` (creates the PHASE) vs `build_pending_worlds_bracket` (creates the
  BRACKET).** Both run at the top of `activate_pending_tournament_phase`, in that order, and only
  that order works.

**Locked names (quick index).**
- **`SeasonPhase`** NEW `TOURNAMENT_MODE_CHOICES` fifth entry `("worlds", "Worlds")`; no new
  `phase_type`, no new field.
- **`Season`** NEW `_ensure_worlds_phase() -> SeasonPhase | None`, `_worlds_phase() -> SeasonPhase |
  None`, **public** `build_pending_worlds_bracket() -> bool` (`@transaction.atomic`); CHANGED
  `_final_tournament_phase()` (narrowed to skip `worlds`), `start_season()` (one inserted call) and
  `activate_pending_tournament_phase()` (two inserted calls at the top). `complete_if_finished` /
  `_stamp_champion_for_final_phase` / `worlds_qualifiers` / `tournaments_for_phase` /
  `_build_tournament_for_phase` **unchanged**.
- **`bracket.py` / `Tournament`** CHANGED `build_bracket(participants, *, minimum=4)`,
  `build_double_elim_bracket(participants, *, minimum=4)` (**forwards** `minimum` inward) and
  `Tournament.lock_and_build(*, minimum=4)`; error strings unchanged;
  `build_rr_de_finals_bracket` keeps the default.
- **Hook sites** `tasks.play_playoffs_task` (build-then-re-resolve-then-`continue`),
  `tasks.play_season_task`'s `_drain_one_stage()` (`nonlocal` re-resolve then one retry),
  `league_views.play_single_round` (before `complete_if_finished`), `league_views.play_playoffs`
  (before the cursor read), `Season.activate_pending_tournament_phase` (ensure + build, the first
  two statements); plus `Season.start_season` (ensure) and `league_views._run_season_rollover`
  (skip the `worlds` phase).
- **Owner mood** `league_views._classify_playoffs_for_team` only — additive `>= 2`-Conference
  branch, signature and return triple unchanged; **`matches/owner_mood.py` untouched**.
- **Screens** `league_screens/playoffs.py` (Worlds branch first in both derivations: `key`
  `"<ord>-worlds"`, `stage` `"worlds"`, `stage_label` `"Worlds"`, stub `name` `"Worlds"`) +
  `league_views._playoff_cursor_keys` (`following_tournament_is_final` generalised);
  **`templates/leagues/playoffs.html` untouched**.
- **Migration** `matches/migrations/0060_alter_seasonphase_tournament_mode.py` (dep `0059`, one
  choices-only `AlterField`, no `RunPython` / backfill).
- **Tests** `matches/tests/test_worlds_tournament.py` (new — phase derivation and its gates, the
  build seam and its idempotence, `seed=q.seed`, the `M = 2` and `M = 3` paths, the drain to a
  champion, the flat-row shape, the `play_playoffs` 202 and `play_single_round` non-dead-click pins,
  `lock_and_build(minimum=…)`, and the Playoffs-screen Worlds section) plus one appended class each
  in `matches/tests/test_regional_playoffs_drain.py` (both task hook sites end to end),
  `test_season_playoffs.py` (parks unbuilt, crowns on completion), `test_owner_evaluations_writer.py`
  (the decision table through the writer), `test_league_next_season.py` (the rollover skip) and
  `test_bracket.py` (the `minimum=` kwarg, keyword-only, DE forwarding, unchanged message). House
  rule unchanged: brackets are drained by **driving the engine or stamping node winners**, never by
  running the simulator, and never by patching the seam under test.
- **ADR / CONTEXT** — [ADR-0037](../../adr/0037-worlds-is-a-derived-season-phase.md) + the
  GLOSSARY.md **Worlds** / **Worlds phase** / **Season phase** terms. Invariants: a
  **0/1-Conference Season is byte-identical**; **every CONF-02 / CONF-03 DOM id is unchanged**;
  **`owner_mood.py`, the bracket engine, `worlds.py` and the Playoffs template are all untouched**;
  **no `qualifier_stage == "worlds"`**; and **no Score Calibration re-baseline**. **DEFERRED to
  later slices:** per-Conference rotating map pools (CONF-06), any placement / elimination-depth
  ranking API on `Tournament`, a persisted qualifier or Worlds-standings table, and any authoring UI
  or composer wire token for the Worlds phase.
