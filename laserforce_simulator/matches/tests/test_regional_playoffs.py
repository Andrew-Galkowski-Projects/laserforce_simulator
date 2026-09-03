"""CONF-02 — per-Conference regional playoffs: model linkage, build, seeding,
the completion gate, the 0/1-Conference regression pins, and the Team-History
amendment.

The seam contract is locked at ``.claude/worktrees/conf-02-seam-contract.md``;
the design rationale is
[ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md)
and the CONTEXT.md **Regional playoff** / **Conference champion** terms.

What is asserted here (contract §9.4 items 1-9 plus the §11.4 amendment):

* **N tournaments built** — a ``>= 2``-Conference Season's ``tournament``
  phase builds one first-class ``Tournament`` per Conference, linked by the new
  ``Tournament.season_phase`` / ``Tournament.conference`` FKs, with
  ``SeasonPhase.tournament`` left NULL.
* **Per-Conference participants + seeds** — each bracket holds exactly its own
  Conference's snapshot teams, seeded ``1..n`` restarting at 1 in each bracket,
  with no cross-Conference participant or bracket-node pairing anywhere.
* **Conference-scoped seeding** — a team that leads the SEASON-WIDE table but
  is not its own Conference's leader must NOT take seed 1 in that Conference's
  bracket. All three ``tournament_mode`` values split.
* **The completion gate** — the Season stays ``active`` until EVERY regional
  bracket has drained, then flips to ``completed`` with ``champion_team`` still
  NULL (a Conference champion is not a Season champion).
* **The 0/1-Conference regression pins** — the load-bearing proof that CONF-02
  is additive: one Season-wide bracket, still on ``SeasonPhase.tournament``,
  still stamping ``Season.champion_team``.
* **Team History (§11.4)** — a regional Tournament now counts toward
  ``playoff_appearances`` and its Rounds enter the Overall-tab corpus exactly
  once, while ``championships`` stays 0.

**No simulation (contract §9.1).** Every fixture here is hand-built: the
round-robin is a deterministic set of completed ``Match`` + ``GameRound`` rows
(so the Standings order — and therefore every seed — is exact, not sampled),
and brackets are "drained" by stamping ``Tournament.champion`` /
``state="completed"`` on the persisted rows. Nothing patches the seam under
test. Assertions are schema-level: row counts, ids, seeds, states, booleans —
never simulated point totals.

**Naming hazard (contract §2.1).** ``Tournament.season_phase`` is the NEW
forward FK (reverse: ``phase.regional_tournaments``);
``Tournament.season_phases`` is the PRE-EXISTING reverse manager of
``SeasonPhase.tournament``. A regional Tournament has ``season_phase_id`` set
and an EMPTY ``season_phases``; a Season-wide embedded one is the exact
opposite. Both directions are pinned below.

NOTE: this file requires the Code agent's ``Tournament`` linkage FKs +
migration ``0058_tournament_regional_linkage`` + the ``Season`` seam
(``tournaments_for_phase`` / ``_build_tournament_for_phase`` / the
``activate_pending_tournament_phase`` branch) + the Team-History amendment to
land. Until then these tests are EXPECTED to fail — the TDD red state, not a
defect in this file.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import (
    BracketNode,
    Conference,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
    SeriesMatch,
    Tournament,
    TournamentParticipant,
)
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Fixtures — hand-built, deterministic, ZERO simulation (contract §9.1)
# ---------------------------------------------------------------------------


def _conf_season(
    prefix: str,
    sizes: list[int],
    *,
    mode: str = "standings",
    cut: int = 0,
    fmt: str = "single_elimination",
    extra_tournament_phases: int = 0,
):
    """An active Season: RR phase (ordinal 1) + tournament phase (ordinal 2).

    ``sizes[i]`` fully-slotted Teams are enrolled in Conference ``i+1``
    (ordinal ``i+1``); an empty ``sizes`` list makes a ZERO-Conference Season
    whose teams are enrolled flat. Returns
    ``(season, conferences, groups, rr_phase, tournament_phase)`` where
    ``groups[i]`` is Conference ``i+1``'s Team list (or, for the
    zero-Conference shape, the single flat Team list).
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="2027", start_date=date(2027, 1, 1)
    )
    conferences: list[Conference] = []
    groups: list[list] = []

    if not sizes:
        raise ValueError("use _flat_season for a zero-Conference Season")

    for ci, size in enumerate(sizes, start=1):
        conference = Conference.objects.create(
            season=season, name=f"{prefix}Conf{ci}", ordinal=ci
        )
        teams = []
        for ti in range(size):
            team, _players = make_team_with_slots(f"{prefix}{ci}x{ti}")
            season.teams.add(team)
            conference.teams.add(team)
            teams.append(team)
        conferences.append(conference)
        groups.append(teams)

    rr_phase = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    tournament_phase = SeasonPhase.objects.create(
        season=season,
        ordinal=2,
        phase_type="tournament",
        tournament_mode=mode,
        tournament_format=fmt,
        tournament_cut=cut,
    )
    # CONF-04 — any ADDITIONAL tournament phase must be composed BEFORE
    # ``start_season()``. Activation derives the Worlds phase at
    # ``max(ordinal) + 1`` (ADR-0037), so a phase created afterwards would
    # collide with it on ``uniq_season_phase_ordinal``. Production only ever
    # authors phases on a DRAFT Season (``league_create`` /
    # ``_run_season_rollover``), so this mirrors the real ordering. Callers
    # fetch the extras by query -- the return arity is deliberately unchanged.
    for extra_ordinal in range(3, 3 + extra_tournament_phases):
        SeasonPhase.objects.create(
            season=season,
            ordinal=extra_ordinal,
            phase_type="tournament",
            tournament_mode=mode,
            tournament_format=fmt,
            tournament_cut=cut,
        )
    season.start_season()
    season.refresh_from_db()
    for conference in conferences:
        conference.refresh_from_db()
    rr_phase.refresh_from_db()
    tournament_phase.refresh_from_db()
    return season, conferences, groups, rr_phase, tournament_phase


def _flat_season(prefix: str, n: int = 4, *, mode: str = "standings"):
    """The zero-Conference regression shape: RR phase + tournament phase, ``n``
    flat-enrolled Teams, no ``Conference`` rows at all.

    Returns ``(season, teams, rr_phase, tournament_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="2027", start_date=date(2027, 1, 1)
    )
    teams = []
    for i in range(n):
        team, _players = make_team_with_slots(f"{prefix}f{i}")
        season.teams.add(team)
        teams.append(team)
    rr_phase = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    tournament_phase = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament_mode=mode
    )
    season.start_season()
    season.refresh_from_db()
    rr_phase.refresh_from_db()
    tournament_phase.refresh_from_db()
    return season, teams, rr_phase, tournament_phase


def _fixtures_for(season: Season, phase: SeasonPhase):
    """The scheduled fixtures of one phase, via the PUBLIC by-phase accessor."""
    for candidate, fixtures in season.scheduled_fixtures_by_phase():
        if candidate.pk == phase.pk:
            return fixtures
    return []


def _hand_play_rr(season: Season, rr_phase: SeasonPhase, order: list[int]) -> None:
    """Complete every fixture of ``rr_phase`` WITHOUT running the simulator.

    ``order`` is a global team-id list, best-first: in each pairing the team
    appearing EARLIER in ``order`` wins both Rounds 100-50. A full round-robin
    therefore produces a strict, exactly-predictable Standings order equal to
    ``order`` restricted to the Conference — which is what lets every seed
    assertion below be an equality rather than a sample.

    One ``Match`` per ``(pairing, leg)`` carrying ``season`` / ``season_phase``
    / ``conference`` / ``leg`` (the CONF-01 stamping the regional seeding
    reads), plus one completed ``GameRound`` per fixture ``round_number`` (what
    ``_rr_phase_complete`` counts).
    """
    rank = {team_id: position for position, team_id in enumerate(order)}
    conf_by_team = season.conference_by_team_id()
    teams_by_id = {
        team.id: team
        for team in season.teams.all()  # enrolled set; ids come off fixtures
    }

    grouped: dict[tuple[frozenset, int], list] = {}
    for fixture in _fixtures_for(season, rr_phase):
        key = (frozenset({fixture.team_a_id, fixture.team_b_id}), fixture.leg)
        grouped.setdefault(key, []).append(fixture)

    for (_pair, leg), fixtures in grouped.items():
        first = fixtures[0]
        team_red = teams_by_id[first.team_a_id]
        team_blue = teams_by_id[first.team_b_id]
        red_wins = rank[team_red.id] < rank[team_blue.id]
        red_points, blue_points = (100, 50) if red_wins else (50, 100)
        match = Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            season=season,
            season_phase=rr_phase,
            conference=conf_by_team.get(team_red.id),
            leg=leg,
            is_completed=True,
            winner=team_red if red_wins else team_blue,
            red_round1_points=red_points,
            blue_round1_points=blue_points,
            red_round2_points=red_points,
            blue_round2_points=blue_points,
        )
        for fixture in fixtures:
            GameRound.objects.create(
                match=match,
                round_number=fixture.round_number,
                team_red=team_red,
                team_blue=team_blue,
                red_points=red_points,
                blue_points=blue_points,
                is_completed=True,
            )


def _ids(teams) -> list[int]:
    return [team.id for team in teams]


def _built_regional_season(prefix: str, sizes: "list[int] | None" = None, **kwargs):
    """``_conf_season`` + a deterministic hand-played RR + the real build.

    The RR winner order is Conference 1's teams best-first, then Conference 2's
    — so within EACH Conference the Standings order is exactly its ``groups``
    order. Returns ``(season, conferences, groups, rr_phase, phase)``.
    """
    season, conferences, groups, rr_phase, phase = _conf_season(
        prefix, sizes or [4, 4], **kwargs
    )
    order = [team.id for group in groups for team in group]
    _hand_play_rr(season, rr_phase, order)
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    phase.refresh_from_db()
    return season, conferences, groups, rr_phase, phase


def _stamp_bracket_completed(tournament: Tournament, champion) -> None:
    """Drain a bracket by STAMPING the persisted rows (contract §9.1 technique
    2) — the gate tests isolate ``_tournament_phase_complete`` /
    ``_stamp_champion_for_final_phase`` from any simulation at all."""
    tournament.champion = champion
    tournament.state = "completed"
    tournament.save(update_fields=["champion", "state"])


def _participant_seeds(tournament: Tournament) -> dict[int, int]:
    """``{team_id: seed}`` for one bracket (insertion order is NOT asserted —
    contract §9.3; the ``seed`` values are)."""
    return {
        participant.team_id: participant.seed
        for participant in TournamentParticipant.objects.filter(tournament=tournament)
    }


def _seed_order(tournament: Tournament) -> list[int]:
    """Team ids of one bracket ordered by ``seed`` ascending."""
    return list(
        TournamentParticipant.objects.filter(tournament=tournament)
        .order_by("seed")
        .values_list("team_id", flat=True)
    )


# ===========================================================================
# 1. N tournaments built — the linkage FKs and the caller seam
# ===========================================================================


class TestRegionalBuild(TestCase):
    """A 2-Conference tournament phase builds exactly TWO Tournaments, linked
    by the new FKs, with ``SeasonPhase.tournament`` left NULL."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("Build")

    def test_builds_exactly_one_tournament_per_conference(self) -> None:
        self.assertEqual(Tournament.objects.filter(season_phase=self.phase).count(), 2)

    def test_phase_tournament_id_stays_null(self) -> None:
        # The single-bracket embed pointer is NOT written on the regional path.
        self.assertIsNone(self.phase.tournament_id)

    def test_regional_tournaments_reverse_manager_holds_both(self) -> None:
        self.assertEqual(self.phase.regional_tournaments.count(), 2)

    def test_tournaments_for_phase_returns_both_in_conference_ordinal_order(
        self,
    ) -> None:
        tournaments = self.season.tournaments_for_phase(self.phase)
        self.assertEqual(len(tournaments), 2)
        self.assertEqual(
            [t.conference_id for t in tournaments],
            [self.conferences[0].id, self.conferences[1].id],
        )

    def test_tournaments_for_phase_returns_a_plain_list(self) -> None:
        # A list, not a queryset — callers iterate it repeatedly (contract §3.5).
        self.assertIsInstance(self.season.tournaments_for_phase(self.phase), list)

    def test_each_tournament_links_its_phase_and_conference(self) -> None:
        for conference in self.conferences:
            tournament = self.phase.regional_tournaments.get(conference=conference)
            self.assertEqual(tournament.season_phase_id, self.phase.id)
            self.assertEqual(tournament.conference_id, conference.id)

    def test_regional_tournament_has_empty_season_phases_manager(self) -> None:
        # NAMING HAZARD (contract §2.1): the new forward FK is ``season_phase``;
        # ``season_phases`` is the pre-existing reverse manager of
        # ``SeasonPhase.tournament`` and must stay EMPTY for a regional bracket.
        for tournament in self.phase.regional_tournaments.all():
            self.assertEqual(tournament.season_phases.count(), 0)

    def test_conference_tournaments_reverse_accessor(self) -> None:
        for conference in self.conferences:
            self.assertEqual(conference.tournaments.count(), 1)

    def test_regional_name_contains_its_conference_name(self) -> None:
        # Contract §9.3 — assert containment, never the em-dash separator.
        for conference in self.conferences:
            tournament = self.phase.regional_tournaments.get(conference=conference)
            self.assertIn(conference.name, tournament.name)

    def test_brackets_are_built_and_active(self) -> None:
        for tournament in self.phase.regional_tournaments.all():
            self.assertEqual(tournament.state, "active")
            self.assertEqual(tournament.nodes.count(), 3)

    def test_season_stays_active_and_uncrowned_at_build_time(self) -> None:
        self.assertEqual(self.season.state, "active")
        self.assertIsNone(self.season.champion_team_id)


# ===========================================================================
# 2. Per-Conference participants, seeds and the intra-Conference invariant
# ===========================================================================


class TestRegionalParticipantsAndSeeds(TestCase):
    """Each bracket holds exactly its own Conference's snapshot teams, seeded
    ``1..n`` from that Conference's OWN Standings."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("Seed")
        self.by_conf = {
            conference.id: self.phase.regional_tournaments.get(conference=conference)
            for conference in self.conferences
        }

    def test_participants_are_exactly_the_conference_snapshot(self) -> None:
        for conference, group in zip(self.conferences, self.groups):
            tournament = self.by_conf[conference.id]
            self.assertEqual(
                set(_participant_seeds(tournament)),
                set(conference.starting_team_ids_json or []),
            )
            self.assertEqual(set(_participant_seeds(tournament)), set(_ids(group)))

    def test_no_cross_conference_participant(self) -> None:
        other_ids = set(_ids(self.groups[1]))
        self.assertEqual(
            set(_participant_seeds(self.by_conf[self.conferences[0].id])) & other_ids,
            set(),
        )
        own_ids = set(_ids(self.groups[0]))
        self.assertEqual(
            set(_participant_seeds(self.by_conf[self.conferences[1].id])) & own_ids,
            set(),
        )

    def test_participant_total_is_the_enrolled_set_split_in_two(self) -> None:
        self.assertEqual(
            TournamentParticipant.objects.filter(
                tournament__season_phase=self.phase
            ).count(),
            8,
        )

    def test_seeds_restart_at_one_in_each_bracket(self) -> None:
        for conference in self.conferences:
            seeds = sorted(_participant_seeds(self.by_conf[conference.id]).values())
            self.assertEqual(seeds, [1, 2, 3, 4])

    def test_seed_order_matches_the_conference_standings_order(self) -> None:
        # ``_hand_play_rr`` made each Conference's Standings order exactly its
        # ``groups`` order, so seeding is an equality, not a sample.
        for conference, group in zip(self.conferences, self.groups):
            self.assertEqual(_seed_order(self.by_conf[conference.id]), _ids(group))

    def test_no_bracket_node_pairs_teams_across_conferences(self) -> None:
        for conference, group in zip(self.conferences, self.groups):
            allowed = set(_ids(group))
            nodes = BracketNode.objects.filter(tournament=self.by_conf[conference.id])
            self.assertTrue(nodes.exists())
            for node in nodes:
                for team_id in (node.team_a_id, node.team_b_id):
                    if team_id is not None:
                        self.assertIn(team_id, allowed)


class TestRegionalSeedingIsConferenceScoped(TestCase):
    """The load-bearing scoping test: seeding reads the Conference's OWN Match
    corpus (the ``Match.conference`` discriminator), not the Season-wide table.

    A team that leads the SEASON-WIDE Standings but is only second in its own
    Conference must NOT take seed 1 in that Conference's bracket. The wedge is
    a completed Season Match that carries NO Conference (a cross-Conference
    exhibition) — it counts Season-wide and is invisible to either
    Conference-scoped corpus.
    """

    def setUp(self) -> None:
        self.season, self.conferences, self.groups, self.rr_phase, self.phase = (
            _conf_season("Scope", [4, 4])
        )
        self.a1, self.a2, self.a3, self.a4 = self.groups[0]
        self.b1, self.b2, self.b3, self.b4 = self.groups[1]
        # Intra-Conference RR: a1 > a2 > a3 > a4 and b1 > b2 > b3 > b4.
        _hand_play_rr(
            self.season,
            self.rr_phase,
            _ids(self.groups[0]) + _ids(self.groups[1]),
        )
        # The wedge: a2 also beats b4 in a Conference-LESS Season Match. It is
        # not part of the RR phase (season_phase=NULL ⇒ RR completion is
        # unaffected) and carries conference=NULL, so it lifts a2 above a1
        # Season-wide while leaving Conference 1's own table untouched.
        Match.objects.create(
            team_red=self.a2,
            team_blue=self.b4,
            season=self.season,
            season_phase=None,
            conference=None,
            is_completed=True,
            winner=self.a2,
            red_round1_points=100,
            blue_round1_points=50,
            red_round2_points=100,
            blue_round2_points=50,
        )
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.phase.refresh_from_db()

    def test_conference_seed_one_is_the_conference_leader(self) -> None:
        tournament = self.phase.regional_tournaments.get(conference=self.conferences[0])
        self.assertEqual(_seed_order(tournament)[0], self.a1.id)

    def test_season_wide_leader_does_not_take_conference_seed_one(self) -> None:
        tournament = self.phase.regional_tournaments.get(conference=self.conferences[0])
        # a2 leads the Season-wide table (see the additive-signature pin below)
        # but is Conference 1's SECOND seed.
        self.assertEqual(_participant_seeds(tournament)[self.a2.id], 2)

    def test_other_conference_is_unaffected_by_the_wedge(self) -> None:
        tournament = self.phase.regional_tournaments.get(conference=self.conferences[1])
        self.assertEqual(_seed_order(tournament), _ids(self.groups[1]))


# ===========================================================================
# 3. All three tournament_mode values split
# ===========================================================================


class TestRegionalSeedingModesSplit(TestCase):
    """``standings`` / ``strength`` / ``unseeded`` all build 2 brackets with
    disjoint, Conference-correct participant sets (ADR-0035)."""

    def _assert_split(self, phase, conferences, groups) -> None:
        tournaments = {t.conference_id: t for t in phase.regional_tournaments.all()}
        self.assertEqual(len(tournaments), 2)
        seen: set[int] = set()
        for conference, group in zip(conferences, groups):
            members = set(_participant_seeds(tournaments[conference.id]))
            self.assertEqual(members, set(_ids(group)))
            self.assertEqual(members & seen, set())
            seen |= members
        self.assertEqual(len(seen), 8)

    def test_standings_mode_splits(self) -> None:
        _season, conferences, groups, _rr, phase = _built_regional_season(
            "ModeStd", mode="standings"
        )
        self._assert_split(phase, conferences, groups)

    def test_strength_mode_splits_and_seeds_by_conference_rating_order(self) -> None:
        from teams.models import Player

        season, conferences, groups, rr_phase, phase = _conf_season(
            "ModeStr", [4, 4], mode="strength"
        )
        # Deterministic talent: one stat per team, strictly descending inside
        # each Conference, so the mean-rating rank order is exactly the group
        # order (no ties ⇒ the team_id tiebreak never fires).
        for group in groups:
            for offset, team in enumerate(group):
                Player.objects.filter(team=team).update(accuracy=90 - offset * 10)
        # ``strength`` permits an incomplete prior phase only when there is
        # none; here the RR phase exists, so complete it first.
        _hand_play_rr(season, rr_phase, _ids(groups[0]) + _ids(groups[1]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()

        self._assert_split(phase, conferences, groups)
        for conference, group in zip(conferences, groups):
            tournament = phase.regional_tournaments.get(conference=conference)
            self.assertEqual(_seed_order(tournament), _ids(group))

    def test_unseeded_mode_splits_by_membership_only(self) -> None:
        # ``unseeded`` shuffles with a fresh ``random.Random()`` — assert set
        # membership ONLY, never order (contract §9.4 item 3).
        _season, conferences, groups, _rr, phase = _built_regional_season(
            "ModeUns", mode="unseeded"
        )
        self._assert_split(phase, conferences, groups)
        for conference in conferences:
            tournament = phase.regional_tournaments.get(conference=conference)
            self.assertEqual(
                sorted(_participant_seeds(tournament).values()), [1, 2, 3, 4]
            )


# ===========================================================================
# 4. tournament_cut applies PER Conference
# ===========================================================================


class TestRegionalCutIsPerConference(TestCase):
    """A cut of 4 in a 6-teams-per-Conference Season yields TWO 4-team
    brackets, not one — the cut is applied inside each Conference's order.

    Conferences of 6 are used deliberately: ``lock_and_build`` requires >= 4
    participants, so the cut must leave each bracket large enough to build
    (contract §9.4 item 4).
    """

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("Cut", [6, 6], cut=4)

    def test_two_brackets_of_four_each(self) -> None:
        self.assertEqual(self.phase.regional_tournaments.count(), 2)
        for conference in self.conferences:
            tournament = self.phase.regional_tournaments.get(conference=conference)
            self.assertEqual(len(_participant_seeds(tournament)), 4)

    def test_cut_keeps_each_conferences_own_top_four(self) -> None:
        for conference, group in zip(self.conferences, self.groups):
            tournament = self.phase.regional_tournaments.get(conference=conference)
            self.assertEqual(_seed_order(tournament), _ids(group[:4]))

    def test_cut_total_is_eight_not_four(self) -> None:
        # The whole point: a cut of 4 in a 2-Conference Season seeds 4 teams
        # PER Conference, not 4 teams overall.
        self.assertEqual(
            TournamentParticipant.objects.filter(
                tournament__season_phase=self.phase
            ).count(),
            8,
        )


# ===========================================================================
# 5. Idempotence
# ===========================================================================


class TestRegionalBuildIdempotence(TestCase):
    """A second ``activate_pending_tournament_phase()`` builds nothing new."""

    def test_second_activation_adds_no_rows(self) -> None:
        season, _conferences, _groups, _rr, phase = _built_regional_season("Idem")
        before = (
            Tournament.objects.count(),
            TournamentParticipant.objects.count(),
            BracketNode.objects.count(),
        )
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        after = (
            Tournament.objects.count(),
            TournamentParticipant.objects.count(),
            BracketNode.objects.count(),
        )
        self.assertEqual(after, before)

    def test_second_activation_keeps_phase_tournament_null(self) -> None:
        season, _conferences, _groups, _rr, phase = _built_regional_season("Idem2")
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        self.assertIsNone(phase.tournament_id)
        self.assertEqual(phase.regional_tournaments.count(), 2)


# ===========================================================================
# 6. The completion gate — ALL N brackets must drain
# ===========================================================================


class TestRegionalCompletionGate(TestCase):
    """The phase does not advance until EVERY regional bracket has drained;
    when it does, the Season completes with ``champion_team`` still NULL."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _built_regional_season("Gate")
        self.first = self.phase.regional_tournaments.get(conference=self.conferences[0])
        self.second = self.phase.regional_tournaments.get(
            conference=self.conferences[1]
        )

    def test_neither_drained_leaves_cursor_on_the_tournament_phase(self) -> None:
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.current_phase().id, self.phase.id)
        self.assertEqual(self.season.state, "active")

    def test_one_of_two_drained_does_not_complete_the_season(self) -> None:
        _stamp_bracket_completed(self.first, self.groups[0][0])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertIsNotNone(self.season.current_phase())
        self.assertEqual(self.season.current_phase().id, self.phase.id)

    def test_both_drained_completes_the_regional_phase(self) -> None:
        # CONF-04 — draining every Regional playoff still completes THE PHASE
        # (the CONF-02 gate this class pins), but no longer the SEASON: since
        # ADR-0037 a derived Worlds phase sits after it, so the cursor advances
        # onto Worlds instead of falling off the end.
        _stamp_bracket_completed(self.first, self.groups[0][0])
        _stamp_bracket_completed(self.second, self.groups[1][1])
        self.season.complete_if_finished()
        self.season.refresh_from_db()

        self.assertTrue(self.season._phase_complete(self.phase))
        cursor = self.season.current_phase()
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.tournament_mode, "worlds")
        self.assertEqual(self.season.state, "active")

    def test_both_drained_leaves_champion_team_null(self) -> None:
        # A Conference champion is NOT a Season champion (ADR-0035 / CONF-01).
        # CONF-04 — still NULL here, now because Worlds has not been played.
        _stamp_bracket_completed(self.first, self.groups[0][0])
        _stamp_bracket_completed(self.second, self.groups[1][1])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIsNone(self.season.champion_team_id)

    def test_both_conference_champions_are_reachable_off_the_phase(self) -> None:
        _stamp_bracket_completed(self.first, self.groups[0][0])
        _stamp_bracket_completed(self.second, self.groups[1][1])
        self.season.complete_if_finished()
        first = self.phase.regional_tournaments.get(conference=self.conferences[0])
        second = self.phase.regional_tournaments.get(conference=self.conferences[1])
        self.assertEqual(first.champion_id, self.groups[0][0].id)
        self.assertEqual(second.champion_id, self.groups[1][1].id)
        # And each champion belongs to its OWN Conference.
        self.assertIn(first.champion_id, _ids(self.groups[0]))
        self.assertIn(second.champion_id, _ids(self.groups[1]))


# ===========================================================================
# 7 + 8. Byte-identical regression pins — 0 and 1 Conference
# ===========================================================================


class TestZeroConferenceRegressionPin(TestCase):
    """A Season with NO Conferences builds exactly ONE Season-wide bracket on
    ``SeasonPhase.tournament``, with both new columns NULL, and still stamps
    ``Season.champion_team``. This is what proves CONF-02 is additive."""

    def setUp(self) -> None:
        self.season, self.teams, self.rr_phase, self.phase = _flat_season("Zero")
        _hand_play_rr(self.season, self.rr_phase, _ids(self.teams))
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.phase.refresh_from_db()

    def test_exactly_one_tournament_reachable_via_phase_tournament(self) -> None:
        self.assertEqual(Tournament.objects.count(), 1)
        self.assertIsNotNone(self.phase.tournament_id)

    def test_both_new_columns_are_null(self) -> None:
        tournament = self.phase.tournament
        self.assertIsNone(tournament.season_phase_id)
        self.assertIsNone(tournament.conference_id)

    def test_regional_tournaments_is_empty(self) -> None:
        self.assertEqual(self.phase.regional_tournaments.count(), 0)

    def test_season_phases_reverse_manager_is_non_empty(self) -> None:
        # The opposite of the regional case (contract §2.1 naming hazard).
        self.assertEqual(self.phase.tournament.season_phases.count(), 1)

    def test_tournaments_for_phase_returns_the_single_bracket(self) -> None:
        tournaments = self.season.tournaments_for_phase(self.phase)
        self.assertEqual([t.id for t in tournaments], [self.phase.tournament_id])

    def test_seeding_is_season_wide(self) -> None:
        self.assertEqual(_seed_order(self.phase.tournament), _ids(self.teams))

    def test_drain_stamps_the_season_champion(self) -> None:
        tournament = self.phase.tournament
        _stamp_bracket_completed(tournament, self.teams[0])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        tournament.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, tournament.champion_id)


class TestOneConferenceRegressionPin(TestCase):
    """Exactly ONE Conference must NOT fire the ``>= 2`` regional predicate —
    it degenerates to the Season-wide path (contract §3.4)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("One", [4])
        _hand_play_rr(self.season, self.rr_phase, _ids(self.groups[0]))
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.phase.refresh_from_db()

    def test_exactly_one_tournament_on_the_embed_pointer(self) -> None:
        self.assertEqual(Tournament.objects.count(), 1)
        self.assertIsNotNone(self.phase.tournament_id)

    def test_no_regional_linkage_written(self) -> None:
        tournament = self.phase.tournament
        self.assertIsNone(tournament.season_phase_id)
        self.assertIsNone(tournament.conference_id)
        self.assertEqual(self.phase.regional_tournaments.count(), 0)
        self.assertEqual(self.conferences[0].tournaments.count(), 0)

    def test_tournaments_for_phase_returns_the_single_bracket(self) -> None:
        tournaments = self.season.tournaments_for_phase(self.phase)
        self.assertEqual([t.id for t in tournaments], [self.phase.tournament_id])

    def test_drain_stamps_the_season_champion(self) -> None:
        tournament = self.phase.tournament
        _stamp_bracket_completed(tournament, self.groups[0][0])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        tournament.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, tournament.champion_id)


class TestUnbuiltPhaseHasNoTournaments(TestCase):
    """``tournaments_for_phase`` on a phase whose bracket has not been built
    returns ``[]`` in both Season shapes (the pending-stub precondition the
    Playoffs screen and every drain caller guard on)."""

    def test_empty_before_the_build_multi_conference(self) -> None:
        season, _conferences, _groups, _rr, phase = _conf_season("PendM", [4, 4])
        self.assertEqual(season.tournaments_for_phase(phase), [])

    def test_empty_before_the_build_zero_conference(self) -> None:
        season, _teams, _rr, phase = _flat_season("PendZ")
        self.assertEqual(season.tournaments_for_phase(phase), [])

    def test_empty_for_a_round_robin_phase(self) -> None:
        season, _conferences, _groups, rr_phase, _phase = _conf_season("PendR", [4, 4])
        self.assertEqual(season.tournaments_for_phase(rr_phase), [])


# ===========================================================================
# 9. Additive-signature pin (contract §9.3's one permitted exception)
# ===========================================================================


class TestAdditiveSignaturePin(TestCase):
    """``_final_standings_for_phase`` / ``_seed_order_for_phase`` called with
    NO ``conference`` stay Season-wide on a multi-Conference Season — proving
    the new parameter is additive and the default path is unscoped.

    Contract §9.3 permits these two private calls, and only these two, because
    proving the signatures are additive is the point of the pin.
    """

    def setUp(self) -> None:
        self.season, self.conferences, self.groups, self.rr_phase, self.phase = (
            _conf_season("Pin", [4, 4])
        )
        self.a1, self.a2 = self.groups[0][0], self.groups[0][1]
        self.b4 = self.groups[1][3]
        _hand_play_rr(
            self.season, self.rr_phase, _ids(self.groups[0]) + _ids(self.groups[1])
        )
        # The same Conference-less wedge as the scoping test: it lifts a2 above
        # a1 in the SEASON-WIDE table only.
        Match.objects.create(
            team_red=self.a2,
            team_blue=self.b4,
            season=self.season,
            season_phase=None,
            conference=None,
            is_completed=True,
            winner=self.a2,
            red_round1_points=100,
            blue_round1_points=50,
            red_round2_points=100,
            blue_round2_points=50,
        )
        self.season.refresh_from_db()

    def test_standings_without_conference_covers_every_enrolled_team(self) -> None:
        rows = self.season._final_standings_for_phase(self.rr_phase)
        self.assertEqual(
            {row.team_id for row in rows},
            set(self.season.starting_team_ids_json or []),
        )
        self.assertEqual(len(rows), 8)

    def test_standings_with_conference_covers_only_that_conference(self) -> None:
        rows = self.season._final_standings_for_phase(
            self.rr_phase, conference=self.conferences[0]
        )
        self.assertEqual({row.team_id for row in rows}, set(_ids(self.groups[0])))
        self.assertEqual(len(rows), 4)

    def test_seed_order_without_conference_is_season_wide(self) -> None:
        order = self.season._seed_order_for_phase(self.phase)
        self.assertEqual(len(order), 8)
        self.assertEqual(set(order), set(self.season.starting_team_ids_json or []))
        # The wedge Match counts Season-wide, so a2 — not a1 — leads.
        self.assertEqual(order[0], self.a2.id)

    def test_seed_order_with_conference_is_scoped_and_reordered(self) -> None:
        order = self.season._seed_order_for_phase(
            self.phase, conference=self.conferences[0]
        )
        self.assertEqual(order, _ids(self.groups[0]))
        # The Conference-scoped corpus ignores the Conference-less wedge.
        self.assertEqual(order[0], self.a1.id)

    def test_seed_order_default_equals_standings_default(self) -> None:
        rows = self.season._final_standings_for_phase(self.rr_phase)
        self.assertEqual(
            self.season._seed_order_for_phase(self.phase),
            [row.team_id for row in rows],
        )


# ===========================================================================
# §11.4 AMENDMENT — Team History counts regional playoffs
# ===========================================================================
#
# ``matches/league_screens/team_history.py`` gains a third ``Q`` term on the
# Overall-tab Round corpus and a two-term ``Q`` OR on ``playoff_appearances``,
# so a REGIONAL Tournament — linked by the forward ``Tournament.season_phase``
# FK rather than by the ``season_phases`` reverse manager — is no longer
# misclassified as standalone-sandbox play. ``championships`` is untouched: a
# Conference champion is NOT a Season champion.
#
# These live here (NOT in ``test_league_team_history.py``) to keep the CONF-02
# file-ownership split of contract §8 intact. Fixtures are hand-built; no
# simulation (§9.1).


def _regional_tournament(phase, conference, teams, *, name="Regional"):
    """Hand-build a REGIONAL Tournament: linked by the forward
    ``season_phase`` FK, with an EMPTY ``season_phases`` reverse manager."""
    tournament = Tournament.objects.create(
        name=name,
        format="single_elimination",
        state="active",
        season_phase=phase,
        conference=conference,
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    node = BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=0,
        team_a=teams[0],
        team_b=teams[1],
        seed_a=1,
        seed_b=2,
    )
    return tournament, node


def _embedded_tournament(season, teams, *, name="Playoffs", ordinal=99):
    """Hand-build a SEASON-WIDE embedded Tournament: reached through
    ``SeasonPhase.tournament`` (non-empty ``season_phases``), both new CONF-02
    columns NULL."""
    tournament = Tournament.objects.create(
        name=name, format="single_elimination", state="active"
    )
    SeasonPhase.objects.create(
        season=season,
        ordinal=ordinal,
        phase_type="tournament",
        tournament=tournament,
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    return tournament


def _sandbox_tournament(teams, *, name="Sandbox Cup"):
    """Hand-build a STANDALONE sandbox Tournament: no SeasonPhase points at it
    AND ``season_phase_id`` is NULL — excluded by BOTH terms."""
    tournament = Tournament.objects.create(
        name=name, format="single_elimination", state="active"
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    return tournament


def _playoff_round(node, team_red, team_blue, *, red_points, blue_points):
    """One completed playoff ``GameRound`` reached through the
    ``GameRound -> Match -> SeriesMatch -> BracketNode -> Tournament`` chain the
    Overall-tab corpus query traverses. The Match keeps ``season=NULL``."""
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=None,
        match_type="tournament",
        is_completed=True,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=team_red)
    return GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )


class TestRegionalPlayoffTeamHistory(TestCase):
    """Contract §11.4 — the Team-History fix, all five required assertions."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("TH", [4, 4])
        self.league = self.season.league
        self.team = self.groups[0][0]
        self.league.current_team = self.team
        self.league.save(update_fields=["current_team"])

    def _record(self):
        response = self.client.get(
            reverse("team_history", args=[self.league.id]),
            {"team_id": self.team.id},
        )
        self.assertEqual(response.status_code, 200)
        return response.context["overall_record"]

    # -- playoff_appearances ------------------------------------------------

    def test_regional_tournament_counts_one_playoff_appearance(self) -> None:
        _regional_tournament(self.phase, self.conferences[0], self.groups[0])
        self.assertEqual(self._record().playoff_appearances, 1)

    def test_regional_and_season_wide_playoffs_both_count(self) -> None:
        # The two Q terms must OR, not shadow each other: one regional bracket
        # here, one Season-wide embedded bracket in a DIFFERENT, single-
        # Conference Season (playoff_appearances is team-global across
        # Leagues).
        _regional_tournament(self.phase, self.conferences[0], self.groups[0])
        other_league = League.objects.create(name="TH Other League")
        other_season = Season.objects.create(
            league=other_league, name="2026", start_date=date(2026, 1, 1)
        )
        other_season.teams.add(*self.groups[0])
        _embedded_tournament(other_season, self.groups[0], name="Other Playoffs")
        self.assertEqual(self._record().playoff_appearances, 2)

    def test_standalone_sandbox_tournament_still_counts_zero(self) -> None:
        _sandbox_tournament(self.groups[0])
        self.assertEqual(self._record().playoff_appearances, 0)

    def test_sandbox_alongside_regional_counts_only_the_regional(self) -> None:
        _regional_tournament(self.phase, self.conferences[0], self.groups[0])
        _sandbox_tournament(self.groups[0])
        self.assertEqual(self._record().playoff_appearances, 1)

    # -- the Overall-tab Round corpus --------------------------------------

    def test_regional_playoff_round_enters_the_corpus_exactly_once(self) -> None:
        before = self._record()
        before_total = before.wins + before.losses + before.ties

        _tournament, node = _regional_tournament(
            self.phase, self.conferences[0], self.groups[0]
        )
        # The team physically played this Round on the red side and won it.
        _playoff_round(
            node, self.team, self.groups[0][1], red_points=120, blue_points=40
        )

        after = self._record()
        after_total = after.wins + after.losses + after.ties
        # Counted exactly ONCE — .distinct() collapses the widened to-many join.
        self.assertEqual(after_total, before_total + 1)
        self.assertEqual(after.wins, before.wins + 1)

    def test_regional_playoff_loss_folds_in_as_a_loss(self) -> None:
        _tournament, node = _regional_tournament(
            self.phase, self.conferences[0], self.groups[0]
        )
        _playoff_round(
            node, self.groups[0][1], self.team, red_points=120, blue_points=40
        )
        record = self._record()
        self.assertEqual((record.wins, record.losses, record.ties), (0, 1, 0))

    def test_sandbox_playoff_round_stays_out_of_the_corpus(self) -> None:
        # The discriminator still discriminates in the Round corpus too.
        tournament = _sandbox_tournament(self.groups[0])
        node = BracketNode.objects.create(
            tournament=tournament,
            bracket_round=1,
            position=0,
            team_a=self.team,
            team_b=self.groups[0][1],
            seed_a=1,
            seed_b=2,
        )
        before = self._record()
        before_total = before.wins + before.losses + before.ties
        _playoff_round(
            node, self.team, self.groups[0][1], red_points=120, blue_points=40
        )
        after = self._record()
        self.assertEqual(after.wins + after.losses + after.ties, before_total)

    # -- championships ------------------------------------------------------

    def test_winning_a_regional_playoff_leaves_championships_zero(self) -> None:
        tournament, _node = _regional_tournament(
            self.phase, self.conferences[0], self.groups[0]
        )
        other, _n = _regional_tournament(
            self.phase, self.conferences[1], self.groups[1], name="Regional 2"
        )
        _stamp_bracket_completed(tournament, self.team)
        _stamp_bracket_completed(other, self.groups[1][0])
        self.season.refresh_from_db()
        self.season.complete_if_finished()
        self.season.refresh_from_db()

        # CONF-04 — winning a Regional playoff still leaves ``championships``
        # at 0: a Conference champion is not a Season champion. The Season is
        # now ACTIVE rather than completed at this point, because the cursor has
        # advanced to the derived Worlds phase (ADR-0037) — which is precisely
        # why ``champion_team`` is still NULL.
        self.assertEqual(self.season.state, "active")
        self.assertIsNone(self.season.champion_team_id)
        self.assertEqual(self._record().championships, 0)


# ===========================================================================
# CONF-02 review fix - a Conference too small to field a bracket.
#
# Found by manual browser testing: a 4-team / 2-Conference Season with a
# tournament phase split into two 2-team brackets. `lock_and_build` raised
# "A tournament requires at least 4 participants." out of the ATOMIC
# `activate_pending_tournament_phase`, rolling back the whole play-week that
# triggered it - so the Season could never finish its round-robin (every
# retry re-simulated and re-failed, losing the work each time).
#
# Pre-CONF-02 this shape built ONE Season-wide 4-team bracket and played
# fine, so the split is what made it degenerate. The build now SKIPS a
# Conference whose seed order is under the engine floor instead of raising.
# ===========================================================================


class TestRegionalBuildSkipsTooSmallConference(TestCase):
    """A Conference under `MIN_BRACKET_PARTICIPANTS` yields no bracket."""

    def test_two_team_conferences_build_no_brackets_and_do_not_raise(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("TinyConf", [2, 2])
        _hand_play_rr(season, rr_phase, [t.id for g in groups for t in g])
        season.refresh_from_db()

        # The regression: this used to raise ValidationError out of the
        # atomic build and roll the caller's transaction back.
        season.activate_pending_tournament_phase()

        phase.refresh_from_db()
        self.assertEqual(phase.regional_tournaments.count(), 0)
        self.assertIsNone(phase.tournament_id)
        self.assertEqual(season.tournaments_for_phase(phase), [])

    def test_rr_phase_still_completes_when_no_bracket_can_build(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("TinyRR", [2, 2])
        _hand_play_rr(season, rr_phase, [t.id for g in groups for t in g])
        season.refresh_from_db()
        season.activate_pending_tournament_phase()

        # The round-robin's own completion is unaffected - the work survives.
        self.assertTrue(season._phase_complete(rr_phase))

    def test_mixed_sizes_build_only_the_viable_conference(self) -> None:
        """A 4-team Conference still gets its bracket; a 2-team one is skipped."""
        season, conferences, groups, rr_phase, phase = _conf_season("MixedConf", [4, 2])
        _hand_play_rr(season, rr_phase, [t.id for g in groups for t in g])
        season.refresh_from_db()
        season.activate_pending_tournament_phase()

        phase.refresh_from_db()
        built = season.tournaments_for_phase(phase)
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0].conference_id, conferences[0].id)
        self.assertEqual(built[0].participants.count(), 4)
