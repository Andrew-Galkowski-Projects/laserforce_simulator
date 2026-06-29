"""LG-07a — pure member-night balanced-split + game-count / pool draws.

Owns the balanced 6/6 split and the per-Site game draws for a member night
(CONTEXT.md **Member night**), as plain data. No Django / ORM / I/O / logging.

Frozen import allowlist (the ONLY modules this file may import):
``dataclasses``, ``typing``, ``random``, ``collections``. NO ``django.*``, NO
``datetime``, NO file I/O, PLUS the single in-module import
``from matches.draw import build_random_role_assignment`` — ``matches/draw.py``
is itself a frozen pure module (imports only ``random`` + ``dataclasses``), so
importing it leaks NO Django. Enforced by a ``TestNoDjangoImportsLeaked``
subprocess fresh-import + ``sys.modules`` walk (mirrors ``matches/draw.py`` /
``matches/bracket.py`` / ``matches/standings.py``).

``random`` is allowlisted because the draws consume an INJECTED
``random.Random`` (the run builds a FRESH ``random.Random()`` per run; seeded
tests inject a seeded one to pin the consumption ORDER). The balanced split
itself consumes NO RNG (deterministic greedy balance).
"""

import random
from dataclasses import dataclass

from matches.draw import build_random_role_assignment

# --- Constants (LOCKED, tunable) -----------------------------------------
MIN_POOL = 12  # a Site is VIABLE iff it has >= MIN_POOL available players
MAX_POOL = 18  # per-run cap: a Site pool larger than this is randomly
#                down-sampled to MAX_POOL players ("whoever shows up")
MIN_GAMES = 5  # inclusive lower bound on the per-Site game count draw
MAX_GAMES = 9  # inclusive upper bound on the per-Site game count draw
PLAYERS_PER_GAME = 12  # each game draws exactly 12 players, split 6 / 6


@dataclass(frozen=True)
class MemberNightGame:
    """One drawn member-night game (the pure-module ↔ view seam).

    ``team_a`` / ``team_b`` are the FINAL role assignments (slot suffix → real
    player id) over the 6 ROLE_SLOTS — the view writes them straight onto the
    two drawn Team rows' ``slot_*`` FKs. The pure module never sees a Django
    object; it consumes ``(player_id, overall_rating)`` tuples and returns
    these. Roles are assigned ONCE here (no per-Round re-draw hook).
    """

    site: str  # the Site this game was drawn for
    game_index: int  # 0-based index within the whole run (all sites)
    team_a: dict[str, int]  # {slot_suffix: player_id} over the 6 ROLE_SLOTS
    team_b: dict[str, int]  # {slot_suffix: player_id} over the 6 ROLE_SLOTS


def split_balanced(players: list[tuple[int, float]]) -> tuple[list[int], list[int]]:
    """Attempt-balanced 6/6 split of EXACTLY 12 players. Consumes NO RNG.

    ``players`` is 12 ``(player_id, overall_rating)`` tuples. Sort by
    overall_rating DESC then player_id ASC; greedily assign each next-strongest
    player to the team with the lower running total rating (team A index
    tiebreak on equal totals), forcing the remainder onto the other team once a
    side fills to 6. Returns ``(team_a_ids, team_b_ids)``, 6 ids each. Raises
    ``ValueError`` if ``len(players) != 12``. Does NOT stack the strong players
    on one side (the compute_draw greedy-balance shape, 2 teams of 6).
    """
    if len(players) != 12:
        raise ValueError(
            f"split_balanced requires exactly 12 players; got {len(players)}"
        )

    ordered = sorted(players, key=lambda pr: (-pr[1], pr[0]))

    team_a_ids: list[int] = []
    team_b_ids: list[int] = []
    total_a = 0.0
    total_b = 0.0
    for player_id, rating in ordered:
        a_full = len(team_a_ids) >= 6
        b_full = len(team_b_ids) >= 6
        if a_full:
            team_b_ids.append(player_id)
            total_b += rating
        elif b_full:
            team_a_ids.append(player_id)
            total_a += rating
        elif total_a <= total_b:
            team_a_ids.append(player_id)
            total_a += rating
        else:
            team_b_ids.append(player_id)
            total_b += rating
    return team_a_ids, team_b_ids


def draw_site_games(
    site: str,
    pool: list[tuple[int, float]],
    rng: random.Random,
    start_index: int,
) -> list[MemberNightGame]:
    """One Site's games. Returns ``[]`` when ``len(pool) < MIN_POOL`` (NOT viable).

    ``pool`` is the Site's ``(player_id, overall_rating)`` list; ``start_index``
    is the running global ``game_index``. RNG-consumption order (LOCKED):

    1. Down-sample — ``run_pool = pool`` when ``len(pool) <= MAX_POOL``, else
       ``rng.sample(pool, MAX_POOL)`` (ONE ``rng.sample``).
    2. Game count — ``rng.randint(MIN_GAMES, MAX_GAMES)`` (ONE ``rng.randint``).
    3. Per game, in order: ``rng.sample(run_pool, 12)`` → ``split_balanced``
       (NO RNG) → ``build_random_role_assignment`` per team (ONE shuffle each).

    A non-viable Site consumes NO RNG; a Site under MAX_POOL skips step 1's
    sample. So one Site of n games consumes
    ``[sample?, randint] + n × [sample, shuffle, shuffle]``.
    """
    if len(pool) < MIN_POOL:
        return []

    if len(pool) <= MAX_POOL:
        run_pool = list(pool)
    else:
        run_pool = rng.sample(pool, MAX_POOL)

    n_games = rng.randint(MIN_GAMES, MAX_GAMES)

    games: list[MemberNightGame] = []
    for game in range(n_games):
        twelve = rng.sample(run_pool, PLAYERS_PER_GAME)
        team_a_ids, team_b_ids = split_balanced(twelve)
        team_a = build_random_role_assignment(team_a_ids, rng)
        team_b = build_random_role_assignment(team_b_ids, rng)
        games.append(
            MemberNightGame(
                site=site,
                game_index=start_index + game,
                team_a=team_a,
                team_b=team_b,
            )
        )
    return games


def draw_member_night_games(
    pool_by_site: dict[str, list[tuple[int, float]]],
    rng: random.Random,
) -> list[MemberNightGame]:
    """The whole run. Iterates Sites in SORTED (site name ASC) order so the RNG
    consumption order is deterministic for a seeded rng. For each Site, calls
    the per-Site draw and appends its games (assigning the global
    ``game_index`` in append order). Returns the flat list across all Sites.
    """
    games: list[MemberNightGame] = []
    for site in sorted(pool_by_site):
        games.extend(draw_site_games(site, pool_by_site[site], rng, len(games)))
    return games
