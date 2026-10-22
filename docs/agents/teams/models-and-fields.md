# teams: models and fields

Player field history (LG-04 / LG-05 / FIN-01 / FIN-04), `stat_for_simulation`, `PlayerForm`, `_random_player_profile()`, `Team.is_draw_team`, the Team finance fields, `Team.manager` (UX-01), the Self-insert link (UX-02) and `teams/constants.py`.
Moved verbatim from `laserforce_simulator/teams/CLAUDE.md`; the app index there points here.

## Player fields and model helpers

As of **LG-04 player development**, the 19 stat fields are **mutated in place** at each League
`next_season` rollover (a ZenGM-style age curve) and `total_games` is **ticked** there too —
cosmetically: an active-roster player by their real regular-season appearances, a free-agent by
a small random amount (`total_games` is never a develop input). A per-Season immutable snapshot
of the 19 stats + age + overall is persisted as `matches.PlayerSeasonRating`. See the
[`docs/agents/matches/awards-and-development.md`](../matches/awards-and-development.md) **LG-04 player development + ratings history** section
for the full mechanism.

As of **LG-05 player potential**, `Player.potential` (a nullable `FloatField`, `default=None`;
migration `0012_player_potential.py`) holds a per-Player **projected peak overall** — set at each
League `next_season` rollover (and the founding `league_create` baseline) by LG-05 alongside
development, **mutated in place** like the 19 stats. It is `None`/`default=None` for players outside
any league flow. Details in [`docs/agents/matches/awards-and-development.md`](../matches/awards-and-development.md) **LG-05 player potential**.

As of **FIN-01 team finance subsystem**, `Player.salary` (a nullable `FloatField`, `default=None`;
migration `0013_player_salary_team_finance.py`) holds a **derived salary** — a deterministic
cap-scaled function of `overall_rating` (`matches.finance.salary_for_overall`), recomputed in place
at the same write sites LG-05 recomputes `potential` (`_write_baseline_ratings` +
`_develop_league_for_new_season`), but **only when the League has `finance_enabled`** — otherwise it
stays `None` (byte-identical to pre-FIN-01). It is **finance-only** (a payroll input — the sum of a
Team's 6 active-roster salaries — for the owner-mood money axis, never a sim input, never edited via
a form) and **derived, not contracted** (no free agency / cap space). `None` for players outside any
finance-enabled flow. Details in [`docs/agents/matches/career-and-finance.md`](../matches/career-and-finance.md) **FIN-01 team finance
subsystem**.

As of **FIN-04 health budget + injury/availability**, `Player.games_unavailable` (a
`PositiveSmallIntegerField(default=0)`, declared after `salary`; migration
`0014_player_team_health_injury.py`) is the **availability counter**: `> 0` ⇒ the player is out and
unavailable for selection; it is decremented by 1 each fixture the player's Team plays (whether the
player is substituted out or fielded hurt) and **reset to 0 at the League `next_season` rollover**
(in `_develop_league_for_new_season`, over the developing set). There is **no injury-type taxonomy** —
a single integer countdown. The counter only ever moves in a **finance-enabled career League**
(`_is_career_league AND finance_enabled`); outside that gate it stays `0`, byte-identical to
pre-FIN-04. The roll/decrement/roster-resolution mechanics live in
[`docs/agents/matches/career-and-finance.md`](../matches/career-and-finance.md) **FIN-04 health budget + injury/availability** and
[ADR-0028](../../adr/0028-health-budget-injury-availability.md).

`stat_for_simulation(stat_name, role)` returns `min(int(raw_value * 1.2), 100)` when `role in self.preferred_roles`, otherwise the raw stat value. Invalid `stat_name` values raise `AttributeError` naturally (no explicit guard). Used by `PlayerRoundState` forwarding properties and `BatchSimulator._make_players` to apply the preferred-role boost at simulation time without affecting stored values or `overall_rating`.

`PlayerForm` exposes all 19 stat fields (default 50) with "Set All to Average (50)" and "Set All to Elite (90)" preset buttons. Profile fields (age, started_playing_age, total_games, home_site, height) are also on the form; when adding a new player, the view pre-fills them via `_random_player_profile()`.

`_random_player_profile()` (in `models.py`) returns a dict of randomised profile values drawn from `teams/constants.py`. Age is 16–50; started_playing_age is 16–age; total_games is 0–5000; height is 4'0"–6'10"; home_site is drawn from `LASERFORCE_SITES`; name is drawn from `PLAYER_NAMES`.

`ROLE_STATS` is imported from `matches.sim_helpers.role_constants` — the canonical source for all role-level constants (`ROLE_STATS`, `MAX_LIVES`, `MAX_SHOTS`, `SPECIAL_COST`). Both `teams/models.py` and `sim_helpers/player_state.py` import from there; the duplicate definition that previously lived in `player_state.py` has been removed.

### Team.is_draw_team

`Team.is_draw_team` (`BooleanField(default=False)`, added by **LG-02x-1**) marks a Team that was assembled by a Player-pool draw Tournament's tier-balanced draw (`team_assembly="player_pool"`, named `"random_draw"` before ANG-01f) rather than hand-built. It carries **NO FK to the Tournament / Match** — the durable link from a drawn Team back to its Tournament and its drawn players lives on `matches.TournamentPlayerEntry` (avoiding a `teams → matches` dependency inversion). A drawn Team's `slot_*` FKs hold only the **transient** per-Round role assignment the simulator's `before_round_hook` rewrites in memory each Round; the source of truth for `(player, tier, drawn_team)` is `TournamentPlayerEntry`. Existing rows take `default=False` (no backfill; ADR-0004 disposable-data precedent).

**`Team.roster_errors` relaxation (draw teams only).** Because a drawn Team **references borrowed Players** without `Player.team` ever being reassigned by the draw, the "all players belong to this team" ownership check is **skipped for draw teams**: the `for player, role, slot_name in filled:` belongs-to-team loop (the `player.team_id != self.pk` check) is wrapped in `if not self.is_draw_team:`. **All other validations stay enforced for draw teams** — the all-6-slots-filled check, the **duplicate-player** check, and the **role-distribution** (Scout-only-twice) check. A non-draw Team with a foreign player **still** errors. See [`docs/agents/matches/tournaments-rr-swiss-draw.md`](../matches/tournaments-rr-swiss-draw.md) `## LG-02x-1 random draw player-pool tournament` and [ADR-0022](../../adr/0022-random-draw-player-pool-tournament.md).

### Team finance fields (FIN-01, FIN-04)

**FIN-01 team finance subsystem** adds five `Team` finance fields (migration
`0013_player_salary_team_finance.py`, all neutral defaults so non-finance Teams are unaffected):
`budget_scouting` / `budget_coaching` / `budget_facilities` (`PositiveSmallIntegerField(default=34)`)
are **ZenGM 1–100 budget levels** (`matches.finance.DEFAULT_LEVEL = 34` is the neutral level —
mid-range cost, zero effect) converted to dollars via `finance.level_to_amount`; `ticket_price` /
`cash` (`FloatField(default=0.0)`) are dollar amounts (`cash` accumulates each Season's profit and
carries across the rollover). **No choices, no validators** — the per-League `finance_enabled` toggle
+ AI defaults keep the levels in band, and `matches.forms.TeamFinancesForm` (ANG-01e, shared by the
HTML POST and `PATCH /api/leagues/<id>/teams/<team_id>/finances/`) rejects out-of-range input
(levels `1..MAX_LEVEL`, `ticket_price >= 0`) instead of clamping it.

**FIN-04 health budget + injury/availability** lands the **fourth budget** — `budget_health`
(`PositiveSmallIntegerField(default=34)`, the same ZenGM 1–100 level on the `finance.DEFAULT_LEVEL = 34`
neutral, migration `0014_player_team_health_injury.py`) — plus `injury_policy`
(`CharField(max_length=16, choices=INJURY_POLICY_CHOICES, default="auto_sub")`, where
`INJURY_POLICY_CHOICES = (("auto_sub", "Auto-substitute"), ("play_hurt", "Play hurt"))`), the
manager-editable per-Team policy for how an injured starter is handled at fixture time (AI teams stay
`"auto_sub"`). `budget_health` is a fourth pure expense line (no revenue side) **and** buys a real
edge: its `finance.health_effect` level scales injury **duration** down (better health spending ⇒
shorter unavailabilities). All four budgets now buy a gameplay edge — **facilities** feeds the
revenue/attendance side (FIN-01), **coaching** feeds LG-04 development (FIN-02), **scouting** feeds
LG-05 potential (FIN-03), **health** shortens injuries (FIN-04). Details in
[`docs/agents/matches/career-and-finance.md`](../matches/career-and-finance.md) **FIN-01 team finance subsystem** /
**FIN-04 health budget + injury/availability** and
[ADR-0027](../../adr/0027-team-finance-subsystem.md) / [ADR-0028](../../adr/0028-health-budget-injury-availability.md).

### Team.manager (UX-01) — and the naming hazard

`Team.manager` (**UX-01**, migration `0015_team_manager.py`) is the **Account** a Team belongs to:
`ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
related_name="teams")`. `Team` is one of the five **Ownership roots** — it has no parent FK, so it is
*always* a root and carries the FK directly; its `Player`s are derived rows and have **no `manager`
column** (one FK per aggregate — `Player` resolves its Manager by traversing `player.team`).

`manager IS NULL` is an **Unmanaged row**: readable **and** writable by any authenticated Account,
and listed to all of them. `SET_NULL` means deleting an Account in Admin **demotes** its Teams to
Unmanaged rather than cascading their Matches and events away. `_generate_teams` gained a
keyword-only `manager=None` parameter (appended last, so existing callers are source-compatible; since
ANG-01a it lives in `teams/services.py`, re-exported from `teams.views`), and
`team_create` stamps `manager_or_none(request)` via `form.save(commit=False)`. `team_list` and
`player_list` are filtered through `accounts.permissions.owned_queryset` (`path="team"` for Players),
and every `get_object_or_404` in `teams/views.py` became `get_owned_or_404(..., request, ...)`.

The global `"Free Agents"` singleton (`get_free_agents_team()`) is **deliberately never stamped** — it
is a cross-Account shared pool, and stamping it would let the first Account to run `generate_players`
with `num_teams == 0` capture it permanently and 404 it for everyone else. `_generate_free_agents`
and `get_free_agents_team()` keep their signatures verbatim.

The `Team.manager` vs `Team.managed_in_leagues` naming-hazard table stays in the app index
(`laserforce_simulator/teams/CLAUDE.md`), because it is a project-wide trap.

See [`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md) for the permission seam and the traversal table,
and [ADR-0038](../../adr/0038-accounts-and-uniform-manager-ownership.md).

### Player.self_insert_of (UX-02) — the Self-insert link

**UX-02** lets an Account link one existing Player as its **Self-insert** (GLOSSARY.md). The FK lives
on the Account — `accounts.User.self_insert = OneToOneField("teams.Player", null=True, blank=True,
on_delete=SET_NULL, related_name="self_insert_of")` (migration `accounts/0002_user_self_insert.py`)
— so **`Player` gains no column and `teams` gains no migration**; it only gets the reverse accessor
`player.self_insert_of`. That accessor raises `RelatedObjectDoesNotExist` when unlinked and
**production code never reads it** — go through `accounts.self_insert.get_self_insert(user)` /
`is_self_insert_eligible(player, user)` instead. It is a **link, not a copy**: linking never moves,
enrols or re-rosters the Player, never touches a `slot_*` FK, and changes nothing the simulator sees.
Deleting the Player (or its Team, which cascades it) leaves the Account with no Self-insert.

Eligibility is **strict**: only a Player whose `team.manager_id == user.pk`. Players on an
**Unmanaged** Team (including the never-stamped global "Free Agents" singleton) are not linkable. If
the Player later leaves the Account's ownership (Team demoted to Unmanaged, Player moved to "Free
Agents"), the link has **drifted** — `get_self_insert` returns `None` at read time with no write, and
the link reappears if ownership returns.

**Sandbox surfaces:**

- `templates/teams/player_detail.html` (`player_detail`, `/teams/<team_id>/player/<player_id>/`) — a
  POST form in the identity card directly under the Team name: *This is me*
  (`self-insert-link-form` / `self-insert-link-button`, posting to `self_insert_link`) when `team` is
  managed by this Account and the Player is not already the Self-insert; *Unlink*
  (`self-insert-unlink-form` / `self-insert-unlink-button`, posting to `self_insert_unlink`) when it
  is; nothing on an Unmanaged Team. **No view change.** This page is also the fixed target of the
  topnav **"My player"** link (`account-my-player-link`).
- The **"You" badge** (`{% load self_insert_tags %}` … `{% you_badge player.id %}`, rendering the
  `you-badge` span) follows every server-rendered Player name on: `player_detail.html` (the `<h3>`),
  `team_detail.html` (active roster + bench), `player_list.html` (`/players/`),
  `player_career_stats.html` (the `<h1>`) and `player_delete.html`. Not on `<title>`, the avatar
  initial, `<option>` text or form widgets (`team_slots_form.html`). No `teams` view changed.

Full contract in [`../accounts/CLAUDE.md`](../../../laserforce_simulator/accounts/CLAUDE.md) **Self-insert (UX-02)**.

## Constants (`teams/constants.py`)

Static name pools used by `_random_player_profile()`:

- `PLAYER_NAMES` — ~386 laser-tag codenames drawn from real venue scorecards.
- `LASERFORCE_SITES` — 12 real Laserforce venue locations.

These are imported into `models.py` and re-exported so existing code that does `from teams.models import PLAYER_NAMES` continues to work.
