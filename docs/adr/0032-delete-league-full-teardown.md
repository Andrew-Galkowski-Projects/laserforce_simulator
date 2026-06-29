# Delete League — full teardown of league-owned data

**Status:** Accepted (DEL-01, 2026-06-28)

## Context

There is no Delete-League surface outside Django admin. DEL-01 adds a guarded
`POST /leagues/<int:league_id>/delete/` with a confirm step. The PLAN's one-line
description assumed deletion could "rely on the existing FK `on_delete` rules to
cascade out Seasons / `SeasonPhase`s / season-scoped Matches (sandbox Matches
`SET_NULL` survive)." Grilling the model against that wording surfaced three gaps,
because a career League (`league_create`) spawns far more than the cascade reaches:

1. **`Match.season` is `SET_NULL`, not `CASCADE`** (`matches/models.py:59-63`).
   Cascading a League deletes its Seasons and (via `Season` CASCADE) its
   `SeasonPhase`s, `PlayerSeasonRating`s, `TeamSeasonFinance`s, and
   `OwnerEvaluation`s — but the season's **played Matches survive orphaned** with
   `season=NULL`, indistinguishable from real sandbox matches, and still feed
   player career stats. The plan's "cascade out season-scoped Matches" is
   contradicted by its own FK.

2. **Season-embedded playoff Tournaments are not reached at all.** A career
   league's playoff games live in standalone `Tournament` rows linked via
   `SeasonPhase.tournament` (`SET_NULL`). Cascading the Season deletes the
   `SeasonPhase` and merely *orphans* the Tournament; its bracket Matches carry
   `season=NULL` (so they dodge any `season`-scoped filter), and `SeriesMatch.match`
   is itself `SET_NULL`, so even deleting the Tournament would leave the bracket
   Matches behind.

3. **Generated Teams + Players are not FK-owned by the League.** `current_team` and
   `free_agent_pool` are `SET_NULL` FKs *on* the League (deleting the League just
   drops those rows; the Teams survive); `Season.teams` is M2M. Nothing in the
   schema cascades the N generated competitive Teams, the free-agent-pool Team, or
   their hundreds of Players. A pure FK-cascade delete leaves all of them behind.

The forcing question grilled was how much DEL-01 should tear down: lean on the FK
rules and accept orphans, or take ownership of the data the schema does not own.

## Decision

**Delete League performs a full teardown of all data the League owns, identified by
PK/FK identity, in a single `@transaction.atomic` block.**

1. **Explicitly delete the league's Matches** — not just rely on `Match.season`
   `SET_NULL`. `Match` → `GameRound` → `GameEvent` / `PlayerRoundState` all CASCADE,
   so deleting the Match rows removes the game data cleanly. No league game orphans
   into the sandbox match list or player career stats.

2. **Full teardown of season-embedded Tournaments.** Before the cascade destroys the
   link, collect the embedded tournament ids
   (`SeasonPhase.objects.filter(season__league=league, tournament__isnull=False)
   .values_list("tournament_id", flat=True)`), delete their bracket Matches (reachable
   via `series_match__node__tournament`), then delete the `Tournament` rows
   (cascading participants / nodes / `SeriesMatch`). No orphaned playoff bracket or
   playoff games remain in `/tournaments/`.

3. **Delete the league's Teams + Players** (Players cascade with their Team via the
   single-CASCADE `Player.team`). This rests on the domain invariant — now written
   into the CONTEXT.md **Team** / **Player** entries — that **a Team and its Players
   belong to exactly one context (the sandbox or a single League) and are never
   shared**. The invariant is convention, not schema-enforced, so:

   - **Identify owned Teams by PK/FK, never by name.** Team and Player names may
     legitimately overlap across contexts; only the FK relationships are reliable.
     The candidate set is the Teams reached by this League's FKs — `Season.teams` of
     its Seasons, `current_team`, `free_agent_pool`, and drawn Teams of its embedded
     tournaments (`TournamentPlayerEntry.drawn_team`).

   - **Defensive post-teardown zero-reference guard.** After steps 1–2 remove the
     league's Seasons / Matches / Tournaments, a candidate Team is deleted **only if**
     it then has zero remaining `red_matches` / `blue_matches`, is enrolled in no
     surviving `Season`, and is no surviving League's `current_team` /
     `free_agent_pool`. Given the one-context invariant this guard is expected to
     pass for every candidate; it exists so that the one unenforced edge (a sandbox
     tournament that "selected existing" a league Team, or a Team mistakenly shared)
     can never CASCADE a foreign Match or foreign career row. Anything still
     referenced is left behind — safe over complete.

4. **Atomic + guarded view shell.** The whole teardown runs in one
   `@transaction.atomic`; the view mirrors the league-screen shell — POST-only with
   the `HttpResponseNotAllowed` 405-guard, `get_object_or_404`, a confirm step, and a
   redirect to the leagues list (`league_list`) on success — and follows the existing
   `player_delete` confirm-page precedent.

### Rejected alternative — rely on FK cascade, leave orphans

Do nothing beyond what the FK rules already do: cascade Seasons / phases / career
snapshots, let Matches survive `season=NULL`, leave Tournaments and Teams behind.
Rejected at grilling: a "Delete League" that silently dumps the league's played games
into the sandbox match list, leaves its playoff brackets in `/tournaments/`, and
abandons dozens of generated teams + hundreds of players in `/teams/` and `/players/`
is not a delete — it is a rename-to-orphan. The user's intent for the button is a
clean removal, which the schema's FK graph cannot express on its own.

## Consequences

- **A deleted League leaves no trace.** Its Seasons, phases, career snapshots
  (ratings / finances / owner evaluations), regular-season + playoff Matches and their
  GameRounds/events, embedded Tournament brackets, and generated Teams + Players are
  all gone. The sandbox match list, `/tournaments/`, `/teams/`, and `/players/` are
  unaffected by the deleted league.

- **The teardown owns work the schema does not.** Because Teams/Players have no
  League FK and Matches/Tournaments use `SET_NULL`, the view must collect ids before
  the cascade runs and delete in the right order (Seasons/Matches/Tournaments →
  then guarded Teams). This is more delete logic + more tests than a one-line
  `league.delete()`, and is the accepted price of a real delete.

- **Correctness rests on the one-context ownership invariant.** Documented in
  CONTEXT.md (**Team** / **Player**). If that invariant is ever broken (a Team
  deliberately shared across Leagues), the zero-reference guard degrades gracefully —
  the shared Team is left behind rather than cascading a foreign Match — but the
  league deletion is then *incomplete* rather than *destructive*, which is the
  intended failure direction.

- **Identification is by PK/FK, never name.** Any future code resolving "which
  Teams/Players belong to this League" must follow the same rule — name matching is a
  latent bug because names overlap across contexts.

- **No model change, no migration, no simulation interaction.** DEL-01 is a view +
  confirm-template + URL addition over the existing FK graph: no schema change, no
  RNG, no `BatchSimulator` touch, and **no Score Calibration re-baseline obligation**.
