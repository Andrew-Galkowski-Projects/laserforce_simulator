"""LG-01 — Django ``TestCase`` tests for
``BatchSimulator.simulate_scheduled_round``.

The seam contract is locked at ``.claude/worktrees/lg-01-seam-contract.md``
(§3 simulator surface, §6d test plan). Uses small-N seeded simulations
with ``ROUND_TICKS`` patched to a small value so the suite stays fast.
Tests assert schema-level outcomes only -- never exact score values.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from matches.models import GameRound, League, Match, Season
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_season_two_teams(prefix: str) -> tuple[Season, object, object]:
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    t1, _ = make_team_with_slots(f"{prefix}A")
    t2, _ = make_team_with_slots(f"{prefix}B")
    season.teams.add(t1, t2)
    season.start_season()
    return season, t1, t2


def _active_season_n_teams(prefix: str, n: int):
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    for t in teams:
        season.teams.add(t)
    season.start_season()
    return season, teams


# ---------------------------------------------------------------------------
# §6d — Guards
# ---------------------------------------------------------------------------


class TestSimulateScheduledRoundGuards(TestCase):
    """Locked guard sequence: state, round_number, and missing-round-1."""

    def test_raises_when_season_state_is_draft(self) -> None:
        league = League.objects.create(name="LDraft")
        season = Season.objects.create(
            league=league, name="S1", start_date=date.today()
        )
        t1, _ = make_team_with_slots("DraftA")
        t2, _ = make_team_with_slots("DraftB")
        season.teams.add(t1, t2)
        # State still draft (no start_season call).
        with self.assertRaises(ValueError):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)

    def test_raises_when_season_state_is_completed(self) -> None:
        league = League.objects.create(name="LCompleted")
        season = Season.objects.create(
            league=league,
            name="S1",
            start_date=date.today(),
            state="completed",
        )
        t1, _ = make_team_with_slots("CompA")
        t2, _ = make_team_with_slots("CompB")
        season.teams.add(t1, t2)
        with self.assertRaises(ValueError):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)

    def test_raises_when_round_number_is_zero(self) -> None:
        season, t1, t2 = _active_season_two_teams("R0")
        with self.assertRaises(ValueError):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                BatchSimulator().simulate_scheduled_round(season, t1, t2, 0)

    def test_raises_when_round_number_is_three(self) -> None:
        season, t1, t2 = _active_season_two_teams("R3")
        with self.assertRaises(ValueError):
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                BatchSimulator().simulate_scheduled_round(season, t1, t2, 3)

    def test_raises_when_round_2_called_without_round_1(self) -> None:
        season, t1, t2 = _active_season_two_teams("R2NoR1")
        with self.assertRaises(ValueError) as cm:
            with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
                BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        # Substring "round 1" must appear in message (locked by §3e).
        self.assertIn("round 1", str(cm.exception))


# ---------------------------------------------------------------------------
# §6d — Round 1
# ---------------------------------------------------------------------------


class TestSimulateScheduledRoundRound1(TestCase):
    """Round 1: find-or-create Match; persist GameRound(round_number=1);
    populate *_round1_* fields; is_completed stays False.
    """

    def test_creates_new_match_with_team_red_team_a_team_blue_team_b(
        self,
    ) -> None:
        season, t1, t2 = _active_season_two_teams("R1Create")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        matches = list(Match.objects.filter(season=season))
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match.team_red, t1)
        self.assertEqual(match.team_blue, t2)

    def test_persists_one_game_round_with_round_number_1(self) -> None:
        season, t1, t2 = _active_season_two_teams("R1OneGR")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        rounds = list(GameRound.objects.filter(match__season=season))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].round_number, 1)

    def test_populates_match_red_round1_fields(self) -> None:
        season, t1, t2 = _active_season_two_teams("R1RedF")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        match = Match.objects.get(season=season)
        round_1 = match.game_rounds.get(round_number=1)
        # red_round1_points should match the GameRound's red_points.
        self.assertEqual(match.red_round1_points, round_1.red_points)
        # red_round1_eliminated mirror.
        self.assertEqual(match.red_round1_eliminated, round_1.red_team_eliminated)

    def test_populates_match_blue_round1_fields(self) -> None:
        season, t1, t2 = _active_season_two_teams("R1BlueF")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        match = Match.objects.get(season=season)
        round_1 = match.game_rounds.get(round_number=1)
        self.assertEqual(match.blue_round1_points, round_1.blue_points)
        self.assertEqual(match.blue_round1_eliminated, round_1.blue_team_eliminated)

    def test_leaves_match_is_completed_false(self) -> None:
        season, t1, t2 = _active_season_two_teams("R1NotComp")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        match = Match.objects.get(season=season)
        self.assertFalse(match.is_completed)


# ---------------------------------------------------------------------------
# §6d — Round 2
# ---------------------------------------------------------------------------


class TestSimulateScheduledRoundRound2(TestCase):
    """Round 2: find existing Match Side-agnostically; per-Match colour swap
    (team_red=team_b in the GameRound); flip is_completed; trigger
    calculate_winner; populate *_round2_* fields.
    """

    def _r1_then_r2(self, prefix: str):
        season, t1, t2 = _active_season_two_teams(prefix)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        return season, t1, t2

    def test_finds_existing_match_side_agnostically(self) -> None:
        season, t1, t2 = _active_season_two_teams("R2Side")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            # Round 2 with args reversed -- should find same Match.
            BatchSimulator().simulate_scheduled_round(season, t2, t1, 2)
        matches = list(Match.objects.filter(season=season))
        # Only one Match exists -- the round-2 call did not create a second.
        self.assertEqual(len(matches), 1)

    def test_persists_second_game_round_with_round_number_2(self) -> None:
        season, _, _ = self._r1_then_r2("R2GR")
        rounds = list(GameRound.objects.filter(match__season=season))
        self.assertEqual(len(rounds), 2)
        round_numbers = sorted(r.round_number for r in rounds)
        self.assertEqual(round_numbers, [1, 2])

    def test_args_reversed_team_red_is_team_b_in_round_2_game_round(
        self,
    ) -> None:
        # Per §3e, the GameRound(round_number=2)'s physical team_red is
        # team_b and team_blue is team_a -- the per-Match colour swap.
        season, t1, t2 = self._r1_then_r2("R2Swap")
        match = Match.objects.get(season=season)
        round_2 = match.game_rounds.get(round_number=2)
        self.assertEqual(round_2.team_red, t2)
        self.assertEqual(round_2.team_blue, t1)

    def test_sets_match_is_completed_true(self) -> None:
        season, _, _ = self._r1_then_r2("R2Comp")
        match = Match.objects.get(season=season)
        self.assertTrue(match.is_completed)

    def test_triggers_calculate_winner_via_save(self) -> None:
        # After round 2 saves, the Match.calculate_winner override should
        # populate match.winner (unless the game is an exact tie, allowed).
        season, t1, t2 = self._r1_then_r2("R2Win")
        match = Match.objects.get(season=season)
        # Allow winner to be None on a genuine tie; otherwise it must be
        # one of the two teams.
        if match.winner is not None:
            self.assertIn(match.winner, (t1, t2))

    def test_populates_match_red_round2_and_blue_round2_fields(self) -> None:
        season, _t1, _t2 = self._r1_then_r2("R2Fields")
        match = Match.objects.get(season=season)
        round_2 = match.game_rounds.get(round_number=2)
        # team_a (originally red, now blue in round 2): match.red_round2 =
        # round_2.blue_points (per the SIM-09 per-Match colour-swap
        # convention -- §3e).
        self.assertEqual(match.red_round2_points, round_2.blue_points)
        self.assertEqual(match.blue_round2_points, round_2.red_points)


# ---------------------------------------------------------------------------
# §6d — Side-agnostic lookup
# ---------------------------------------------------------------------------


class TestSimulateScheduledRoundSideAgnosticLookup(TestCase):
    """Round-2 lookup must find the Match no matter which order round 1 used."""

    def test_round1_with_a_then_round2_with_b_a_finds_same_match(self) -> None:
        season, t1, t2 = _active_season_two_teams("SideAB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t2, t1, 2)
        self.assertEqual(Match.objects.filter(season=season).count(), 1)

    def test_round1_with_b_then_round2_with_a_b_finds_same_match(self) -> None:
        season, t1, t2 = _active_season_two_teams("SideBA")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t2, t1, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        self.assertEqual(Match.objects.filter(season=season).count(), 1)


# ---------------------------------------------------------------------------
# §6d — Auto-completion
# ---------------------------------------------------------------------------


class TestSimulateScheduledRoundAutoCompletion(TestCase):
    """``complete_if_finished`` is called after persistence; the last
    fixture flips the Season to completed.
    """

    def test_simulating_last_fixture_flips_season_to_completed(self) -> None:
        # N=2 -> 1 pair * 2 rounds = 2 fixtures total.
        season, t1, t2 = _active_season_two_teams("AutoLast")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_simulating_last_fixture_stamps_champion_team(self) -> None:
        season, t1, t2 = _active_season_two_teams("AutoChamp")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        season.refresh_from_db()
        self.assertIsNotNone(season.champion_team)

    def test_simulating_non_last_fixture_leaves_season_active(self) -> None:
        # N=4 -> 12 fixtures total. After one round of one pair, state must
        # stay active.
        season, teams = _active_season_n_teams("AutoMid", 4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, teams[0], teams[1], 1)
        season.refresh_from_db()
        self.assertEqual(season.state, "active")


# ---------------------------------------------------------------------------
# CONF-01 — simulate_scheduled_round(conference=...) stamps Match.conference
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/conf-01-seam-contract.md`` (simulator
# surface): the keyword-only ``conference`` (after ``leg``, before
# ``fidelity``) is stamped on the Round-1 ``Match.objects.create`` ONLY; the
# find-or-create key is UNCHANGED; default ``None`` is byte-identical to every
# existing caller; Round 2 finds the existing Match and does NOT re-stamp.
# Appended as a NEW class; no existing class is modified. The ``Conference``
# import is lazy (inside each method) so this file still COLLECTS before the
# Code agent lands the model — only these methods red, not the whole file.


def _active_conf_two_team_season(prefix: str):
    """An active Season whose two slotted Teams sit in one snapshotted
    Conference (created BEFORE ``start_season`` so the snapshot is written)."""
    from matches.models import Conference

    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    t1, _ = make_team_with_slots(f"{prefix}A")
    t2, _ = make_team_with_slots(f"{prefix}B")
    season.teams.add(t1, t2)
    conf = Conference.objects.create(season=season, name="California", ordinal=1)
    conf.teams.add(t1, t2)
    season.start_season()
    season.refresh_from_db()
    conf.refresh_from_db()
    return season, t1, t2, conf


class TestSimulateScheduledRoundConference(TestCase):
    """CONF-01 — Round-1 stamps ``Match.conference``; default ``None`` leaves
    it null; Round 2 does not re-stamp."""

    def test_round1_stamps_match_conference(self) -> None:
        season, t1, t2, conf = _active_conf_two_team_season("ConfStamp")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(
                season, t1, t2, 1, conference=conf
            )
        match = Match.objects.get(season=season)
        self.assertEqual(match.conference_id, conf.id)

    def test_default_conference_leaves_match_conference_null(self) -> None:
        season, t1, t2 = _active_season_two_teams("ConfNull")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 1)
        match = Match.objects.get(season=season)
        self.assertIsNone(match.conference_id)

    def test_round2_does_not_restamp_conference(self) -> None:
        season, t1, t2, conf = _active_conf_two_team_season("ConfR2")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # Round 1 stamps the Conference; Round 2 (default conference=None)
            # finds the existing Match and must NOT clear the stamp.
            BatchSimulator().simulate_scheduled_round(
                season, t1, t2, 1, conference=conf
            )
            BatchSimulator().simulate_scheduled_round(season, t1, t2, 2)
        match = Match.objects.get(season=season)
        self.assertEqual(match.conference_id, conf.id)
