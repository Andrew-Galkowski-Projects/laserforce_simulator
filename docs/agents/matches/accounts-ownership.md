# matches: Accounts and ownership in matches

UX-01 `manager` ownership roots in this app, UX-02 Self-insert badges.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## UX-01 accounts + the `manager` ownership FK

**UX-01** gives every row an owner. The `matches` app carries **four of the five Ownership roots**
(`teams.Team` is the fifth), the dormant `League.visibility` column, and 72 of the 83 converted
permission call sites. The **Account** model, the permission seam and the auth surfaces live in the
new fourth app — see [`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md) and
[ADR-0038](../../adr/0038-accounts-and-uniform-manager-ownership.md).

> ### ⚠️ The **Manager** is not the **Owner**
>
> `manager` (the new Account FK) and `OwnerEvaluation` / `owner_mood.py` / **Owner mood** / **Owner
> evaluation** (the ADR-0026 career fiction) are **unrelated concepts that happen to sit in the same
> app**. The **Owner** is the fictional boss who judges and fires the **Manager**; it is **never a
> login** and was **deliberately not renamed** — churning ADR-0026, the `OwnerEvaluation` model,
> `owner_mood.py`, GLOSSARY.md and the career-screen DOM ids to free up the word "owner" was rejected
> as buying a word we did not need. So: nothing containing `owner` / `Owner` is renamed;
> `OwnerEvaluation` gets **no** `manager` column (it derives its Manager via `league`); and the word
> "owner" is never used for an Account in code, comments, templates, DOM ids or docs.

**Four roots in this app, under one rule: *a row is a root exactly when its parent FK is null*.**
Each carries the byte-identical field
`ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
related_name=...)`, appended as the last field of its model:

| Model | `related_name` | Root when | Otherwise derives via |
|---|---|---|---|
| `League` | `leagues` | **always** (no parent FK) | — |
| `Tournament` | `tournaments` | **always** — even when `season_phase` is set | — |
| `Match` | `matches` | `season_id IS NULL` (a **sandbox** Match) | `season → league` |
| `GameRound` | `game_rounds` | `match_id IS NULL` (a **standalone** Round) | `match` (→ `season → league`) |

Everything else in the app is a **derived row** with no `manager` column — `Season`, `Conference`,
`SeasonPhase`, `PlayerSeasonRating`, `OwnerEvaluation`, `TeamSeasonFinance` (→ League);
`PlayerRoundState`, `GameEvent` (→ GameRound); `TournamentParticipant`, `BracketNode`,
`TournamentPlayerEntry`, `SeriesMatch` (→ Tournament). Two traps in that table:
`TeamSeasonFinance.team` and `OwnerEvaluation.team_managed` are `SET_NULL` and are **not** the
ownership parent (`season` / `league` is), and `PlayerSeasonRating.player` is **not** the parent
either (`season` is — a rating is League data). `matches.standings.StandingsRow` is a **frozen
dataclass, not a model**: never persisted, no `manager`, no ownership axis.

**`Tournament` stays its own root even when embedded.** A CONF-02 regional or CONF-04 Worlds bracket
has a non-null `season_phase`, but that FK is **not** treated as an ownership parent — otherwise the
sandbox Tournament and the embedded Tournament would need two different resolution rules. The
invariant *an embedded Tournament's Manager equals its League's Manager* is instead held by
**propagation at creation**: `Season._build_tournament_for_phase`,
`Season._build_last_chance_tournament` and `Season.build_pending_worlds_bracket` each pass
`manager=self.league.manager` (`self` is a `Season`, and `Season.league` is non-null). Because of
that, the flat predicate and a hypothetical traversal would agree — the flat one is simply cheaper.

**`SET_NULL`, and what NULL means.** Deleting an Account in Admin **demotes** its rows to **Unmanaged**
(`manager IS NULL`) rather than cascading a League's whole Season / Match / GameEvent history out of
existence — the same posture as `League.current_team`, `Tournament.champion`, `Match.season` and the
`*.winner` FKs. An **Unmanaged row is readable AND writable by any authenticated Account, and listed
to all of them** — NULL means *open*, not *frozen* and not *hidden*. That is what keeps request-less
writers working: `BatchSimulator.simulate_match` / `simulate_scheduled_round` (worker processes) and
`persistence.flush_to_db` create Matches and GameRounds with **no `manager`** by design, and
`manager` is deliberately **not** threaded through `matches/simulation/*`. Note also that
`Match.season` is `SET_NULL`, so deleting a Season silently demotes its Matches to Unmanaged sandbox
roots — accepted behaviour, not a bug.

**The permission seam in this app.** Every read path goes through `accounts.permissions`:

- **72 `get_object_or_404` → `get_owned_or_404(Model, request, **lookup)`** conversions —
  `league_views.py` ×28, `league_screens/*.py` ×17 across 15 modules, `views.py` ×14,
  `tournament_views.py` ×13 — plus two adjacent `.objects.get(` sites converted for the same reason
  (`views.py::compare_rounds`, `league_views.py::reassign_team`). `request` is inserted as the
  **second positional** argument; everything else on the line is unchanged. Cross-Account access
  raises **`Http404`, never 403**, so another Account's row is indistinguishable from a nonexistent
  one.
- **`api_views.py:SimulateBatchAPIView.post`'s `Team.objects.get(id=tid)` is deliberately left
  alone** — it is wrapped in `try/except Team.DoesNotExist → 400` and must keep returning **400, not
  404**. The viewset-level `IsAuthenticated` is the gate there. (ANG-01a: the 400 is now a raised
  `ValidationError` in the envelope; the lookup is still not ownership-scoped — ANG-01b follow-up.)
- **List scoping:** `league_list` wraps both its `state="active"` / `"archived"` querysets in
  `owned_queryset`; `tournament_list` likewise. `match_list` uses the two longhand `Q` builders —
  `Match.objects.filter(owned_match_q(request.user))` and, for the standalone-round list,
  `GameRound.objects.filter(match__isnull=True).filter(owned_game_round_q(request.user))` — because
  those two roots are **conditional** and `owned_queryset` is not valid for them.
- **DRF:** `MatchViewSet` / `GameRoundViewSet` gain `@method_decorator(login_not_required,
  name="dispatch")` and a manager-scoped `get_queryset()` built on the same two `Q` builders, while
  **keeping** their class-level `queryset` attribute (the router needs it for basename
  introspection). `GameRoundViewSet` keeps its `self.action == "retrieve"` prefetch branch and its
  `@action(url_path="events")` method unchanged; only the base queryset is wrapped. Router
  registration and every API URL name are untouched. `SimulateBatchAPIView` /
  `SimulateBatchStatusAPIView` take the same decorator. The middleware exemption is what makes DRF's
  `IsAuthenticated` return a **401 JSON** body (403 before ANG-03) instead of an HTML **302** to the login page.

**Stamping sites in this app (8 of the 13).** `_create_league_and_season` gained a keyword-only
`manager: AbstractBaseUser | None = None` parameter (appended last; the helper takes no `request`, so
the parameter *is* how the Account reaches it) and stamps both the `League` and the League's
free-agent pool `Team`; `member_night_setup` stamps its two `is_draw_team=True` Teams;
`tournament_create` stamps the `Tournament` (and passes `manager=` into `_generate_teams`);
`tournament_draw` stamps its drawn Team; `views.create_match` and `views.create_single_round` call
`stamp_manager(obj, request.user)` **post-hoc** on the `Match` / `GameRound` returned by the
simulator, because those views never construct the row themselves; and the three `Season._build_*`
sites propagate the League's Manager as above. Creation sites that intentionally stay Unmanaged:
every `Season`, `SeasonPhase`, `Conference` and `SeriesMatch` (all derived rows),
`member_night_setup`'s `Match` (it has `season=season`, so it is derived, not a root), and everything
`matches/simulation/*` writes.

**`League.visibility` — a dormant column (3 touch points, and no more).** `CharField(max_length=16,
choices=VISIBILITY_CHOICES, default="closed")` where `VISIBILITY_CHOICES = (("closed", "Closed"),
("open", "Open"))` is a class-level constant on `League` alongside the existing `MODE_CHOICES` /
`STATE_CHOICES`; a `required=False` `CreateLeagueForm.visibility` `ChoiceField` (widget id
`league-create-visibility`, declared last in the class body) rendered on
`templates/leagues/create_advanced.html` **only** (not `create.html`), between the
`league-create-number-of-conferences` and `league-create-schedule-format` blocks; and
`_create_league_and_season` passing `visibility=cleaned.get("visibility") or "closed"` (with
`_template_to_form_data` supplying `"visibility": "closed"`). **Nothing reads it this slice** — no
branch, no filter, no context key, no sidebar entry, no admin column; outside the model, the
migration, the form field, the template block and the tests the string `visibility` occurs **zero**
times. It is the forward-compatible marker for who may join a League once **League membership**
exists, which — along with invitations and joining — is **deferred** (PLAN.md **Deferred Items**).
The precedent is LG-02-Part2b's `schedule_format` / LG-02-Part2c-3b's `SeasonPhase.tournament_mode`
dormant column.

**Migration.** `matches/migrations/0062_manager_ownership_and_league_visibility.py` (deps
`migrations.swappable_dependency(settings.AUTH_USER_MODEL)` + `0061_conference_map_rotation`) — **one**
file, five `AddField`s in order: `league.manager`, `tournament.manager`, `match.manager`,
`gameround.manager`, `league.visibility`. **NO `RunPython` / NO backfill**
([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)): existing rows stay
`manager=NULL`, i.e. Unmanaged, until `manage.py claim_unmanaged --user <email>`. ADR-0038 rejects a
superuser backfill as *vacuous* — a custom user model means an empty user table on every existing
database, so it would stamp nothing. ⚠️ The `AUTH_USER_MODEL` swap itself raises
`InconsistentMigrationHistory` on any **pre-UX-01 database** while leaving **CI and the test suite
green** (a fresh test DB each run); the recovery is a fresh database, not a data migration — see
`README.md` and [`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md).

**Determinism / scope-out.** No RNG, no ordering, no parallelism and no simulator edit —
`matches/simulation/*`, `tournament_engine.py`, `owner_mood.py` and `standings.py` are **untouched** —
so there is **no Score Calibration re-baseline** and every seeded outcome stays byte-identical; no
serial-vs-parallel determinism test was required. Also out of scope: League membership / invitations /
joining / any *read* of `visibility`, cross-Account sharing, per-object permissions or Groups, and
renaming anything Owner-flavoured. Tests: new `matches/tests/test_ownership.py`, plus the
`league-create-visibility` id added to `test_league_create.py`'s locked-id tuple, the topnav auth ids
added to `test_topnav.py`, and `force_login` calls added to the locally-instantiated clients in
`views_tests.py` (42), `test_batch_views.py` (15), `test_heatmap.py` (10), `test_missile_log.py` (4),
`test_playback_map.py` (2) and `test_apis.py` (the `setUp` `APIClient`) — the root `conftest.py`
autouse login fixture cannot reach those. Seam contract:
[`.claude/worktrees/ux-01-seam-contract.md`](../../../.claude/worktrees/ux-01-seam-contract.md);
GLOSSARY.md **Account** / **Ownership root** / **Unmanaged row** / **League visibility** terms and the
rewritten **Manager** entry; PLAN.md **UX-01**.

## UX-02 Self-insert badges + League player page buttons

**UX-02** lets an Account link one existing Player as its **Self-insert** (GLOSSARY.md). The model
field (`accounts.User.self_insert`), the four domain helpers in `accounts/self_insert.py`, the two
POST-only URLs, the context processor and the `you_badge` tag all live in the `accounts` app — see
[`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md) **Self-insert (UX-02)**. In this app the feature is
**presentation only**: **no simulation / RNG / `BatchSimulator` / persistence change, no Score
Calibration re-baseline, no `matches` migration**, and nothing a Self-insert does affects a roster,
enrolment or result.

**The "You" badge.** Every **server-rendered, visible** Player name gets
`{% you_badge <player id> %}` right after it (after the closing `</a>` when the name is a link), and
each template — *including every included partial* — does its own `{% load self_insert_tags %}`. The
tag reads the request-scoped `self_insert_player_id` context key, so it renders `""` whenever the
Account has no (non-drifted) Self-insert. Sites in this app:

| Area | Templates (player-id expression) |
|---|---|
| League | `leagues/player_detail.html` (`<h1>` + the no-Rounds empty-state alert; `player.id`), `free_agents.html`, `player_ratings.html`, `team_roster.html` starters + bench (`player.id`); `player_stats.html`, `league_leaders.html` (all four boards), `statistical_feats.html`, `watch_list.html` (`row.player_id`); `team_history.html` (`p.player_id`); `history.html` Season MVP / Finals MVP, in-progress and completed rows (`AwardWinner.player_id`); `team_finances.html` roster salaries + availability (`r.player_id` / `entry.player_id`); `_partials/dashboard_leaders_snippet.html`, shared by the League and Season dashboards (`LeaderRow.player_id`) |
| Season | `seasons/awards.html` — all seven award rows incl. the `kd_rows` loop (`w.player_id`) |
| Round / sandbox | `matches/game_round_detail.html` box score — both scoreboards and both resources lists, badge before the colon (`perf.player_id`); `compare_rounds.html` deltas (`row.player_id`); `head_to_head.html` top-impactful pair; `player_head_to_head.html` `<h2>` (`player_a.id` / `player_b.id`); `team_history.html` (`row.player.id`) |
| Tournament | `tournament_detail.html` pool table + drawn-team list (`entry.player_id`) |

**Two view changes, both additive row-dict enrichments:**

- **`views.py::game_round_detail`** builds
  `red_players = [{**_player_row(s), "player_id": s.player_id} for s in red_states]` (same for blue).
  The `player_id` key is a **view-local enrichment, NOT a scoreboard key**: `_player_row` and
  `round_summary.PLAYER_ROW_KEYS` are **unchanged (still 28 keys)**, and the `export_round_report` PDF
  — which builds its rows from `_player_row` directly — is unaffected.
- **`league_screens/team_finances.py::team_finances`** adds `"player_id": p.id` to the
  `roster_salaries` and `availability` dict comprehensions. The existing keys (`name`, `salary` /
  `games_remaining`) are unchanged.

Every other badge site already had a player id in its context; no other view changed.

**League player page buttons.** `templates/leagues/player_detail.html`
(`league_screens.player_detail.player_detail`, `league_player_detail`,
`/leagues/<league_id>/players/<player_id>/`) renders the same POST form as the sandbox player page,
inside `#league-player-header` after the "Career stats (global)" link: *This is me*
(`self-insert-link-form` / `self-insert-link-button`) when `player.team` is managed by this Account
and the Player is not already the Self-insert, *Unlink* (`self-insert-unlink-form` /
`self-insert-unlink-button`) when it is, nothing on an Unmanaged Team. Both carry a hidden
`next={{ request.get_full_path }}` so the round-trip returns here. **No view change** — the frozen
LG-06h context keys are untouched; `player.team` lazy-loads one row. (The topnav "My player" link
still always targets the *sandbox* `player_detail`, since a Player carries no `league_id`.)

**Out of scope (by rule).** No badge in `{% block title %}`, `<option>` text (e.g.
`player_head_to_head.html`'s pickers, `tournament_detail.html`'s enrol option), free-text event
descriptions (`missile_log.html`), or any **JS-drawn view** — `game_round_events.html` playback map
and feed, `movement_heatmap.html`'s roster JSON, `play_week_live.html`, every chart. The **PLAY-01
live dashboard** fragments re-rendered by `league_views._render_live_play_panels` through
`render_to_string` with **no request** have no `self_insert_player_id`, so the badge drops off a
JS-patched leaders card mid-run and returns on reload — accepted, and logged in PLAN.md **Deferred
Items**. Tests: `matches/tests/test_self_insert_badges.py` (`league_player_detail` badge + both
button states, League `team_roster`, `stats_league_leaders`, the `game_round_detail` box score with
`PLAYER_ROW_KEYS` still 28, `season_awards`, `team_finances`, `compare_rounds`). Seam contract:
[`.claude/worktrees/ux-02-seam-contract.md`](../../../.claude/worktrees/ux-02-seam-contract.md);
GLOSSARY.md **Self-insert**; PLAN.md **UX-02**.
