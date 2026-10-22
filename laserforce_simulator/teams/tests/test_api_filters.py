"""ANG-01a — Team / Player list filters and ordering (seam contract §4, §5.10).

``TeamFilterSet`` (``name``, free-agent default exclusion,
``include_free_agents``), ``PlayerFilterSet`` (``team``, ``name``) and the
player ordering over exactly the 23 LG-00c ``_SORT_KEYS``. Filters are
list-only: detail lookups ignore query params.

Boolean filters are lenient (an unparseable value is treated as absent) — no
test asserts a 400 on a boolean.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import League
from teams.api_views import PlayerViewSet
from teams.models import Player, Team, get_free_agents_team
from teams.views import _SORT_KEYS

User = get_user_model()


class _FilterTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="api-filters-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)

    def names(self, path: str, params: dict | None = None) -> list[str]:
        response = self.api.get(path, {"page_size": 100, **(params or {})})
        self.assertEqual(response.status_code, 200, response.content)
        return [row["name"] for row in response.json()["data"]]


# ---------------------------------------------------------------------------
# TeamFilterSet
# ---------------------------------------------------------------------------


class TestTeamFilterSet(_FilterTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.red = Team.objects.create(name="Red Phoenix", manager=self.user_a)
        self.blue = Team.objects.create(name="Blue Ice", manager=self.user_a)
        self.free_agents = get_free_agents_team()
        self.pool = Team.objects.create(name="Spring Pool", manager=self.user_a)
        league = League.objects.create(name="Spring", manager=self.user_a)
        league.free_agent_pool = self.pool
        league.save(update_fields=["free_agent_pool"])

    def test_name_icontains(self) -> None:
        self.assertEqual(self.names("/api/teams/", {"name": "phoe"}), ["Red Phoenix"])

    def test_name_no_match_is_empty_200(self) -> None:
        self.assertEqual(self.names("/api/teams/", {"name": "zzz-none"}), [])

    def test_default_excludes_every_free_agent_pool(self) -> None:
        names = self.names("/api/teams/")
        self.assertIn("Red Phoenix", names)
        self.assertIn("Blue Ice", names)
        self.assertNotIn("Free Agents", names)
        self.assertNotIn("Spring Pool", names)

    def test_include_free_agents_true_includes_pools(self) -> None:
        names = self.names("/api/teams/", {"include_free_agents": "true"})
        self.assertIn("Free Agents", names)
        self.assertIn("Spring Pool", names)
        self.assertIn("Red Phoenix", names)

    def test_include_free_agents_accepts_1(self) -> None:
        self.assertIn(
            "Free Agents", self.names("/api/teams/", {"include_free_agents": "1"})
        )

    def test_include_free_agents_false_excludes(self) -> None:
        names = self.names("/api/teams/", {"include_free_agents": "false"})
        self.assertNotIn("Free Agents", names)
        self.assertNotIn("Spring Pool", names)

    def test_unparseable_boolean_is_lenient_and_excludes(self) -> None:
        """BooleanWidget ignores an unparseable value (treated as absent)."""
        names = self.names("/api/teams/", {"include_free_agents": "maybe"})
        self.assertNotIn("Free Agents", names)
        self.assertIn("Red Phoenix", names)

    def test_free_agents_detail_still_reachable_by_id(self) -> None:
        response = self.api.get(f"/api/teams/{self.free_agents.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["name"], "Free Agents")

    def test_detail_ignores_list_query_params(self) -> None:
        response = self.api.get(
            f"/api/teams/{self.red.pk}/", {"name": "zzz-none", "page": "999"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["id"], self.red.pk)

    def test_unknown_param_is_ignored(self) -> None:
        self.assertIn("Red Phoenix", self.names("/api/teams/", {"colour": "red"}))

    def test_foreign_teams_never_listed(self) -> None:
        Team.objects.create(name="Red Foreign", manager=self.user_b)
        names = self.names("/api/teams/", {"name": "red"})
        self.assertEqual(names, ["Red Phoenix"])

    def test_team_ordering(self) -> None:
        self.red.wins = 5
        self.red.save()
        self.blue.wins = 9
        self.blue.save()
        self.assertEqual(
            self.names("/api/teams/", {"ordering": "-wins"})[:2],
            ["Blue Ice", "Red Phoenix"],
        )
        # Default ordering is by name.
        self.assertEqual(self.names("/api/teams/"), sorted(self.names("/api/teams/")))


# ---------------------------------------------------------------------------
# PlayerFilterSet
# ---------------------------------------------------------------------------


class TestPlayerFilterSet(_FilterTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alpha = Team.objects.create(name="Alpha", manager=self.user_a)
        self.bravo = Team.objects.create(name="Bravo", manager=self.user_a)
        Player.objects.create(team=self.alpha, name="Alpine")
        Player.objects.create(team=self.alpha, name="Zed")
        Player.objects.create(team=self.bravo, name="Alfred")
        self.theirs = Team.objects.create(name="Theirs", manager=self.user_b)
        Player.objects.create(team=self.theirs, name="Alpha Foreigner")

    def test_team_filter_exact(self) -> None:
        self.assertEqual(
            sorted(self.names("/api/players/", {"team": self.alpha.pk})),
            ["Alpine", "Zed"],
        )

    def test_name_icontains(self) -> None:
        self.assertEqual(
            sorted(self.names("/api/players/", {"name": "alp"})), ["Alpine"]
        )

    def test_team_and_name_combine(self) -> None:
        self.assertEqual(
            self.names("/api/players/", {"team": self.bravo.pk, "name": "al"}),
            ["Alfred"],
        )

    def test_non_numeric_team_is_400_with_param_field(self) -> None:
        response = self.api.get("/api/players/", {"team": "abc"})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(
            [(e["code"], e["field"]) for e in body["errors"]], [("invalid", "team")]
        )

    def test_foreign_team_id_is_200_empty(self) -> None:
        response = self.api.get("/api/players/", {"team": self.theirs.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])
        self.assertEqual(response.json()["meta"]["count"], 0)

    def test_nonexistent_team_id_is_200_empty(self) -> None:
        response = self.api.get("/api/players/", {"team": 999999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_foreign_players_never_listed(self) -> None:
        self.assertNotIn("Alpha Foreigner", self.names("/api/players/"))

    def test_default_order_is_team_then_name(self) -> None:
        self.assertEqual(self.names("/api/players/"), ["Alpine", "Zed", "Alfred"])


# ---------------------------------------------------------------------------
# Player ordering (§5.10)
# ---------------------------------------------------------------------------


class TestPlayerOrdering(_FilterTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Ordering", manager=self.user_a)
        self.low = Player.objects.create(
            team=self.team, name="Low", Offensive_synergy=10, accuracy=10
        )
        self.mid = Player.objects.create(
            team=self.team, name="Mid", Offensive_synergy=50, accuracy=90
        )
        self.high = Player.objects.create(
            team=self.team, name="High", Offensive_synergy=90, accuracy=50
        )

    def test_ordering_fields_equal_sort_keys(self) -> None:
        self.assertEqual(set(PlayerViewSet.ordering_fields), set(_SORT_KEYS))
        self.assertEqual(len(set(PlayerViewSet.ordering_fields)), 23)

    def test_every_sort_key_accepted_both_directions(self) -> None:
        for key in _SORT_KEYS:
            for term in (key, f"-{key}"):
                with self.subTest(ordering=term):
                    response = self.api.get("/api/players/", {"ordering": term})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(response.json()["meta"]["count"], 3)

    def test_offensive_synergy_alias_orders_by_capital_o_field(self) -> None:
        self.assertEqual(
            self.names("/api/players/", {"ordering": "offensive_synergy"}),
            ["Low", "Mid", "High"],
        )
        self.assertEqual(
            self.names("/api/players/", {"ordering": "-offensive_synergy"}),
            ["High", "Mid", "Low"],
        )

    def test_raw_orm_name_is_dropped(self) -> None:
        """``Offensive_synergy`` is not a public term → default (team, name)."""
        self.assertEqual(
            self.names("/api/players/", {"ordering": "-Offensive_synergy"}),
            ["High", "Low", "Mid"],
        )

    def test_preferred_roles_is_dropped(self) -> None:
        response = self.api.get("/api/players/", {"ordering": "preferred_roles"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [r["name"] for r in response.json()["data"]], ["High", "Low", "Mid"]
        )

    def test_bogus_ordering_is_dropped_not_400(self) -> None:
        response = self.api.get("/api/players/", {"ordering": "nonsense"})
        self.assertEqual(response.status_code, 200)

    def test_overall_rating_desc(self) -> None:
        # Overall = mean of 19 stats; only Offensive_synergy / accuracy differ.
        # Low: 10+10, Mid: 50+90=140, High: 90+50=140 → Mid/High tie, then name.
        self.low.stamina = 0
        self.low.save()
        self.high.stamina = 100
        self.high.save()
        self.assertEqual(
            self.names("/api/players/", {"ordering": "-overall_rating"}),
            ["High", "Mid", "Low"],
        )
        self.assertEqual(
            self.names("/api/players/", {"ordering": "overall_rating"}),
            ["Low", "Mid", "High"],
        )

    def test_team_alias_orders_by_team_name(self) -> None:
        other = Team.objects.create(name="Aardvarks", manager=self.user_a)
        Player.objects.create(team=other, name="Zulu")
        self.assertEqual(self.names("/api/players/", {"ordering": "team"})[0], "Zulu")
        self.assertEqual(self.names("/api/players/", {"ordering": "-team"})[-1], "Zulu")

    def test_tiebreak_is_name(self) -> None:
        for p in (self.low, self.mid, self.high):
            p.accuracy = 70
            p.save()
        self.assertEqual(
            self.names("/api/players/", {"ordering": "accuracy"}),
            ["High", "Low", "Mid"],
        )

    def test_potential_ordering_accepted(self) -> None:
        self.low.potential = 80.0
        self.low.save()
        self.mid.potential = 60.0
        self.mid.save()
        self.high.potential = 70.0
        self.high.save()
        self.assertEqual(
            self.names("/api/players/", {"ordering": "-potential"}),
            ["Low", "High", "Mid"],
        )

    def test_serializer_exposes_property_not_annotation(self) -> None:
        # ANG-06b added ``overall_rating`` (the model property) to
        # PlayerSerializer on purpose; the ordering annotation must still not
        # leak into the output.
        row = self.api.get("/api/players/").json()["data"][0]
        self.assertNotIn("overall_rating_db", row)
        player = Player.objects.get(pk=row["id"])
        self.assertAlmostEqual(row["overall_rating"], player.overall_rating)
