# HX-04 — Player Head-to-Head Record Seam Contract

This document is the locked, copy-pasteable contract for the **HX-04 Player
head-to-head record** feature. It is pasted verbatim into the parallel agent
prompts (Code / Tests / Docs). Every name, signature, and dict shape below is
**frozen** — deviations must be raised against this artifact, not silently
renamed. Mirrors the HX-03 contract structure; HX-04 is the Player analogue of
HX-03's Team-level Head-to-head record.

## Cross-check findings

Verified against the real codebase (`matches/views.py`, `matches/models.py`,
`matches/urls.py`, `matches/h2h_stats.py`, `templates/teams/player_career_stats.html`,
`CONTEXT.md`). **All locked decisions verified verbatim — no deviations.**

- **`GameEvent`** (`matches/models.py` L694+) has FK fields `actor` (related
  `events_as_actor`) and `target` (related `events_as_target`), both pointing
  at `Player` with `on_delete=CASCADE`. `event_type` is a `CharField(choices=EVENT_TYPES)`
  and `"tag"` is a real value (confirmed by `events.filter(event_type="tag").count()`
  at `models.py:226`). The tag-direction count `A→B` therefore = rows where
  `actor_id == player_a_id` AND `target_id == player_b_id`.
- **`PlayerRoundState`** (`matches/models.py` L250+) has `team_color =
  CharField` (`"red"` / `"blue"`, used throughout simulation) at L280 and
  `role = CharField` at L283 — both **per-Round** values (the "what the player
  actually played in this round" role, distinct from `Player.preferred_roles`
  which is the team-level preference list).
- **`matches/urls.py`** L38 has `path("h2h/", views.head_to_head, name="head_to_head")`
  — the existing HX-03 URL name is `head_to_head` (Team H2H). HX-04 takes the
  sibling URL name **`player_head_to_head`** (Player H2H), at path `h2h/player/`.
- **`templates/teams/player_career_stats.html`** exists and is the HX-01
  career-stats page; the existing pattern is an outline button anchor in the
  header `<div class="d-flex justify-content-between">` block (sibling to the
  existing `role-benchmarks-link` anchor at L12-17). HX-04's entry point edit
  drops in the exact same anchor pattern.
- **`matches/h2h_stats.py`** is the HX-03 pure-module precedent (8 public
  functions, frozen import allowlist `typing.Iterable`, `typing.Mapping`,
  `typing.Sequence`, `collections.defaultdict`, empty-input zeros, all return
  shapes documented). HX-04's pure module mirrors the docstring style and
  import allowlist exactly.
- **CONTEXT.md** already has the **Player head-to-head record** term added
  during the grilling session (adjacent to **Head-to-head record** near L344);
  no further CONTEXT.md edit by the Docs agent.

## Background

HX-04 is a read-only analytics surface that aggregates the head-to-head history
between two **Players** — restricted to Rounds where both Players appeared on
**opposite teams**. The view mirrors the HX-03 four-mode pattern and reuses
the RV-01 `picker` / `404` / `error` / `results` skeleton.

The view is **read-only**: no RNG, no simulation, no `_flush_to_db` touch, no
SIM-07/SIM-08 contract interaction, no Score Calibration re-baseline. No model
change, no migration, no ADR.

## Locked domain decisions

- **Corpus:** every `GameRound` where both Players appeared with **different
  `PlayerRoundState.team_color`** (i.e. on opposite teams). Same-team Rounds
  are excluded entirely — no fallback display, no "of which N on the same
  team" footnote.
- **Round record:** W/L/T per Round across the opposite-teams basket. A
  Round's winner from player_a's perspective is the higher-scoring side of
  the team they played on that Round; equal scores = tie.
- **Score margin:** mean of `(player_a_team_score − player_b_team_score)` per
  Round across the opposite-teams basket, signed from player_a's perspective.
- **Tag aggregation:** per-Round mean of A→B and B→A
  `GameEvent(event_type="tag")` counts (two symmetric floats — `avg_tags_a_to_b`
  and `avg_tags_b_to_a`). Also exposed: raw totals (`total_tags_a_to_b` /
  `total_tags_b_to_a`) and `n`.
- **Role filter:** single `?role=<role>` query param with **'both' semantics**
  — when set, filter the basket to Rounds where **both** Players played that
  role (per-Round `PlayerRoundState.role`, NOT `Player.preferred_roles`).
  Default = no role filter (any).
- **Other filters:** mirror HX-03 — `?provenance=all|real|sim` (default
  `all`, invalid silently falls back to `all` — HX-02 forgiving-fallback
  precedent) filtering `GameRound.is_simulated`; `?from=YYYY-MM-DD&to=YYYY-MM-DD`
  (both optional, invalid silently ignored, treated as unbounded that side)
  filtering `GameRound.date_played`.
- **Per-Round attribution:** per-Round `team_color` resolved against the
  Round's actual `team_red_id` / `team_blue_id` — a player who switched
  teams between H2H Rounds is handled naturally by the opposite-teams gate
  (each Round is independently evaluated).
- **Empty basket:** `mode='results'` (NOT error), all aggregates render as
  zeros, single notice block DOM id `player-h2h-no-games-notice`. Detail
  list / charts / per-role / per-map sections omit when empty.
- **No model change, no migration, no ADR.** Read-only view, consumes no RNG,
  no simulation, no SIM-07/08 contract interaction, no Score Calibration
  re-baseline.

## Query params

- `?player_a=<int>&player_b=<int>` — both required for results; either missing
  → picker.
- `?role=<role>` — optional. Single role string (e.g. `"scout"`); empty / not
  supplied → no role filter. When set, basket restricted to Rounds where
  **both** Players have `PlayerRoundState.role == role` (both-semantics, locked).
  Invalid role string (not in the canonical role list) → silently ignored
  (treated as no filter — HX-02 forgiving-fallback precedent).
- `?provenance=all|real|sim` (default `all`) — filters `GameRound.is_simulated`
  (`real` ⇒ `False`, `sim` ⇒ `True`, `all` ⇒ no filter). Invalid silently
  falls back to `all`.
- `?from=YYYY-MM-DD&to=YYYY-MM-DD` — both optional, default unbounded, invalid
  silently ignored. Filters `GameRound.date_played`.

## View modes (RV-01 pattern, mirrors HX-03)

- `picker` — either `player_a` or `player_b` query param missing → render two
  `<select>` chooser + filter controls (HTTP 200).
- `404` — supplied player id does not resolve → `get_object_or_404`.
- `error` — `player_a_id == player_b_id` → `mode="error"` + `error_message`,
  picker re-renders above banner, HTTP 200.
- `results` — both ids valid + distinct → full render (including the empty-
  basket sub-case with zeroed aggregates + `player-h2h-no-games-notice`).

## Module structure

**New pure module:** `laserforce_simulator/matches/player_h2h_stats.py`

- **Frozen import allowlist:** `typing.Iterable`, `typing.Mapping`,
  `typing.Sequence`, `collections.defaultdict`. NO Django, NO ORM, NO RNG,
  NO I/O, NO model imports. Pinned by `TestNoDjangoImportsLeaked` defensive
  check (HX-01 / HX-02 / HX-03 / RES-04 / RV-03 precedent).
- Mirror the HX-03 / HX-01 / HX-02 docstring style.

**View:** `matches/views.py::player_head_to_head(request)` with module-level
helpers (RV-01 pattern), routed via `matches/urls.py`
`path("h2h/player/", views.player_head_to_head, name="player_head_to_head")`.

## Seam: 1 flat dict list (view → pure module)

The opposite-teams gate, tag-direction grouping, and team-attribution all
happen view-side. The pure module receives a single normalised list keyed
from player_a's perspective.

**`rounds_list: list[dict]`** — one entry per Round in the opposite-teams
basket (after all filters applied), already normalised from player_a's
perspective by the view. **EXACTLY 12 keys:**

```python
{
    "round_id": int,
    "date_played": datetime,
    "player_a_team_score": int,     # score of the team player_a played on
    "player_b_team_score": int,     # score of the team player_b played on
    "tags_a_to_b": int,             # GameEvent(actor=A, target=B, event_type="tag") count for this Round
    "tags_b_to_a": int,             # GameEvent(actor=B, target=A, event_type="tag") count for this Round
    "role_a": str,                  # PlayerRoundState.role for player_a in this Round
    "role_b": str,                  # PlayerRoundState.role for player_b in this Round
    "match_id": int | None,         # None = standalone Round
    "arena_map_id": int | None,
    "arena_map_name": str | None,   # None when arena_map is null
    "is_simulated": bool,
}
```

## Pure module public surface

All signatures and return shapes below are frozen. Empty-input branches must
return zeros (or `[]` / `None`), never raise.

```python
def compute_round_record(rounds_list: Iterable[Mapping]) -> dict:
    """W/L/T per Round across the opposite-teams basket.

    Returns ``{"wins": int, "losses": int, "ties": int, "n": int}``.
    ``player_a_team_score > player_b_team_score`` → wins; ``<`` → losses;
    ``==`` → ties.
    """

def compute_score_margin(rounds_list: Iterable[Mapping]) -> dict:
    """Mean signed margin ``(player_a_team_score − player_b_team_score)`` per Round.

    Returns ``{"mean_margin": float, "n": int}``. Empty input ⇒
    ``{"mean_margin": 0.0, "n": 0}`` (no div-by-zero).
    """

def compute_tag_stats(rounds_list: Iterable[Mapping]) -> dict:
    """Per-Round mean of A→B and B→A tag counts plus raw totals.

    Returns ``{
        "avg_tags_a_to_b": float,
        "avg_tags_b_to_a": float,
        "total_tags_a_to_b": int,
        "total_tags_b_to_a": int,
        "n": int,
    }``. Empty input ⇒ all zeros (floats 0.0, ints 0), no div-by-zero.
    """

def compute_per_role_breakdown(rounds_list: Iterable[Mapping]) -> list[dict]:
    """Per-role W/L/T + margin table.

    Bucket key is ``role_a`` from player_a's perspective — one row per
    distinct ``role_a`` observed (rows where player_a played that role,
    regardless of what player_b played; this is the *display* breakdown,
    not the 'both played role X' filter which is a query-param concern).

    Returns ``list[{
        "role": str,
        "games": int,
        "wins": int, "losses": int, "ties": int,
        "mean_margin": float,
        "avg_tags_a_to_b": float,
        "avg_tags_b_to_a": float,
    }]``, sorted by ``games`` desc; tiebreaker ``role`` asc. Empty input ⇒ ``[]``.
    """

def compute_per_map_breakdown(rounds_list: Iterable[Mapping]) -> list[dict]:
    """Per-``arena_map`` W/L/T + margin table.

    One entry per ``arena_map_id`` observed (including a single entry for
    ``arena_map_id=None`` labelled ``"No map (3-zone)"``, mirroring HX-03).

    Returns ``list[{"arena_map_id": int|None, "arena_map_name": str,
    "games": int, "wins": int, "losses": int, "ties": int,
    "mean_margin": float}]``, sorted by ``games`` desc; tiebreaker
    ``arena_map_id`` ascending with ``None`` last. Empty input ⇒ ``[]``.
    """

def margin_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — signed margin per Round chronologically.

    Returns ``[[round_idx_1based, signed_margin_int], ...]`` sorted by
    ``(date_played, round_id)`` ascending. ``list[list]`` (not
    ``list[tuple]``) for ``json_script`` serialisation. Empty input ⇒ ``[]``.
    """

def cumulative_wl_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — cumulative ``(player_a_wins − player_b_wins)`` Round-level.

    Returns ``[[round_idx_1based, cum_diff], ...]`` sorted by
    ``(date_played, round_id)`` ascending. Ties don't move the running diff.
    Empty input ⇒ ``[]``.
    """
```

## View module-level helpers

All helpers live in `matches/views.py` (RV-01 pattern — flat module-level
helpers prefixed `_`, no class). Reuse the HX-03 `_parse_provenance` /
`_parse_date` helpers in-place (don't duplicate).

- `_player_h2h_rounds_qs(player_a, player_b, provenance, date_from, date_to) -> QuerySet[GameRound]` —
  filters to Rounds where **both** Players have a `PlayerRoundState` row;
  applies date + provenance filters. **Does NOT apply the opposite-teams
  gate or role filter** — those happen view-side after the per-Round
  `team_color` / `role` resolution.
- `_normalize_player_round(game_round, prs_a, prs_b) -> dict | None` —
  returns the `rounds_list` shape (the 12-key dict above) keyed from
  player_a's perspective by reading the two `PlayerRoundState` rows;
  returns **`None`** if `prs_a.team_color == prs_b.team_color` (same-team
  gate — caller filters out the `None` rows). Flips `player_a_team_score`
  / `player_b_team_score` based on each PRS's `team_color` against the
  Round's `team_red_id` / `team_blue_id`.
- `_build_player_h2h_tag_counts(rounds_qs, player_a_id, player_b_id) -> dict[int, tuple[int, int]]` —
  **single ORM iterate query** (the locked tag-ORM strategy):
  ```python
  GameEvent.objects.filter(
      game_round__in=rounds_qs,
      event_type="tag",
      actor_id__in={player_a_id, player_b_id},
      target_id__in={player_a_id, player_b_id},
  ).values_list("game_round_id", "actor_id", "target_id")
  ```
  Groups in Python into `{round_id: (tags_a_to_b, tags_b_to_a)}`. **NOT**
  two separate `.annotate(Count())` calls.
- `_filter_by_role_both(rounds_list, role) -> list[dict]` — applies the
  both-semantics `?role=` filter on already-normalised dicts: returns rows
  where `row["role_a"] == role AND row["role_b"] == role`. When `role` is
  `None` / empty / invalid, returns the input unchanged.
- `_build_player_h2h_detail_list(rounds_list) -> list[dict]` — reverse-
  chronological list, one row per Round in the basket, carrying display
  fields (round_id, date_played, role_a, role_b, player_a_team_score,
  player_b_team_score, tags_a_to_b, tags_b_to_a, match_id, arena_map_name,
  is_simulated, detail URL).
- `player_head_to_head(request) -> HttpResponse` — assembles everything,
  picks the mode, renders.

## URL routing

In `matches/urls.py`, add:

```python
path("h2h/player/", views.player_head_to_head, name="player_head_to_head"),
```

## Context keys (results mode — frozen)

```
mode                  # "picker" | "404" (raised, not rendered) | "error" | "results"
error_message         # str | None — only set in "error" mode
player_a              # Player | None
player_b              # Player | None
all_players           # QuerySet[Player] ordered by name — for picker dropdowns
role                  # str | None — the active ?role= filter (None when unset/invalid)
provenance            # "all" | "real" | "sim"
date_from             # date | None
date_to               # date | None
round_record          # dict from compute_round_record (or zeros)
score_margin          # dict from compute_score_margin
tag_stats             # dict from compute_tag_stats
per_role_breakdown    # list[dict] from compute_per_role_breakdown
per_map_breakdown     # list[dict] from compute_per_map_breakdown
detail_list           # list[dict] from _build_player_h2h_detail_list
margin_series         # list[list] from margin_series
cumulative_wl_series  # list[list] from cumulative_wl_series
```

Picker mode and error mode still include `all_players`, `role`, `provenance`,
`date_from`, `date_to` so the form re-renders with the user's prior selections;
aggregates can be omitted (template gates on `mode == "results"`).

## Template

**Path:** `laserforce_simulator/templates/matches/player_head_to_head.html`.
Extends `base.html`.

**Locked DOM ids** (every template id is testable via substring):

Picker form:
- `player-h2h-picker-form` (form), `player-h2h-select-a`, `player-h2h-select-b`,
  `player-h2h-role`, `player-h2h-provenance`, `player-h2h-from`, `player-h2h-to`,
  `player-h2h-submit`.

Results — headline:
- `player-h2h-round-record` (wraps "W-L-T"), `player-h2h-score-margin`,
  `player-h2h-tags-a-to-b`, `player-h2h-tags-b-to-a`.

Results — sections:
- `player-h2h-per-role-table`, `player-h2h-per-map-table`,
  `player-h2h-detail-list`, `player-h2h-no-games-notice` (only rendered when
  `round_record.n == 0`).

Charts:
- canvas ids `player-h2h-margin-chart`, `player-h2h-cumulative-wl-chart`.
- `json_script` ids `player-h2h-margin-series` (renders `margin_series`),
  `player-h2h-cumulative-wl-series` (renders `cumulative_wl_series`).

Error banner:
- `player-h2h-error-banner` containing `{{ error_message }}`.

**Chart.js:** mirror HX-03's `head_to_head.html` overlay pattern. Margin chart
= stepped line with zero reference. Cumulative chart = stepped line, no
reference.

**Time display:** `date_played` rendered via Django's `|date:"Y-m-d H:i"`
filter (real wall-clock — not tick conversion).

## Entry point

**Career page only** (per locked decisions — no top-nav / match_list / team
history edit). Single template edit:

- `templates/teams/player_career_stats.html`: add a "Head-to-head" outline
  button anchor in the header `<div class="d-flex justify-content-between">`
  block, sibling to the existing `role-benchmarks-link` anchor. URL:
  `{% url 'player_head_to_head' %}?player_a={{ player.id }}` (pre-fills the
  player_a slot only; picker prompts for player_b).

## Tests

**New `matches/tests/test_player_h2h_stats.py`** — pure-unit, no Django
imports, no DB, built from hand-crafted dict-list seam inputs. Class names +
test names locked:

- `TestComputeRoundRecord`:
  - `test_empty_input_returns_zeros`
  - `test_higher_player_a_team_score_is_win`
  - `test_lower_is_loss`
  - `test_equal_is_tie`
- `TestComputeScoreMargin`:
  - `test_empty_input_zero_no_div_by_zero`
  - `test_signed_mean_from_player_a_perspective`
  - `test_negative_margin_when_player_b_team_dominates`
- `TestComputeTagStats`:
  - `test_empty_input_all_zeros_no_div_by_zero`
  - `test_per_round_means_and_raw_totals`
  - `test_asymmetric_tag_direction_a_to_b_vs_b_to_a`
- `TestComputePerRoleBreakdown`:
  - `test_empty_input_returns_empty_list`
  - `test_one_row_per_role_a`
  - `test_sorted_by_games_desc_role_asc_tiebreaker`
  - `test_per_role_margin_and_tag_means_match_subset`
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
  matches.player_h2h_stats` does not pull in `django`. Mirror the HX-01 /
  HX-02 / HX-03 / RES-04 / RV-03 implementation exactly: walk `sys.modules`
  after importing the pure module from a fresh subprocess, assert no module
  whose name starts with `django` was loaded.

**Extend `matches/tests/views_tests.py`** — new class `TestHx04PlayerHeadToHead`
(Django `TestCase`). Locked test names (covers all locked behaviours: 4
modes, opposite-teams gate, role both-semantics, tag direction, charts,
per-role / per-map sections, forgiving-fallback variants, side-agnostic
team_color attribution):

- `test_picker_mode_both_params_missing_renders_form_200`
- `test_picker_mode_only_player_a_param_renders_form_with_a_preselected_200`
- `test_404_when_player_a_id_does_not_resolve`
- `test_404_when_player_b_id_does_not_resolve`
- `test_error_mode_when_player_a_equals_player_b_200_with_error_banner`
- `test_empty_basket_results_mode_with_player_h2h_no_games_notice_200`
- `test_same_team_rounds_are_excluded_from_basket`
- `test_opposite_teams_round_included_in_basket`
- `test_full_results_renders_round_record_margin_tag_dom_ids`
- `test_full_results_renders_per_role_breakdown_table`
- `test_full_results_renders_per_map_breakdown_table_with_no_map_3_zone_row`
- `test_full_results_renders_detail_list_reverse_chronological`
- `test_charts_render_canvas_and_json_script_blocks`
- `test_tag_direction_a_to_b_distinct_from_b_to_a`
- `test_role_filter_both_semantics_includes_round_when_both_players_played_role`
- `test_role_filter_both_semantics_excludes_round_when_only_one_player_played_role`
- `test_role_filter_invalid_role_silently_ignored_no_filter_applied`
- `test_provenance_param_real_filters_to_is_simulated_false`
- `test_provenance_param_sim_filters_to_is_simulated_true`
- `test_provenance_param_invalid_falls_back_to_all`
- `test_from_and_to_date_filter_applied_to_rounds`
- `test_invalid_from_date_silently_ignored`
- `test_side_agnostic_team_color_attribution_player_a_red_in_one_round_blue_in_another`
- `test_career_page_anchor_links_to_player_head_to_head_with_player_a_prefilled`

## Owner split (Step 7 preview)

- **Code agent** writes: `matches/player_h2h_stats.py` (NEW pure module
  respecting the import allowlist), additions to `matches/views.py`
  (`player_head_to_head` view + all `_*` helpers — reusing existing
  `_parse_provenance` / `_parse_date` HX-03 helpers in-place), addition to
  `matches/urls.py` (the `path(...)`),
  `templates/matches/player_head_to_head.html` (NEW file), tiny edit to
  `templates/teams/player_career_stats.html` for the header anchor entry
  point. NO test files; NO docs. `black` the touched `.py` files; import /
  smoke-check only.
- **Tests agent** writes: `matches/tests/test_player_h2h_stats.py` (NEW),
  extends `matches/tests/views_tests.py` with `TestHx04PlayerHeadToHead`.
  NO production code. May run only its own new test file.
- **Docs agent** writes: marks PLAN.md HX-04 entry `- completed` + dense
  implementation note (HX-03 / HX-01b / RV-02 / RV-03 style); extends
  `laserforce_simulator/matches/CLAUDE.md` with an `## HX-04 player
  head-to-head` subsection mirroring the existing `## HX-03 head-to-head`
  subsection (URL, modes, seam, locked DOM ids, scope-out); adds the
  `h2h/player/` URL line to the URLs ASCII block in `matches/CLAUDE.md`;
  adds the `test_player_h2h_stats.py` line to the Tests bullet list in
  `matches/CLAUDE.md`. CONTEXT.md is **already done** (the **Player head-to-head
  record** term was added inline during the grilling session). No ADR.
  NO touch to production `.py` or test `.py`.

## Out of scope (locked)

- **Same-team Rounds excluded entirely** — no fallback display, no "of which
  N on the same team" footnote.
- **No MVP / most-impactful surface** — that's HX-03 only. HX-04 is a pure
  pairwise-comparison view; the per-team MVP concept doesn't transfer to a
  two-player comparison.
- **No per-player-per-role asymmetric matchup view** (e.g. "what happens
  when player_a scouts and player_b heavies") — deferred. The `?role=`
  filter is symmetric ('both' semantics) only.
- **No seasonal / tournament / month filter** — only `?from` / `?to` date
  range and `?provenance`.
- **No model change, no migration, no ADR** (read-only view + pure
  aggregation module is fully reversible).
- **No CONTEXT.md edit** by Docs agent (term added inline at grilling time).
- **No simulation-mechanics change** anywhere (`matches/sim_helpers/`,
  `matches/simulation.py`, `matches/models.py` all untouched).
- **No new ORM column** on `Player`, `GameRound`, `PlayerRoundState`, or
  `GameEvent`; no new serializer; no REST API surface.
- **No backfill** (no rounds to backfill — pure derived view).
- **No Score Calibration re-baseline** (view consumes no RNG, runs no
  simulation, never touches `_flush_to_db` — outside the SIM-07 / SIM-08
  contract surface).
- **No top-nav / match_list / team-history entry point** — career page only
  (the single locked entry point).
