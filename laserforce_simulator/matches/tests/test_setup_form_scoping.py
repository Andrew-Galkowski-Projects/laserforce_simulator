"""ANG-01b -- ownership scoping of the sandbox setup Forms.

Seam contract §4 / §7. ``MatchSetupForm`` / ``SingleRoundSetupForm`` take a
keyword-only ``user`` and narrow their team choices to the valid-roster subset
of ``owned_queryset(Team.objects.regular(), user)``. ANG-08 deleted the HTML
create / picker / batch-save views (and ``BatchSimulateForm``) these tests
also drove; the API equivalents are pinned in the ``test_api_*`` files.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from conftest import get_shared_manager
from matches.forms import MatchSetupForm, SingleRoundSetupForm
from matches.models import League
from matches.tests.conftest import make_team_with_slots
from teams.models import FREE_AGENTS_TEAM_NAME, Team

User = get_user_model()

_FORMS = (MatchSetupForm, SingleRoundSetupForm)


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
