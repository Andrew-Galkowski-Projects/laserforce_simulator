"""ANG-01a — Match / GameRound / GameEvent API filters and ordering
(seam contract §4, §5.11).

``MatchFilterSet`` (``team`` either side, ``season``, ``match_type``,
``date_played_after`` / ``_before``), ``GameRoundFilterSet`` (``match``,
``arena_map``), the ``events`` action's ``GameEventFilterSet`` (``event_type``,
``actor``, ``target``, ``timestamp_min`` / ``_max``), fixed ``timestamp``
ordering + pagination on events, and match / round ordering. Typed filters
with unparseable values are 400 envelopes whose ``field`` is the param (range
filters report the base name).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.models import ArenaMap
from matches.models import GameEvent, GameRound, League, Match, Season
from matches.tests.conftest import make_team_with_slots

User = get_user_model()


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc)


class _ApiFilterCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="match-filters-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)

    def get_ok(self, path: str, params: dict | None = None) -> dict:
        response = self.api.get(path, {"page_size": 100, **(params or {})})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def ids(self, path: str, params: dict | None = None) -> list[int]:
        return [row["id"] for row in self.get_ok(path, params)["data"]]

    def assert_filter_400(self, path: str, params: dict, field: str, code: str) -> None:
        response = self.api.get(path, params)
        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertIn((code, field), [(e["code"], e["field"]) for e in body["errors"]])


# ---------------------------------------------------------------------------
# MatchFilterSet
# ---------------------------------------------------------------------------


class TestMatchFilterSet(_ApiFilterCase):
    def setUp(self) -> None:
        super().setUp()
        self.ta, _ = make_team_with_slots("FltA")
        self.tb, _ = make_team_with_slots("FltB")
        self.tc, _ = make_team_with_slots("FltC")
        league = League.objects.create(name="Flt League", manager=self.user_a)
        self.season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        self.m1 = Match.objects.create(
            team_red=self.ta, team_blue=self.tb, match_type="friendly"
        )
        self.m2 = Match.objects.create(
            team_red=self.tc, team_blue=self.ta, match_type="tournament"
        )
        self.m3 = Match.objects.create(
            team_red=self.tb,
            team_blue=self.tc,
            match_type="friendly",
            season=self.season,
        )
        Match.objects.filter(pk=self.m1.pk).update(date_played=_dt(1))
        Match.objects.filter(pk=self.m2.pk).update(date_played=_dt(2))
        Match.objects.filter(pk=self.m3.pk).update(date_played=_dt(3))

    def test_team_matches_either_side(self) -> None:
        self.assertEqual(
            set(self.ids("/api/matches/", {"team": self.ta.pk})),
            {self.m1.pk, self.m2.pk},
        )

    def test_team_non_numeric_is_400(self) -> None:
        self.assert_filter_400("/api/matches/", {"team": "abc"}, "team", "invalid")

    def test_season(self) -> None:
        self.assertEqual(
            self.ids("/api/matches/", {"season": self.season.pk}), [self.m3.pk]
        )

    def test_match_type(self) -> None:
        self.assertEqual(
            self.ids("/api/matches/", {"match_type": "tournament"}), [self.m2.pk]
        )

    def test_invalid_match_type_is_400_invalid_choice(self) -> None:
        self.assert_filter_400(
            "/api/matches/", {"match_type": "bogus"}, "match_type", "invalid_choice"
        )

    def test_date_range_inclusive_by_calendar_day(self) -> None:
        self.assertEqual(
            set(
                self.ids(
                    "/api/matches/",
                    {
                        "date_played_after": "2026-01-02",
                        "date_played_before": "2026-01-03",
                    },
                )
            ),
            {self.m2.pk, self.m3.pk},
        )

    def test_date_after_only(self) -> None:
        self.assertEqual(
            set(self.ids("/api/matches/", {"date_played_after": "2026-01-03"})),
            {self.m3.pk},
        )

    def test_date_before_only(self) -> None:
        self.assertEqual(
            set(self.ids("/api/matches/", {"date_played_before": "2026-01-01"})),
            {self.m1.pk},
        )

    def test_invalid_date_is_400_on_base_name(self) -> None:
        self.assert_filter_400(
            "/api/matches/",
            {"date_played_after": "not-a-date"},
            "date_played",
            "invalid",
        )

    def test_default_ordering_newest_first(self) -> None:
        ids = self.ids("/api/matches/")
        self.assertEqual(ids[:3], [self.m3.pk, self.m2.pk, self.m1.pk])

    def test_ordering_date_played_ascending(self) -> None:
        ids = self.ids("/api/matches/", {"ordering": "date_played"})
        self.assertEqual(ids, [self.m1.pk, self.m2.pk, self.m3.pk])

    def test_ordering_by_id(self) -> None:
        self.assertEqual(
            self.ids("/api/matches/", {"ordering": "-id"}),
            sorted([self.m1.pk, self.m2.pk, self.m3.pk], reverse=True),
        )

    def test_invalid_ordering_dropped(self) -> None:
        ids = self.ids("/api/matches/", {"ordering": "match_type"})
        self.assertEqual(ids, [self.m3.pk, self.m2.pk, self.m1.pk])

    def test_filters_do_not_apply_to_detail(self) -> None:
        response = self.api.get(
            f"/api/matches/{self.m1.pk}/", {"match_type": "bogus", "team": "abc"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["id"], self.m1.pk)

    def test_foreign_match_hidden_and_404(self) -> None:
        theirs = Match.objects.create(
            team_red=self.ta, team_blue=self.tb, manager=self.user_b
        )
        self.assertNotIn(theirs.pk, self.ids("/api/matches/", {"team": self.ta.pk}))
        response = self.api.get(f"/api/matches/{theirs.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")


# ---------------------------------------------------------------------------
# GameRoundFilterSet
# ---------------------------------------------------------------------------


class TestGameRoundFilterSet(_ApiFilterCase):
    def setUp(self) -> None:
        super().setUp()
        red, _ = make_team_with_slots("RndR")
        blue, _ = make_team_with_slots("RndB")
        self.map_a = ArenaMap.objects.create(name="Map A", image="maps/a.png")
        self.match = Match.objects.create(team_red=red, team_blue=blue)
        self.r1 = GameRound.objects.create(
            match=self.match,
            round_number=1,
            team_red=red,
            team_blue=blue,
            arena_map=self.map_a,
        )
        self.r2 = GameRound.objects.create(
            match=self.match, round_number=2, team_red=red, team_blue=blue
        )
        self.solo = GameRound.objects.create(
            round_number=1, team_red=red, team_blue=blue
        )
        GameRound.objects.filter(pk=self.r1.pk).update(date_played=_dt(1))
        GameRound.objects.filter(pk=self.r2.pk).update(date_played=_dt(2))
        GameRound.objects.filter(pk=self.solo.pk).update(date_played=_dt(3))

    def test_match_filter(self) -> None:
        self.assertEqual(
            set(self.ids("/api/rounds/", {"match": self.match.pk})),
            {self.r1.pk, self.r2.pk},
        )

    def test_arena_map_filter(self) -> None:
        self.assertEqual(
            self.ids("/api/rounds/", {"arena_map": self.map_a.pk}), [self.r1.pk]
        )

    def test_match_non_numeric_is_400(self) -> None:
        self.assert_filter_400("/api/rounds/", {"match": "x"}, "match", "invalid")

    def test_default_ordering_newest_first(self) -> None:
        self.assertEqual(
            self.ids("/api/rounds/"), [self.solo.pk, self.r2.pk, self.r1.pk]
        )

    def test_ordering_round_number(self) -> None:
        ids = self.ids(
            "/api/rounds/", {"match": self.match.pk, "ordering": "-round_number"}
        )
        self.assertEqual(ids, [self.r2.pk, self.r1.pk])

    def test_ordering_date_played_ascending(self) -> None:
        self.assertEqual(
            self.ids("/api/rounds/", {"ordering": "date_played"}),
            [self.r1.pk, self.r2.pk, self.solo.pk],
        )

    def test_detail_ignores_filters(self) -> None:
        response = self.api.get(f"/api/rounds/{self.solo.pk}/", {"match": "x"})
        self.assertEqual(response.status_code, 200)

    def test_foreign_round_404(self) -> None:
        theirs = GameRound.objects.create(round_number=1, manager=self.user_b)
        self.assertNotIn(theirs.pk, self.ids("/api/rounds/"))
        self.assertEqual(self.api.get(f"/api/rounds/{theirs.pk}/").status_code, 404)


# ---------------------------------------------------------------------------
# events action — GameEventFilterSet
# ---------------------------------------------------------------------------


class TestGameEventFilters(_ApiFilterCase):
    def setUp(self) -> None:
        super().setUp()
        red, self.rp = make_team_with_slots("EvtR")
        blue, self.bp = make_team_with_slots("EvtB")
        self.round = GameRound.objects.create(
            round_number=1, team_red=red, team_blue=blue
        )
        self.path = f"/api/rounds/{self.round.pk}/events/"
        self.e_tag = GameEvent.objects.create(
            game_round=self.round,
            timestamp=30,
            event_type="tag",
            actor=self.rp["commander"],
            target=self.bp["heavy"],
        )
        self.e_miss = GameEvent.objects.create(
            game_round=self.round,
            timestamp=10,
            event_type="miss",
            actor=self.bp["heavy"],
        )
        self.e_tag2 = GameEvent.objects.create(
            game_round=self.round,
            timestamp=20,
            event_type="tag",
            actor=self.bp["heavy"],
            target=self.rp["commander"],
        )

    def test_unfiltered_ordered_by_timestamp(self) -> None:
        body = self.get_ok(self.path)
        self.assertEqual([e["timestamp"] for e in body["data"]], [10, 20, 30])
        self.assertEqual(body["meta"]["count"], 3)

    def test_event_type(self) -> None:
        self.assertEqual(
            self.ids(self.path, {"event_type": "tag"}), [self.e_tag2.pk, self.e_tag.pk]
        )

    def test_invalid_event_type_is_400_invalid_choice(self) -> None:
        self.assert_filter_400(
            self.path, {"event_type": "explode"}, "event_type", "invalid_choice"
        )

    def test_actor(self) -> None:
        self.assertEqual(
            self.ids(self.path, {"actor": self.bp["heavy"].pk}),
            [self.e_miss.pk, self.e_tag2.pk],
        )

    def test_target(self) -> None:
        self.assertEqual(
            self.ids(self.path, {"target": self.rp["commander"].pk}), [self.e_tag2.pk]
        )

    def test_timestamp_range_inclusive(self) -> None:
        self.assertEqual(
            self.ids(self.path, {"timestamp_min": 10, "timestamp_max": 20}),
            [self.e_miss.pk, self.e_tag2.pk],
        )

    def test_timestamp_min_only(self) -> None:
        self.assertEqual(self.ids(self.path, {"timestamp_min": 30}), [self.e_tag.pk])

    def test_invalid_timestamp_is_400_on_base_name(self) -> None:
        self.assert_filter_400(
            self.path, {"timestamp_min": "abc"}, "timestamp", "invalid"
        )

    def test_actor_non_numeric_is_400(self) -> None:
        self.assert_filter_400(self.path, {"actor": "abc"}, "actor", "invalid")

    def test_ordering_param_is_ignored(self) -> None:
        body = self.get_ok(self.path, {"ordering": "-timestamp"})
        self.assertEqual([e["timestamp"] for e in body["data"]], [10, 20, 30])

    def test_events_always_paginated(self) -> None:
        for t in range(100, 125):
            GameEvent.objects.create(
                game_round=self.round,
                timestamp=t,
                event_type="miss",
                actor=self.rp["commander"],
            )
        body = self.api.get(self.path).json()
        self.assertEqual(
            body["meta"], {"page": 1, "page_size": 20, "count": 28, "total_pages": 2}
        )
        self.assertEqual(len(body["data"]), 20)
        page2 = self.api.get(self.path, {"page": 2}).json()
        self.assertEqual(len(page2["data"]), 8)
        stamps = [e["timestamp"] for e in body["data"]] + [
            e["timestamp"] for e in page2["data"]
        ]
        self.assertEqual(stamps, sorted(stamps))

    def test_events_of_foreign_round_404(self) -> None:
        theirs = GameRound.objects.create(round_number=1, manager=self.user_b)
        response = self.api.get(f"/api/rounds/{theirs.pk}/events/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_events_are_scoped_to_the_round(self) -> None:
        other = GameRound.objects.create(round_number=2)
        GameEvent.objects.create(
            game_round=other, timestamp=5, event_type="tag", actor=self.rp["commander"]
        )
        self.assertEqual(self.get_ok(self.path)["meta"]["count"], 3)
