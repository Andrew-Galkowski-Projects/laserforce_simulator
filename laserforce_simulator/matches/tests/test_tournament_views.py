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


class TestTournamentCreateSeriesLength(TestCase):
    """GET form renders the series-length <select>; POST persists 1/3/5 with a
    forgiving fallback to 1 on invalid input."""

    def test_get_form_renders_series_length_select(self) -> None:
        make_team_with_slots("Existing")
        response = self.client.get(reverse("tournament_create"))
        self.assertIn('id="tournament-create-series-length"', response.content.decode())

    def test_post_series_length_three_persisted(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Bo3 Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "series_length": "3",
            },
        )
        t = Tournament.objects.get(name="Bo3 Cup")
        self.assertEqual(t.series_length, 3)

    def test_post_series_length_five_persisted(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Bo5 Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "series_length": "5",
            },
        )
        t = Tournament.objects.get(name="Bo5 Cup")
        self.assertEqual(t.series_length, 5)

    def test_post_invalid_series_length_falls_back_to_one(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Bad Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "series_length": "4",  # not a valid choice (1/3/5)
            },
        )
        t = Tournament.objects.get(name="Bad Cup")
        self.assertEqual(t.series_length, 1)

    def test_post_junk_series_length_falls_back_to_one(self) -> None:
        teams = _make_teams(4)
        self.client.post(
            reverse("tournament_create"),
            {
                "name": "Junk Cup",
                "teams": [str(t.id) for t in teams],
                "generate_count": "0",
                "generate_ppt": "6",
                "series_length": "abc",
            },
        )
        t = Tournament.objects.get(name="Junk Cup")
        self.assertEqual(t.series_length, 1)

    def test_post_missing_series_length_defaults_to_one(self) -> None:
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
        self.assertEqual(t.series_length, 1)


class TestTournamentDetailSeriesScore(TestCase):
    """A locked Bo3 tournament renders the per-node running series score; a
    completed tournament still shows the champion banner."""

    def _bo3_active(self, n: int = 4, *, name: str = "Bo3Detail") -> Tournament:
        t = _setup_tournament(n, name=name)
        t.series_length = 3
        t.save(update_fields=["series_length"])
        t.lock_and_build()
        t.refresh_from_db()
        return t

    def test_node_series_score_dom_id_present(self) -> None:
        t = self._bo3_active()
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        body = response.content.decode()
        # The round-1 node at position 0 carries the series-score element.
        self.assertIn('id="tournament-node-series-score-1-0"', body)

    def test_series_score_shows_running_wins(self) -> None:
        from matches.models import Match, SeriesMatch

        t = self._bo3_active()
        node = t.find_next_playable_node()
        # Record one game won by team_a so the running score reads 1-0.
        match = Match.objects.create(
            team_red=node.team_a,
            team_blue=node.team_b,
            match_type="tournament",
        )
        SeriesMatch.objects.create(
            node=node, match=match, game_number=1, winner=node.team_a
        )
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        body = response.content.decode()
        score_id = (
            f'id="tournament-node-series-score-'
            f'{node.bracket_round}-{node.position}"'
        )
        self.assertIn(score_id, body)
        # The running wins_a-wins_b (1-0) appears in the rendered score element.
        marker = body.index(score_id)
        window = body[marker : marker + 400]
        self.assertIn("1", window)
        self.assertIn("0", window)

    def test_completed_bo3_shows_champion_banner(self) -> None:
        from matches.tournament_engine import play_next_node

        t = self._bo3_active(name="Bo3Completed")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for _ in range(30):
                t.refresh_from_db()
                if t.state == "completed":
                    break
                if play_next_node(t) is None:
                    break
        t.refresh_from_db()
        self.assertEqual(t.state, "completed")
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertIn('id="tournament-champion-banner"', response.content.decode())
