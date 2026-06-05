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

    def test_bracket_team_names_link_to_team_game_list(self) -> None:
        t = _active_tournament(4)
        team = t.participants.first().team
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        expected = f'href="{reverse("team_match_history", args=[team.id])}"'
        self.assertIn(expected, response.content.decode())

    def test_seeding_team_names_link_to_team_game_list(self) -> None:
        t = _setup_tournament(4)
        team = t.participants.first().team
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        expected = f'href="{reverse("team_match_history", args=[team.id])}"'
        self.assertIn(expected, response.content.decode())


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
        # Bo1: exactly one node now has a SeriesMatch row + a winner.
        played = t.nodes.filter(series_matches__isnull=False).distinct()
        self.assertEqual(played.count(), 1)
        node = played.get()
        self.assertIsNotNone(node.winner)

    def test_play_next_winner_fills_parent_slot(self) -> None:
        t = _active_tournament(4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.client.post(reverse("tournament_play_next", args=[t.id]))
        node = t.nodes.filter(series_matches__isnull=False).distinct().get()
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


# ===========================================================================
# LG-02a-2 — CSV participant import + async play-all
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02a-2-seam-contract.md`` §4 views,
# §5 URLs, §6 templates.
#
# CSV import reuses the LG-00b roster importer (``teams.roster_importer``); the
# CSV builders mirror ``teams/tests/test_roster_import_view.py``. Only the
# Teams created by the import become participants; the whole field is re-seeded
# by talent via ``default_seed_order``.

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

from matches.bracket import default_seed_order  # noqa: E402
from matches.tournament_views import _team_mean_rating  # noqa: E402
from teams.roster_importer import REQUIRED_COLUMNS  # noqa: E402

_REQUIRED_HEADER = ",".join(REQUIRED_COLUMNS)


def _valid_required_row(
    team: str,
    name: str,
    role: str = "commander",
    age: int = 28,
    started_playing_age: int = 16,
    total_games: int = 100,
    home_site: str = "Ultrazone Chicago",
    height: str = "5'7\"",
) -> str:
    return (
        f"{team},{name},{role},{age},{started_playing_age},"
        f"{total_games},{home_site},{height}"
    )


def _six_role_rows_for(team_name: str, name_prefix: str) -> list[str]:
    """Six well-formed rows covering every slot for ``team_name``."""
    return [
        _valid_required_row(team_name, f"{name_prefix}-Cdr", role="commander"),
        _valid_required_row(team_name, f"{name_prefix}-Hvy", role="heavy"),
        _valid_required_row(team_name, f"{name_prefix}-Sc1", role="scout"),
        _valid_required_row(team_name, f"{name_prefix}-Sc2", role="scout"),
        _valid_required_row(team_name, f"{name_prefix}-Med", role="medic"),
        _valid_required_row(team_name, f"{name_prefix}-Amm", role="ammo"),
    ]


def _required_csv(*rows: str) -> bytes:
    body = "\n".join([_REQUIRED_HEADER, *rows]) + "\n"
    return body.encode("utf-8")


def _upload(body: bytes, filename: str = "roster.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(filename, body, content_type="text/csv")


def _four_team_csv() -> bytes:
    """A valid CSV creating 4 brand-new Teams (24 players)."""
    rows: list[str] = []
    for i in range(4):
        rows.extend(_six_role_rows_for(f"ImpTeam{i}", f"I{i}"))
    return _required_csv(*rows)


# ---------------------------------------------------------------------------
# TestTournamentImportParticipants
# ---------------------------------------------------------------------------


class TestTournamentImportParticipants(TestCase):
    """POST /tournaments/<id>/import-participants/ — CSV reuse, created-only
    participants, full re-seed by talent, setup-only guard, error rollback.
    """

    def test_only_created_teams_become_participants(self) -> None:
        t = Tournament.objects.create(name="ImpCup")
        before_participants = t.participants.count()
        response = self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_four_team_csv())},
        )
        self.assertEqual(response.status_code, 302)
        # The 4 brand-new Teams became participants.
        self.assertEqual(t.participants.count() - before_participants, 4)
        created_team_names = {
            p.team.name for p in t.participants.select_related("team")
        }
        self.assertEqual(
            created_team_names,
            {"ImpTeam0", "ImpTeam1", "ImpTeam2", "ImpTeam3"},
        )

    def test_appended_teams_not_auto_added(self) -> None:
        # Pre-existing Team that the CSV will APPEND to (not create) — it must
        # NOT become a participant. Use a bare Team (no slots filled) so the
        # appended commander row fills the free slot_commander with NO DB slot
        # collision (a true append, not a create).
        existing = Team.objects.create(name="Appendee")
        # CSV that appends a player to the existing Team plus creates 4
        # brand-new Teams.
        rows = [
            _valid_required_row(existing.name, "BenchGuy", role="commander"),
        ]
        for i in range(4):
            rows.extend(_six_role_rows_for(f"FreshTeam{i}", f"F{i}"))
        t = Tournament.objects.create(name="ImpAppendCup")
        response = self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_required_csv(*rows))},
        )
        self.assertEqual(response.status_code, 302)
        participant_team_ids = set(t.participants.values_list("team_id", flat=True))
        self.assertNotIn(
            existing.id,
            participant_team_ids,
            "an appended-to Team must NOT auto-join as a participant",
        )

    def test_full_field_reseeded_by_talent(self) -> None:
        # Seed an existing participant first, then import — the WHOLE field
        # (existing + newly created) is re-seeded by mean-rating talent order.
        t = Tournament.objects.create(name="ImpReseedCup")
        seed_team = make_team_with_slots("Seeded")[0]
        TournamentParticipant.objects.create(tournament=t, team=seed_team, seed=1)
        self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_four_team_csv())},
        )
        # Seeds are dense 1..N over the full field (existing + 4 created = 5).
        parts = list(t.participants.all())
        self.assertEqual(len(parts), 5)
        seeds = sorted(p.seed for p in parts)
        self.assertEqual(seeds, [1, 2, 3, 4, 5])
        # Order matches default_seed_order over _team_mean_rating talent.
        team_ratings = [(p.team_id, _team_mean_rating(p.team)) for p in parts]
        expected_order = default_seed_order(team_ratings)
        actual_order = [p.team_id for p in sorted(parts, key=lambda p: p.seed)]
        self.assertEqual(actual_order, expected_order)

    def test_setup_only_guard_rejects_locked_tournament(self) -> None:
        t = _active_tournament(4, name="LockedImpCup")
        before_participants = t.participants.count()
        teams_before = Team.objects.count()
        players_before = _player_count()
        response = self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_four_team_csv())},
        )
        # Rejected — flash + redirect, ZERO writes.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(t.participants.count(), before_participants)
        self.assertEqual(Team.objects.count(), teams_before)
        self.assertEqual(_player_count(), players_before)

    def test_csv_error_re_renders_detail_with_row_errors_zero_writes(
        self,
    ) -> None:
        t = Tournament.objects.create(name="ImpErrCup")
        teams_before = Team.objects.count()
        players_before = _player_count()
        participants_before = t.participants.count()

        # Row 1 has a bad role ("captain") ⇒ parse-level RowError.
        bad_rows = [
            _valid_required_row("BadTeam", "X", role="captain"),
        ]
        response = self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_required_csv(*bad_rows))},
        )
        # Re-renders the tournament_detail template (HTTP 200) with row errors.
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "matches/tournament_detail.html")
        body = response.content.decode()
        # Per-row error DOM id mirrors LG-00b: error-{row}-{field|row}.
        self.assertIn("tournament-import-error-1-role", body)
        # transaction.set_rollback(True) ⇒ ZERO writes.
        self.assertEqual(Team.objects.count(), teams_before)
        self.assertEqual(_player_count(), players_before)
        self.assertEqual(t.participants.count(), participants_before)

    def test_import_errors_block_rendered_on_error(self) -> None:
        t = Tournament.objects.create(name="ImpErrBlockCup")
        bad_rows = [_valid_required_row("BadTeam2", "Y", role="captain")]
        response = self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": _upload(_required_csv(*bad_rows))},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("tournament-import-errors", response.content.decode())


# ---------------------------------------------------------------------------
# TestTournamentPlayAll
# ---------------------------------------------------------------------------


class TestTournamentPlayAll(TestCase):
    """POST /tournaments/<id>/play-all/ — 202 enqueue, 409 non-active, 405."""

    def test_non_post_returns_405(self) -> None:
        t = _active_tournament(4, name="PlayAll405")
        response = self.client.get(reverse("tournament_play_all", args=[t.id]))
        self.assertEqual(response.status_code, 405)

    def test_play_all_enqueues_returns_202_json(self) -> None:
        t = _active_tournament(4, name="PlayAll202")
        # Patch the task's .delay so we assert the enqueue shape without running
        # the (EAGER) task body here — the task is covered in test_tournament_tasks.
        with patch("matches.tournament_views.play_tournament_task") as task_mock:
            fake_result = task_mock.apply_async.return_value
            fake_result.id = "job-abc-123"
            response = self.client.post(reverse("tournament_play_all", args=[t.id]))
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"job_id", "tournament_id"})
        self.assertEqual(payload["job_id"], "job-abc-123")
        self.assertEqual(payload["tournament_id"], t.id)
        # apply_async(..., retry=False) so a dead broker fails fast, not hangs.
        task_mock.apply_async.assert_called_once_with((t.id,), retry=False)

    def test_play_all_on_non_active_returns_409(self) -> None:
        t = _setup_tournament(4, name="PlayAll409")  # setup state, not active
        with patch("matches.tournament_views.play_tournament_task") as task_mock:
            response = self.client.post(reverse("tournament_play_all", args=[t.id]))
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertIn("error", payload)
        # No task enqueued on the 409 path.
        task_mock.apply_async.assert_not_called()

    def test_play_all_404_on_missing_tournament(self) -> None:
        response = self.client.post(reverse("tournament_play_all", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_play_all_broker_down_returns_503_json(self) -> None:
        # When the Celery broker is unreachable, .delay() raises
        # kombu.exceptions.OperationalError; the view must return a clean JSON
        # 503 (not a 500 HTML page) so the UI shows a readable message instead
        # of a JSON-parse failure on the error page.
        from kombu.exceptions import OperationalError

        t = _active_tournament(4, name="PlayAllBrokerDown")
        with patch("matches.tournament_views.play_tournament_task") as task_mock:
            task_mock.apply_async.side_effect = OperationalError("broker unreachable")
            response = self.client.post(reverse("tournament_play_all", args=[t.id]))
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertIn("error", payload)
        # The message should point the user at the fix, not leak a traceback.
        self.assertIn("LF_CELERY_EAGER", payload["error"])


# ---------------------------------------------------------------------------
# TestTournamentPlayStatus
# ---------------------------------------------------------------------------


class TestTournamentPlayStatus(TestCase):
    """GET /tournaments/<id>/play-status/<job_id>/ — 5-key polling JSON."""

    def test_non_get_returns_405(self) -> None:
        t = _active_tournament(4, name="Status405")
        response = self.client.post(
            reverse("tournament_play_status", args=[t.id, "job-1"])
        )
        self.assertEqual(response.status_code, 405)

    def test_404_on_missing_tournament(self) -> None:
        response = self.client.get(
            reverse("tournament_play_status", args=[999999, "job-1"])
        )
        self.assertEqual(response.status_code, 404)

    def test_status_json_has_five_locked_keys(self) -> None:
        t = _active_tournament(4, name="Status5Key")
        response = self.client.get(
            reverse("tournament_play_status", args=[t.id, "no-such-job"])
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload.keys()),
            {"status", "completed", "total", "error", "tournament_id"},
        )
        # tournament_id is echoed from the URL kwarg (authoritative).
        self.assertEqual(payload["tournament_id"], t.id)

    def test_status_maps_pending_to_running(self) -> None:
        # An unknown / never-submitted job id resolves to Celery PENDING which
        # maps to "running" (expiry-asymmetry; reuses _celery_state_to_job_status).
        t = _active_tournament(4, name="StatusPending")
        response = self.client.get(
            reverse("tournament_play_status", args=[t.id, "never-submitted"])
        )
        payload = response.json()
        self.assertEqual(payload["status"], "running")
        # No progress known for an unknown id ⇒ 0 / 0.
        self.assertEqual(payload["completed"], 0)
        self.assertEqual(payload["total"], 0)
        self.assertIsNone(payload["error"])


# ---------------------------------------------------------------------------
# TestTournamentPlayNextStillResolvesOneNode — refactor regression
# ---------------------------------------------------------------------------


class TestTournamentPlayNextStillResolvesOneNode(TestCase):
    """The sync ``tournament_play_next`` view is refactored to call
    ``play_next_node`` but must still resolve exactly one node (no regression).
    """

    def test_sync_play_next_still_resolves_one_node(self) -> None:
        t = _active_tournament(4, name="SyncRegression")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(response.status_code, 302)
        played = t.nodes.filter(series_matches__isnull=False).distinct()
        self.assertEqual(played.count(), 1)
        self.assertIsNotNone(played.get().winner)

    def test_sync_play_next_redirects_to_detail(self) -> None:
        t = _active_tournament(4, name="SyncRedirect")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(
            response["Location"], reverse("tournament_detail", args=[t.id])
        )

    def test_sync_play_next_none_path_flashes_and_redirects(self) -> None:
        # Active tournament already played to completion ⇒ play_next returns
        # None and the view flashes + redirects (no crash).
        t = _active_tournament(4, name="SyncNone")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(10):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                self.client.post(reverse("tournament_play_next", args=[t.id]))
            # State is completed now; a further POST hits the state guard.
            response = self.client.post(reverse("tournament_play_next", args=[t.id]))
        self.assertEqual(response.status_code, 302)


def _player_count() -> int:
    from teams.models import Player

    return Player.objects.count()


# ===========================================================================
# LG-02b — Best-of-N series: create-form series-length + detail series score
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-seam-contract.md`` §views/§templates.


# ===========================================================================
# LG-02b-2 — per-Bracket-round Series escalation: create-form four selects +
# per-non-bye-node Bo-N label
# ===========================================================================
#
# MIGRATED from the LG-02b ``TestTournamentCreateSeriesLength`` (single select +
# single POST field) + ``TestTournamentDetailSeriesScore`` (single per-Tournament
# series_length) to the LG-02b-2 four-select + per-node Bo-N label shape. Seam
# contract: ``.claude/worktrees/lg-02b-2-seam-contract.md`` §4 / §6c.


class TestTournamentCreateSeriesLength(TestCase):
    """GET form renders ALL FOUR series-length <select>s by DOM id with Bo1
    selected by default + the old single id absent; POST persists all four with
    a forgiving per-field fallback to 1 on invalid input."""

    _SELECT_IDS = (
        "tournament-create-final-series-length",
        "tournament-create-semifinal-series-length",
        "tournament-create-quarterfinal-series-length",
        "tournament-create-earlier-series-length",
    )

    def test_get_form_renders_all_four_selects(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        for dom_id in self._SELECT_IDS:
            self.assertIn(f'id="{dom_id}"', body, f"missing select {dom_id!r}")

    def test_get_form_old_single_series_length_id_absent(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertNotIn('id="tournament-create-series-length"', body)

    def test_get_form_bo1_selected_by_default_each_select(self) -> None:
        # Each select defaults to Bo1 selected. We check the option value "1"
        # carries the ``selected`` attribute within each select's markup window.
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        for dom_id in self._SELECT_IDS:
            start = body.index(f'id="{dom_id}"')
            window = body[start : start + 600]
            self.assertIn("selected", window, f"{dom_id} has no selected option")

    def test_post_persists_all_four_fields(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Escalation Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "final_series_length": "5",
                "semifinal_series_length": "3",
                "quarterfinal_series_length": "1",
                "earlier_series_length": "3",
            },
        )
        t = Tournament.objects.get(name="Escalation Cup")
        self.assertEqual(t.final_series_length, 5)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 3)

    def test_post_tampered_value_falls_back_to_one_per_field(self) -> None:
        # "4" is not a valid choice -> that one field falls back to 1; the
        # other three persist their valid values independently.
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Tampered Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "final_series_length": "4",  # invalid -> 1
                "semifinal_series_length": "3",
                "quarterfinal_series_length": "5",
                "earlier_series_length": "1",
            },
        )
        t = Tournament.objects.get(name="Tampered Cup")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 5)
        self.assertEqual(t.earlier_series_length, 1)

    def test_post_junk_value_falls_back_to_one_per_field(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Junk Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "final_series_length": "abc",  # junk -> 1
                "semifinal_series_length": "5",
                "quarterfinal_series_length": "xyz",  # junk -> 1
                "earlier_series_length": "3",
            },
        )
        t = Tournament.objects.get(name="Junk Cup")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.semifinal_series_length, 5)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 3)

    def test_post_missing_fields_default_to_one(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "NoSeries Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="NoSeries Cup")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.semifinal_series_length, 1)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)


class TestTournamentDetailSeriesLengthLabel(TestCase):
    """A locked escalation tournament renders the per-non-bye-node Bo-N label by
    DOM id with text ``Bo{n}`` matching the stamped node value; bye nodes have
    no label. The existing series-score element + champion banner still render.
    """

    def _escalation_active(self, n: int = 8, *, name: str = "EscDetail") -> Tournament:
        # final=5 (r3), semifinal=3 (r2), quarterfinal=1 (r1) for N=8.
        t = Tournament.objects.create(
            name=name,
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
        )
        for seed, team in enumerate(_make_teams(n), start=1):
            TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_per_node_series_length_label_dom_id_and_text(self) -> None:
        t = self._escalation_active()
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # Each non-bye node renders ``Bo{series_length}`` in its label element.
        for node in t.nodes.filter(is_bye=False):
            label_id = (
                f'id="tournament-node-series-length-'
                f'{node.bracket_round}-{node.position}"'
            )
            self.assertIn(label_id, body, f"missing label for node {node}")
            start = body.index(label_id)
            window = body[start : start + 200]
            self.assertIn(
                f"Bo{node.series_length}",
                window,
                f"label text should be Bo{node.series_length} for node {node}",
            )

    def test_bye_node_has_no_series_length_label(self) -> None:
        # N=5 -> 3 byes in round 1; bye nodes get no Bo-N label.
        t = self._escalation_active(5, name="EscByeDetail")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        for node in t.nodes.filter(is_bye=True):
            label_id = (
                f'id="tournament-node-series-length-'
                f'{node.bracket_round}-{node.position}"'
            )
            self.assertNotIn(
                label_id, body, f"bye node {node} must not carry a Bo-N label"
            )

    def test_final_node_renders_its_own_bo_label(self) -> None:
        # The final (depth 0) is stamped Bo5; its label reads Bo5, distinct from
        # the round-1 quarterfinals' Bo1 — proving the per-node (not flat) value.
        t = self._escalation_active()
        final = t.nodes.get(advances_to__isnull=True)
        self.assertEqual(final.series_length, 5)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        label_id = (
            f'id="tournament-node-series-length-'
            f'{final.bracket_round}-{final.position}"'
        )
        start = body.index(label_id)
        self.assertIn("Bo5", body[start : start + 200])

    def test_series_score_element_still_renders(self) -> None:
        t = self._escalation_active()
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # A non-bye round-1 node still carries the LG-02b series-score element.
        node = (
            t.nodes.filter(is_bye=False, bracket_round=1).order_by("position").first()
        )
        self.assertIn(
            f'id="tournament-node-series-score-{node.bracket_round}-{node.position}"',
            body,
        )

    def test_completed_escalation_shows_champion_banner(self) -> None:
        from matches.tournament_engine import play_next_node

        t = self._escalation_active(4, name="EscCompleted")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(40):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-champion-banner"', body)


# ===========================================================================
# LG-02c — Double-elimination tournaments (views / templates)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single-elim view tests stay green as regression guards). Seam contract:
# ``.claude/worktrees/lg-02c-seam-contract.md`` §4 (views/templates) / §6c.
#
# Create form gains the ``tournament-create-format`` select (single/double,
# forgiving fallback). Detail page renders three DE sections
# (``tournament-bracket-{winners,losers,grand-final}``) with DE node ids
# ``tournament-node-{bracket_type}-{round}-{position}``; single-elim keeps the
# LEGACY ids ``tournament-node-{round}-{position}`` and the ``tournament-bracket``
# container (no -losers / -grand-final). These assertions WILL fail until the
# Code agent lands the view + template edits.


def _de_active_tournament(n: int, *, name: str = "DEViewCup") -> Tournament:
    """A locked/active DOUBLE-elim Tournament with its two-tree bracket built."""
    t = Tournament.objects.create(name=name, format="double_elimination")
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestCreateFormFormat(TestCase):
    """GET renders the ``tournament-create-format`` select (single selected by
    default); POST ``format=double_elimination`` persists it; a tampered/absent
    value falls back to single_elimination (forgiving-fallback precedent)."""

    def test_get_form_renders_format_select(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('id="tournament-create-format"', body)

    def test_get_form_single_selected_by_default(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        start = body.index('id="tournament-create-format"')
        window = body[start : start + 600]
        self.assertIn("selected", window, "format select has no selected option")
        # The selected option is single_elimination.
        self.assertIn("single_elimination", window)

    def test_post_double_elimination_persists(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "DE Created Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "double_elimination",
            },
        )
        t = Tournament.objects.get(name="DE Created Cup")
        self.assertEqual(t.format, "double_elimination")

    def test_post_single_elimination_persists(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "SE Created Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "single_elimination",
            },
        )
        t = Tournament.objects.get(name="SE Created Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_post_tampered_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Tampered Format Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "not_a_format",  # not a valid choice -> single
            },
        )
        t = Tournament.objects.get(name="Tampered Format Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_post_absent_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "No Format Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="No Format Cup")
        self.assertEqual(t.format, "single_elimination")


class TestDetailDoubleElimSections(TestCase):
    """A locked DE tournament renders the three section containers
    ``tournament-bracket-{winners,losers,grand-final}`` and DE node ids
    ``tournament-node-{bracket_type}-{round}-{position}``, plus per-non-bye-node
    series-score / series-length ids namespaced by bracket_type."""

    def test_three_section_containers_render(self) -> None:
        t = _de_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-bracket-winners"', body)
        self.assertIn('id="tournament-bracket-losers"', body)
        self.assertIn('id="tournament-bracket-grand-final"', body)

    def test_de_node_dom_ids_render(self) -> None:
        t = _de_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # Every node renders its bracket_type-namespaced id.
        for node in t.nodes.all():
            bt = node.bracket_type
            node_id = f'id="tournament-node-{bt}-{node.bracket_round}-{node.position}"'
            self.assertIn(node_id, body, f"missing DE node id for {node}")

    def test_grand_final_node_ids_render(self) -> None:
        t = _de_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        for node in t.nodes.filter(bracket_type="grand_final"):
            node_id = (
                f'id="tournament-node-grand_final-'
                f'{node.bracket_round}-{node.position}"'
            )
            self.assertIn(node_id, body)

    def test_series_score_and_length_ids_namespaced_by_bracket_type(self) -> None:
        t = _de_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        node = t.nodes.filter(bracket_type="losers", is_bye=False).first()
        self.assertIsNotNone(node)
        bt = node.bracket_type
        self.assertIn(
            f'id="tournament-node-series-score-{bt}-{node.bracket_round}-{node.position}"',
            body,
        )
        self.assertIn(
            f'id="tournament-node-series-length-{bt}-{node.bracket_round}-{node.position}"',
            body,
        )

    def test_legacy_single_elim_node_ids_absent_in_de(self) -> None:
        # The DE branch must NOT also render the legacy un-namespaced node ids.
        t = _de_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertNotIn('id="tournament-node-1-0"', body)


class TestDetailSingleElimIdsUnchanged(TestCase):
    """Regression: a single-elim tournament keeps the LEGACY DOM ids and does
    NOT render the DE -losers / -grand-final containers."""

    def test_legacy_bracket_container_and_node_ids(self) -> None:
        t = _active_tournament(4, name="SEIdsCup")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-bracket"', body)
        self.assertIn('id="tournament-node-1-0"', body)

    def test_legacy_series_ids_present(self) -> None:
        t = _active_tournament(4, name="SESeriesIds")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        node = (
            t.nodes.filter(is_bye=False, bracket_round=1).order_by("position").first()
        )
        self.assertIn(
            f'id="tournament-node-series-score-{node.bracket_round}-{node.position}"',
            body,
        )
        self.assertIn(
            f'id="tournament-node-series-length-{node.bracket_round}-{node.position}"',
            body,
        )

    def test_de_containers_absent_in_single_elim(self) -> None:
        t = _active_tournament(4, name="SENoDESections")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertNotIn('id="tournament-bracket-losers"', body)
        self.assertNotIn('id="tournament-bracket-grand-final"', body)


# ===========================================================================
# LG-02c — Round robin tournament format (views + template)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single/double-elim view tests stay green as regression guards). Seam
# contract: ``.claude/worktrees/lg-02c-round-robin-seam-contract.md`` §7 / §8 /
# §10.
#
# The create form's ``format`` select offers a round_robin option; a POST with
# format=round_robin persists it; a tampered / absent value falls back to
# single_elimination. The RR detail page renders the locked DOM ids
# ``tournament-rr-crosstable`` (the N×N leg-mapped crosstable) +
# ``tournament-rr-standings`` (the live standings table); the elim section
# containers (``tournament-bracket*`` / single-elim ``tournament-bracket`` /
# ``tournament-node-*``) are ABSENT for an RR Tournament; the shared lock /
# play-next / play-all controls + champion banner render; the four series-length
# selects are hidden for RR.
#
# Crosstable cell-mapping rule (LOCKED, §7.2): a leg with round_number==1 fills
# cell[team_a][team_b]; round_number==2 fills cell[team_b][team_a]; the diagonal
# is blank. Because generate_schedule doesn't persist round_number onto the node,
# the view re-derives it by matching each RR node to its fixture by (matchday,
# position-within-matchday). We assert the mapping rule against the
# ``rr_crosstable`` context (the load-bearing lock) rather than the precise DOM
# nesting (the Code agent's discretion).


def _rr_setup_tournament(n: int, *, name: str = "RRViewCup") -> Tournament:
    """A setup-state round_robin Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name, format="round_robin")
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _rr_active_tournament(n: int, *, name: str = "RRViewCup") -> Tournament:
    """A locked/active round_robin Tournament with its flat RR nodes built."""
    t = _rr_setup_tournament(n, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestCreateFormRoundRobin(TestCase):
    """The ``tournament-create-format`` select offers a round_robin option; a
    POST with format=round_robin persists it; a tampered / absent value falls
    back to single_elimination (forgiving-fallback precedent)."""

    def test_format_select_offers_round_robin_option(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        start = body.index('id="tournament-create-format"')
        window = body[start : start + 800]
        # The select must offer the round_robin option value.
        self.assertIn("round_robin", window, "format select offers no round_robin")

    def test_post_round_robin_persists(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RR Created Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin",
            },
        )
        t = Tournament.objects.get(name="RR Created Cup")
        self.assertEqual(t.format, "round_robin")

    def test_post_tampered_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RR Tampered Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "not_a_format",  # not a valid choice -> single
            },
        )
        t = Tournament.objects.get(name="RR Tampered Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_post_absent_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RR No Format Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="RR No Format Cup")
        self.assertEqual(t.format, "single_elimination")


class TestDetailRoundRobinCrosstable(TestCase):
    """An RR detail page renders ``tournament-rr-crosstable`` + the leg→cell
    mapping and ``tournament-rr-standings``; the elim section containers are
    absent; the shared controls + champion banner path are present; the four
    series-length selects are hidden for RR."""

    def test_rr_crosstable_dom_id_renders(self) -> None:
        t = _rr_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-rr-crosstable"', body)

    def test_rr_standings_dom_id_renders(self) -> None:
        t = _rr_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-rr-standings"', body)

    def test_elim_section_containers_absent(self) -> None:
        t = _rr_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # The elim WB/LB/GF + single-elim bracket containers must NOT render.
        self.assertNotIn('id="tournament-bracket"', body)
        self.assertNotIn('id="tournament-bracket-winners"', body)
        self.assertNotIn('id="tournament-bracket-losers"', body)
        self.assertNotIn('id="tournament-bracket-grand-final"', body)
        # No elim node ids either (RR is flat, no per-round node cards).
        self.assertNotIn('id="tournament-node-1-0"', body)

    def test_context_carries_rr_crosstable_and_rr_standings(self) -> None:
        t = _rr_active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn("rr_crosstable", response.context)
        self.assertIn("rr_standings", response.context)

    def test_rr_standings_context_has_one_row_per_team(self) -> None:
        from matches.standings import StandingsRow

        from teams.models import Team

        t = _rr_active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        rr_standings = response.context["rr_standings"]
        self.assertEqual(len(rr_standings), 4)
        # Each entry is a (StandingsRow, Team) pair so the template can render
        # the team NAME (StandingsRow carries only team_id) — the row's team_id
        # must match the paired Team.
        for row, team in rr_standings:
            self.assertIsInstance(row, StandingsRow)
            self.assertIsInstance(team, Team)
            self.assertEqual(row.team_id, team.id)

    def test_rr_crosstable_diagonal_is_blank(self) -> None:
        # cell[t][t] (a team versus itself) is always None (rendered blank).
        t = _rr_active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        crosstable = response.context["rr_crosstable"]
        # rows are in standings order; index i's i-th cell is the diagonal.
        for i, row in enumerate(crosstable):
            self.assertIsNone(
                row["cells"][i], "the diagonal cell (team vs itself) must be None"
            )

    def test_rr_crosstable_leg_mapping_round1_to_team_a_row(self) -> None:
        # LOCKED §7.2: the leg with round_number==1 of a pair (team_a=min id,
        # team_b=max id) fills cell[team_a][team_b]; round_number==2 fills
        # cell[team_b][team_a]. Build the row-team -> {opponent: cell} index from
        # the crosstable and verify a filled cell exists in BOTH directions for
        # every pair that played (an enrolled RR field always has both legs).
        t = _rr_active_tournament(4)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        crosstable = response.context["rr_crosstable"]

        # Map each row's team id -> {col team id: cell}.
        row_team_ids = [row["team"].id for row in crosstable]
        index: dict = {}
        for row in crosstable:
            rt = row["team"].id
            index[rt] = {}
            for col_idx, cell in enumerate(row["cells"]):
                index[rt][row_team_ids[col_idx]] = cell

        # For every unordered pair (a, b) of distinct teams, BOTH directions
        # carry a non-None cell descriptor (leg1 lives in one direction, leg2 in
        # the reverse) — that is exactly the round1->cell[a][b] /
        # round2->cell[b][a] mapping.
        ids = row_team_ids
        for i in range(len(ids)):
            for j in range(len(ids)):
                if i == j:
                    continue
                self.assertIsNotNone(
                    index[ids[i]][ids[j]],
                    f"off-diagonal cell for ({ids[i]}, {ids[j]}) must be filled",
                )

    def test_played_leg_carries_match_score(self) -> None:
        # A resolved leg dict carries the 6-point Match score (match_team /
        # match_opp), row-team perspective, each in [0, 6].
        t = _rr_active_tournament(4, name="RRViewLegMS")
        _resolve_all_rr_view(t)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        crosstable = response.context["rr_crosstable"]
        seen_played = False
        for row in crosstable:
            for cell in row["cells"]:
                if cell is None:
                    continue
                for leg in (cell["leg1"], cell["leg2"]):
                    if leg and leg["played"]:
                        seen_played = True
                        self.assertIsInstance(leg["match_team"], int)
                        self.assertIsInstance(leg["match_opp"], int)
                        self.assertIn(leg["match_team"], range(0, 7))
                        self.assertIn(leg["match_opp"], range(0, 7))
        self.assertTrue(seen_played, "expected at least one played leg")

    def test_crosstable_renders_match_score_span(self) -> None:
        t = _rr_active_tournament(4, name="RRViewMSSpan")
        _resolve_all_rr_view(t)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn("match-score", body)

    def test_rr_standings_labels_match_points(self) -> None:
        t = _rr_active_tournament(4, name="RRViewStdHeader")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        start = body.index('id="tournament-rr-standings"')
        self.assertIn("Match Pts", body[start : start + 600])

    def test_shared_controls_and_champion_banner_path_present(self) -> None:
        # The play-next + play-all controls render on an active RR Tournament
        # (shared verbatim across formats).
        t = _rr_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-play-next-form"', body)
        self.assertIn('id="tournament-play-all-form"', body)

    def test_lock_control_present_in_setup_rr(self) -> None:
        # The shared lock control renders on a setup RR Tournament.
        t = _rr_setup_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-lock-form"', body)

    def test_champion_banner_renders_when_rr_completed(self) -> None:
        # Drive the RR to completion via the engine, then assert the shared
        # champion banner renders (RR completion stamps tournament.champion
        # identically to elim).
        from matches.tournament_engine import play_next_node

        t = _rr_active_tournament(4, name="RRViewChampion")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(40):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-champion-banner"', body)
        self.assertIn("Champion", body)

    def test_series_length_selects_hidden_for_rr(self) -> None:
        # RR nodes are always Bo1, so the four create-form series-length selects
        # do not apply on the RR DETAIL page (no per-node series-score / Bo-N
        # labels). The four create-form select ids must be absent on RR detail.
        t = _rr_active_tournament(4)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        for dom_id in (
            "tournament-create-final-series-length",
            "tournament-create-semifinal-series-length",
            "tournament-create-quarterfinal-series-length",
            "tournament-create-earlier-series-length",
        ):
            self.assertNotIn(f'id="{dom_id}"', body)
        # And no per-node Bo-N series-length label element renders for RR.
        self.assertNotIn('id="tournament-node-series-length-', body)


# ===========================================================================
# LG-02c — RR -> Double-elimination tournament format (views + template)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single/double-elim/round-robin view tests stay green as regression guards).
# Seam contract: ``.claude/worktrees/lg-02c-rr-de-seam-contract.md``
# §"View / form / template spec" / §"Test boundary".
#
# The create form offers the round_robin_double_elim option and renders the
# ``tournament-create-rrde-combo`` <select> with the 6 (wb, lb) combos
# (4/0, 4/2, 8/0, 8/4, 16/0, 16/8). A POST persists format + wb_advancers +
# lb_advancers parsed from the combo (forgiving fallback to (4, 0) on
# absent/invalid combo on an RRDE create; 0/0 for non-RRDE). The detail page
# renders ``tournament-stage-badge`` and tags each RR standings row with
# ``tournament-standings-cut-{wb|lb|out}``; once finals are built the DE
# three-section containers render alongside the RR tables.
#
# The exact rrde_combo value-string format is INTERNAL (not asserted) — the
# locked combo values are the 6 shapes. We POST the suggested "wb/lb" value
# string and assert the wb_advancers / lb_advancers that persist.


def _rrde_setup_tournament(
    n: int, *, wb: int, lb: int, name: str = "RRDEViewCup"
) -> Tournament:
    """A setup-state round_robin_double_elim Tournament with ``n`` seeded
    participants and the advancer counts set."""
    t = Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        wb_advancers=wb,
        lb_advancers=lb,
    )
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _rrde_active_tournament(
    n: int, *, wb: int, lb: int, name: str = "RRDEViewCup"
) -> Tournament:
    """A locked/active RRDE Tournament — only the RR Seeding nodes built."""
    t = _rrde_setup_tournament(n, wb=wb, lb=lb, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


def _resolve_all_rr_view(t: Tournament) -> None:
    """Drive all RR nodes to resolution via the engine (random sims), then the
    deferred finals build fires on the last RR node."""
    from matches.tournament_engine import play_next_node

    rr_total = t.nodes.filter(bracket_type="round_robin").count()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(rr_total):
            play_next_node(t)
    t.refresh_from_db()


class TestCreateFormRrDeCombo(TestCase):
    """The create form offers the round_robin_double_elim option and renders
    ``tournament-create-rrde-combo`` with the 6 combos; a POST persists
    format + wb_advancers + lb_advancers with forgiving fallback."""

    def test_format_select_offers_rrde_option(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        start = body.index('id="tournament-create-format"')
        window = body[start : start + 1000]
        self.assertIn("round_robin_double_elim", window)

    def test_rrde_combo_select_renders(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('id="tournament-create-rrde-combo"', body)

    def test_rrde_combo_offers_six_combos(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        start = body.index('id="tournament-create-rrde-combo"')
        window = body[start : start + 1500]
        # The 6 locked shape combos must each surface as an <option> value.
        for combo in ("4/0", "4/2", "8/0", "8/4", "16/0", "16/8"):
            self.assertIn(combo, window, f"combo {combo!r} missing from the select")

    def test_post_rrde_persists_format_and_advancers(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RRDE Created Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin_double_elim",
                "rrde_combo": "4/2",
            },
        )
        t = Tournament.objects.get(name="RRDE Created Cup")
        self.assertEqual(t.format, "round_robin_double_elim")
        self.assertEqual(t.wb_advancers, 4)
        self.assertEqual(t.lb_advancers, 2)

    def test_post_rrde_combo_eight_four(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RRDE 8/4 Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin_double_elim",
                "rrde_combo": "8/4",
            },
        )
        t = Tournament.objects.get(name="RRDE 8/4 Cup")
        self.assertEqual(t.wb_advancers, 8)
        self.assertEqual(t.lb_advancers, 4)

    def test_post_rrde_absent_combo_falls_back_to_four_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RRDE NoCombo Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin_double_elim",
                # rrde_combo absent -> fall back to the first combo (4, 0).
            },
        )
        t = Tournament.objects.get(name="RRDE NoCombo Cup")
        self.assertEqual(t.wb_advancers, 4)
        self.assertEqual(t.lb_advancers, 0)

    def test_post_rrde_invalid_combo_falls_back_to_four_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "RRDE BadCombo Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin_double_elim",
                "rrde_combo": "7/3",  # not one of the 6 locked combos -> (4, 0)
            },
        )
        t = Tournament.objects.get(name="RRDE BadCombo Cup")
        self.assertEqual(t.wb_advancers, 4)
        self.assertEqual(t.lb_advancers, 0)

    def test_post_non_rrde_persists_zero_advancers(self) -> None:
        # On a non-RRDE create the combo is ignored and both advancers are 0.
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "SE Zero Advancers Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "single_elimination",
                "rrde_combo": "8/4",  # ignored for a non-RRDE create
            },
        )
        t = Tournament.objects.get(name="SE Zero Advancers Cup")
        self.assertEqual(t.format, "single_elimination")
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)


class TestDetailRrDeStageBadgeAndCutMarkers(TestCase):
    """The RRDE detail page renders ``tournament-stage-badge`` (seeding vs
    finals) and tags each RR standings row with the cut-line class substring
    ``tournament-standings-cut-{wb|lb|out}`` for the right teams."""

    def test_stage_badge_renders_in_seeding(self) -> None:
        t = _rrde_active_tournament(6, wb=4, lb=2, name="RRDEBadgeSeed")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-stage-badge"', body)

    def test_stage_value_is_seeding_before_finals(self) -> None:
        t = _rrde_active_tournament(6, wb=4, lb=2, name="RRDEStageSeed")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertEqual(response.context["tournament_stage"], "seeding")

    def test_cut_markers_tag_wb_lb_out_rows(self) -> None:
        # wb=4 + lb=2 over 6 teams ⇒ ranks 1-4 -> "wb", 5-6 -> "lb", none "out".
        # Use wb=4 + lb=0 over 6 teams so ranks 5-6 are "out" and tagged.
        t = _rrde_active_tournament(6, wb=4, lb=0, name="RRDECutMarkers")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # All three cut-line class substrings must appear (4 wb, 0 lb, 2 out).
        self.assertIn("tournament-standings-cut-wb", body)
        self.assertIn("tournament-standings-cut-out", body)

    def test_cut_labels_context_maps_team_ids(self) -> None:
        t = _rrde_active_tournament(6, wb=4, lb=2, name="RRDECutLabels")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        cut_labels = response.context["cut_labels"]
        rows = t.round_robin_standings()
        # Top 4 -> "wb", next 2 -> "lb", rest -> "out".
        self.assertEqual(cut_labels[rows[0].team_id], "wb")
        self.assertEqual(cut_labels[rows[3].team_id], "wb")
        self.assertEqual(cut_labels[rows[4].team_id], "lb")
        self.assertEqual(cut_labels[rows[5].team_id], "lb")

    def test_cut_labels_marks_eliminated_out(self) -> None:
        # 6 teams, wb=4 + lb=0 ⇒ ranks 5-6 are "out".
        t = _rrde_active_tournament(6, wb=4, lb=0, name="RRDECutOut")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        cut_labels = response.context["cut_labels"]
        rows = t.round_robin_standings()
        self.assertEqual(cut_labels[rows[4].team_id], "out")
        self.assertEqual(cut_labels[rows[5].team_id], "out")


class TestDetailRrDeFinalsSections(TestCase):
    """Once the deferred finals are built, the RRDE detail page renders the DE
    three-section containers alongside the RR tables, and the stage badge reads
    'finals'."""

    def test_stage_value_is_finals_after_build(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEStageFinals")
        _resolve_all_rr_view(t)
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertEqual(response.context["tournament_stage"], "finals")

    def test_de_three_section_containers_render(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEFinalsSections")
        _resolve_all_rr_view(t)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-bracket-winners"', body)
        self.assertIn('id="tournament-bracket-losers"', body)
        self.assertIn('id="tournament-bracket-grand-final"', body)

    def test_rr_tables_still_render_in_finals_stage(self) -> None:
        # The RR crosstable + standings are reused verbatim; once finals exist
        # the DE sections render ALONGSIDE the RR tables.
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDEFinalsRrTables")
        _resolve_all_rr_view(t)
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-rr-crosstable"', body)
        self.assertIn('id="tournament-rr-standings"', body)

    def test_rr_tables_render_in_seeding_stage(self) -> None:
        t = _rrde_active_tournament(4, wb=4, lb=0, name="RRDESeedRrTables")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-rr-crosstable"', body)
        self.assertIn('id="tournament-rr-standings"', body)
        # No DE sections yet in the seeding stage.
        self.assertNotIn('id="tournament-bracket-winners"', body)


# ===========================================================================
# LG-02c — Swiss tournament format (views + template)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — the
# single/double-elim/round-robin/RR->DE view tests stay green as regression
# guards). Seam contract: ``.claude/worktrees/lg-02c-swiss-seam-contract.md``
# (VIEW + TEMPLATE section + TEST BOUNDARY).
#
# The create form's ``format`` select offers a swiss option + a swiss_rounds
# numeric input (DOM id ``tournament-create-swiss-rounds``). A POST persists
# format=="swiss" + the coerced swiss_rounds (forgiving parse:
# absent/blank/invalid/negative -> 0); a tampered/absent format falls back to
# single_elimination. The swiss DETAIL page renders ``tournament-swiss-rounds``
# (outer container) + per-round ``tournament-swiss-round-{n}`` + per-pairing
# ``tournament-node-swiss-{br}-{pos}`` + ``tournament-swiss-standings``; the
# series-length selects AND the rrde-combo control are hidden for swiss; the
# shared champion / lock / play-next / play-all ids render; elim + RR ids absent.


def _swiss_setup_tournament(
    n: int, *, swiss_rounds: int = 0, name: str = "SwissViewCup"
) -> Tournament:
    """A setup-state swiss Tournament with ``n`` seeded participants."""
    t = Tournament.objects.create(name=name, format="swiss", swiss_rounds=swiss_rounds)
    for seed, team in enumerate(_make_teams(n), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _swiss_active_tournament(
    n: int, *, swiss_rounds: int = 0, name: str = "SwissViewCup"
) -> Tournament:
    """A locked/active swiss Tournament — only the R1 fold nodes are built."""
    t = _swiss_setup_tournament(n, swiss_rounds=swiss_rounds, name=name)
    t.lock_and_build()
    t.refresh_from_db()
    return t


class TestCreateFormSwiss(TestCase):
    """The ``tournament-create-format`` select offers a swiss option; a
    ``tournament-create-swiss-rounds`` numeric input exists; a POST persists
    format=="swiss" + the coerced swiss_rounds (forgiving parse); a
    tampered/absent format falls back to single_elimination."""

    def test_format_select_offers_swiss_option(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        start = body.index('id="tournament-create-format"')
        window = body[start : start + 1200]
        self.assertIn("swiss", window, "format select offers no swiss")

    def test_swiss_rounds_input_renders(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('id="tournament-create-swiss-rounds"', body)

    def test_post_swiss_persists_format_and_rounds(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss Created Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "swiss",
                "swiss_rounds": "3",
            },
        )
        t = Tournament.objects.get(name="Swiss Created Cup")
        self.assertEqual(t.format, "swiss")
        self.assertEqual(t.swiss_rounds, 3)

    def test_post_swiss_rounds_absent_coerces_to_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss NoRounds Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "swiss",
                # swiss_rounds absent -> 0 (auto).
            },
        )
        t = Tournament.objects.get(name="Swiss NoRounds Cup")
        self.assertEqual(t.swiss_rounds, 0)

    def test_post_swiss_rounds_blank_coerces_to_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss Blank Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "swiss",
                "swiss_rounds": "",
            },
        )
        t = Tournament.objects.get(name="Swiss Blank Cup")
        self.assertEqual(t.swiss_rounds, 0)

    def test_post_swiss_rounds_invalid_coerces_to_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss Junk Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "swiss",
                "swiss_rounds": "abc",
            },
        )
        t = Tournament.objects.get(name="Swiss Junk Cup")
        self.assertEqual(t.swiss_rounds, 0)

    def test_post_swiss_rounds_negative_coerces_to_zero(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss Neg Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "swiss",
                "swiss_rounds": "-5",
            },
        )
        t = Tournament.objects.get(name="Swiss Neg Cup")
        self.assertEqual(t.swiss_rounds, 0)

    def test_post_tampered_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss Tampered Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "not_a_format",
            },
        )
        t = Tournament.objects.get(name="Swiss Tampered Cup")
        self.assertEqual(t.format, "single_elimination")

    def test_post_absent_format_falls_back_to_single(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Swiss NoFormat Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="Swiss NoFormat Cup")
        self.assertEqual(t.format, "single_elimination")


class TestDetailSwiss(TestCase):
    """A swiss detail page renders ``tournament-swiss-rounds`` + per-round
    ``tournament-swiss-round-{n}`` + per-pairing ``tournament-node-swiss-{br}-
    {pos}`` + ``tournament-swiss-standings``; series-length selects + the rrde
    combo are hidden; shared champion / lock / play-next / play-all ids render;
    elim + RR ids absent."""

    def test_swiss_rounds_container_renders(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewContainer")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-swiss-rounds"', body)

    def test_per_round_section_renders(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewRound")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # Round 1 exists at lock ⇒ its per-round section renders.
        self.assertIn('id="tournament-swiss-round-1"', body)

    def test_per_pairing_node_card_renders(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewNode")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # Each R1 pairing renders a tournament-node-swiss-{br}-{pos} card.
        for node in t.nodes.filter(bracket_round=1):
            dom_id = f"tournament-node-swiss-{node.bracket_round}-{node.position}"
            self.assertIn(f'id="{dom_id}"', body, f"missing pairing card {dom_id}")

    def test_swiss_standings_table_renders(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewStandings")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-swiss-standings"', body)

    def test_context_carries_swiss_keys(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewContext")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn("swiss_rounds_view", response.context)
        self.assertIn("swiss_standings", response.context)

    def test_swiss_standings_context_one_row_per_team_paired_with_team(self) -> None:
        from matches.standings import StandingsRow

        t = _swiss_active_tournament(4, name="SwissViewStdCtx")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        swiss_standings = response.context["swiss_standings"]
        self.assertEqual(len(swiss_standings), 4)
        for row, team in swiss_standings:
            self.assertIsInstance(row, StandingsRow)
            self.assertIsInstance(team, Team)
            self.assertEqual(row.team_id, team.id)

    def test_series_length_selects_hidden_for_swiss(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewNoSeries")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        for dom_id in (
            "tournament-create-final-series-length",
            "tournament-create-semifinal-series-length",
            "tournament-create-quarterfinal-series-length",
            "tournament-create-earlier-series-length",
        ):
            self.assertNotIn(f'id="{dom_id}"', body)

    def test_rrde_combo_hidden_for_swiss(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewNoCombo")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertNotIn('id="tournament-create-rrde-combo"', body)

    def test_elim_and_rr_section_ids_absent(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewNoElim")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # Elim WB/LB/GF + single-elim bracket containers absent.
        self.assertNotIn('id="tournament-bracket"', body)
        self.assertNotIn('id="tournament-bracket-winners"', body)
        self.assertNotIn('id="tournament-bracket-losers"', body)
        self.assertNotIn('id="tournament-bracket-grand-final"', body)
        # RR crosstable / standings ids absent (swiss has its own).
        self.assertNotIn('id="tournament-rr-crosstable"', body)
        self.assertNotIn('id="tournament-rr-standings"', body)

    def test_shared_play_controls_present(self) -> None:
        t = _swiss_active_tournament(4, name="SwissViewControls")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-play-next-form"', body)
        self.assertIn('id="tournament-play-all-form"', body)

    def test_lock_control_present_in_setup_swiss(self) -> None:
        t = _swiss_setup_tournament(4, name="SwissViewLock")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-lock-form"', body)

    def test_champion_banner_renders_when_swiss_completed(self) -> None:
        from matches.tournament_engine import play_next_node

        t = _swiss_active_tournament(4, swiss_rounds=1, name="SwissViewChampion")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(20):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-champion-banner"', body)
        self.assertIn("Champion", body)


class TestTournamentCreateRosterEligibility(TestCase):
    """Only Teams with a full valid roster (all 6 slots filled, no duplicate
    player — ``Team.is_valid_roster``) may enter a Tournament. The create form
    hides ineligible Teams, a tampered/stale POST of an ineligible Team id is
    rejected, and the generate path can never create an unplayable participant
    (players-per-team clamped to >= 6).
    """

    @staticmethod
    def _invalid_team(prefix: str) -> Team:
        """A Team missing one slot (Ammo) -> is_valid_roster is False."""
        team = make_team_with_slots(prefix)[0]
        team.slot_ammo = None
        team.save()
        assert not team.is_valid_roster
        return team

    def test_invalid_roster_team_excluded_from_select_list(self) -> None:
        valid = make_team_with_slots("Valid")[0]
        invalid = self._invalid_team("Incomplete")
        response = self.client.get(reverse("tournament_create"))
        self.assertEqual(response.status_code, 200)
        available_ids = {t.id for t in response.context["available_teams"]}
        self.assertIn(valid.id, available_ids)
        self.assertNotIn(invalid.id, available_ids)
        body = response.content.decode()
        self.assertIn(f'value="{valid.id}"', body)
        self.assertNotIn(f'value="{invalid.id}"', body)

    def test_post_selecting_invalid_roster_team_rejected(self) -> None:
        valid = [make_team_with_slots(f"OK{i}")[0] for i in range(3)]
        invalid = self._invalid_team("Bad")
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Bad Roster Cup",
                "teams": [str(t.id) for t in valid] + [str(invalid.id)],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        # Re-render (200), nothing created.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tournament.objects.filter(name="Bad Roster Cup").exists())

    def test_post_all_valid_rosters_succeeds(self) -> None:
        teams = _make_teams(4)
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Valid Roster Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        self.assertEqual(response.status_code, 302)
        t = Tournament.objects.get(name="Valid Roster Cup")
        self.assertEqual(t.participants.count(), 4)

    def test_generate_ppt_below_six_clamped_to_valid_rosters(self) -> None:
        # A user requesting 3 players/team must still get full, playable rosters.
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Clamped Cup",
                "teams": [],
                "generate_count": "4",
                "generate_ppt": "3",
            },
        )
        self.assertEqual(response.status_code, 302)
        t = Tournament.objects.get(name="Clamped Cup")
        self.assertEqual(t.participants.count(), 4)
        for p in t.participants.all():
            self.assertTrue(
                p.team.is_valid_roster,
                f"{p.team.name} has an incomplete roster",
            )


# ===========================================================================
# Match score node element (the 6-point Match score on each node card)
# ===========================================================================
#
# Each played Series Match renders a per-game match-score element
# (``tournament-node-match-scores-...`` with a ``match-score`` span) alongside
# the existing ``Bo{n}`` label and ``wins_a–wins_b`` series-score. Single-elim
# keeps the legacy ``{round}-{position}`` id shape; the other formats use the
# ``{bracket_type}-{round}-{position}`` shape via the shared node-card include.


def _play_one_node(t: Tournament) -> None:
    """Resolve exactly one playable node's Series to a winner via the engine
    (fast ticks), so a played Match exists to render a match score for."""
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        # A Bo1 node clinches in one call; loop a few times defensively.
        for _ in range(6):
            node = play_next_node(t)
            if node is not None and node.winner_id is not None:
                break
    t.refresh_from_db()


class TestMatchScoreNodeElement(TestCase):
    """A played node renders the 6-point match-score element; an unplayed node
    does not."""

    def test_single_elim_played_node_renders_match_score(self) -> None:
        t = _active_tournament(4, name="MSViewSE")
        _play_one_node(t)
        resolved = t.nodes.filter(winner__isnull=False, is_bye=False).first()
        self.assertIsNotNone(resolved, "expected one resolved node after play")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        dom_id = (
            f"tournament-node-match-scores-"
            f"{resolved.bracket_round}-{resolved.position}"
        )
        self.assertIn(f'id="{dom_id}"', body)
        # The element carries a match-score span with an en-dash score.
        start = body.index(f'id="{dom_id}"')
        self.assertIn("match-score", body[start : start + 300])

    def test_swiss_played_node_renders_match_score(self) -> None:
        t = _swiss_active_tournament(4, name="MSViewSwiss")
        _play_one_node(t)
        resolved = t.nodes.filter(bracket_type="swiss", winner__isnull=False).first()
        self.assertIsNotNone(resolved, "expected one resolved swiss node")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        dom_id = (
            f"tournament-node-match-scores-swiss-"
            f"{resolved.bracket_round}-{resolved.position}"
        )
        self.assertIn(f'id="{dom_id}"', body)

    def test_unplayed_node_has_no_match_score_element(self) -> None:
        t = _active_tournament(4, name="MSViewUnplayed")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        # No node has a played Match yet at lock, so no match-score element.
        self.assertNotIn("tournament-node-match-scores-", body)

    def test_swiss_standings_labels_match_points(self) -> None:
        # The Swiss standings header makes the 6-point Match score explicit.
        t = _swiss_active_tournament(4, name="MSViewStdHeader")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        start = body.index('id="tournament-swiss-standings"')
        self.assertIn("Match Pts", body[start : start + 600])


# ===========================================================================
# LG-02x-1 — Random Draw player-pool Tournament (views)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified). Seam
# contract: ``.claude/worktrees/lg-02x-1-seam-contract.md`` §5 / §7.
#
# Surfaces under test:
#   - tournament_create: team_assembly + role_assignment_mode selects, POST
#     persistence, forgiving fallback;
#   - pool intake (existing / generate / CSV) creates TournamentPlayerEntry rows
#     on the Free Agents Team; CSV error branch re-renders 200 + zero writes;
#   - tournament_draw: validates N%6 / N>=24, builds drawn Teams
#     (is_draw_team=True) + TournamentParticipant + fills tier/drawn_team,
#     re-roll idempotent, hand-edit mutates one entry;
#   - lock reached via the existing tournament_lock over the drawn Teams;
#   - new _detail_context keys + pool/draw DOM ids render.
#
# Draw sims are NON-DETERMINISTIC, so these tests assert STRUCTURE (entries,
# drawn Teams, participants, tiers, DOM, state) — never simulated point totals.
#
# Player / TournamentPlayerEntry / get_free_agents_team are imported LAZILY
# inside methods/helpers so their absence pre-Code-landing isolates the failure
# to these new classes.


def _draw_player_pool(n: int, *, prefix: str = "PoolP") -> list:
    """Create ``n`` Players on the Free Agents Team with DISTINCT, descending
    overall ratings (so the rating-DESC tiering is deterministic).

    Returns the list of Players (rating-descending). overall_rating is the mean
    of the 19 stats; we set every stat to a single per-player value so the mean
    equals that value and the ordering is unambiguous.
    """
    from teams.models import Player, get_free_agents_team

    fa = get_free_agents_team()
    # 19 stat field names (capital-O Offensive_synergy) — set them all so
    # overall_rating is a clean, distinct integer per player.
    stat_fields = [
        "player_awareness",
        "game_awareness",
        "resource_awareness",
        "decision_making",
        "positioning",
        "stamina",
        "speed",
        "flexibility",
        "adaptability",
        "communication",
        "teamwork",
        "Offensive_synergy",
        "defensive_synergy",
        "midfield_synergy",
        "resupply_synergy",
        "resupply_efficiency",
        "accuracy",
        "survival",
        "special_usage",
    ]
    players = []
    for i in range(n):
        value = 90 - i  # strictly decreasing, distinct
        stats = {f: value for f in stat_fields}
        players.append(Player.objects.create(team=fa, name=f"{prefix}{i}", **stats))
    return players


def _random_draw_tournament(*, name: str) -> Tournament:
    """A setup-state random_draw RR->DE Tournament with an EMPTY pool."""
    return Tournament.objects.create(
        name=name,
        format="round_robin_double_elim",
        team_assembly="random_draw",
        role_assignment_mode="random",
        wb_advancers=4,
        lb_advancers=0,
    )


def _pool_entries(tournament, players) -> None:
    """Register ``players`` as pool entries (tier/drawn_team null) on the
    tournament — the post-intake, pre-draw state."""
    from matches.models import TournamentPlayerEntry

    for p in players:
        TournamentPlayerEntry.objects.create(tournament=tournament, player=p)


# ---------------------------------------------------------------------------
# Create form — team_assembly / role_assignment_mode
# ---------------------------------------------------------------------------


class TestCreateFormTeamAssembly(TestCase):
    """GET renders the two new selects; POST persists them with a forgiving
    fallback (preset / random)."""

    def test_get_renders_team_assembly_select(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('id="tournament-create-team-assembly"', body)

    def test_get_renders_role_assignment_mode_select(self) -> None:
        make_team_with_slots("Existing")
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('id="tournament-create-role-assignment-mode"', body)

    def test_post_random_draw_persists_team_assembly(self) -> None:
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Draw Cup",
                "teams": [],
                "generate_count": "0",
                "generate_ppt": "6",
                "format": "round_robin_double_elim",
                "team_assembly": "random_draw",
                "role_assignment_mode": "per_tier",
            },
        )
        t = Tournament.objects.get(name="Draw Cup")
        self.assertEqual(t.team_assembly, "random_draw")
        self.assertEqual(t.role_assignment_mode, "per_tier")

    def test_post_default_is_preset(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Preset Default Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
            },
        )
        t = Tournament.objects.get(name="Preset Default Cup")
        self.assertEqual(t.team_assembly, "preset")
        self.assertEqual(t.role_assignment_mode, "random")

    def test_post_tampered_team_assembly_falls_back_to_preset(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Tampered Assembly Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "team_assembly": "not_a_mode",
                "role_assignment_mode": "also_bad",
            },
        )
        t = Tournament.objects.get(name="Tampered Assembly Cup")
        self.assertEqual(t.team_assembly, "preset")
        self.assertEqual(t.role_assignment_mode, "random")


# ---------------------------------------------------------------------------
# Pool intake — add-existing / generate / CSV
# ---------------------------------------------------------------------------


class TestTournamentPoolAddExisting(TestCase):
    """POST add-existing creates TournamentPlayerEntry rows for selected
    Players; setup-only."""

    def test_adds_selected_players_as_entries(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="PoolAddCup")
        players = _draw_player_pool(6, prefix="AddP")
        response = self.client.post(
            reverse("tournament_pool_add_existing", args=[t.id]),
            {"players": [str(p.id) for p in players]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TournamentPlayerEntry.objects.filter(tournament=t).count(), 6)

    def test_entries_have_null_tier_and_drawn_team(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="PoolAddNullCup")
        players = _draw_player_pool(6, prefix="AddNull")
        self.client.post(
            reverse("tournament_pool_add_existing", args=[t.id]),
            {"players": [str(p.id) for p in players]},
        )
        for entry in TournamentPlayerEntry.objects.filter(tournament=t):
            self.assertIsNone(entry.tier)
            self.assertIsNone(entry.drawn_team)

    def test_setup_only_guard(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="PoolAddLockedCup")
        t.state = "active"
        t.save(update_fields=["state"])
        players = _draw_player_pool(6, prefix="AddLocked")
        self.client.post(
            reverse("tournament_pool_add_existing", args=[t.id]),
            {"players": [str(p.id) for p in players]},
        )
        # Locked ⇒ no entries created.
        self.assertEqual(TournamentPlayerEntry.objects.filter(tournament=t).count(), 0)


class TestTournamentPoolGenerate(TestCase):
    """POST generate creates N fresh Players on the Free Agents Team + pool
    entries (real LG-00 generator, no mocks)."""

    def test_generate_creates_entries_on_free_agents_team(self) -> None:
        from matches.models import TournamentPlayerEntry
        from teams.models import get_free_agents_team

        t = _random_draw_tournament(name="PoolGenCup")
        fa = get_free_agents_team()
        before = fa.players.count()
        response = self.client.post(
            reverse("tournament_pool_generate", args=[t.id]),
            {"count": "6", "mean": "50", "std_dev": "15"},
        )
        self.assertEqual(response.status_code, 302)
        entries = TournamentPlayerEntry.objects.filter(tournament=t)
        self.assertEqual(entries.count(), 6)
        # The generated Players live on the Free Agents Team.
        self.assertEqual(fa.players.count() - before, 6)
        for entry in entries:
            self.assertEqual(entry.player.team_id, fa.id)


class TestTournamentPoolImport(TestCase):
    """POST CSV import — each row = one pool Player on the Free Agents Team;
    error branch re-renders 200 + zero writes."""

    def test_csv_import_creates_one_entry_per_row(self) -> None:
        from matches.models import TournamentPlayerEntry
        from teams.models import get_free_agents_team

        t = _random_draw_tournament(name="PoolCsvCup")
        fa = get_free_agents_team()
        before_players = fa.players.count()
        # 6 well-formed rows (the team column is IGNORED for a player pool).
        rows = _six_role_rows_for("IgnoredTeam", "Csv")
        response = self.client.post(
            reverse("tournament_pool_import", args=[t.id]),
            {"csv_file": _upload(_required_csv(*rows))},
        )
        self.assertEqual(response.status_code, 302)
        entries = TournamentPlayerEntry.objects.filter(tournament=t)
        self.assertEqual(entries.count(), 6)
        # Each CSV row became a Player on the Free Agents Team + a pool entry.
        self.assertEqual(fa.players.count() - before_players, 6)
        for entry in entries:
            self.assertEqual(entry.player.team_id, fa.id)

    def test_csv_error_re_renders_200_with_zero_writes(self) -> None:
        from matches.models import TournamentPlayerEntry
        from teams.models import Player

        t = _random_draw_tournament(name="PoolCsvErrCup")
        players_before = Player.objects.count()
        entries_before = TournamentPlayerEntry.objects.filter(tournament=t).count()
        # A bad role ("captain") ⇒ parse-level RowError.
        bad_rows = [_valid_required_row("BadTeam", "X", role="captain")]
        response = self.client.post(
            reverse("tournament_pool_import", args=[t.id]),
            {"csv_file": _upload(_required_csv(*bad_rows))},
        )
        # Error branch re-renders the detail page (HTTP 200), zero writes.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(
            TournamentPlayerEntry.objects.filter(tournament=t).count(),
            entries_before,
        )

    def test_csv_setup_only_guard(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="PoolCsvLockedCup")
        t.state = "active"
        t.save(update_fields=["state"])
        rows = _six_role_rows_for("IgnoredTeam", "CsvLocked")
        self.client.post(
            reverse("tournament_pool_import", args=[t.id]),
            {"csv_file": _upload(_required_csv(*rows))},
        )
        self.assertEqual(TournamentPlayerEntry.objects.filter(tournament=t).count(), 0)


class TestTournamentPoolRemove(TestCase):
    """POST remove drops a pool entry while in setup."""

    def test_remove_drops_entry(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="PoolRemoveCup")
        players = _draw_player_pool(6, prefix="Rem")
        _pool_entries(t, players)
        victim = players[0]
        response = self.client.post(
            reverse("tournament_pool_remove", args=[t.id]),
            {"player_id": str(victim.id)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TournamentPlayerEntry.objects.filter(tournament=t, player=victim).exists()
        )
        self.assertEqual(TournamentPlayerEntry.objects.filter(tournament=t).count(), 5)


# ---------------------------------------------------------------------------
# Draw — validation / build / re-roll / hand-edit
# ---------------------------------------------------------------------------


class TestTournamentDrawValidation(TestCase):
    """tournament_draw rejects N % 6 != 0 and N < 24 with no writes."""

    def test_rejects_non_divisible_by_six(self) -> None:
        from teams.models import Team

        t = _random_draw_tournament(name="DrawBad6Cup")
        _pool_entries(t, _draw_player_pool(25, prefix="Bad6"))
        teams_before = Team.objects.filter(is_draw_team=True).count()
        response = self.client.post(reverse("tournament_draw", args=[t.id]))
        # Rejected — redirect (flash), no drawn Teams built.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Team.objects.filter(is_draw_team=True).count(), teams_before)

    def test_rejects_below_24(self) -> None:
        from teams.models import Team

        t = _random_draw_tournament(name="DrawBelow24Cup")
        _pool_entries(t, _draw_player_pool(18, prefix="Below24"))
        teams_before = Team.objects.filter(is_draw_team=True).count()
        response = self.client.post(reverse("tournament_draw", args=[t.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Team.objects.filter(is_draw_team=True).count(), teams_before)


class TestTournamentDrawBuild(TestCase):
    """A valid draw builds drawn Teams (is_draw_team=True) + participants and
    fills each entry's tier/drawn_team."""

    def _drawn(self, n: int = 24, *, name: str = "DrawBuildCup") -> Tournament:
        t = _random_draw_tournament(name=name)
        _pool_entries(t, _draw_player_pool(n, prefix=name))
        self.client.post(reverse("tournament_draw", args=[t.id]))
        t.refresh_from_db()
        return t

    def test_builds_n_over_6_drawn_teams(self) -> None:
        from teams.models import Team

        t = self._drawn(24, name="DrawBuild24")
        drawn = Team.objects.filter(is_draw_team=True)
        self.assertEqual(drawn.count(), 4)

    def test_drawn_teams_become_participants(self) -> None:
        t = self._drawn(30, name="DrawBuild30")
        self.assertEqual(t.participants.count(), 5)
        # Every participant Team is a draw team.
        for p in t.participants.select_related("team"):
            self.assertTrue(p.team.is_draw_team)

    def test_every_entry_gets_tier_and_drawn_team(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = self._drawn(24, name="DrawBuildFill")
        entries = TournamentPlayerEntry.objects.filter(tournament=t)
        self.assertEqual(entries.count(), 24)
        for entry in entries:
            self.assertIsNotNone(entry.tier)
            self.assertIn(entry.tier, range(1, 7))
            self.assertIsNotNone(entry.drawn_team_id)
            self.assertTrue(entry.drawn_team.is_draw_team)

    def test_does_not_reassign_player_team(self) -> None:
        from matches.models import TournamentPlayerEntry
        from teams.models import get_free_agents_team

        t = self._drawn(24, name="DrawBuildNoReassign")
        fa = get_free_agents_team()
        # Drawn Teams reference borrowed Players via slot FKs only; Player.team
        # stays the Free Agents Team.
        for entry in TournamentPlayerEntry.objects.filter(tournament=t):
            self.assertEqual(entry.player.team_id, fa.id)


class TestTournamentDrawReroll(TestCase):
    """A re-roll over the SAME pool reproduces the same (deterministic) split —
    idempotent; prior drawn Teams + participants are cleaned up first."""

    def test_reroll_is_idempotent_split(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="DrawRerollCup")
        _pool_entries(t, _draw_player_pool(24, prefix="Reroll"))

        self.client.post(reverse("tournament_draw", args=[t.id]))
        first = {
            e.player_id: e.tier
            for e in TournamentPlayerEntry.objects.filter(tournament=t)
        }

        # Re-roll: compute_draw is deterministic ⇒ the per-player tier split is
        # reproduced exactly.
        self.client.post(reverse("tournament_draw", args=[t.id]))
        second = {
            e.player_id: e.tier
            for e in TournamentPlayerEntry.objects.filter(tournament=t)
        }
        self.assertEqual(first, second, "a re-roll reproduces the same tier split")

    def test_reroll_does_not_multiply_drawn_teams(self) -> None:
        from teams.models import Team

        t = _random_draw_tournament(name="DrawRerollCountCup")
        _pool_entries(t, _draw_player_pool(24, prefix="RerollCount"))
        self.client.post(reverse("tournament_draw", args=[t.id]))
        self.client.post(reverse("tournament_draw", args=[t.id]))
        # Re-roll cleans up the prior drawn Teams ⇒ still exactly 4.
        self.assertEqual(Team.objects.filter(is_draw_team=True).count(), 4)
        t.refresh_from_db()
        self.assertEqual(t.participants.count(), 4)


class TestTournamentDrawEdit(TestCase):
    """Hand-edit mutates a single entry's tier / drawn_team (the variation
    mechanism over the deterministic draw)."""

    def test_hand_edit_mutates_one_entry(self) -> None:
        from matches.models import TournamentPlayerEntry

        t = _random_draw_tournament(name="DrawEditCup")
        _pool_entries(t, _draw_player_pool(24, prefix="Edit"))
        self.client.post(reverse("tournament_draw", args=[t.id]))

        # Pick an entry and move it to a different drawn Team (and/or tier).
        entries = list(TournamentPlayerEntry.objects.filter(tournament=t))
        victim = entries[0]
        other_team = next(
            e.drawn_team for e in entries if e.drawn_team_id != victim.drawn_team_id
        )
        new_tier = 6 if victim.tier != 6 else 1

        response = self.client.post(
            reverse("tournament_draw_edit", args=[t.id]),
            {
                "player_id": str(victim.player_id),
                "tier": str(new_tier),
                "drawn_team": str(other_team.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        victim.refresh_from_db()
        self.assertEqual(victim.tier, new_tier)
        self.assertEqual(victim.drawn_team_id, other_team.id)


# ---------------------------------------------------------------------------
# Lock via the existing tournament_lock over the drawn Teams
# ---------------------------------------------------------------------------


class TestRandomDrawLockReusesTournamentLock(TestCase):
    """Once drawn, the existing tournament_lock reaches 'active' over the drawn
    Teams (lock_and_build is unchanged)."""

    def test_lock_after_draw_activates(self) -> None:
        t = _random_draw_tournament(name="DrawLockCup")
        _pool_entries(t, _draw_player_pool(24, prefix="Lock"))
        self.client.post(reverse("tournament_draw", args=[t.id]))
        response = self.client.post(reverse("tournament_lock", args=[t.id]))
        self.assertEqual(response.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.state, "active")
        # The RR Seeding nodes were built over the 4 drawn Teams.
        self.assertTrue(t.nodes.filter(bracket_type="round_robin").exists())


# ---------------------------------------------------------------------------
# Detail context + pool/draw DOM ids
# ---------------------------------------------------------------------------


class TestRandomDrawDetailContextAndDom(TestCase):
    """``_detail_context`` adds the random_draw keys and the detail page renders
    the pool + draw DOM ids when team_assembly == 'random_draw'."""

    def test_detail_context_has_random_draw_keys(self) -> None:
        t = _random_draw_tournament(name="DrawCtxCup")
        _pool_entries(t, _draw_player_pool(6, prefix="Ctx"))
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        ctx = response.context
        for key in (
            "team_assembly",
            "role_assignment_mode",
            "pool_entries",
            "pool_size",
            "is_drawn",
            "pool_import_form",
            "pool_import_row_errors",
        ):
            self.assertIn(key, ctx, f"missing _detail_context key {key!r}")

    def test_pool_size_reflects_entry_count(self) -> None:
        t = _random_draw_tournament(name="DrawSizeCup")
        _pool_entries(t, _draw_player_pool(6, prefix="Size"))
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertEqual(response.context["pool_size"], 6)

    def test_is_drawn_false_before_draw_true_after(self) -> None:
        t = _random_draw_tournament(name="DrawIsDrawnCup")
        _pool_entries(t, _draw_player_pool(24, prefix="IsDrawn"))
        before = self.client.get(reverse("tournament_detail", args=[t.id])).context[
            "is_drawn"
        ]
        self.assertFalse(before)
        self.client.post(reverse("tournament_draw", args=[t.id]))
        after = self.client.get(reverse("tournament_detail", args=[t.id])).context[
            "is_drawn"
        ]
        self.assertTrue(after)

    def test_pool_section_dom_ids_render(self) -> None:
        t = _random_draw_tournament(name="DrawPoolDomCup")
        _pool_entries(t, _draw_player_pool(6, prefix="PoolDom"))
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        for dom_id in (
            "tournament-pool-section",
            "tournament-pool-add-existing-form",
            "tournament-pool-generate-form",
            "tournament-pool-import-form",
            "tournament-pool-table",
            "tournament-pool-size",
            "tournament-draw-form",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing pool DOM id {dom_id!r}")

    def test_invalid_pool_notice_when_size_invalid(self) -> None:
        t = _random_draw_tournament(name="DrawInvalidNoticeCup")
        _pool_entries(t, _draw_player_pool(6, prefix="Invalid"))  # 6 < 24
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-pool-invalid-notice"', body)
        # The notice mentions the locked constraints.
        self.assertIn("at least 24", body)

    def test_draw_table_and_reroll_render_after_draw(self) -> None:
        t = _random_draw_tournament(name="DrawTableCup")
        _pool_entries(t, _draw_player_pool(24, prefix="Table"))
        self.client.post(reverse("tournament_draw", args=[t.id]))
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertIn('id="tournament-draw-table"', body)
        self.assertIn('id="tournament-draw-reroll-submit"', body)
        # One section per drawn team.
        for team in t.participants.all():
            self.assertIn(
                f'id="tournament-draw-team-{team.team_id}"',
                body,
                "missing per-drawn-team draw-table section",
            )

    def test_preset_tournament_omits_pool_section(self) -> None:
        # A preset tournament must NOT render the pool surface.
        t = _active_tournament(4, name="PresetNoPool")
        body = self.client.get(
            reverse("tournament_detail", args=[t.id])
        ).content.decode()
        self.assertNotIn('id="tournament-pool-section"', body)
