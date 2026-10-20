"""ANG-01f-3 -- the HTML create page binds the strict ``TournamentCreateForm``
(spec #190, ticket #193).

Regressions: the team picker is scoped to the requesting Account (the leak
fix), a junk value is a form error instead of a silent default, a valid POST
still redirects to the detail page, the Manager is stamped, and the page offers
"Player pool draw" with the RR->DE preselect.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from matches.models import Tournament
from matches.tests.league_api_fixtures import make_teams

User = get_user_model()


class TestCreatePageTeamPickerScope(TestCase):
    def setUp(self) -> None:
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="ang01f3-other@example.com", password="Str0ng-Passphrase-9"
        )

    def test_picker_lists_own_and_unmanaged_but_not_another_accounts_teams(
        self,
    ) -> None:
        mine = make_teams("Mine", 1, manager=self.me)[0]
        unmanaged = make_teams("Unmanaged", 1)[0]
        foreign = make_teams("Foreign", 1, manager=self.other)[0]
        response = self.client.get(reverse("tournament_create"))
        self.assertEqual(response.status_code, 200)
        listed = {t.id for t in response.context["available_teams"]}
        self.assertEqual(listed, {mine.id, unmanaged.id})
        body = response.content.decode()
        start = body.index('id="tournament-create-team-select"')
        picker = body[start : body.index("</select>", start)]
        self.assertIn(f'<option value="{mine.id}">{mine.name}</option>', picker)
        self.assertNotIn(f'<option value="{foreign.id}">', picker)
        self.assertNotIn(foreign.name, body)

    def test_post_with_another_accounts_team_is_rejected(self) -> None:
        ids = [t.id for t in make_teams("M", 3, manager=self.me)]
        foreign = make_teams("Foreign", 1, manager=self.other)[0]
        response = self.client.post(
            reverse("tournament_create"),
            {"name": "Leak Cup", "teams": ids + [foreign.id]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tournament.objects.filter(name="Leak Cup").exists())


class TestCreatePageStrictValidation(TestCase):
    def test_junk_format_shows_error_instead_of_single_elimination(self) -> None:
        ids = [t.id for t in make_teams("J", 4)]
        response = self.client.post(
            reverse("tournament_create"),
            {"name": "Junk Cup", "teams": ids, "format": "not_a_format"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "matches/tournament_create.html")
        self.assertFalse(Tournament.objects.filter(name="Junk Cup").exists())
        flashed = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("not_a_format" in m for m in flashed), flashed)
        self.assertIn("not_a_format", response.content.decode())

    def test_missing_name_keeps_old_message(self) -> None:
        ids = [t.id for t in make_teams("N", 4)]
        response = self.client.post(reverse("tournament_create"), {"teams": ids})
        self.assertEqual(response.status_code, 200)
        flashed = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Tournament name is required.", flashed)

    def test_too_few_teams_keeps_old_message(self) -> None:
        ids = [t.id for t in make_teams("F", 3)]
        response = self.client.post(
            reverse("tournament_create"), {"name": "Few", "teams": ids}
        )
        self.assertEqual(response.status_code, 200)
        flashed = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("A tournament requires at least 4 teams." in m for m in flashed),
            flashed,
        )


class TestCreatePageValidPost(TestCase):
    def test_valid_post_redirects_to_detail_and_stamps_manager(self) -> None:
        ids = [t.id for t in make_teams("V", 4)]
        response = self.client.post(
            reverse("tournament_create"),
            {"name": "Valid Cup", "teams": ids, "format": "double_elimination"},
        )
        t = Tournament.objects.get(name="Valid Cup")
        self.assertRedirects(
            response,
            reverse("tournament_detail", args=[t.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(t.manager, get_shared_manager())
        self.assertEqual(t.participants.count(), 4)

    def test_player_pool_post_redirects_with_empty_pool(self) -> None:
        response = self.client.post(
            reverse("tournament_create"),
            {
                "name": "Pool Cup",
                "team_assembly": "player_pool",
                "format": "round_robin_double_elim",
            },
        )
        t = Tournament.objects.get(name="Pool Cup")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(t.team_assembly, "player_pool")
        self.assertEqual(t.participants.count(), 0)
        self.assertEqual(t.manager, get_shared_manager())


class TestCreatePagePlayerPoolPreselect(TestCase):
    def test_page_offers_player_pool_draw(self) -> None:
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn('<option value="player_pool">Player pool draw</option>', body)

    def test_picking_player_pool_preselects_rrde(self) -> None:
        # The preselect is client-side: the assembly change handler sets the
        # format select to round_robin_double_elim.
        body = self.client.get(reverse("tournament_create")).content.decode()
        self.assertIn("tournamentAssemblyChanged(this.value)", body)
        start = body.index("function tournamentAssemblyChanged")
        handler = body[start : start + 800]
        self.assertIn('"player_pool"', handler)
        self.assertIn('"round_robin_double_elim"', handler)
