# HX-03 — Head-to-Head Record Seam Contract

This document is the locked, copy-pasteable contract for the **HX-03 Head-to-head
record** feature. It is pasted verbatim into 3 parallel agent prompts (Code /
Tests / Docs). Every name, signature, and dict shape below is **frozen** —
deviations must be raised against this artifact, not silently renamed.

## Cross-check findings

Verified against the real codebase (`matches/views.py`, `matches/models.py`,
`matches/urls.py`, `teams/career_stats.py`, `teams/role_benchmarks.py`,
`templates/matches/match_list.html`, `templates/matches/team_history.html`,
`CONTEXT.md`). Two deviations from the locked phrasing in the briefing — both
mechanical naming corrections, **not** behavioural changes:

1. **Team-history view name.** The briefing referred to the "existing
   `team_history` view in `matches/views.py`". The actual view function is
   **`team_match_history(request, team_id)`** (URL name `team_match_history`,
   URL pattern `team/<int:team_id>/history/`); only the template file is
   `templates/matches/team_history.html`. The entry-point edit in the **Entry
   points** section below is written against the template's actual filename
   and `team_match_history` view context.

2. **`Match.red_total_points` / `Match.blue_total_points` are `@property`,
   not fields.** The briefing listed them under "Match model fields"; they are
   read-only `@property` methods on `Match` that sum
   `red_round1_points + red_round2_points + red_bonus_points` (likewise blue).
   The contract uses them as **read-only attribute access only** (no filter,
   no `update`), which is fully compatible. The corpus-filter rules below use
   `Match.winner_id`, `Match.is_completed`, `Match.date_played`, and the
   per-round `red_points` / `blue_points` Integer fields on `GameRound` — all
   of which are real fields and safe to filter on.

All other locked names confirmed verbatim:
`Match.team_red`/`team_blue`/`winner`/`is_completed`/`date_played` (fields);
`GameRound.match` (nullable FK)/`team_red`/`team_blue`/`red_points`/
`blue_points`/`arena_map` (nullable FK to `core.ArenaMap`)/`date_played`/
`is_simulated`; `PlayerRoundState.player`/`team_color`/`final_lives`/`get_mvp`
(**property**, not method — read as `state.get_mvp`, no parentheses);
`templates/matches/match_list.html` exists; `matches/urls.py` is the correct
URL file; the **Head-to-head record** term is present at the bottom of the
`### Analytics and review` section of `CONTEXT.md`.

## Background

HX-03 is a read-only analytics surface that aggregates the history between
two **Teams** — see the **Head-to-head record** entry at the end of the
`### Analytics and review` section of `CONTEXT.md` for the full domain
definition. The view mirrors three existing patterns:

- **RV-01 (`matches/views.py::compare_rounds`)** — the 4-mode read-only view
  pattern (`picker` / `404` / `error` / `results`) routed through query
  params, with side-agnostic Team-id pairing.
- **HX-01 (`teams/career_stats.py`)** — the pure-Python aggregation module
  with a frozen import allowlist and `list[Mapping]` input shape.
- **HX-02 (`teams/role_benchmarks.py`)** — the import-allowlist + defensive
  "no Django imports leaked" test, plus the forgiving query-param fallback
  for invalid input.

The view is **read-only**: no RNG, no simulation, no `_flush_to_db` touch, no
SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline. No
model change, no migration, no ADR.

## Locked domain decisions

- **Corpus:** every H2H **Match** (`{team_red, team_blue} == {team_a, team_b}`)
  PLUS every standalone H2H **Round** (no `Match` parent). Side-agnostic
  Team-id pairing (orientation-independent — a Team that played red in one
  game and blue in another still pairs by Team id).
- **Match record:** W/L/T from `Match.winner` over Matches only (filter
  `is_completed=True`). `winner_id NULL → T`; `== team_a_id → W`;
  `== team_b_id → L`.
- **Round record:** W/L/T per Round across the unified basket (the 2 Rounds of
  each H2H Match + every standalone H2H Round). A Round's winner is the
  higher-scoring side; equal scores = tie.
- **Score margin:** mean of `(team_a_score − team_b_score)` per Round across
  the unified basket, signed from team_a's perspective.
- **Avg survivors:** per-team mean of `count(PlayerRoundState.final_lives > 0)`
  per Round, two numbers (team_a's avg, team_b's avg).
- **Most impactful player:** cumulative `get_mvp` summed across every H2H
  Round each player appeared in, reported one per team (highest sum on each
  team's pool). **Per-Round team attribution** via
  `PlayerRoundState.team_color` mapped to that Round's `team_red_id` /
  `team_blue_id` and resolved against `{team_a_id, team_b_id}`. A player who
  switched teams between H2H games can appear in BOTH per-team pools (with
  their MVP from games on that team only). View resolves; pure module
  receives pre-attributed `(player_id, name, team_id, mvp, round_id)` per
  PlayerRoundState row.
- **Empty H2H:** `mode='results'` (NOT error), all aggregates render as
  zeros, single notice block DOM id `h2h-no-games-notice`. Top-impactful and
  detail list omit when empty.
- **No model change, no migration, no ADR.** Read-only view, consumes no
  RNG, no simulation, no SIM-07/08 contract interaction, no Score
  Calibration re-baseline.

## Query params

- `?team_a=<int>&team_b=<int>` — both required for results; either missing →
  picker.
- `?provenance=all|real|sim` (default `all`) — filters `GameRound.is_simulated`
  (`real` ⇒ `False`, `sim` ⇒ `True`, `all` ⇒ no filter). Invalid value falls
  back to `all` (HX-02 forgiving-fallback pattern). Match record filters
  Matches whose Rounds match the provenance — exclude a Match if neither of
  its Rounds matches; for the `real` / `sim` branches the Match record only
  counts Matches where **BOTH** Rounds match the filter (conservative; locked
  rule).
- `?from=YYYY-MM-DD&to=YYYY-MM-DD` — both optional, default unbounded,
  invalid silently ignored (treated as unbounded that side). Filters
  `Match.date_played` for the Match record and `GameRound.date_played` for
  the Round corpus.

## View modes (RV-01 pattern)

- `picker` — either `team_a` or `team_b` query param missing → render two
  `<select>` chooser + filter controls (HTTP 200).
- `404` — supplied team id does not resolve → `get_object_or_404`.
- `error` — `team_a_id == team_b_id` → `mode="error"` + `error_message`,
  picker re-renders above banner, HTTP 200.
- `results` — both ids valid + distinct → full render (incl. the
  empty-history sub-case with zeroed aggregates + `h2h-no-games-notice`).

## Module structure

**New pure module:** `laserforce_simulator/matches/h2h_stats.py`

- **Frozen import allowlist:** `typing.Iterable`, `typing.Mapping`,
  `typing.Sequence`, `collections.defaultdict`. NO Django, NO ORM, NO RNG,
  NO I/O, NO model imports. Pinned by `test_no_django_imports_leaked`
  defensive check (HX-01 / HX-02 / RES-04 / RV-03 precedent).
- Mirror the HX-01 / HX-02 docstring style.

**View:** `matches/views.py::head_to_head(request)` with module-level helpers
(RV-01 pattern), routed via `matches/urls.py` `path("h2h/", views.head_to_head,
name="head_to_head")`.

## Seam: 3 flat dict lists (view → pure module)

**`matches_list: list[dict]`** — one entry per H2H Match (after
`is_completed=True`, provenance, date filters):

```python
{
    "match_id": int,
    "winner_team_id": int | None,   # None = tie
    "date_played": datetime,
    "is_simulated": bool,           # carried for downstream display, not used in compute
}
```

**`rounds_list: list[dict]`** — one entry per Round in the unified basket
(Match-rounds + standalone), already normalised from team_a perspective by
the view:

```python
{
    "round_id": int,
    "date_played": datetime,
    "team_a_score": int,            # view flips red/blue based on actual sides
    "team_b_score": int,
    "team_a_survivors": int,        # view counts final_lives > 0 per team
    "team_b_survivors": int,
    "match_id": int | None,         # None = standalone Round
    "arena_map_id": int | None,
    "arena_map_name": str | None,   # None when arena_map is null
    "is_simulated": bool,
}
```

**`player_rounds_list: list[dict]`** — one entry per `PlayerRoundState` in
the rounds_list, already attributed to team_a or team_b by the view
(per-Round team_color resolution):

```python
{
    "player_id": int,
    "player_name": str,
    "team_id": int,                 # team_a_id or team_b_id (per-Round attribution)
    "mvp": float,                   # PlayerRoundState.get_mvp (property, no parens) — pre-computed
    "round_id": int,
}
```

## Pure module public surface

All signatures and return shapes below are frozen. Empty-input branches must
return zeros (or `[]` / `None`), never raise.

```python
def compute_match_record(
    matches_list: Iterable[Mapping], team_a_id: int, team_b_id: int
) -> dict:
    """W/L/T over H2H Matches.
    Returns {"wins": int, "losses": int, "ties": int, "n": int}.
    winner_team_id == team_a_id → wins; == team_b_id → losses; None → ties.
    Defensive: a winner_team_id that is neither (legacy/corrupt) → ties.
    """

def compute_round_record(rounds_list: Iterable[Mapping]) -> dict:
    """W/L/T per Round across unified basket.
    Returns {"wins": int, "losses": int, "ties": int, "n": int}.
    team_a_score > team_b_score → wins; < → losses; == → ties.
    """

def compute_score_margin(rounds_list: Iterable[Mapping]) -> dict:
    """Mean signed margin (team_a − team_b) per Round.
    Returns {"mean_margin": float, "n": int}.
    Empty input ⇒ {"mean_margin": 0.0, "n": 0} (no div-by-zero).
    """

def compute_avg_survivors(rounds_list: Iterable[Mapping]) -> dict:
    """Per-team mean survivors per Round.
    Returns {"team_a_avg": float, "team_b_avg": float, "n": int}.
    Empty input ⇒ {"team_a_avg": 0.0, "team_b_avg": 0.0, "n": 0}.
    """

def top_impactful_per_team(
    player_rounds_list: Iterable[Mapping], team_a_id: int, team_b_id: int
) -> dict:
    """Top cumulative-MVP player per team.
    Returns {
        "team_a": {"player_id": int, "name": str, "mvp_total": float, "games": int} | None,
        "team_b": {"player_id": int, "name": str, "mvp_total": float, "games": int} | None,
    }
    None when that team has no rows in player_rounds_list.
    Tiebreaker: highest mvp_total wins; equal totals → lower player_id wins (deterministic).
    """

def compute_per_map_breakdown(rounds_list: Iterable[Mapping]) -> list[dict]:
    """Per-arena_map W/L/T + margin table.
    One entry per arena_map_id observed in rounds_list (including a single
    entry for arena_map_id=None labelled "No map (3-zone)").
    Returns list[{
        "arena_map_id": int | None,
        "arena_map_name": str,        # "No map (3-zone)" when arena_map_id is None
        "games": int,
        "wins": int, "losses": int, "ties": int,
        "mean_margin": float,
    }]
    Sorted by games desc; tiebreaker arena_map_id asc with None last.
    Empty input ⇒ [].
    """

def margin_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — signed margin per Round chronologically.
    Returns [[round_idx_1based, signed_margin_int], ...] sorted by
    (date_played, round_id) ascending.
    list[list] (not list[tuple]) for json_script serialisation.
    Empty input ⇒ [].
    """

def cumulative_wl_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — cumulative (team_a_wins − team_b_wins) Round-level.
    Returns [[round_idx_1based, cum_diff], ...] sorted by
    (date_played, round_id) ascending.
    Ties don't move the running diff.
    Empty input ⇒ [].
    """
```

## View module-level helpers

All helpers live in `matches/views.py` (RV-01 pattern — flat module-level
helpers prefixed `_`, no class).

- `_parse_provenance(raw: str | None) -> str` — returns one of `"all"`,
  `"real"`, `"sim"`. Anything else (None, "", malformed) → `"all"`.
- `_parse_date(raw: str | None) -> date | None` — `None` / `""` /
  `ValueError` → `None`.
- `_h2h_matches_qs(team_a, team_b, provenance, date_from, date_to) -> QuerySet[Match]` —
  filters by id-pair (either red or blue), `is_completed=True`, date range,
  provenance (when provenance != `"all"`, requires **BOTH** `game_rounds` to
  match `is_simulated`).
- `_h2h_rounds_qs(team_a, team_b, provenance, date_from, date_to) -> QuerySet[GameRound]` —
  filters by id-pair (either red or blue), date range, provenance. Includes
  Rounds with `match=None` and Rounds whose Match is in the H2H Match set.
- `_normalize_round(game_round, team_a_id) -> dict` — returns the
  `rounds_list` shape; flips red/blue when `game_round.team_red_id !=
  team_a_id` so `team_a_score` / `team_b_score` are always from team_a's
  perspective.
- `_team_a_or_b(round_, team_color, team_a_id) -> int` — resolves
  `"red"`/`"blue"` + which side team_a is on for this Round → returns
  `team_a_id` or `team_b_id`.
- `_build_player_rounds(rounds_qs, team_a_id, team_b_id) -> list[dict]` —
  single ORM query (`PlayerRoundState.objects.filter(game_round__in=
  rounds_qs).select_related("player", "game_round")`), maps each row to the
  `player_rounds_list` shape using `_team_a_or_b`. Computes `mvp =
  state.get_mvp` (property — **no parentheses**).
- `_build_detail_list(matches_list, rounds_list) -> list[dict]` — unified
  reverse-chronological list, one row per Match (with 2-round totals +
  winner) AND one row per standalone Round (with that Round's score). Each
  row carries `kind` ∈ `{"match", "round"}`, plus the display fields needed
  by the template (date, type label, score string, winner name or `"—"`,
  detail URL).
- `head_to_head(request) -> HttpResponse` — assembles everything, picks the
  mode, renders.

## URL routing

In `matches/urls.py`, add:

```python
path("h2h/", views.head_to_head, name="head_to_head"),
```

## Context keys (results mode — frozen)

```
mode               # "picker" | "404" (raised, not rendered) | "error" | "results"
error_message      # str | None — only set in "error" mode
team_a             # Team | None
team_b             # Team | None
all_teams          # QuerySet[Team] ordered by name — for picker dropdowns
date_from          # date | None
date_to            # date | None
provenance         # "all" | "real" | "sim"
match_record       # dict from compute_match_record (or zeros)
round_record       # dict from compute_round_record
score_margin       # dict from compute_score_margin
avg_survivors      # dict from compute_avg_survivors
top_impactful      # dict from top_impactful_per_team
per_map_breakdown  # list[dict] from compute_per_map_breakdown
detail_list        # list[dict] from _build_detail_list
margin_series      # list[list] from margin_series
cumulative_wl_series  # list[list] from cumulative_wl_series
```

Picker mode and error mode still include `all_teams`, `date_from`, `date_to`,
`provenance` so the form re-renders with the user's prior selections;
aggregates can be omitted (template gates on `mode == "results"`).

## Template

**Path:** `laserforce_simulator/templates/matches/head_to_head.html`. Extends
`base.html`.

**Locked DOM ids** (every template id is testable via substring):

Picker form:
- `h2h-picker-form` (form), `h2h-select-a`, `h2h-select-b`,
  `h2h-provenance`, `h2h-from`, `h2h-to`, `h2h-submit`.

Results — headline:
- `h2h-match-record` (wraps "W-L-T"), `h2h-round-record`,
  `h2h-score-margin`, `h2h-team-a-survivors`, `h2h-team-b-survivors`,
  `h2h-top-impactful-a`, `h2h-top-impactful-b`.

Results — sections:
- `h2h-per-map-table`, `h2h-detail-list`, `h2h-no-games-notice` (only
  rendered when `match_record.n == 0` AND `round_record.n == 0`).

Charts:
- canvas ids `h2h-margin-chart`, `h2h-cumulative-wl-chart`.
- `json_script` ids `h2h-margin-series` (renders `margin_series`),
  `h2h-cumulative-wl-series` (renders `cumulative_wl_series`).

Error banner:
- `h2h-error-banner` containing `{{ error_message }}`.

**Chart.js:** mirror RV-01's `compare_rounds.html` overlay pattern. Margin
chart = stepped line with zero reference. Cumulative chart = stepped line, no
reference.

**Time display:** `date_played` rendered via Django's `|date:"Y-m-d H:i"`
filter (not tick conversion — this is real wall-clock).

## Entry points

Template-only edits to existing files (no view-level changes):

- `templates/matches/match_list.html`: add a "View Head-to-Head" anchor in
  the header area, sibling to the existing "Compare Rounds" button. URL:
  `{% url 'head_to_head' %}` (no params → picker mode).
- `templates/matches/team_history.html` (rendered by view
  `team_match_history`): in the matches list, for each unique opponent the
  team has faced, add a "vs. {opponent} — H2H" link that pre-fills both team
  ids: `{% url 'head_to_head' %}?team_a={{ team.id }}&team_b={{ opponent.id
  }}`.

## Tests

**New `matches/tests/test_h2h_stats.py`** — pure-unit, no Django imports,
no DB. Class names + test names locked:

- `TestComputeMatchRecord`:
  - `test_empty_input_returns_zeros`
  - `test_team_a_wins_losses_ties_counted_correctly`
  - `test_null_winner_counts_as_tie`
  - `test_unknown_winner_id_counts_as_tie_defensive`
- `TestComputeRoundRecord`:
  - `test_empty_input_returns_zeros`
  - `test_higher_team_a_score_is_win`
  - `test_lower_is_loss`
  - `test_equal_is_tie`
- `TestComputeScoreMargin`:
  - `test_empty_input_zero_no_div_by_zero`
  - `test_signed_mean_from_team_a_perspective`
  - `test_negative_margin_when_team_b_dominates`
- `TestComputeAvgSurvivors`:
  - `test_empty_input_zeros`
  - `test_per_team_mean_independent`
- `TestTopImpactfulPerTeam`:
  - `test_empty_input_both_teams_none`
  - `test_top_player_per_team_by_cumulative_mvp`
  - `test_player_appearing_on_both_teams_attributed_per_round`
  - `test_tiebreaker_lower_player_id_wins`
  - `test_only_team_a_has_rows_returns_team_b_none`
- `TestComputePerMapBreakdown`:
  - `test_empty_input_returns_empty_list`
  - `test_one_row_per_arena_map`
  - `test_arena_map_none_labelled_no_map_3_zone`
  - `test_sorted_by_games_desc`
- `TestMarginSeries`:
  - `test_empty_input_empty_list`
  - `test_chronological_with_date_then_round_id_tiebreaker`
  - `test_returns_list_of_lists_not_tuples`
- `TestCumulativeWlSeries`:
  - `test_empty_input_empty_list`
  - `test_ties_do_not_move_running_diff`
  - `test_returns_list_of_lists_not_tuples`
- `TestNoDjangoImportsLeaked` — single test asserting `import
  matches.h2h_stats` does not pull in `django`. Mirror the HX-01 / HX-02 /
  RES-04 / RV-03 implementation exactly: walk `sys.modules` after importing
  the pure module from a fresh subprocess, assert no module whose name
  starts with `django` was loaded. (The Tests agent writes this; the Code
  agent must respect the import allowlist so the test passes.)

**Extend `matches/tests/views_tests.py`** — new class `TestHx03HeadToHead`
(Django `TestCase`):

- `test_picker_mode_both_params_missing_renders_form_200`
- `test_picker_mode_only_team_a_param_renders_form_with_a_preselected_200`
- `test_404_when_team_a_id_does_not_resolve`
- `test_404_when_team_b_id_does_not_resolve`
- `test_error_mode_when_team_a_equals_team_b_200_with_error_banner`
- `test_empty_history_results_mode_with_h2h_no_games_notice_200`
- `test_full_results_renders_match_record_round_record_margin_survivors_dom_ids`
- `test_full_results_renders_per_map_breakdown_table_with_no_map_3_zone_row`
- `test_full_results_renders_detail_list_with_unified_match_and_standalone_rounds`
- `test_full_results_renders_top_impactful_per_team_dom_ids`
- `test_charts_render_canvas_and_json_script_blocks`
- `test_provenance_param_real_filters_to_is_simulated_false`
- `test_provenance_param_sim_filters_to_is_simulated_true`
- `test_provenance_param_invalid_falls_back_to_all`
- `test_from_and_to_date_filter_applied_to_rounds_and_matches`
- `test_invalid_from_date_silently_ignored`
- `test_side_agnostic_pairing_team_a_red_in_one_match_blue_in_other`
- `test_player_who_switched_teams_appears_in_both_team_pools_per_round_attribution`
- `test_match_filter_is_completed_true_only`
- `test_match_record_excludes_match_when_provenance_real_and_either_round_is_simulated`

## Owner split (Step 7 preview)

- **Code agent** writes: `matches/h2h_stats.py` (pure module respecting the
  import allowlist), additions to `matches/views.py` (`head_to_head` view +
  all `_*` helpers), addition to `matches/urls.py` (the `path(...)`),
  `templates/matches/head_to_head.html` (NEW file), tiny edits to
  `templates/matches/match_list.html` and `templates/matches/team_history.html`
  for entry points. NO test files; NO docs. `black` the touched `.py` files;
  import / smoke-check only.
- **Tests agent** writes: `matches/tests/test_h2h_stats.py` (NEW), extends
  `matches/tests/views_tests.py` with `TestHx03HeadToHead`. NO production
  code. May run only its own new test file.
- **Docs agent** writes: marks PLAN.md HX-03 entry `- completed` + dense
  implementation note (HX-01b / RV-02 / RV-03 style); extends
  `laserforce_simulator/matches/CLAUDE.md` with an `## HX-03 head-to-head`
  subsection mirroring the RV-01 / RV-02 subsections (URL, modes, seam,
  locked DOM ids, scope-out); adds the `h2h/` URL line to the URLs ASCII
  block in `matches/CLAUDE.md`; adds the `test_h2h_stats.py` line to the
  Tests bullet list in `matches/CLAUDE.md`. CONTEXT.md is already done
  (Head-to-head record term added at grill time). No ADR. NO touch to
  production `.py` or test `.py`.

## Out of scope (locked)

- No 2nd-tier player breakdown (per-player career row inside the H2H page).
- No per-role MVP split (Most-impactful is a single per-team line — no
  per-role rows).
- No season / month / tournament filter (only `from` / `to` date range and
  `provenance`).
- No model change, no migration, no ADR (read-only view + pure aggregation
  module is fully reversible).
- No CONTEXT.md edit (the **Head-to-head record** term was added at
  grilling time and is already present in `### Analytics and review`).
- No Score Calibration re-baseline (view consumes no RNG, runs no
  simulation, never touches `_flush_to_db` — outside the SIM-07 / SIM-08
  contract surface).
- No simulation-mechanics change anywhere (`matches/sim_helpers/`,
  `matches/simulation.py`, `matches/models.py` all untouched).
- No new ORM column on `Match`, `GameRound`, or `PlayerRoundState`; no new
  serializer; no REST API surface.
- No backfill (no rounds to backfill — pure derived view).
