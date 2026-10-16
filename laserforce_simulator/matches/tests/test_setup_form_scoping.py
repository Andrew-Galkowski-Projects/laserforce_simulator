"""ANG-01b -- ownership scoping of the sandbox setup Forms + HTML pickers.

Seam contract §4 / §7. ``MatchSetupForm`` / ``SingleRoundSetupForm`` /
``BatchSimulateForm`` take a keyword-only ``user`` and narrow their team
choices to the valid-roster subset of ``owned_queryset(Team.objects.regular(),
user)``. The HTML compare / h2h / player-h2h pickers (and their preselect
lookups) are scoped to owned + Unmanaged rows, and the HTML batch save stamps
the saver as the rounds' Manager.

Django's ``self.client`` is logged in as the shared Account by the root
conftest autouse fixture.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from matches.forms import BatchSimulateForm, MatchSetupForm, SingleRoundSetupForm
from matches.models import GameRound, League, Match
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import FREE_AGENTS_TEAM_NAME, Player, Team

User = get_user_model()

_FORMS = (MatchSetupForm, SingleRoundSetupForm, BatchSimulateForm)


class _ScopingTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-scoping-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine, _ = make_team_with_slots("ScopeMine")
        self.mine.manager = self.user_a
        self.mine.save()
        self.unmanaged, _ = make_team_with_slots("ScopeUnmanaged")
        self.foreign, _ = make_team_with_slots("ScopeForeign")
        self.foreign.manager = self.user_b
        self.foreign.save()
        self.free_agents, _ = make_team_with_slots("ScopeFA")
        self.free_agents.name = FREE_AGENTS_TEAM_NAME
        self.free_agents.save()
        self.league_pool, _ = make_team_with_slots("ScopePool")
        League.objects.create(name="Scope League", free_agent_pool=self.league_pool)
        self.invalid = Team.objects.create(name="Scope No Slots")


class TestSetupFormScoping(_ScopingTestCase):
    def _choice_ids(self, form_cls, field: str, user) -> set[int]:
        form = form_cls(user=user)
        return set(form.fields[field].queryset.values_list("id", flat=True))

    def test_user_sees_own_and_unmanaged(self) -> None:
        for form_cls in _FORMS:
            for field in ("team_red", "team_blue"):
                with self.subTest(form=form_cls.__name__, field=field):
                    ids = self._choice_ids(form_cls, field, self.user_a)
                    self.assertIn(self.mine.pk, ids)
                    self.assertIn(self.unmanaged.pk, ids)

    def test_excludes_foreign_free_agents_pool_and_invalid(self) -> None:
        for form_cls in _FORMS:
            for field in ("team_red", "team_blue"):
                with self.subTest(form=form_cls.__name__, field=field):
                    ids = self._choice_ids(form_cls, field, self.user_a)
                    self.assertNotIn(self.foreign.pk, ids)
                    self.assertNotIn(self.free_agents.pk, ids)
                    self.assertNotIn(self.league_pool.pk, ids)
                    self.assertNotIn(self.invalid.pk, ids)

    def test_user_none_sees_only_unmanaged(self) -> None:
        for form_cls in _FORMS:
            with self.subTest(form=form_cls.__name__):
                ids = self._choice_ids(form_cls, "team_red", None)
                self.assertIn(self.unmanaged.pk, ids)
                self.assertNotIn(self.mine.pk, ids)
                self.assertNotIn(self.foreign.pk, ids)

    def test_bound_form_accepts_user_kwarg(self) -> None:
        for form_cls in _FORMS:
            with self.subTest(form=form_cls.__name__):
                form = form_cls({}, user=self.user_a)
                self.assertFalse(form.is_valid())

    def test_foreign_id_is_invalid_choice(self) -> None:
        form = MatchSetupForm(
            data={
                "team_red": self.foreign.pk,
                "team_blue": self.mine.pk,
                "match_type": "friendly",
            },
            user=self.user_a,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["team_red"][0].code, "invalid_choice")


class TestHtmlCreateScoping(_ScopingTestCase):
    def test_create_match_with_foreign_team_is_form_error(self) -> None:
        before = Match.objects.count()
        response = self.client.post(
            reverse("create_match"),
            {
                "team_red": self.foreign.pk,
                "team_blue": self.mine.pk,
                "match_type": "friendly",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("team_red", response.context["form"].errors)
        self.assertEqual(Match.objects.count(), before)

    def test_create_single_round_with_foreign_team_is_form_error(self) -> None:
        before = GameRound.objects.count()
        response = self.client.post(
            reverse("create_single_round"),
            {"team_red": self.mine.pk, "team_blue": self.foreign.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("team_blue", response.context["form"].errors)
        self.assertEqual(GameRound.objects.count(), before)

    def test_create_match_with_own_teams_still_works(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = self.client.post(
                reverse("create_match"),
                {
                    "team_red": self.mine.pk,
                    "team_blue": self.unmanaged.pk,
                    "match_type": "friendly",
                },
            )
        self.assertEqual(response.status_code, 302)
        match = Match.objects.latest("id")
        self.assertEqual(match.manager_id, self.user_a.pk)

    def test_create_match_page_choices_exclude_foreign(self) -> None:
        response = self.client.get(reverse("create_match"))
        ids = set(
            response.context["form"]
            .fields["team_red"]
            .queryset.values_list("id", flat=True)
        )
        self.assertNotIn(self.foreign.pk, ids)
        self.assertIn(self.mine.pk, ids)

    def test_simulate_batch_with_foreign_team_is_form_error(self) -> None:
        response = self.client.post(
            reverse("simulate_batch"),
            {"team_red": self.foreign.pk, "team_blue": self.mine.pk, "n": "10"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("team_red", response.context["form"].errors)


class TestHtmlPickerScoping(_ScopingTestCase):
    def test_head_to_head_picker_excludes_foreign_team(self) -> None:
        response = self.client.get(reverse("head_to_head"))
        ids = {team.pk for team in response.context["all_teams"]}
        self.assertIn(self.mine.pk, ids)
        self.assertIn(self.unmanaged.pk, ids)
        self.assertNotIn(self.foreign.pk, ids)

    def test_head_to_head_preselect_does_not_leak_foreign_team(self) -> None:
        response = self.client.get(reverse("head_to_head"), {"team_a": self.foreign.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["team_a"])
        self.assertNotContains(response, self.foreign.name)

    def test_player_head_to_head_picker_excludes_foreign_players(self) -> None:
        response = self.client.get(reverse("player_head_to_head"))
        ids = {player.pk for player in response.context["all_players"]}
        foreign_ids = set(
            Player.objects.filter(team=self.foreign).values_list("id", flat=True)
        )
        mine_ids = set(
            Player.objects.filter(team=self.mine).values_list("id", flat=True)
        )
        self.assertFalse(ids & foreign_ids)
        self.assertTrue(mine_ids <= ids)

    def test_player_head_to_head_preselect_does_not_leak(self) -> None:
        foreign_player = Player.objects.filter(team=self.foreign).first()
        response = self.client.get(
            reverse("player_head_to_head"), {"player_a": foreign_player.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["player_a"])

    def test_compare_picker_excludes_foreign_round(self) -> None:
        mine_round = GameRound.objects.create(
            round_number=1, team_red=self.mine, team_blue=self.unmanaged
        )
        foreign_round = GameRound.objects.create(
            round_number=1,
            team_red=self.mine,
            team_blue=self.unmanaged,
            manager=self.user_b,
        )
        response = self.client.get(reverse("compare_rounds"))
        ids = {gr.pk for gr in response.context["all_rounds"]}
        self.assertIn(mine_round.pk, ids)
        self.assertNotIn(foreign_round.pk, ids)


class TestHtmlBatchSaveStampsManager(_ScopingTestCase):
    def test_html_save_stamps_saver_as_manager(self) -> None:
        session = self.client.session
        session["batch_seeds"] = {
            "job_id": "ang01b-html-job",
            "team_red_id": self.mine.pk,
            "team_blue_id": self.unmanaged.pk,
            "arena_map_id": None,
            "avg_seeds": [[12345, False]],
            "outlier_seeds": [],
        }
        session.save()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = self.client.post(
                reverse("save_batch_games"), {"game_type": "avg", "n": "1"}
            )
        self.assertEqual(response.status_code, 200, response.content)
        job_id = json.loads(response.content)["job_id"]

        status = json.loads(
            self.client.get(reverse("save_batch_status", args=[job_id])).content
        )
        self.assertEqual(status["status"], "complete")
        self.assertTrue(status["round_ids"])
        for game_round in GameRound.objects.filter(id__in=status["round_ids"]):
            self.assertEqual(game_round.manager_id, self.user_a.pk)

    def test_html_save_json_shape_unchanged(self) -> None:
        session = self.client.session
        session["batch_seeds"] = {
            "job_id": "ang01b-html-job-2",
            "team_red_id": self.mine.pk,
            "team_blue_id": self.unmanaged.pk,
            "arena_map_id": None,
            "avg_seeds": [[54321, False]],
            "outlier_seeds": [],
        }
        session.save()
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = self.client.post(
                reverse("save_batch_games"), {"game_type": "avg", "n": "1"}
            )
        self.assertEqual(set(json.loads(response.content)), {"job_id"})
