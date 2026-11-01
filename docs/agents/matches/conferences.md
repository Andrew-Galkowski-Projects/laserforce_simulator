# matches: Conferences

CONF-01 partition, CONF-02 regional playoffs, CONF-05 manage conferences, CONF-06 per-Conference map rotations. Worlds is in `worlds.md`.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## CONF-01 conference foundation

The first slice of the **Conference** epic (the re-sliced SUB-01 piece 3, reframed
from "Sub-league + map pools" to the ZenGM-**worlds** game type). A **Conference** is a
named, Season-level partition of a Season's enrolled Teams into a **disjoint** competitive
group; a Season has **zero** Conferences (the default — one implicit all-Teams group,
**byte-identical to a flat single-table Season**) or **two or more**, each a disjoint subset.
CONF-01 ships **only the foundation**: the `Conference` partition model, intra-Conference
round-robin scheduling (one RR *per* Conference, played in **parallel** on the shared
**Matchday** calendar), and per-Conference Standings. Conferences are **admin-created** this
slice (the `ConferenceAdmin` mirrors the Part2a `SeasonPhase`-foundation precedent — the
create-League composer is deferred to CONF-05). The load-bearing insight: because
Conferences play **intra-Conference only**, every regular-season fixture is *always* within
exactly one Conference, so the cross-pool map-resolution ambiguity that worried the original
PLAN never arises and the Django-free `ScheduleFixture` dataclass stays **unchanged**.
**No simulation mechanic changes — no Score Calibration re-baseline** (the only shifts are
which `Match` a Round attaches to + a new discriminator FK; tournament/playoff paths
untouched). See [ADR-0034](../../adr/0034-conference-partition.md), the GLOSSARY.md
**Conference** glossary term (already written, retiring the reserved "Sub-league"), and the
seam contract
[`.claude/worktrees/conf-01-seam-contract.md`](../../../.claude/worktrees/conf-01-seam-contract.md).

**NEW `Conference` model (`matches/models.py`, declared after `Season`, before
`SeasonPhase`).** Fields: `season` FK CASCADE `related_name="conferences"`; `name`
(`CharField(max_length=100)`); `ordinal` (`PositiveSmallIntegerField`, 1-based display
order); `teams` M2M to `teams.Team` `related_name="conferences"` (the disjoint subset); and
`starting_team_ids_json` (`JSONField(null=True, blank=True, default=None)`) — the
**activation snapshot** of this Conference's team ids (sorted asc), mirroring
`Season.starting_team_ids_json`, `None` pre-activation and written by `start_season()`.
`Meta.ordering = ["ordinal"]` (so `conferences.all()` is always ordinal order) plus the
`UniqueConstraint(fields=["season", "ordinal"], name="uniq_season_conference_ordinal")`.
`__str__` returns `f"{self.season} — {self.name}"` (em-dash U+2014).

**CHANGED `Match` — discriminator FK (`matches/models.py`, after `Match.leg`).** A new
`conference = models.ForeignKey("matches.Conference", null=True, blank=True,
on_delete=models.SET_NULL, related_name="matches")`, the same nullable-discriminator pattern
as `Match.season_phase` (Part2c-2) / `Match.leg` (Part2c-3a) — chosen over join-derived
"both teams in this Conference" scoping (per-Conference Standings read
`Match.objects.filter(conference=conf)`, and the FK future-proofs the deferred regional
playoffs). Stamped on the **Round-1 `Match` create**, left **out of the find-or-create key**
(a pairing is already unique within a phase — the two teams share one Conference and meet
only there), so the key `(season, season_phase, frozenset(teams), leg)` is **unchanged**.

**NEW `Season` helpers (`matches/models.py`).** `ordered_conferences(self) ->
list[Conference]` returns `list(self.conferences.all())` (Meta.ordering guarantees ordinal
order; `[]` when none). `_scheduled_conference_partitions(self) -> list[tuple[Conference |
None, list[int]]]` is the per-Conference team-id partition feeding scheduling: **zero
Conferences** ⇒ `[(None, self._scheduled_team_ids())]` (one implicit all-Teams partition —
byte-identical to today); **>= 1 Conference** ⇒ one `(conf, ids)` per `ordered_conferences()`
where `ids` is the **draft** Season → `sorted(conf.teams)` intersected with
`_scheduled_team_ids()`, **active/completed** → `list(conf.starting_team_ids_json or [])`
(mirrors `_scheduled_team_ids`'s draft-vs-snapshot rule, per-Conference).
`conference_by_team_id(self) -> dict[int, Conference]` maps every team in every Conference to
its Conference (draft live M2M / active snapshot, matching the partition rule; empty dict for
a zero-Conference Season) — the play loop uses it to stamp `Match.conference`.

**CHANGED `Season.scheduled_fixtures_by_phase()` — parallel overlay (return shape
UNCHANGED).** Still returns `list[tuple[SeasonPhase, list[ScheduleFixture]]]`; internally, per
`round_robin` phase it generates **one round-robin per partition** from
`_scheduled_conference_partitions()` and overlays them on the shared matchday calendar. For
each `(conf, conf_ids)` with `len >= 2`: `base = generate_schedule(conf_ids,
phase.schedule_format or self.schedule_format)`, emit one offset `ScheduleFixture` per `f`
(`matchday = f.matchday + offset`, round_number / team ids / leg unchanged); the phase's
fixtures are the **concatenation** of all partitions' offset fixtures. The phase **span**
added to the next phase's `offset` is the **max** over partitions of `max(f.matchday for f in
base)` — the *parallel-overlay* rule (California Matchday 1 and Nevada Matchday 1 are the
same calendar week; phase span = the largest Conference's span). A `< 2`-team partition
contributes nothing and does not raise; an all-empty phase is skipped (existing behaviour).
**`scheduled_fixtures()` / `_fixtures_for_phase()` / `_rr_phase_complete()` / `_is_finished()`
are UNCHANGED** — they consume the per-phase union, so an RR phase completes only when
**every** Conference's round-robin is played. Zero-Conference Season ⇒ one `(None, all ids)`
partition ⇒ byte-identical output.

**CHANGED `Season.start_season()` — Conference snapshot.** After the existing
`starting_team_ids_json` / `starting_map_*` snapshots and inside the same
`@transaction.atomic`, snapshot each Conference: `for conf in self.conferences.all():
conf.starting_team_ids_json = sorted(t.id for t in conf.teams.all());
conf.save(update_fields=["starting_team_ids_json"])` — so mid-cycle admin edits to a
Conference's membership cannot drift the active schedule (the activation-snapshot pattern,
per-Conference).

**CHANGED `Season._stamp_champion_for_final_phase()` — multi-Conference NULL champion.** In
the round-robin / implicit-fallback branch (the `else` after the `tournament` branch), if
`self.conferences.count() >= 2` the Season **completes but leaves `champion_team` NULL** —
there is no legitimate cross-Conference champion until Worlds (disjoint
schedules make a cross-Conference record comparison meaningless; deciding it on the field is
the whole point of Worlds). CONF-04 shipped Worlds, but **this branch is unaffected**: a
Season whose *final* phase is a round robin has no non-`worlds` `tournament` phase, so
`_ensure_worlds_phase` creates nothing and such a Season still completes championless —
permanently. Concretely: a `>= 2`-Conference RR-final Season still flips
`state="completed"` + saves, only the champion stamp is skipped; a **0/1-Conference** Season
is unchanged (`champion_team = compute_standings(...)[0]`). The `tournament` branch is
untouched. (Verify the state flip is not lost against `complete_if_finished`'s flow — the
cursor / dashboard must read "completed".)

**CHANGED `BatchSimulator.simulate_scheduled_round` (`matches/simulation/entrypoints.py`) —
keyword-only `conference`.** A new `conference=None` keyword-only param **after `leg`, before
`fidelity`**; default `None` ⇒ byte-identical to every existing caller. The find-or-create
**key is UNCHANGED**; on the **Round-1 `Match.objects.create(...)` site only** it passes
`conference=conference` (Round 2 finds the existing Match and does NOT re-stamp). A
`conference` with `pk is None` coerces to `None` like the `season_phase` guard.

**Play-loop stamp — three sites.** Each builds `conf_by_team =
season.conference_by_team_id()` once (outside the per-fixture loop) and passes
`conference=conf_by_team.get(fixture.team_a_id)` (== `team_b_id`'s Conference, since fixtures
are intra-Conference) into `simulate_scheduled_round`: `matches/tasks.py::play_season_task`
(the per-fixture loop, also carrying `play_two_months` / `play_until_end` which enqueue this
task), `matches/league_play_services.py::play_week`, and
`matches/league_play_services.py::_commit_live_rr_round` (the One Week (Live) **RR** commit;
the playoff commit uses `play_specific_node`, untouched).
Zero-Conference Season ⇒ `conf_by_team` empty ⇒ `conference=None` ⇒ byte-identical.

**Per-Conference Standings (`matches/league_services.py::build_season_standings`, served by
`GET /api/seasons/<id>/standings/` as `groups`).** **One independently ranked group per
Conference**: for `>= 1` Conference, per Conference it scopes `completed_matches` to
`Match.objects.filter(conference=conf, is_completed=True)` (keeping the existing member-night
`.exclude`), takes `enrolled_teams` from that Conference's `starting_team_ids_json`
(active/completed) or live M2M (draft preview), then `compute_standings(...)`; the result is
a `standings_groups` list of `{"conference": Conference | None, "name": str | None,
"rows_with_teams": [...]}` (beside `is_draft_preview`). **Zero-Conference rule (LOCKED):** a
Season with no Conference rows yields **exactly one group** with `conference` / `name` `None`.

**CHANGED `matches/league_services.py::build_dashboard_context` — manager-Conference snippet.**
The dashboard's top-3 standings snippet: when the displayed Season has `>= 2` Conferences,
scope `compute_standings` to the **manager's** Conference — the Conference containing
`displayed_season.league.current_team` (resolve via the team's membership; fall back to the
first Conference, else empty). Otherwise (0/1 Conference) unchanged (overall top-3). Only the
snippet's match corpus + enrolled-teams set change.

**Admin (`matches/admin.py`).** `@admin.register(Conference)` →
`ConferenceAdmin(filter_horizontal=("teams",), list_display=("season", "ordinal", "name"))`;
no existing registration touched. This is the **sole** create surface this slice (composer
deferred to CONF-05).

**Migration `0057_conference_match_conference.py`** (dep `0056_season_play_job_cancel` + the
latest `teams` migration, which `makemigrations` resolves for the M2M / FK cross-app dep):
`CreateModel(Conference)` (with the M2M, `starting_team_ids_json`, `Meta.ordering` +
`uniq_season_conference_ordinal`) → `AddField(Match.conference)`. **No `RunPython`, no
`RunSQL`, no backfill** ([ADR-0004](../../adr/0004-simulation-data-is-disposable.md) —
existing Seasons have zero Conferences and stay byte-identical).

**Invariants (the two load-bearing guarantees).** (1) **Byte-identical-when-zero-Conference**
— every changed path falls back to the single `(None, all ids)` partition / `conference=None`
/ single standings group / unchanged champion, so a Season with no Conference rows is
indistinguishable from today. (2) **No Score Calibration re-baseline** — CONF-01 changes no
simulation mechanic and consumes no new RNG; only the `Match` a Round attaches to + a new
discriminator change.

**Locked names (quick index).**
- **Model** `matches.models.Conference` — `season` FK CASCADE `related_name="conferences"`,
  `name`, `ordinal` (`PositiveSmallIntegerField`), `teams` M2M `related_name="conferences"`,
  `starting_team_ids_json` (`JSONField`); constraint `uniq_season_conference_ordinal`;
  `Meta.ordering = ["ordinal"]`; `__str__ = "{season} — {name}"`.
- **Match** `Match.conference` FK (`SET_NULL`, null/blank, `related_name="matches"`, after
  `Match.leg`; stamped on Round-1 create, out of the find-or-create key).
- **`Season`** NEW `ordered_conferences()` / `_scheduled_conference_partitions()` /
  `conference_by_team_id()`; CHANGED `scheduled_fixtures_by_phase()` (parallel overlay, return
  shape unchanged) / `start_season()` (per-Conference snapshot) /
  `_stamp_champion_for_final_phase()` (NULL champion but `state="completed"` for `>= 2`
  Conferences). UNCHANGED: `scheduled_fixtures` / `_fixtures_for_phase` / `_rr_phase_complete`
  / `_is_finished`.
- **Simulator** `BatchSimulator.simulate_scheduled_round(..., *, conference=None, ...)`
  (keyword-only, after `leg`, before `fidelity`; key unchanged).
- **Play sites** `tasks.play_season_task` / `league_play_services.play_week` /
  `league_play_services._commit_live_rr_round` — `conf_by_team = conference_by_team_id()` +
  `conference=conf_by_team.get(fixture.team_a_id)`.
- **Services** `league_services.build_season_standings` (per-Conference `standings_groups`)
  + `build_dashboard_context` (manager-Conference snippet for `>= 2`).
- **Admin** `matches.admin.ConferenceAdmin` (`filter_horizontal=("teams",)`,
  `list_display=("season", "ordinal", "name")`).
- **Migration** `matches/migrations/0057_conference_match_conference.py` (dep `0056`,
  CreateModel → AddField, no `RunPython` / backfill).
- **ADR / CONTEXT** — [ADR-0034](../../adr/0034-conference-partition.md) + the GLOSSARY.md
  **Conference** term (already written, retiring "Sub-league"). Invariants:
  byte-identical-when-zero-Conference; **no Score Calibration re-baseline**. **DEFERRED to
  later CONF slices:** per-Conference regional playoffs (CONF-02), Worlds qualification
  (CONF-03) + the Worlds Tournament (CONF-04), the create-League Conference composer (CONF-05),
  per-Conference rotating map pools (CONF-06).

## CONF-02 per-conference regional playoffs

The second slice of the **Conference** epic, and the one that makes a Conference a full
competitive unit: after a Conference's regular season completes, that Conference runs **its own**
seeded playoff bracket — a **Regional playoff** — crowning one **Conference champion**.

**Read this first: CONF-02 is also a bug fix.** Before this slice,
`Season._final_standings_for_phase` computed Standings over the **whole Season** with no
Conference scoping, and `Season.activate_pending_tournament_phase` carried **no Conference
guard**. A `>= 2`-Conference Season with a trailing `tournament` phase therefore built **one
cross-Conference playoff** — teams from different Conferences meeting in a bracket, a direct
contradiction of the intra-Conference invariant [ADR-0034](../../adr/0034-conference-partition.md)
established and CONF-01 shipped. CONF-02 does not merely add regional playoffs; it corrects that.
See [ADR-0035](../../adr/0035-regional-playoffs-one-tournament-per-conference.md), the
GLOSSARY.md **Regional playoff** / **Conference champion** terms, and the seam contract
[`.claude/worktrees/conf-02-seam-contract.md`](../../../.claude/worktrees/conf-02-seam-contract.md).

The shape: **one `tournament` `SeasonPhase` builds N first-class `Tournament` rows, one per
Conference.** Each is a real Tournament with its own participants, seeding, bracket nodes and
champion, so the bracket engine is reused **verbatim** — `lock_and_build`,
`play_next_bracket_round`, `play_next_node`, `find_next_node`, `stage_progress`,
`_collapse_drop_byes`, `series_length_for_round` and **all five formats are unchanged**. Every
generalisation lives in the callers. **A 0/1-Conference Season is byte-identical** in rows,
reads, champion stamping and bracket `key`s; **no Score Calibration re-baseline** (no
simulation mechanic changes — the only shifts are how many `Tournament` rows a tournament phase
produces and which Match corpus seeds them).

**CHANGED `Tournament` — two additive nullable FKs (`matches/models.py`, after `champion`,
before the `final_series_length` block).** `season_phase = FK("matches.SeasonPhase", null=True,
blank=True, on_delete=SET_NULL, related_name="regional_tournaments")` and `conference =
FK("matches.Conference", null=True, blank=True, on_delete=SET_NULL, related_name="tournaments")`
— the same nullable-discriminator-FK precedent as `Match.season_phase` / `Match.conference` /
`SeasonPhase.tournament`, chosen over deriving the relationship from a join so a phase can
**enumerate its own brackets** (which is what makes "all N drained" a clean query). `SET_NULL`
on both: deleting a Season must not cascade a Tournament's history away. Both are **NULL on a
sandbox Tournament and on the Season-wide bracket of a 0/1-Conference Season**; non-NULL **only**
on a regional Tournament. `Tournament` still has **no `class Meta`** and none was added — no
`unique_together` on `(season_phase, conference)`; the build path is guarded by an idempotence
check, not a DB constraint.

> **Naming hazard — the single most confusable pair in this app.** `Tournament` now carries
> **both** `tournament.season_phase` (the **new forward FK** added here — the regional link,
> reverse-accessed as `phase.regional_tournaments`) and `tournament.season_phases` (the
> **pre-existing reverse manager** of `SeasonPhase.tournament`, i.e. "the phases whose single
> embed pointer targets me"). They differ by one character and mean **opposite directions**.
> A regional Tournament has `season_phase_id` set and an **empty** `season_phases`; a Season-wide
> embedded Tournament has `season_phase_id is None` and a **non-empty** `season_phases`. Never
> substitute one for the other.

**NOT changed: `SeasonPhase.tournament`.** It keeps its exact meaning, type, kwargs and
`related_name="season_phases"`. It is **not** widened to an M2M, not deprecated, and **never
written on a regional build**. The two storage paths are mutually exclusive per phase:

| Season shape | `phase.tournament_id` | `phase.regional_tournaments` |
| --- | --- | --- |
| 0 or 1 Conference, tournament phase built | the one Tournament's id | empty |
| `>= 2` Conferences, tournament phase built | **stays NULL** | N rows, one per Conference |
| any shape, not yet built | NULL | empty |

So a **non-empty `regional_tournaments` *is* the "this phase went regional" discriminator**.

**Conference-scoped seeding — additive `conference=None` parameters, not sibling methods.**
`Season._final_standings_for_phase(self, phase, conference=None)` and
`Season._seed_order_for_phase(self, phase, conference=None)` each gained one keyword parameter.
With `conference is None` both are **byte-identical to before** — not one character of the
resulting query changes — so every existing call site keeps working unedited. With a Conference:
`_final_standings_for_phase` takes `team_ids` from `conference.starting_team_ids_json` and adds
`conference=conference` to the Match `filter(...)` (the member-night `.exclude` and the
`compute_standings(completed_matches, enrolled_teams)` call downstream are untouched);
`_seed_order_for_phase` sources its `team_ids` from the Conference snapshot for the `strength`
and `unseeded` branches and passes `conference=` through on the `standings` branch. A parameter
rather than a `_regional_seed_order_for_phase` twin, deliberately — a second method would
duplicate all three mode branches and give the two paths room to drift. **All three
`tournament_mode` values split** in a `>= 2`-Conference Season (`standings` from that
Conference's Standings, `strength` by that Conference's mean ratings, `unseeded` shuffling that
Conference's teams) — there is no cross-Conference fixture of any kind before Worlds, so the
intra-Conference invariant needs no exception clause.

**NEW `Season._build_tournament_for_phase(self, phase, conference=None) -> Tournament | None`**
(declared after `activate_pending_tournament_phase`, before `_seed_order_for_phase`) — the
extraction of the old inline build body, parameterised by Conference, building **exactly one**
bracket. `tournament_cut` applies **per Conference** (a cut of 4 in a 2-Conference Season yields
two 4-team brackets, not one); an **empty seed order returns `None` and creates no row at all**;
seeds restart at **1** in each bracket; every `Tournament` field is copied off the phase exactly
as before, plus `season_phase=phase` + `conference=conference` **only** when scoped. Names are
`f"{season.name} — {conference.name} Playoffs"` / `" … Tournament"` (em-dash U+2014, matching
`Conference.__str__`), and **byte-identical to before** when unscoped. The helper does **not**
write `phase.tournament` — wiring the single-bracket embed pointer stays the caller's job, which
is what keeps the two storage paths from both firing.

**CHANGED `Season.activate_pending_tournament_phase()` — branches on Conference count**
(signature and every pre-existing gate unchanged: the `current_phase()` lookup, the phase-type
guard, the `phase.tournament_id is not None` early return, `_preceding_phase`, and the
`standings`-needs-a-complete-prior mode gate). After those gates: `len(self.ordered_conferences())
>= 2` takes the regional path — return early if `phase.regional_tournaments.exists()` (the
idempotence guard, deliberately **inside** the `>= 2` branch so the 0/1-Conference path runs the
same query count it always did), then `_build_tournament_for_phase(phase, conference=c)` per
Conference. Otherwise the single Season-wide build, followed by
`phase.save(update_fields=["tournament"])` as before. The method is already
`@transaction.atomic`, so the N-build loop is **all-or-nothing** and a partial failure retries
cleanly. A Conference with an empty seed order simply contributes no bracket. **The three
activation call sites (`Season.start_season`, `simulation/entrypoints.py` ×2) are unchanged** —
the generalisation is entirely inside the method.

**NEW `Season.tournaments_for_phase(self, phase) -> list[Tournament]` — the one caller seam.**
**Public**, because `tasks.py`, `league_play_services.py` and `league_screen_services.py` all call it and
**nobody else re-implements the "regional else single" fallback**. Returns `[]` for a `pk is
None` (implicit fallback) phase or a non-`tournament` phase; else the regional rows
`select_related("conference").order_by("conference__ordinal", "id")` if any (so the N brackets
always build, drain and render in declared Conference order, with `id` as the deterministic
tiebreak); else `[phase.tournament]` when the embed pointer is set. A plain `list`, not a
queryset, so callers can iterate repeatedly without re-querying.

**NEW `Season._tournament_phase_complete(self, phase) -> bool` — the completion gate** (beside
its siblings `_rr_phase_complete` / `_member_night_phase_complete`, whose naming it mirrors):
`bool(tournaments) and all(t.state == "completed" for t in tournaments)` over
`tournaments_for_phase(phase)`. **The phase does not advance until every one of its N regional
brackets has drained** — mirroring how an RR phase completes only when every Conference's
round-robin is done. `Season._phase_complete`'s `tournament` arm now just delegates to it, which
reduces to the exact old expression for a 0/1-Conference Season.

**CHANGED `Season._stamp_champion_for_final_phase()` — `champion_team` stays NULL.** Only the
`tournament` arm changed: if `final_phase.regional_tournaments` is non-empty, the Season flips
`state="completed"` and **returns without ever assigning `champion_team`** (guarded by a
defensive `any(t.champion_id is None ...)` mirroring the existing single-bracket guard). **A
Conference champion is not a Season champion** — CONF-01's NULL-champion rule now holds through
the playoff phase as well as the round-robin phase, and only Worlds (CONF-04) crowns a Season
champion. The round-robin / implicit-fallback arm, including CONF-01's
`self.conferences.count() >= 2` block, is untouched, as is `complete_if_finished`.

**Drain — the callers generalise, the engine does not.** The pacing rule follows CONF-01's
parallel overlay: **one stage-step advances every regional bracket by one stage** (California and
Nevada play their semifinals the same week, not one after the other). Three callers, each
wrapping its per-tournament call in a loop over `season.tournaments_for_phase(phase)`:
`tasks.play_playoffs_task` (its guard becomes `not tournaments`; `_stage_counts()` **aggregates**
`stage_progress` element-wise across all N; the node-granular drain loop round-robins across the
tournaments and keeps both PLAY-01 cancel checks and its `{"completed", "total", "cancelled"}`
return shape verbatim), `tasks.play_season_task`'s tournament tail (a local `_drain_one_stage() ->
int` returning `sum(play_next_bracket_round(t) for t in tournaments)` — `sum` over a generator
evaluates **every** term, so do not rewrite it as a short-circuiting `any(...)`; **one budget unit
= one stage across all N brackets**), and `league_play_services.play_week`'s playoff branch (so "Play One
Week" advances every Conference's bracket by one stage). For `N == 1` every one of these returns
exactly what it returned before.

**Playoffs screen (`matches/league_screen_services.py::build_playoffs`, served by
`GET /api/leagues/<id>/playoffs/`).** The `brackets` list appends **one entry per regional
Tournament** instead of one per tournament phase; `build_rounds(tournament, "winners")` is called
per Tournament. Two keys join `phase` / `tournament` / `name` / `rounds` / `champion` / `pending`:
`conference` (the `Conference`, or `None`) and **`key`**, the discriminator that stops the
N regional brackets of one phase colliding on `phase.ordinal` — `str(phase.ordinal)` when
unscoped, `f"{phase.ordinal}-{conference.ordinal}"` when regional. Because
`key == str(phase.ordinal)` whenever there is no Conference, **no existing `key` changes** for a
0/1-Conference Season.

**Team History now counts regional playoffs (`matches/league_screen_services.py`).** This
was a post-approval amendment — the gap was originally scoped out and then pulled in.
`_team_overall_record` identified a season-embedded playoff purely through
`tournament.season_phases`, the reverse manager; a regional Tournament is linked the **other**
way, so without a fix its Rounds would be misfiled as standalone-sandbox play and its brackets
would not count toward `playoff_appearances`. Both queries gained a term keyed on the new forward
FK, **alongside** the untouched `season_phases` term (purely additional, never a replacement):
the Round corpus gains a third `Q(match__series_match__node__tournament__season_phase__isnull=False)`,
and `playoff_appearances` becomes `Q(season_phases__isnull=False) | Q(season_phase__isnull=False)`
passed **positionally before** the `participants__team=team` kwarg. The existing `.distinct()` is
**load-bearing on both** — the added term introduces another to-many join and would otherwise
duplicate rows. **`championships` is deliberately NOT touched**: it counts
`Season.objects.filter(champion_team=team)`, and a Conference champion is not a Season champion.
Standalone sandbox Tournaments still have both `season_phase_id IS NULL` and an empty
`season_phases`, so they stay excluded by both terms.

**Migration `0058_tournament_regional_linkage.py`** (dep `0057_conference_match_conference`):
exactly two `AddField`s. **No `RunPython`, no backfill, no data migration** — both columns are
nullable and NULL is the correct terminal state for every existing row
([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)).

**Explicitly NOT changed.** The bracket engine and `Tournament.lock_and_build` (reused verbatim,
all five formats); `SeasonPhase.tournament`; `Match.conference` **stamping** (CONF-02 only
**reads** the discriminator); `compute_standings`; the simulator; `Season.complete_if_finished` /
`_preceding_phase` / `_rr_phase_complete` / `_member_night_phase_complete` / `scheduled_fixtures*`
/ `ordered_conferences` / `start_season`; the `Conference` model; and — a deliberate call — **the
CONF-01 standings builder** (`build_season_standings`), which keeps its own per-Conference
`standings_groups` block rather than being refactored onto `_final_standings_for_phase`. The two
Conference-scoped queries coexist for now; ADR-0035 records consolidation as a follow-up rather
than widening this slice into the CONF-01 standings surface.

**Locked names (quick index).**
- **`Tournament`** NEW `season_phase` FK (`SET_NULL`, `related_name="regional_tournaments"`) +
  `conference` FK (`SET_NULL`, `related_name="tournaments"`); no `Meta`. Beware
  `season_phase` (forward) vs `season_phases` (reverse) — opposite directions.
- **`Season`** CHANGED `_final_standings_for_phase(phase, conference=None)` /
  `_seed_order_for_phase(phase, conference=None)` (both additive) /
  `activate_pending_tournament_phase()` (branches on `>= 2` Conferences) / `_phase_complete()`
  (tournament arm delegates) / `_stamp_champion_for_final_phase()` (regional arm leaves
  `champion_team` NULL). NEW `_build_tournament_for_phase(phase, conference=None) -> Tournament |
  None`, **public** `tournaments_for_phase(phase) -> list[Tournament]`,
  `_tournament_phase_complete(phase) -> bool`.
- **Drain callers** `tasks.play_playoffs_task` / `tasks.play_season_task` (tournament tail,
  `_drain_one_stage()`) / `league_play_services.play_week` (playoff branch) — all loop over
  `tournaments_for_phase(phase)`; `tournament_engine` itself unchanged.
- **Screens** `league_screen_services.build_playoffs` (new `conference` + `key` bracket keys);
  `league_screen_services._team_overall_record` (the two OR'd queries).
- **Migration** `matches/migrations/0058_tournament_regional_linkage.py` (dep `0057`, two
  `AddField`s, no `RunPython` / backfill).
- **Tests** `matches/tests/test_regional_playoffs.py` (build, per-Conference participants and
  seeds, all three modes splitting, per-Conference cut, idempotence, the completion gate, the
  0-/1-Conference byte-identity pins, the additive-signature pin, and the Team-History class) +
  `matches/tests/test_regional_playoffs_drain.py` (the three callers) +
  `matches/tests/test_api_feats_playoffs.py::RegionalPlayoffsTests` (N labelled brackets).
  House rule: brackets are drained by **driving the engine or stamping node winners**, never by
  running the simulator, and never by patching the seam under test.
- **ADR / CONTEXT** —
  [ADR-0035](../../adr/0035-regional-playoffs-one-tournament-per-conference.md) + the
  GLOSSARY.md **Regional playoff** / **Conference champion** terms. Invariants:
  byte-identical-for-0/1-Conference; **no Score Calibration re-baseline**. **DEFERRED to later
  CONF slices:** Worlds qualification / top-N-per-Conference (CONF-03), the Worlds Tournament and
  the Season champion it finally crowns (CONF-04), per-Conference rotating map pools (CONF-06),
  and any placement / elimination-depth ranking API on `Tournament`.

## CONF-05 manage conferences

The in-app **draft-Season Conference composer** (the user-facing surface CONF-01 deferred,
"Option B") — partitions a draft Season's enrolled Teams into Conferences before Start Season,
replacing the original "create-League composer" framing. **No model change, no migration, no
simulator touch, no Score Calibration re-baseline** — on top of the CONF-01 `Conference` model.

**Surface.** `GET` / `PUT /api/seasons/<id>/conferences/` (`build_conferences_state` /
`ConferencePartitionForm` then `replace_conferences`, all in `matches/league_services.py` /
`matches/forms.py`; see `api.md`), driven by the SPA `ManageConferences` screen
(`frontend/src/app/leagues/seasons/manage-conferences/`). The partition is editable only while
`season.state == "draft"` (membership is snapshotted at Start Season, so it is locked after); a
non-draft PUT is 409 `season_not_draft`. `replace_conferences` **replaces** the Season's
Conferences inside `@transaction.atomic` (delete all + recreate with ordinals `1..N` +
`conf.teams.set(...)`); an **empty list clears** all Conferences (flat Season), and a failed
validation **writes nothing**.

**Pure validator `matches.league_services._validate_conference_partition(names, team_to_conf_idx,
enrolled_team_ids, *, min_per_conference=2) -> tuple[list[str], list[tuple[str, list[int]]] | None]`** — the locked
partition rules: an empty `names` list is the valid **zero-Conference** (flat) case ⇒
`([], [])`; otherwise every name must be non-empty, every enrolled Team must map to a valid
Conference index (**full partition**), and each Conference must have **≥2 Teams**. Returns
`(errors, None)` on failure or `([], [(name, sorted_team_ids), ...])` on success. Locked error
strings: `"Conference names cannot be empty."` / `"Every team must be assigned to a
conference."` / `"Each conference needs at least 2 teams."`.

**Create-flow integration.** `CreateLeagueForm` gains a **`number_of_conferences`** choice
(`None (single league)` / `2` / `3` / `4`, `TypedChoiceField → int`, default `0`); the form's
`clean()` rejects too-few teams (`num_teams >= 2 * N`, error attached to
`number_of_conferences`: `"{n} conferences need at least {2n} teams."`). When `N > 0`,
`league_services.create_league_and_season` (after the existing create + baseline writes)
**pre-creates `N` Conferences** named `"Conference 1".."Conference N"` with the generated Teams
**auto-split evenly** (round-robin `ordered_teams[i::N]`, so each gets `>= 2`), ready to rename /
reassign before Start Season.

**Tests** `matches/tests/test_manage_conferences.py` (the pure validator: zero-Conference,
full-partition happy path, single-Conference, unassigned / `<2`-per-Conference / empty-name /
out-of-range rejections) + `matches/tests/test_api_conferences.py` (the GET / PUT endpoint).
See [ADR-0034](../../adr/0034-conference-partition.md) and the GLOSSARY.md
**Conference** term; PLAN.md **CONF-05**.

## CONF-06 rotate_by_conference per-Conference map pools

A **5th `Season.map_mode` value `rotate_by_conference`** (label `Rotate by Conference`, appended
as the fifth and last `MAP_MODE_CHOICES` tuple): each **Conference** carries its own
author-ordered `ArenaMap` rotation, and a regular-season fixture resolves its map from **its own**
Conference's rotation — "Nevada games on the Nevada map". Unambiguous because regular-season
fixtures are always **intra-Conference**, so `fixture.team_a_id` alone identifies the Conference.
The per-partition analogue of SUB-01's Season-level `rotate_by_matchday`, one level down on the
CONF-01 partition; the epic's closing slice. Fully deterministic, **NO RNG** (the SIM-07 seed
chain is untouched), **no simulator edit** (reuses the SIM-09 `arena_map=` kwarg through the same
three play loops), and **no Score Calibration re-baseline** (see *Scope-out*). `Season.clean()` is
**UNCHANGED** — it derives `valid_map_modes` from `MAP_MODE_CHOICES`, so the new literal validates
automatically.

**Model fields (2 NEW on `Conference`, declared immediately after `starting_team_ids_json`).**
(1) `Conference.map_rotation_ids_json` — the LIVE author-ordered id list, written by
`replace_conferences` (the conferences PUT); (2) `Conference.starting_map_rotation_ids_json` — the **activation
snapshot**, written by `Season.start_season()` and the ONLY list the play loops read. Both
`models.JSONField(null=True, blank=True, default=None)`. `None` is the pre-authoring /
pre-activation sentinel and `[]` is an authored-empty rotation — the two are deliberately
distinct, and every consumer reads `(x or [])`. The order-preserving twin of the SUB-01
Season-level pair: **author order is PRESERVED, NEVER `sorted()`** in either field (the deliberate
divergence from CONF-01's ascending-`id` `starting_team_ids_json`), because the matchday index
keys directly into the ordered list. Migration
`matches/migrations/0061_conference_map_rotation.py` (dep
`0060_alter_seasonphase_tournament_mode`), **3 ops**: 2× `AddField` (`map_rotation_ids_json` then
`starting_map_rotation_ids_json`) followed by the `AlterField` Django emits for the widened
`Season.map_mode` choices — kept in the same file, after the two `AddField`s. A choices-only
`AlterField` is non-schema for a `CharField` on both supported backends, so the migration is
still pure schema — **NO `RunPython` / NO backfill** (ADR-0004:
pre-CONF-06 Conferences take `None`, and `None` never reaches the new branch because the mode
cannot be selected without authoring a rotation).

**`Season.start_season()` extension (2 edits, both inside the existing method).** (a) An
**activation guard** placed after the existing `self.teams.count() < 2` check and before the
`starting_team_ids_json` assignment: under `map_mode == "rotate_by_conference"`, fewer than 2
Conferences **or** any Conference with an empty live rotation raises `ValidationError("A Season
with map mode 'Rotate by Conference' requires at least 2 conferences, each with at least 1
rotation map.")` — one locked string covering both failure shapes. (b) The **snapshot**, written
inside the EXISTING per-Conference loop (not a second loop) as
`conf.starting_map_rotation_ids_json = list(conf.map_rotation_ids_json or [])` with
`"starting_map_rotation_ids_json"` added to that loop's `update_fields`. The snapshot is written
for **every** Season that has Conferences regardless of `map_mode` — an unused snapshot is
harmless and keeps the loop mode-agnostic.

**`_resolve_fixture_map` — 4th argument + NEW branch (`matches/tasks.py`).** The helper grows a
fourth **keyword-with-default** parameter: `_resolve_fixture_map(season, fixture, pool_by_id,
conf_by_team: "dict[int, Conference] | None" = None) -> "ArenaMap | None"` (`Conference` joins the
`TYPE_CHECKING` import). **Backwards compatibility is load-bearing** — all 37 existing
3-argument call sites in `matches/tests/test_season_map_config.py` and `test_league_play.py` keep
working unchanged, and `None` is treated as `{}`. The new branch sits after the
`rotate_by_matchday` branch and before the final `raise`: look up `fixture.team_a_id` in
`conf_by_team`, read `ids = (conf and conf.starting_map_rotation_ids_json) or []`, `return None`
if empty, else `return pool_by_id.get(ids[fixture.matchday % len(ids)])`. Purity is preserved (no
ORM inside the helper) and the branch never constructs a `random.Random`. Every failure shape
returns `None` rather than raising: missing / `None` `conf_by_team`, a `team_a_id` absent from the
map, a `None`/`[]` snapshot, or an `ArenaMap` deleted after activation (`pool_by_id.get` misses).
Unknown modes still `raise ValueError(f"Unknown map_mode: {mode!r}")`.

> **The rotation index is 1-BASED and that is correct-by-contract.** `fixture.matchday` is
> Season-global and 1-based, and `matchday % len(ids)` is applied to that value **directly**,
> byte-mirroring the shipped `rotate_by_matchday` formula. With a 2-map rotation, matchday 1
> resolves `ids[1]` and matchday 2 resolves `ids[0]`. This is **NOT an off-by-one** — do **not**
> "fix" it to `(matchday - 1) % len(ids)`; a pinned test in `test_season_map_config.py` asserts
> the 1-based expectation.

**NEW pure helper `matches.tasks._fixture_map_ids(season, conf_by_team=None) -> list[int]`**
(module-level, immediately after `_resolve_fixture_map`, no ORM) — the union of every `ArenaMap`
id the resolver can reach this Season, and the single argument to the play loops' one
`ArenaMap.objects.in_bulk`. Order-preserving concatenation: `season.starting_map_pool_ids_json`,
then `season.starting_map_rotation_ids_json`, then each **first-seen** Conference's
`starting_map_rotation_ids_json` iterating `conf_by_team.values()` in dict order. Conferences are
deduped by `conf.id` (the same Conference appears once per member team); **map ids are NOT
deduped** — duplicates are harmless for `in_bulk`. With no Conferences it reduces to the old
`(pool or []) + (rotation or [])` expression **exactly**.

**NEW pure helper `matches.forms.parse_rotation_ids(raw: "str | None", valid_map_ids: "set[int]")
-> "tuple[list[int], list[str]]"`** (module-level, below `_maps_with_confirmed_config()`; no ORM,
no form state) — parses a comma-separated `ArenaMap`-id string into an **author-ordered** id list
plus a list of human error strings. Extracted **verbatim** from the old inline
`CreateLeagueForm.clean` token loop and now shared by the create form and
`ConferencePartitionForm`. Locked behaviour: author order, never sorted; **duplicates kept** (`"3,3"` → `[3, 3]`);
blank tokens skipped silently; whitespace stripped (`" 3 , 4 "` → `[3, 4]`); one error string **per
offending token**, not deduped; valid ids from a partly-bad string still returned alongside the
errors; `None` / `""` → `([], [])`. Its two error strings are unchanged from LG-01j/SUB-01:
`"Map rotation contains an invalid id."` (non-integer token) and `"Map rotation contains an unknown
map id."` (id not in `_maps_with_confirmed_config()`). In `CreateLeagueForm.clean` the refactor is
behaviour-preserving — `cleaned_data["map_rotation_ids"]` keeps its exact current name and shape.

**Four guards** enforce the mode's shape — **≥2 Conferences** (Conferences are 0 or ≥2 by the
CONF-01 partition rule, so "≥2" is the only meaningful floor) each with **≥1 rotation map**:

| # | Where | Verbatim string(s) |
|---|---|---|
| 1 | `CreateLeagueForm.clean`, a `rotate_by_conference` block after the `rotate_by_matchday` one, using `add_error` (NOT `raise`) so all three surface in one submission | `Map pool must be empty when Map mode is 'Rotate by Conference'.` (field `map_pool`) / `Map rotation must be empty when Map mode is 'Rotate by Conference'.` (field `map_rotation`) / `Map mode 'Rotate by Conference' requires at least 2 conferences.` (field `number_of_conferences`) |
| 2 | `ConferencePartitionForm.clean_conferences`, appended **once** (not per Conference) and only when `season.map_mode == "rotate_by_conference"`, the list is non-empty and any parsed row is empty | `Each conference needs at least 1 rotation map.` (a `conferences` field error) |
| 3 | `Season.start_season()` activation guard | `A Season with map mode 'Rotate by Conference' requires at least 2 conferences, each with at least 1 rotation map.` |
| 4 | `run_season_rollover` — the downgrade below | (no message; silent mode change) |

**Three play-loop call sites**, identical shape, each reordered so `conf_by_team` is built
**above** the `pool_by_id` assignment (all three previously built it *after* the `in_bulk`):
**build `conf_by_team = season.conference_by_team_id()` → `_fixture_map_ids(season, conf_by_team)`
→ `ArenaMap.objects.in_bulk(...)` → per-fixture `_resolve_fixture_map(..., conf_by_team=conf_by_team)`**.
The sites are `matches.tasks.play_season_task`, `matches.league_play_services.play_week`, and
`matches.league_play_services._commit_live_rr_round` (the manager live-game RR commit); the latter
two use a deferred `from matches.tasks import _fixture_map_ids, _resolve_fixture_map`.
Nothing else at these sites changes — for every pre-existing Season (`map_mode !=
"rotate_by_conference"`) both the `in_bulk` argument and the resolved map are byte-identical to
before.

**`league_services._build_map_config_label` 6th branch — the dashboard map label.** The LG-01j /
SUB-01 label ladder gains a `rotate_by_conference` arm; without it the mode fell
through to the ladder's defensive "unknown enum value" return and both dashboards
reported **`Map: 3-zone fallback (no map)`** for a Season that was in fact running
per-Conference rotations — the label actively lied about the configuration. (Caught
in the CONF-06 browser pass, not by the seam contract, which never named this
helper.) The arm mirrors the `rotate_by_matchday` one but per Conference: same
snapshot-vs-live rule (`starting_map_rotation_ids_json` for active / completed, the
live `map_rotation_ids_json` for draft), Conferences in `ordinal` order, map names in
**AUTHOR order** (NOT name- or id-sorted). Conferences with an empty rotation
contribute no part, so the count reflects only those that do. **ONE `ArenaMap` query
for the whole label** — the ids are collected across every Conference first and
resolved in a single `filter(id__in=...)`, never one query per Conference. Two NEW
byte-equal label strings: `"Map: Rotating per Conference ({n} conferences: {conf}:
{maps}; …)"` and `"Map: Rotating per Conference (no maps)"`.

**Rollover downgrade (`matches.league_services.run_season_rollover`).** The
`Season.objects.create(...)` call takes `map_mode=("none" if latest_completed.map_mode ==
"rotate_by_conference" else latest_completed.map_mode)`. **The why:** the rollover carries no
Conferences forward (and CONF-06 deliberately does not start doing so), so a carried
`rotate_by_conference` would meet an empty `conf_by_team`, resolve `None` for **every** fixture
all season, and silently produce a map-less Season that still claimed a map mode. Downgrading to
the 3-zone fallback makes that honest. Every other mode still carries **verbatim**, and the
Season-level `map_rotation_ids_json` carry-forward is untouched.

**Authoring — the CONF-05 conferences surface.** The rotation rides the Conference partition
(each `map_rotation_ids` entry of the conferences PUT) rather than a new screen, so it is editable
**only while the Season is `draft`**. `ConferencePartitionForm.clean_conferences` runs the
partition validator, then `parse_rotation_ids` per row **for every map mode** (a malformed id is
invalid regardless of mode), appending its errors in submitted Conference order, then the
once-only empty-rotation guard; on failure nothing is written. On success `replace_conferences`
stores each parsed list verbatim on `map_rotation_ids_json` (`[]` for an authored-empty rotation,
never `None` once saved). `build_conferences_state` returns each Conference's live
`map_rotation_ids` and its `starting_map_rotation_ids` snapshot.

**Scope-out (locked).** **Per-fixture map override DEFERRED** — a `ScheduleFixture` is a plain
dataclass regenerated on demand by `generate_schedule`, not a persisted row, so there is nowhere to
hang an override; reopening it needs its own model keyed on synthetic fixture identity plus an
authoring surface (PLAN.md **Deferred Items**). **Bracket / tournament maps out of scope by
construction, not by omission** — Regional-playoff and Worlds `Match` rows never carry an
`arena_map` at all and always run the 3-zone fallback, so `rotate_by_conference` governs
**regular-season fixtures only**. **NO Score Calibration re-baseline** — the new branch is
unreachable without the new mode and `_fixture_map_ids` reduces to the current expression when
there are no Conferences, so every existing seeded outcome stays byte-identical and no
fixture/expected-score file is regenerated. **NO `SeasonAdmin` / Django-admin surface** for the
per-Conference rotation. **NO carrying of Conferences (or their rotations) through `next_season`**
— the downgrade is the whole of the rollover story. **NO change to
`_validate_conference_partition`'s signature or strings** (`"Conference names cannot be empty."` /
`"Every team must be assigned to a conference."` / `"Each conference needs at least 2 teams."`
stand). **NO new ADR** (reversible model fields + a deterministic helper branch — SUB-01's no-ADR
rationale) and **NO `RunPython` / backfill** (ADR-0004).

**Locked label names.** EXTENDED helper
`matches.league_services._build_map_config_label` (6th branch, single `ArenaMap` query);
label string literals (2 NEW, byte-equal) `"Map: Rotating per Conference ({n}
conferences: …)"` / `"Map: Rotating per Conference (no maps)"`.

**Tests.** `matches/tests/test_season_map_config.py` (pure, no DB:
the `rotate_by_conference` branch against stub `season` / `fixture` / `Conference` objects incl.
the 1-based-modulo pin and every defensive-`None` path; `_fixture_map_ids` order, Conference
dedupe by `conf.id`, and no-Conference equivalence to the old expression; `parse_rotation_ids`
across all six locked properties and both error strings); `test_conference.py` (DB: author order
survives `start_season()` via a deliberately non-ascending list, `[]` snapshot from a `None`/`[]`
live list, the activation `ValidationError` for 0 Conferences and for any empty rotation, and its
silence under other modes); `test_api_conferences.py` (the PUT saves per-Conference ids, the
rotation errors and the rotate-by-conference guard); `test_league_next_season.py` (the
downgrade, other modes verbatim); and
`test_league_create.py` (the three `CreateLeagueForm` guards under their exact field keys plus a
valid `rotate_by_conference` submission). Plus the regression guard: an existing
`rotate_by_matchday` / `random_per_round` / `single` / `none` Season resolves byte-identical maps
with and without a `conf_by_team` argument. Seam contract:
[`.claude/worktrees/conf-06-seam-contract.md`](../../../.claude/worktrees/conf-06-seam-contract.md);
GLOSSARY.md **Map mode** / **Map pool** / **Per-fixture map resolution** / **Conference** terms;
PLAN.md **CONF-06**.
