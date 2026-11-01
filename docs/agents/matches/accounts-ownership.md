# matches: Accounts and ownership in matches

UX-01 `manager` ownership roots in this app, UX-02 Self-insert.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## UX-01 accounts + the `manager` ownership FK

**UX-01** gives every row an owner. The `matches` app carries **four of the five Ownership roots**
(`teams.Team` is the fifth) and the dormant `League.visibility` column. The **Account** model, the permission seam and the auth surfaces live in the
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

- **Single-row lookups** use `get_owned_or_404(Model, request, **lookup)`. Cross-Account access
  raises **`Http404`, never 403**, so another Account's row is indistinguishable from a nonexistent
  one.
- **`SimulateBatchAPIView.post`** resolves its Teams through
  `matches.services.resolve_batch_teams(..., request.user)` (ownership-scoped since ANG-01b) and
  keeps returning **400, not 404** for a missing or foreign Team (a raised `ValidationError` in the
  envelope).
- **List scoping:** `LeagueViewSet` and the Tournament viewsets filter with `owned_queryset`.
  `MatchViewSet` / `GameRoundViewSet` use the two longhand `Q` builders (`owned_match_q` /
  `owned_game_round_q`) because those two roots are **conditional** and `owned_queryset` is not
  valid for them.
- **DRF:** `MatchViewSet` / `GameRoundViewSet` gain `@method_decorator(login_not_required,
  name="dispatch")` and a manager-scoped `get_queryset()` built on the same two `Q` builders, while
  **keeping** their class-level `queryset` attribute (the router needs it for basename
  introspection). `GameRoundViewSet` keeps its `self.action == "retrieve"` prefetch branch and its
  `@action(url_path="events")` method unchanged; only the base queryset is wrapped. Router
  registration and every API URL name are untouched. `SimulateBatchAPIView` /
  `SimulateBatchStatusAPIView` take the same decorator. The middleware exemption is what makes DRF's
  `IsAuthenticated` return a **401 JSON** body (403 before ANG-03) instead of an HTML **302** to the login page.

**Stamping sites in this app.** `league_services.create_league_and_season` takes a keyword-only
`manager: AbstractBaseUser | None = None` parameter (the helper takes no `request`, so the parameter
*is* how the Account reaches it) and stamps both the `League` and the League's free-agent pool
`Team`; `league_play_services.setup_member_night` stamps its `is_draw_team=True` Teams;
`tournament_services.create.create_tournament` stamps the `Tournament` and its generated Teams;
`tournament_services.draw.run_draw` stamps its drawn Teams; `services.create_sandbox_match` and
`services.create_sandbox_round` call `stamp_manager(obj, user)` **post-hoc** on the `Match` /
`GameRound` returned by the simulator, because they never construct the row themselves; and the
three `Season._build_*` sites propagate the League's Manager as above. Creation sites that
intentionally stay Unmanaged: every `Season`, `SeasonPhase`, `Conference` and `SeriesMatch` (all
derived rows), the member-night `Match` (it has `season=season`, so it is derived, not a root), and
everything `matches/simulation/*` writes.

**`League.visibility` — a dormant column (3 touch points, and no more).** `CharField(max_length=16,
choices=VISIBILITY_CHOICES, default="closed")` where `VISIBILITY_CHOICES = (("closed", "Closed"),
("open", "Open"))` is a class-level constant on `League` alongside the existing `MODE_CHOICES` /
`STATE_CHOICES`; a `required=False` `CreateLeagueForm.visibility` `ChoiceField` (widget id
`league-create-visibility`, declared last in the class body), which **UXP-21 removed from the
page** (it did nothing, so offering it confused users; the field still binds, so the API keeps
accepting it); and `league_services.create_league_and_season` passing
`visibility=cleaned.get("visibility") or "closed"` (with `league_services.template_to_form_data`
supplying `"visibility": "closed"`). **Nothing reads it this slice** — no branch, no filter, no
sidebar entry, no admin column. It is the forward-compatible marker for who may join a League once **League membership**
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
renaming anything Owner-flavoured. Tests: new `matches/tests/test_ownership.py`, plus
`force_login` calls added to the locally-instantiated clients in `test_batch_views.py`,
`test_heatmap.py`, `test_missile_log.py`, `test_playback_map.py` and `test_apis.py` (the `setUp`
`APIClient`) — the root `conftest.py` autouse login fixture cannot reach those. Seam contract:
[`.claude/worktrees/ux-01-seam-contract.md`](../../../.claude/worktrees/ux-01-seam-contract.md);
GLOSSARY.md **Account** / **Ownership root** / **Unmanaged row** / **League visibility** terms and the
rewritten **Manager** entry; PLAN.md **UX-01**.

## UX-02 Self-insert

**UX-02** lets an Account link one existing Player as its **Self-insert** (GLOSSARY.md). The model
field (`accounts.User.self_insert`), the four domain helpers in `accounts/self_insert.py` and
`/api/accounts/me/self-insert/` all live in the `accounts` app — see
[`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md) **Self-insert (UX-02)**. In this app the feature is
**presentation only**: **no simulation / RNG / `BatchSimulator` / persistence change, no Score
Calibration re-baseline, no `matches` migration**, and nothing a Self-insert does affects a roster,
enrolment or result.

Seam contract:
[`.claude/worktrees/ux-02-seam-contract.md`](../../../.claude/worktrees/ux-02-seam-contract.md);
GLOSSARY.md **Self-insert**; PLAN.md **UX-02**.
