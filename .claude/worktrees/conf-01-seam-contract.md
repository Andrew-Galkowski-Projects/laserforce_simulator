# CONF-01 seam contract — Conference foundation (SUB-01 piece 3, slice 1)

The **single source of truth** for every name / signature / field / DOM id the
Code, Tests, and Docs agents share. Decisions locked at the CONF-01 grill
(2026-06-29); rationale in
[ADR-0034](../../docs/adr/0034-conference-partition.md); domain language in the
CONTEXT.md **Conference** term (already written).

## Scope (this slice ONLY)

Ship: the `Conference` partition model + intra-Conference round-robin scheduling +
per-Conference Standings. Conferences are **admin-created** (Part2a precedent —
composer deferred). A **zero-Conference Season is byte-identical to today**.

Deferred (later CONF slices, do NOT build): per-Conference regional playoffs;
top-N-per-Conference Worlds qualification + the Worlds Tournament; the create-League
Conference composer UI; per-Conference rotating map pools.

**No Score Calibration re-baseline** — no simulation mechanic changes (only which
`Match` a Round attaches to + a new discriminator FK). Tournament/playoff paths
untouched.

## Model — `matches/models.py`

### NEW `Conference` (declared after `Season`, before `SeasonPhase`)

```python
class Conference(models.Model):
    season = models.ForeignKey(
        "matches.Season", on_delete=models.CASCADE, related_name="conferences"
    )
    name = models.CharField(max_length=100)
    ordinal = models.PositiveSmallIntegerField()          # 1-based display order
    teams = models.ManyToManyField(
        "teams.Team", related_name="conferences"
    )
    # Activation snapshot of this Conference's team ids (sorted asc), mirroring
    # Season.starting_team_ids_json. None pre-activation; written by start_season().
    starting_team_ids_json = models.JSONField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "ordinal"],
                name="uniq_season_conference_ordinal",
            )
        ]

    def __str__(self) -> str:
        return f"{self.season} — {self.name}"   # em-dash U+2014
```

### CHANGED `Match` — add discriminator FK (after `Match.leg`)

```python
conference = models.ForeignKey(
    "matches.Conference",
    null=True, blank=True, on_delete=models.SET_NULL,
    related_name="matches",
)
```

### NEW `Season` methods

- `ordered_conferences(self) -> list["Conference"]` — `list(self.conferences.all())`
  (Meta.ordering guarantees ordinal order). Empty list when none.
- `_scheduled_conference_partitions(self) -> list[tuple["Conference | None", list[int]]]`
  — the per-Conference team-id partitions for scheduling. Rules:
  - **Zero Conferences** ⇒ `[(None, self._scheduled_team_ids())]` (one implicit
    all-Teams partition — byte-identical to today).
  - **>= 1 Conference** ⇒ one `(conf, ids)` per `ordered_conferences()`, where `ids`
    is: **draft** Season → `sorted(t.id for t in conf.teams.all())` intersected with
    `_scheduled_team_ids()`; **active/completed** → `list(conf.starting_team_ids_json or [])`.
    (Mirrors `_scheduled_team_ids` draft-vs-snapshot rule, per-Conference.)
- `conference_by_team_id(self) -> dict[int, "Conference"]` — `{team_id: Conference}`
  for every team in every Conference (draft live M2M / active snapshot, matching
  `_scheduled_conference_partitions`). Empty dict for a zero-Conference Season. Used
  by the play loop to stamp `Match.conference`.

### CHANGED `Season.scheduled_fixtures_by_phase()` — internal only, **return shape UNCHANGED** (`list[tuple[SeasonPhase, list[ScheduleFixture]]]`)

Per `round_robin` phase, generate **one round-robin per partition** from
`_scheduled_conference_partitions()` and overlay on the shared matchday calendar:

- For each `(conf, conf_ids)` with `len(conf_ids) >= 2`:
  `base = generate_schedule(conf_ids, phase.schedule_format or self.schedule_format)`;
  emit `ScheduleFixture(matchday=f.matchday + offset, round_number=f.round_number,
  team_a_id=f.team_a_id, team_b_id=f.team_b_id, leg=f.leg)` for each `f in base`.
- The phase's fixtures = the **concatenation** of all partitions' offset fixtures.
- The phase **span** (added to `offset` for the next phase) = the **max** over
  partitions of `max(f.matchday for f in base)` — the *parallel-overlay* rule
  (Conferences share matchday numbers; phase span = largest Conference's span). A
  partition with `< 2` teams contributes nothing and does not raise.
- A phase whose fixtures are all empty is skipped (existing behaviour).

Zero-Conference Season: one partition `(None, all ids)` ⇒ byte-identical output.

`scheduled_fixtures()`, `_fixtures_for_phase()`, `_rr_phase_complete()`,
`_is_finished()` are **UNCHANGED** — they consume the per-phase union, so an RR
phase completes only when every Conference's round-robin is played.

### CHANGED `Season.start_season()` — append Conference snapshot

After the existing `starting_team_ids_json` / `starting_map_*` snapshots and BEFORE
`self.save()` (or right after — must persist), snapshot each Conference:
`for conf in self.conferences.all(): conf.starting_team_ids_json =
sorted(t.id for t in conf.teams.all()); conf.save(update_fields=["starting_team_ids_json"])`.
Stays inside the existing `@transaction.atomic`.

### CHANGED `Season._stamp_champion_for_final_phase(self, final_phase)` — multi-Conference NULL champion

In the round-robin / implicit-fallback branch (the `else` after the `tournament`
branch), **before** computing `compute_standings`: if `self.conferences.count() >= 2`,
**return without stamping** (leave `champion_team` NULL, but the Season still
completes — set `state="completed"` + save, just no champion). Concretely: a
`>= 2`-Conference Season flips `state="completed"` with `champion_team` left NULL;
a 0/1-Conference Season is unchanged (`champion_team = compute_standings(...)[0]`).
The `tournament` branch is untouched.

> Implementation note: ensure the Season still transitions to `completed` for a
> multi-Conference RR-final Season (so the cursor / dashboard read "completed") —
> only the champion stamp is skipped. Confirm against `complete_if_finished`'s
> existing flow so the state flip is not lost.

## Simulator — `matches/simulation/entrypoints.py`

### CHANGED `BatchSimulator.simulate_scheduled_round` — keyword-only `conference`

```python
def simulate_scheduled_round(
    self, season, team_a, team_b, round_number, *,
    arena_map=None, season_phase=None, leg: int = 1,
    conference=None,                      # NEW — keyword-only, after leg, before fidelity
    fidelity: str = "scores",
) -> "GameRound":
```

`conference` defaults `None` ⇒ byte-identical to every existing caller. The
find-or-create **key is UNCHANGED** (`season`, `season_phase`, `leg`, frozenset
teams). On the **Round-1 `Match.objects.create(...)`** site only, pass
`conference=conference`. Round 2 finds the existing Match and does NOT re-stamp.
(Defensive: a `conference` instance with `pk is None` should coerce to None like
the `season_phase` guard does — though Conferences are always persisted here.)

## Play loop — stamp `Match.conference`

Three sites build `conf_by_team = season.conference_by_team_id()` once (outside the
per-fixture loop) and pass `conference=conf_by_team.get(fixture.team_a_id)` (==
`team_b_id`'s Conference — intra-Conference) into `simulate_scheduled_round`:

- `matches/tasks.py::play_season_task` — the per-fixture loop.
- `matches/league_views.py::play_week` — the per-fixture loop.
- `matches/league_views.py::play_week_live` — the **RR branch** `simulate_scheduled_round`
  call only (the playoff branch uses `play_specific_node` → untouched).

`play_two_months` / `play_until_end` enqueue `play_season_task` → carried through
unchanged. Zero-Conference Season ⇒ `conf_by_team` empty ⇒ `conference=None` ⇒
byte-identical.

## Standings view + template

### CHANGED `matches/league_views.py::season_standings`

Render **one table per Conference**. For a Season with `>= 1` Conference: per
Conference, `completed_matches` from
`Match.objects.filter(conference=conf, is_completed=True)` (exclude member-night as
the existing query does), `enrolled_teams` from that Conference's
`starting_team_ids_json` (active/completed) or live M2M (draft preview), then
`compute_standings(...)`. Context: a list of `(conference, rows_with_teams)` groups
(plus the draft-preview path per Conference). **Zero Conferences** ⇒ the existing
single-group behaviour unchanged (one table over all season matches).

Context-key shape (Code agent picks exact names, but pin these): a
`standings_groups` list of `{"conference": Conference | None, "name": str | None,
"rows_with_teams": [...]}`; the template iterates it. The existing top-level
`season`, `is_draft_preview` keys stay.

### CHANGED `templates/seasons/standings.html`

Iterate `standings_groups`, rendering one table per group with a name header.
**Preserve** the existing DOM ids for the zero-Conference single-table case so
existing tests stay green: `season-standings-table` (the table — present for the
single zero-Conference group), `season-standings-empty`, `season-draft-preview-banner`,
`season-state-badge`. **New** per-Conference DOM ids (only when Conferences exist):
`season-standings-conference-{conference_id}` (wrapper around that Conference's
table) and `season-standings-conference-name-{conference_id}` (its name header).

> Zero-Conference rule (LOCKED): a Season with no Conference rows renders exactly
> one table with id `season-standings-table` and no `season-standings-conference-*`
> ids — byte-identical to today.

### CHANGED `matches/league_views.py::_build_dashboard_context`

The top-3 standings snippet: when the displayed Season has `>= 2` Conferences,
scope `compute_standings` to the **manager's** Conference — the Conference
containing `displayed_season.league.current_team` (resolve via the team's
membership; fall back to the first Conference, else empty). Otherwise (0/1
Conference) unchanged (overall top-3). Keep this minimal — only the snippet's match
corpus + enrolled-teams set change.

## Admin — `matches/admin.py`

`@admin.register(Conference)` `ConferenceAdmin(filter_horizontal=("teams",),
list_display=("season", "ordinal", "name"))`. No existing registration touched.

## Migration

`matches/migrations/0057_conference_match_conference.py`, dep
`("matches", "0056_season_play_job_cancel")` (+ the latest `teams` migration —
`makemigrations` resolves the M2M / FK cross-app dep). Operations in order:
`CreateModel(Conference)` (with the M2M, `starting_team_ids_json`, Meta ordering +
`uniq_season_conference_ordinal`) → `AddField(Match.conference)`. **No `RunPython`,
no `RunSQL`, no backfill.**

## Tests — `matches/tests/`

NEW `test_conference.py` (Django `TestCase`): model fields / defaults / `__str__` /
`uniq_season_conference_ordinal` / `Meta.ordering` / CASCADE-on-Season-delete; the
`Season` helpers (`ordered_conferences`, `_scheduled_conference_partitions`
draft-vs-snapshot + zero-Conference fallback, `conference_by_team_id`); the
**parallel-overlay scheduling** (a 2-Conference RR phase yields each Conference's
RR on the SAME matchday numbers; phase span = max Conference span; zero-Conference
byte-identical to a flat Season's `scheduled_fixtures`); `start_season` snapshots
each Conference; per-Conference completion (RR phase completes only when BOTH
Conferences' RRs are played); `_stamp_champion_for_final_phase` leaves NULL for
`>= 2` Conferences but still flips `state="completed"`, and crowns
`compute_standings[0]` for 0/1 Conference.

EXTEND (existing files, do not rewrite): `test_simulation_view_paths.py` (or
`test_league_simulator.py`) — `simulate_scheduled_round(conference=...)` stamps
`Match.conference` on Round 1, default `None` byte-identical; `test_league_play.py`
— `play_season_task` stamps each Match's `conference` from `conference_by_team_id`;
the season-standings view test file (`test_season_views.py`) — per-Conference
tables render with the new DOM ids, zero-Conference renders the single
`season-standings-table` unchanged.

**Assertion discipline:** schema-level outcomes only — fixture lists (counts /
matchday spans / which teams pair), `Match.conference` values, completion flags,
`champion_team` null-vs-id, standings ORDER, DOM ids. **Never** raw simulated point
totals. Use small N (N=2/3 per Conference) seeded sims.

## Locked names (quick index)

- Model `matches.models.Conference` (`season` FK CASCADE `related_name="conferences"`,
  `name`, `ordinal` PositiveSmallInt, `teams` M2M `related_name="conferences"`,
  `starting_team_ids_json` JSON); constraint `uniq_season_conference_ordinal`;
  `Meta.ordering=["ordinal"]`.
- `Match.conference` FK (`SET_NULL`, null/blank, `related_name="matches"`).
- `Season.ordered_conferences()` / `_scheduled_conference_partitions()` /
  `conference_by_team_id()`; CHANGED `scheduled_fixtures_by_phase()` (parallel
  overlay) / `start_season()` (snapshot) / `_stamp_champion_for_final_phase()`
  (NULL for `>= 2` Conferences).
- `BatchSimulator.simulate_scheduled_round(..., *, conference=None, ...)` (keyword-only,
  after `leg`, before `fidelity`; stamp on Round-1 create; key unchanged).
- Play sites `tasks.play_season_task` / `league_views.play_week` /
  `league_views.play_week_live` (RR branch) — `conf_by_team` + `conference=`.
- View `league_views.season_standings` (per-Conference groups) /
  `_build_dashboard_context` (manager-Conference snippet for `>= 2`); template
  `templates/seasons/standings.html` (DOM ids `season-standings-table` preserved;
  new `season-standings-conference-{id}` / `-conference-name-{id}`).
- Admin `matches.admin.ConferenceAdmin`.
- Migration `matches/migrations/0057_conference_match_conference.py` (dep `0056`,
  CreateModel → AddField, no `RunPython`).
- ADR-0034; CONTEXT.md **Conference** term (already written).
- Tests `matches/tests/test_conference.py` (NEW) + extensions.
