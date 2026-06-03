"""LG-02a — Django ``TestCase`` tests for the Tournament views.

Seam contract locked at ``.claude/worktrees/lg-02a-seam-contract.md`` (§3 views,
§4 URLs, §6 templates). Six views: ``tournament_list`` / ``tournament_create``
/ ``tournament_detail`` / ``tournament_reseed`` / ``tournament_lock`` /
``tournament_play_next``. Bare URL names.

Tests assert schema-level outcomes (HTTP status, state transitions, DOM ids,
champion presence) — NOT exact point totals. The play-next path patches
``BatchSimulator.ROUND_TICKS`` small for speed and exercises the real
``_generate_teams`` on the create-generate path so signature drift fails loudly.

These assertions WILL fail until the Code agent lands the views / URLs /
templates; that is expected for the parallel build.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import (
    BracketNode,
    Match,
    Tournament,
    TournamentParticipant,
)
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# Small tick window so a played Match round terminates fast.
_FAST_TICKS = 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teams(n: int) -> list[Team]:
    return [make_team_with_slots(f"TV{i}")[0] for i in range(n)]


def _setup_tournament(n: int, *, name: str = "Cup") -> Tournament:
    """A setup-state Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name)
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _active_tournament(n: int, *, name: str = "Cup") -> Tournament:
    """A locked/active Tournament with its bracket built."""
    t = _setup_tournament(n, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


# ---------------------------------------------------------------------------
# TestTournamentList
# ---------------------------------------------------------------------------


class TestTournamentList(TestCase):
    """GET /tournaments/ — list, empty state, DOM ids."""

    def test_get_returns_200(self) -> None:
        response = self.client.get(reverse("tournament_list"))
        self.assertEqual(response.status_code, 200)

    def test_empty_state_notice(self) -> None:
        response = self.client.get(reverse("tournament_list"))
        body = response.content.decode()
        self.assertIn('id="tournament-list-empty"', body)
        self.assertIn("No tournaments yet", body)

    def test_create_link_always_present(self) -> None:
        response = self.client.get(reverse("tournament_list"))
        self.assertIn('id="tournament-create-link"', response.content.decode())

    def test_table_rendered_when_non_empty(self) -> None:
        Tournament.objects.create(name="Cup A")
        response = self.client.get(reverse("tournament_list"))
        body = response.content.decode()
        self.assertIn('id="tournament-list-table"', body)
        self.assertIn("Cup A", body)

    def test_state_badge_class_present(self) -> None:
        Tournament.objects.create(name="Cup A")
        response = self.client.get(reverse("tournament_list"))
        self.assertIn("state-badge", response.content.decode())

    def test_newest_first_ordering(self) -> None:
        Tournament.objects.create(name="First")
        Tournament.objects.create(name="Second")
        response = self.client.get(reverse("tournament_list"))
        body = response.content.decode()
        self.assertLess(body.index("Second"), body.index("First"))

    def test_context_key_tournaments(self) -> None:
        Tournament.objects.create(name="Cup A")
        response = self.client.get(reverse("tournament_list"))
        self.assertIn("tournaments", response.context)


# ---------------------------------------------------------------------------
# TestTournamentCreate
# ---------------------------------------------------------------------------


class TestTournamentCreate(TestCase):
    """GET/POST /tournaments/create/ — form DOM ids, select + generate paths."""

    def test_get_returns_200(self) -> None:
        response = self.client.get(reverse("tournament_create"))
        self.assertEqual(response.status_code, 200)

    def test_get_form_dom_ids(self) -> None:
        # Seed a regular Team so the team <select> renders (the empty-state
        # swaps the select for the no-teams notice — see the separate test).
        make_team_with_slots("Existing")
        response = self.client.get(reverse("tournament_create"))
        body = response.content.decode()
        for dom_id in (
            "tournament-create-form",
            "tournament-create-name",
            "tournament-create-team-select",
            "tournament-create-generate-count",
            "tournament-create-generate-ppt",
            "tournament-create-submit",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_no_teams_notice_when_no_regular_teams(self) -> None:
        response = self.client.get(reverse("tournament_create"))
        # No regular Teams exist ⇒ the empty-state notice renders.
        self.assertIn("tournament-create-no-teams-notice", response.content.decode())

    def test_post_select_existing_creates_tournament_and_redirects(self) -> None:
        teams = _make_teams(4)
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Selected Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        self.assertEqual(response.status_code, 302)
        t = Tournament.objects.get(name="Selected Cup")
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.participants.count(), 4)

    def test_post_select_existing_redirects_to_detail(self) -> None:
        teams = _make_teams(4)
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Selected Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="Selected Cup")
        self.assertEqual(
            response["Location"], reverse("tournament_detail", args=[t.id])
        )

    def test_post_generate_path_exercises_real_generate_teams(self) -> None:
        # NO mock.patch on _generate_teams — signature drift must fail loudly.
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Generated Cup",
                "teams": [],
                "generate_count": "4",
                "generate_ppt": "6",
            },
        )
        self.assertEqual(response.status_code, 302)
        t = Tournament.objects.get(name="Generated Cup")
        self.assertEqual(t.participants.count(), 4)
        # Each generated participant Team has 6 players.
        for p in t.participants.all():
            self.assertEqual(p.team.players.count(), 6)

    def test_post_default_seeding_applied(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Seeded Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="Seeded Cup")
        seeds = sorted(t.participants.values_list("seed", flat=True))
        # Seeds are dense 1..N.
        self.assertEqual(seeds, [1, 2, 3, 4])


# ---------------------------------------------------------------------------
# TestTournamentDetail
# ---------------------------------------------------------------------------


class TestTournamentDetail(TestCase):
    """GET /tournaments/<id>/ — 200/405/DOM ids, setup vs active rendering."""

    def test_get_returns_200(self) -> None:
        t = _setup_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertEqual(response.status_code, 200)

    def test_non_get_returns_405(self) -> None:
        t = _setup_tournament(4)
        response = self.client.post(reverse("tournament_detail", args=[t.id]))
        self.assertEqual(response.status_code, 405)

    def test_404_on_missing_tournament(self) -> None:
        response = self.client.get(reverse("tournament_detail", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_bracket_container_dom_id(self) -> None:
        t = _active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn('id="tournament-bracket"', response.content.decode())

    def test_node_dom_ids_present_after_build(self) -> None:
        t = _active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        body = response.content.decode()
        # Round-1 node at position 0.
        self.assertIn('id="tournament-node-1-0"', body)

    def test_bye_node_css_class(self) -> None:
        t = _active_tournament(5)  # 3 byes
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn("bye-node", response.content.decode())

    def test_seeding_form_present_in_setup_only(self) -> None:
        t = _setup_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn('id="tournament-seeding-form"', response.content.decode())

    def test_seeding_form_absent_when_locked(self) -> None:
        t = _active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertNotIn('id="tournament-seeding-form"', response.content.decode())

    def test_lock_form_present_in_setup_only(self) -> None:
        t = _setup_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn('id="tournament-lock-form"', response.content.decode())

    def test_play_next_form_present_in_active_only(self) -> None:
        setup = _setup_tournament(4, name="Setup")
        active = _active_tournament(4, name="Active")
        setup_body = self.client.get(
            reverse("tournament_detail", args=[setup.id])
        ).content.decode()
        active_body = self.client.get(
            reverse("tournament_detail", args=[active.id])
        ).content.decode()
        self.assertNotIn('id="tournament-play-next-form"', setup_body)
        self.assertIn('id="tournament-play-next-form"', active_body)

    def test_context_keys(self) -> None:
        t = _active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        for key in (
            "tournament",
            "participants",
            "rounds",
            "next_node",
            "is_locked",
            "can_play",
        ):
            self.assertIn(key, response.context, f"missing context key {key!r}")


# ---------------------------------------------------------------------------
# TestTournamentReseed
# ---------------------------------------------------------------------------


class TestTournamentReseed(TestCase):
    """POST /tournaments/<id>/reseed/ — setup-only persistence, locked reject."""

    def test_non_post_returns_405(self) -> None:
        t = _setup_tournament(4)
        response = self.client.get(reverse("tournament_reseed", args=[t.id]))
        self.assertEqual(response.status_code, 405)

    def test_reseed_persists_new_seed_ints_in_setup(self) -> None:
        t = _setup_tournament(4)
        # Reverse the seeding: team currently seed 1 becomes seed 4, etc.
        parts = list(t.participants.all())
        post = {f"seed_{p.team_id}": str(5 - p.seed) for p in parts}
        response = self.client.post(reverse("tournament_reseed", args=[t.id]), post)
        self.assertEqual(response.status_code, 302)
        # The seeds were reassigned.
        by_team = {p.team_id: p.seed for p in t.participants.all()}
        for p in parts:
            self.assertEqual(by_team[p.team_id], 5 - p.seed)

    def test_reseed_rejected_once_locked(self) -> None:
        t = _active_tournament(4)
        parts = list(t.participants.all())
        before = {p.team_id: p.seed for p in parts}
        post = {f"seed_{p.team_id}": str(5 - p.seed) for p in parts}
        response = self.client.post(reverse("tournament_reseed", args=[t.id]), post)
        # Rejected — 302 back with a message, seeds unchanged.
        self.assertEqual(response.status_code, 302)
        after = {p.team_id: p.seed for p in t.participants.all()}
        self.assertEqual(after, before)


# ---------------------------------------------------------------------------
# TestTournamentLock
# ---------------------------------------------------------------------------


class TestTournamentLock(TestCase):
    """POST /tournaments/<id>/lock/ — setup->active, <4 error path."""

    def test_non_post_returns_405(self) -> None:
        t = _setup_tournament(4)
        response = self.client.get(reverse("tournament_lock", args=[t.id]))
        self.assertEqual(response.status_code, 405)

    def test_lock_flips_state_to_active(self) -> None:
        t = _setup_tournament(4)
        response = self.client.post(reverse("tournament_lock", args=[t.id]))
        self.assertEqual(response.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.state, "active")
        self.assertEqual(t.nodes.count(), 3)

    def test_lock_redirects_to_detail(self) -> None:
        t = _setup_tournament(4)
        response = self.client.post(reverse("tournament_lock", args=[t.id]))
        self.assertEqual(
            response["Location"], reverse("tournament_detail", args=[t.id])
        )

    def test_lock_below_four_stays_setup(self) -> None:
        t = _setup_tournament(3)
        response = self.client.post(reverse("tournament_lock", args=[t.id]))
        # ValidationError caught -> redirect back with messages.error.
        self.assertEqual(response.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)


# ---------------------------------------------------------------------------
# TestTournamentPlayNext
# ---------------------------------------------------------------------------


class TestTournamentPlayNext(TestCase):
    """POST /tournaments/<id>/play-next/ — sims one node, advances, champion."""

    def test_non_post_returns_405(self) -> None:
        t = _active_tournament(4)
        response = self.client.get(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(response.status_code, 405)

    def test_play_next_rejected_when_not_active(self) -> None:
        t = _setup_tournament(4)
        response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        # State guard — not active. Redirect (no node simulated).
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Match.objects.count(), 0)

    def test_play_next_sims_one_node_and_advances(self) -> None:
        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(response.status_code, 302)
        # Exactly one node now has a Match attached + a winner.
        played = t.nodes.filter(match__isnull=False)
        self.assertEqual(played.count(), 1)
        node = played.get()
        self.assertIsNotNone(node.winner)

    def test_play_next_winner_fills_parent_slot(self) -> None:
        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.client.post(reverse("tournament_play_next", args=[t.id]))
        node = t.nodes.filter(match__isnull=False).get()
        parent = node.advances_to
        self.assertIsNotNone(parent)
        parent.refresh_from_db()
        slot_team = getattr(parent, f"team_{node.advances_to_slot}")
        self.assertEqual(slot_team_id := slot_team.id, node.winner_id)

    def test_play_to_completion_stamps_champion(self) -> None:
        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            # 4-team bracket = 3 playable games (2 round-1 + 1 final).
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                self.client.post(reverse("tournament_play_next", args=[t.id]))
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        self.assertIsNotNone(t.champion)

    def test_play_next_redirects_to_detail(self) -> None:
        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(
            response["Location"], reverse("tournament_detail", args=[t.id])
        )


# ---------------------------------------------------------------------------
# TestTournamentPlayNextForcedTie — break_tie exercised through the view
# ---------------------------------------------------------------------------


class TestTournamentPlayNextForcedTie(TestCase):
    """A hand-forced true tie (rounds split 1-1, equal total points) drives
    the view's break_tie path: higher best single-Round score advances, and
    on equal best the lower Bracket seed advances.

    We patch ``simulate_match`` to return a pre-built tied ``Match`` so the
    tie-break branch fires deterministically without depending on RNG.
    """

    def _forced_tie_match(
        self, team_red: Team, team_blue: Team, *, red_best: int, blue_best: int
    ) -> Match:
        """A completed Match with rounds split 1-1 and equal totals so
        ``calculate_winner`` returns ``None`` (a true tie). ``red_best`` /
        ``blue_best`` set each team's max single-round total."""
        # Round 1: red wins by red_best vs a small number.
        # Round 2: blue wins by blue_best vs a small number.
        # Totals equal so the match-level winner is None.
        # red_total = red_best + low_r2 ; blue_total = low_r1 + blue_best.
        # Choose low values so red_total == blue_total.
        low = 10
        red_r1, blue_r1 = red_best, low
        # Round 2: blue (as red-side team_blue physically?) — keep it simple,
        # use the Match column semantics directly.
        red_r2, blue_r2 = low, blue_best
        # Force equal totals by construction: red_total = red_best+low,
        # blue_total = low+blue_best ⇒ equal iff red_best == blue_best.
        match = Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            match_type="tournament",
            red_round1_points=red_r1,
            blue_round1_points=blue_r1,
            red_round2_points=red_r2,
            blue_round2_points=blue_r2,
            is_completed=True,
        )
        return match

    def test_forced_tie_equal_best_lower_seed_advances(self) -> None:
        t = _active_tournament(4)
        node = t.find_next_playable_node()
        # Equal best single-round score on both sides ⇒ lower seed wins.
        lower_seed = min(node.seed_a, node.seed_b)
        lower_team_id = node.team_a_id if node.seed_a == lower_seed else node.team_b_id

        # autospec=True so the patched callable carries the bound-method
        # signature: side_effect receives (self, team_red, team_blue, ...).
        def _fake_simulate_match(sim_self, team_red, team_blue, *args, **kwargs):
            return self._forced_tie_match(
                team_red, team_blue, red_best=500, blue_best=500
            )

        with patch.object(
            BatchSimulator,
            "simulate_match",
            autospec=True,
            side_effect=_fake_simulate_match,
        ):
            self.client.post(reverse("tournament_play_next", args=[t.id]))

        node.refresh_from_db()
        self.assertIsNotNone(node.winner)
        # On equal best-round-score, the higher Bracket seed (lower int) advances.
        self.assertEqual(node.winner_id, lower_team_id)

    def test_forced_tie_higher_best_round_score_advances(self) -> None:
        t = _active_tournament(4, name="HigherBest")
        node = t.find_next_playable_node()
        # team_a is passed as team_red; give team_a the higher best-round score.
        team_a_id = node.team_a_id

        def _fake_simulate_match(sim_self, team_red, team_blue, *args, **kwargs):
            # True tie at the Match level (rounds split 1-1 AND totals equal),
            # but distinct best single-round scores so break_tie's first rule
            # (higher best advances) fires rather than the seed tiebreak.
            #   red:  r1=800, r2=100  -> best 800, total 900
            #   blue: r1=200, r2=700  -> best 700, total 900
            # red wins r1 (800>200), blue wins r2 (700>100) -> 1-1 rounds,
            # equal totals (900==900) -> winner None.
            match = Match.objects.create(
                team_red=team_red,
                team_blue=team_blue,
                match_type="tournament",
                red_round1_points=800,
                blue_round1_points=200,
                red_round2_points=100,
                blue_round2_points=700,
                is_completed=True,
            )
            return match

        with patch.object(
            BatchSimulator,
            "simulate_match",
            autospec=True,
            side_effect=_fake_simulate_match,
        ):
            self.client.post(reverse("tournament_play_next", args=[t.id]))

        node.refresh_from_db()
        self.assertIsNotNone(node.winner)
        # team_a's best single-round score (800) beats team_b's (700) ⇒ team_a.
        self.assertEqual(node.winner_id, team_a_id)
