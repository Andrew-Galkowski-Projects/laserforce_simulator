# matches: League create and delete

DEL-01 delete League, CRE-01 templates + difficulty, CRE-02 league spread.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## DEL-01 delete league

A guarded **Delete League** that performs a FULL TEARDOWN of all data a
**career-mode** (`League.mode == "league"`) League owns, identified by **PK/FK
identity, never by name**, in a single atomic block. It runs through
`DELETE /api/leagues/<id>/`, confirmed first by `GET /api/leagues/<id>/delete-summary/`.
The teardown owns work the schema does NOT express: `Match.season`
is `SET_NULL` (a plain `league.delete()` would orphan the season's played Matches
into the sandbox match list with `season=NULL`, still feeding player career
stats), season-embedded playoff Tournaments are reached only via
`SeasonPhase.tournament` (`SET_NULL` — cascading the Season merely orphans them),
and the generated competitive Teams + free-agent pool + their hundreds of Players
have **no League FK** (`current_team` / `free_agent_pool` are `SET_NULL` FKs *on*
the League; `Season.teams` is M2M). Correctness rests on the one-context
ownership invariant — **a Team and its Players belong to exactly one context (the
sandbox or a single League) and are never shared** — written into the GLOSSARY.md
**Team** / **Player** entries. Design locked in
[ADR-0032](../../adr/0032-delete-league-full-teardown.md).

**API — `LeagueViewSet.perform_destroy` (`matches/league_api_views.py`).** The
**career gate** `league_services.is_career_league(league) -> bool` (returning
`league.mode == "league"`) sits after the owned-queryset lookup and before any write,
so a foreign or missing id still 404s and a sandbox / multiplayer League gets **409
`not_career_league`** with nothing deleted; otherwise it calls `teardown_league(league)`.
`league_services.league_delete_summary(league)` builds the counts dict (5 int keys:
`seasons` / `matches` / `tournaments` / `teams` / `players`) from the same target
helper the teardown uses; `GET /api/leagues/<id>/delete-summary/` and the Angular
delete confirm share it (`api.md`, **ANG-06-14 League core API gaps**).

**Teardown helper — `matches.league_services.teardown_league(league) -> None`.**
Ordered, all inside ONE inline `with transaction.atomic():`. Candidate Team ids
are collected **before** any delete (critical:
`TournamentPlayerEntry.tournament` is CASCADE, so the drawn-team reverse accessor
vanishes once the Tournaments are deleted). The six steps:

1. **Collect embedded tournament ids** —
   `SeasonPhase.objects.filter(season__league=league, tournament__isnull=False)
   .values_list("tournament_id", flat=True)`. `Season.league` is CASCADE
   (`related_name="seasons"`); `SeasonPhase.season` CASCADE (`related_name=
   "phases"`); `SeasonPhase.tournament` SET_NULL (`related_name="season_phases"`)
   — so the Tournament rows are NOT cascaded and step 4 must delete them
   explicitly.
2. **Collect candidate Team ids by PK/FK only** (set, never by name): Teams
   enrolled in this league's Seasons (`Team.enrolled_seasons` —
   `Season.teams` M2M, `related_name="enrolled_seasons"`); `league.current_team_id`
   (`League.current_team` SET_NULL, `related_name="managed_in_leagues"`);
   `league.free_agent_pool_id` (`League.free_agent_pool` SET_NULL,
   `related_name="free_agent_pool_for"`); drawn teams of the embedded tournaments
   (`Team.drawn_player_entries__tournament_id__in=tournament_ids` —
   `TournamentPlayerEntry.drawn_team` SET_NULL, `related_name=
   "drawn_player_entries"`).
3. **Delete league Matches** — both regular-season and embedded-tournament
   bracket: `Match.objects.filter(Q(season__league=league) |
   Q(series_match__node__tournament_id__in=tournament_ids)).distinct().delete()`.
   `Match.season` is **SET_NULL** (`related_name="matches"`), so a season-scoped
   Match would survive orphaned without this explicit delete — the whole point of
   DEL-01. The playoff Matches are reached via the verified
   Match→SeriesMatch→BracketNode→Tournament chain: `SeriesMatch.match` SET_NULL
   (reverse accessor `series_match`, **singular**); `SeriesMatch.node` →
   `BracketNode` CASCADE (`related_name="series_matches"`); `BracketNode.tournament`
   CASCADE (`related_name="nodes"`). `.distinct()` is required (the to-many join
   can duplicate Match rows). Deleting the Match rows cleanly cascades
   `GameRound` → `GameEvent` / `PlayerRoundState` (all `match`/`game_round` FKs
   CASCADE), so no league game orphans into the sandbox match list or player
   career stats.
4. **Delete the embedded Tournaments** —
   `Tournament.objects.filter(id__in=tournament_ids).delete()`; cascades
   participants / nodes / `SeriesMatch` rows / pool entries
   (`TournamentParticipant` / `BracketNode` / `TournamentPlayerEntry.tournament`
   all CASCADE). No orphaned bracket remains in `/tournaments/`.
5. **Delete the League** — `league.delete()` cascades each Season and its
   dependents (`SeasonPhase`, `PlayerSeasonRating`, `TeamSeasonFinance`,
   `OwnerEvaluation`, and the `Season.teams` M2M join rows) and the directly-owned
   `OwnerEvaluation.league` rows; the `current_team` / `free_agent_pool` SET_NULL
   columns drop with the row (the referenced Teams are NOT cascaded — that's why
   step 6 exists).
6. **Zero-reference Team guard, per candidate id** — a candidate Team is deleted
   **only if** all five of these are False: `team.red_matches.exists()` /
   `team.blue_matches.exists()` (`Match.team_red`/`team_blue` CASCADE,
   `related_name="red_matches"`/`"blue_matches"`) / `team.enrolled_seasons.exists()`
   (surviving Season) / `team.managed_in_leagues.exists()` (surviving League's
   `current_team`) / `team.free_agent_pool_for.exists()` (surviving League's
   `free_agent_pool`). Deleting the Team cascades its Players (`Player.team`
   CASCADE, `related_name="players"`). Given the one-context invariant this guard
   passes for every candidate; it exists so the single unenforced edge (a sandbox
   tournament that "selected existing" a league Team, or a deliberately shared
   Team) can never CASCADE a foreign Match or foreign career row — anything still
   referenced is **left behind** (safe over complete, ADR-0032).

**Tests** assert on row counts / existence / status codes, never simulated point
totals. `matches/tests/test_api_leagues.py::TestLeagueDetail` covers the full-teardown
happy path, the load-bearing **cross-context SAFETY** cases (a candidate Team also
enrolled in ANOTHER League's Season, or one that played a sandbox `season=NULL` Match,
SURVIVES with its Players), embedded-tournament + playoff-Match teardown and the
mode-gate 409; `TestLeagueDeleteSummary` there and
`matches/tests/test_league_delete.py::TestLeagueDeleteSummaryService` cover the counts.

**Scope-out (locked).** **No model change, no migration** (a teardown over the
existing FK graph). **No simulator
touch, no RNG, no `_flush_to_db` / `BatchSimulator` interaction.** **No Score
Calibration re-baseline obligation.** **No GLOSSARY.md / ADR edit** (ADR-0032 +
the GLOSSARY.md Team/Player one-context ownership invariant are already written).

**Locked names (quick index).** API `DELETE /api/leagues/<id>/`
(`LeagueViewSet.perform_destroy`; `is_career_league` gate → 409 `not_career_league`)
+ `GET /api/leagues/<id>/delete-summary/`; helper
`matches.league_services.teardown_league(league) -> None` (inline
`with transaction.atomic():`, 6 ordered steps; candidate ids collected before
delete; per-candidate zero-reference Team guard); gate
`matches.league_services.is_career_league(league) -> bool`; counts
`league_services.league_delete_summary(league)` (keys
`seasons`/`matches`/`tournaments`/`teams`/`players`); tests
`matches/tests/test_api_leagues.py` + `matches/tests/test_league_delete.py`. ADR:
[ADR-0032](../../adr/0032-delete-league-full-teardown.md). Seam contract:
[`.claude/worktrees/del-01-seam-contract.md`](../../../.claude/worktrees/del-01-seam-contract.md).

## CRE-01 league templates + difficulty

Adds a **template chooser** create path beside the full **Advanced** form, and a
transient **Difficulty** choice shared by both. Creation logic is ONE shared helper so
there is no duplicated/new creation path. The GLOSSARY.md **League template**
(a server-side named config bundle that resolves to `CreateLeagueForm` field values
+ phase-composer wire tokens; NOT persisted, NOT user-savable) and **League
difficulty** (the Easy/Medium/Hard knob selecting WHICH generated team the manager
gets) terms are the domain language. **No model change, no migration, no simulator /
RNG touch, no Score Calibration re-baseline, no ADR** (a constants table + a
transient form field over the shipped creation path). Seam contract:
[`.claude/worktrees/cre-01-seam-contract.md`](../../../.claude/worktrees/cre-01-seam-contract.md).

**API — `POST /api/leagues/` (`LeagueViewSet.create`).** A body with `template` is
validated by `LeagueTemplateForm` (unknown / missing key ⇒ 400, no creation), resolved
through `LEAGUE_TEMPLATES_BY_KEY`, and turned into `CreateLeagueForm` data by
`template_to_form_data(...)`; any other body binds `CreateLeagueForm` directly (the
Advanced path). Both call `create_league_and_season(form, manager=request.user)` and
return 201 (the League plus `draft_season` and `conference_ids`).
`GET /api/leagues/templates/` lists `LEAGUE_TEMPLATES`. The SPA screens are
`/leagues/new` and `/leagues/new/advanced` (`league-foundation.md`, **ANG-06-14**).

**Templates module — NEW `matches/league_templates.py`.** A server-side constants
bundle, NOT user-savable, NOT persisted, NO `League` back-reference. Frozen dataclass
`LeagueTemplate(key: str, label: str, num_teams: int, phases: str,
finance_enabled: bool = False, challenge_fired_luxury_tax: bool = False,
mean: int = 50, std_dev: int = 15, map_mode: str = "none")` (`phases` is a
`phase_composer` wire string; `map_mode="none"` is the 3-zone fallback). The **5-row**
`LEAGUE_TEMPLATES: tuple[LeagueTemplate, ...]` (order-preserving) +
`LEAGUE_TEMPLATES_BY_KEY: dict[str, LeagueTemplate]` lookup:
`4_team_quick` ("4-Team Quick", 4, `"round_robin,tournament"`); `8_team_classic`
("8-Team Classic", 8, `"round_robin,tournament"`); `8_team_career` ("8-Team Career",
8, `"round_robin,tournament"`, `finance_enabled=True`); `8_team_double_rr` ("8-Team
Double-RR", 8, `"round_robin:double_round_robin,tournament"`); `8_team_member_nights`
("8-Team Member Nights", 8, `"round_robin,member_night,tournament"`). All 5 wire
strings parse against the real grammar — bare `tournament` ⇒ mode `standings` (the
preceding-RR guard satisfied), `round_robin:double_round_robin` sets the RR phase
schedule format, bare `member_night` is a valid token that may sit anywhere. The
16-Team Conferences preset stays **deferred** behind SUB-01 piece 3.

**Form field — `matches/forms.py`.** A NEW **transient** `CreateLeagueForm.difficulty
= forms.ChoiceField(choices=DIFFICULTY_CHOICES, initial="medium", required=False,
widget=forms.Select(attrs={"id": "league-create-difficulty", "class": "form-select"}),
label="Difficulty")` with `DIFFICULTY_CHOICES = (("easy","Easy"),("medium","Medium"),
("hard","Hard"))`, inserted **after `manager_team_name` / before `season_name`** in
the pinned field order. **Real, shared** (consumed by BOTH the chooser-assembled form
and the Advanced form), **transient** (consumed at create time only) — **NO
`League.difficulty` field, NO migration**. `required=False` is locked so existing
Advanced POSTs that omit `difficulty` stay valid (default Medium); the pick helper
coerces a falsy/unknown value to `"medium"`. **No `clean()` change** beyond the field
declaration (the phase parsing is untouched).

**Shared creation + ranking/pick helpers — `matches/league_services.py`.** ONE
`@transaction.atomic create_league_and_season(form: CreateLeagueForm, *, manager=None)
-> Season` is the **sole** creation path, called by both API create paths. Step order: `_generate_teams`
→ `League.objects.create` → **manager pick + rename + `current_team`** → free-agent
pool + free agents → `Season.objects.create` → `season.teams.add(*created_teams)` →
`season.map_pool.set` → `SeasonPhase` create loop over `cleaned_data["phase_specs"]` →
`_seed_team_budgets_by_strength` (finance ON only) → `_write_baseline_ratings`. The
manager pick (superseding **CAR-01**'s alphabetical `sorted(created_teams,
key=name)[0]`, which is **removed**) is `_pick_manager_team(created_teams, difficulty)
-> Team`: it ranks via `_rank_teams_by_strength(teams) -> list[Team]` (mean active-roster
`overall_rating` DESC, `team_id` ASC tiebreak — via `_compute_team_overall`) then
indexes `{easy: 0 (strongest), medium: N//2, hard: N-1 (weakest)}` (`N = num_teams`,
falsy/unknown difficulty ⇒ `"medium"`). `_seed_team_budgets_by_strength` is **refactored
to call `_rank_teams_by_strength`** instead of inlining the `sorted(...)` (one ranking,
one place; behaviour unchanged — it is the **same** ranking FIN-03 already used).
Difficulty PICKS the team; a non-blank `manager_team_name` RENAMES that picked team —
**they compose**. The template path assembles form data via `template_to_form_data(
template, *, league_name, difficulty) -> dict` (merges the template row ∪ `league_name`
∪ `difficulty` ∪ static defaults — `manager_team_name=""`, `season_name="Season 1"`,
`start_date=localdate`, `schedule_format="single_round_robin"`, `map_pool` omitted,
booleans pass python `True`/`False`) so it reuses the Advanced validation +
`phase_composer.parse_phase_composition` verbatim.

**Tests** (`matches/tests/test_league_templates.py`, through the API). Every
`LEAGUE_TEMPLATES` row fed through `template_to_form_data(...)` produces a
`CreateLeagueForm` that `is_valid()` and a `phases` string that parses (5 distinct
compositions); a template-path POST per key creates the League + draft Season with the
right `num_teams` / `SeasonPhase` shape / `finance_enabled` (row 3 ON, others OFF) /
`member_night` phase — **NO `_generate_teams` mock** (real generator, so signature
drift surfaces). Difficulty pick verified with deterministic injected per-team stats on
BOTH the template and Advanced paths (`easy` ⇒ strongest, `medium` ⇒ `N//2`-th, `hard`
⇒ weakest by `_rank_teams_by_strength`); difficulty + rename compose
(`TestCre01DifficultyAndRenameCompose`: the rename applies to whichever team difficulty
picked).

**Scope-out (locked).** **No model change, no migration** — `difficulty` is transient,
there is NO `League.difficulty` field and the created League carries no template /
difficulty back-reference. **No simulator touch, no RNG-into-the-sim change, no Score
Calibration re-baseline. No Score Calibration interaction of any kind.** Templates are
not persisted, not user-savable. The *generation-based* power-tiered
complement is the sibling **CRE-02** (tiered expected-finish team generation), shipped
as **League spread** — see the next subsection.

**Locked names (quick index).** API `POST /api/leagues/` (template path +
Advanced path) + `GET /api/leagues/templates/`; helpers
`matches.league_services.create_league_and_season(form, *, manager=None) -> Season`
(`@transaction.atomic`, sole creation path) + `_rank_teams_by_strength(teams) ->
list[Team]` + `_pick_manager_team(created_teams, difficulty) -> Team` +
`template_to_form_data(template, *, league_name, difficulty) -> dict`; API form
`matches.forms.LeagueTemplateForm` (the `template` key); refactored
`_seed_team_budgets_by_strength` (now calls `_rank_teams_by_strength`); NEW module
`matches/league_templates.py` with dataclass `LeagueTemplate(key, label, num_teams,
phases, finance_enabled=False, challenge_fired_luxury_tax=False, mean=50, std_dev=15,
map_mode="none")` + `LEAGUE_TEMPLATES` (5 rows) + `LEAGUE_TEMPLATES_BY_KEY`; form field
`CreateLeagueForm.difficulty` (`ChoiceField`, `initial="medium"`, `required=False`,
widget id `league-create-difficulty`) + const `DIFFICULTY_CHOICES = (("easy","Easy"),
("medium","Medium"),("hard","Hard"))`; pick map `{easy:0, medium:N//2, hard:N-1}`.
Seam contract:
[`.claude/worktrees/cre-01-seam-contract.md`](../../../.claude/worktrees/cre-01-seam-contract.md).

### UXP-18 — a second League gets unused team names

League create pops team names from an **unshuffled** `TEAM_NAMES` copy, so every League used to
draw the same names and a second League collided into `"Aurora Aces #2"` copies.
`teams.services._pop_unique_name` now **discards** a taken pool name and tries the next one; the
`" #k"` suffix (on the first taken name popped) is only the fallback once no unused pool name is
left. Team-name uniqueness stays global (`_team_name_exists`). `_generate_free_agents` also counts
names already picked in the current batch as taken, because its Players are written by one
`bulk_create` at the end.

## CRE-02 league spread

The *generation-based* complement to CRE-01's *selection-based* **League difficulty**.
CRE-01 draws all N competitive Teams from one stat distribution and difficulty merely
re-picks which of the N (equal) Teams becomes the manager's `current_team` — because a
Team's mean active-roster `overall_rating` averages 114 i.i.d. stat draws, its own SD
is only ≈1.4 even at `std_dev=15`, so an 8-team League's best-to-worst gap is ~4 points
and Easy↔Hard is close to a coin-flip among equals. CRE-02 adds a create-time
**League spread** choice — **Even** / **Tiered** / **Steep**, Δ = 0 / 8 / 16 — that
draws each Team from **its own tier mean** on a linear, mean-preserving ramp, giving
the League a real preseason favourite / wooden-spoon structure (~16-point gap at
Tiered, ~32 at Steep) while its **average** talent stays unchanged at every spread. The
GLOSSARY.md **League spread** term (and the amended **League difficulty** entry) is the
domain language; the ramp arithmetic and the generation call live in the teams app —
see the **CRE-02 tiered generation** subsection in
[`docs/agents/teams/generation-and-import.md`](../teams/generation-and-import.md). **No model change, no migration, no
simulator / RNG-into-the-sim touch, no Score Calibration re-baseline, no ADR** (a
transient form field + a pure function + an additive kwarg over the shipped CRE-01
creation path — reversible). Seam contract:
[`.claude/worktrees/cre-02-seam-contract.md`](../../../.claude/worktrees/cre-02-seam-contract.md).

**ONE ORDERING ONLY (the load-bearing decision).** The tier a Team was drawn from is a
**generation input**: it is never persisted, never queried, never surfaced. After
generation the League still has exactly one ordering — the measured strength rank
computed by `_rank_teams_by_strength` (mean active-roster `overall_rating` DESC,
`team_id` ASC). There is **no** "projected finishing order" concept, **no** preseason
power-ranking screen, **no** new model field and **no** migration. `_pick_manager_team`
is therefore untouched and still reads that same measured rank; the spread simply makes
the Easy / Medium / Hard pick a genuinely different roster relative to the field. RNG
means the measured rank legitimately disagrees with the tier order for some Teams —
that is expected, and nothing in the code or the tests may infer a Team's tier.

**Form field — `matches/forms.py`.** A NEW **transient**
`CreateLeagueForm.league_spread = forms.ChoiceField(choices=LEAGUE_SPREAD_CHOICES,
initial="even", required=False, widget=forms.Select(attrs={"id":
"league-create-league-spread", "class": "form-select"}), label="League spread")` with
`LEAGUE_SPREAD_CHOICES = (("even","Even"),("tiered","Tiered"),("steep","Steep"))`. The
constant is declared adjacent to CRE-01's `DIFFICULTY_CHOICES` and the field
immediately **after** the `difficulty` field, so the two transient create-time
selectors sit together. The three value tokens are exactly the
`teams.player_generator.LEAGUE_SPREAD_DELTAS` keys, so a `cleaned_data` value indexes
that dict directly. `required=False` is locked (the CRE-01 `difficulty` precedent): an
Advanced POST that omits the field stays valid and yields `cleaned_data
["league_spread"] == ""`, which the create writer coerces to `"even"`. **NO
`League.league_spread` field, NO migration.** No `clean()` change beyond the field
declaration.

**Create writer — `matches/league_services.py::create_league_and_season`.** A new import
`from teams.player_generator import LEAGUE_SPREAD_DELTAS, compute_tier_means` (both
names from the **pure** module), and one block
immediately before the existing `_generate_teams(...)` call:

```python
spread = cleaned.get("league_spread") or "even"
delta = LEAGUE_SPREAD_DELTAS.get(spread, 0.0)
tier_means = (
    compute_tier_means(cleaned["num_teams"], cleaned["mean"], delta) if delta else None
)
created_teams = _generate_teams(..., tier_means=tier_means)
```

Two independent layers make a bad value harmless: `or "even"` handles falsy (the field
omitted, or `""`), and `LEAGUE_SPREAD_DELTAS.get(spread, 0.0)` handles
unknown-but-truthy. The `if delta else None` step is what guarantees the
**byte-identical Even path** — Even passes `tier_means=None` and never constructs a
list at all, so pre-CRE-02 generation is reproduced exactly rather than approximated by
an all-equal vector. Everything else in the writer is unchanged: `rng = random.Random()`
stays **unseeded**, `_generate_free_agents(...)` still passes the flat
`mean=cleaned["mean"]` (the spread reaches the N competitive Teams only),
`_pick_manager_team` / the manager rename / League + draft Season creation /
`season.teams.add` / `map_pool.set` / the `SeasonPhase` loop / `_write_baseline_ratings`
/ `_seed_team_budgets_by_strength` all keep their current order and behaviour.

**Template path stays Even — `template_to_form_data`.** The returned dict gains **exactly
one** new key adjacent to `"difficulty"`: `"league_spread": "even"`. The **signature is
unchanged** (`(template, *, league_name, difficulty)`) — the template path has no
spread selector at all, so the value is a static constant rather than caller data;
threading a parameter through would imply the chooser can vary it. `LeagueTemplate` and
`LEAGUE_TEMPLATES` gain **no** field and **no** row.

**Tests.** NEW `matches/tests/test_league_spread.py` (kept off the large shared
`test_league_create.py` / `test_league_templates.py` files):
`TestCre02LeagueSpreadFormField` (the field exists, is `required=False` with
`initial="even"`, its choices are exactly the three tuples, each token validates, an
omitted field validates to `""`, and the widget carries the DOM id
`league-create-league-spread`); `TestCre02AdvancedCreateWithSpread` (Advanced-path
`POST /api/leagues/` with `tiered` / `steep` / the field omitted each return 201 and
create one League, one draft Season and `num_teams` enrolled Teams; an unknown value is
400); `TestCre02TemplateFormDataIsAlwaysEven`
(`template_to_form_data(...)["league_spread"] == "even"` for **every**
`LEAGUE_TEMPLATES` row, and each resulting dict still yields a valid `CreateLeagueForm`).
**The create-path flakiness rule is mandatory:** `create_league_and_season` builds an
**unseeded** `random.Random()`, so **no create-path test may assert anything about team
strength, tier ordering, or the strong-vs-weak gap** — those tests cover form validity,
HTTP status and object counts only. Every strength assertion lives at the `_generate_teams` layer with an
injected fixed seed (see the teams app guide).

**Scope-out (locked).** No model change, no migration, no ADR. `_pick_manager_team` /
`_rank_teams_by_strength` / `_seed_team_budgets_by_strength` are zero-diff (all three
read the one measured ranking, which the tier ramp is invisible to). `LeagueTemplate` /
`LEAGUE_TEMPLATES` are zero-diff — the template path always creates an Even League. The
next-Season rollover / `_develop_league_for_new_season` do **not** re-apply the spread: it is create-time only, and **LG-04 development** owns
Team strength thereafter. No simulator, `BatchSimulator` or sim-RNG touch, and because
the ramp is symmetric about `mean` the League's average talent is unchanged, so there is
**no Score Calibration re-baseline obligation**. Nothing new is exposed on the REST API,
serializers or admin. GLOSSARY.md's **League spread** term and its amendment to **League
difficulty** were written during the grill that produced the seam contract.

**Locked names (quick index).** Form constant
`matches.forms.CreateLeagueForm.LEAGUE_SPREAD_CHOICES = (("even","Even"),
("tiered","Tiered"),("steep","Steep"))` + field `CreateLeagueForm.league_spread`
(`ChoiceField`, `initial="even"`, `required=False`, label `"League spread"`, widget DOM
id `league-create-league-spread`, widget class `form-select`); import in
`matches/league_services.py` of `LEAGUE_SPREAD_DELTAS` + `compute_tier_means` from
`teams.player_generator`; `template_to_form_data(template, *, league_name, difficulty)
-> dict` **signature unchanged**, returned dict gains `"league_spread": "even"`;
`create_league_and_season` passes `tier_means=` to `_generate_teams`; test file
`matches/tests/test_league_spread.py` with `TestCre02LeagueSpreadFormField` /
`TestCre02AdvancedCreateWithSpread` / `TestCre02TemplateFormDataIsAlwaysEven`. Seam
contract:
[`.claude/worktrees/cre-02-seam-contract.md`](../../../.claude/worktrees/cre-02-seam-contract.md).
