"""ANG-01d-1 -- ``/api/seasons/<pk>/conferences/`` GET + PUT (section 6, #19/#20).

GET returns ``build_conferences_state`` with Teams flattened to refs. PUT is a
full replace bound through the shared ``ConferencePartitionForm``: a non-draft
Season is a 409 ``season_not_draft`` *before* any body validation; every form
error is a 400 on field ``conferences`` (``invalid``, or ``required`` when the
key is absent). JSON only -- multipart is 415, a non-object body is a 400
``parse_error``. A foreign Season is 404.

Real ArenaMap rows (a tiny PNG with a confirmed zone config) under a temp
``MEDIA_ROOT`` back the rotation cases.
"""

from __future__ import annotations

from core.map_test_fixtures import TempMediaMixin
from matches.models import Conference
from matches.tests.league_api_fixtures import (
    BRACKET_FLOOR_MSG,
    CONFERENCE_STATE_ROW_KEYS,
    CONFERENCES_STATE_KEYS,
    NAMES_EMPTY_MSG,
    REF_KEYS,
    ROTATION_GUARD_MSG,
    ROTATION_INVALID_MSG,
    ROTATION_UNKNOWN_MSG,
    SEASON_NOT_DRAFT_MSG,
    SHAPE_MSG,
    UNASSIGNED_MSG,
    UNDER_TWO_MSG,
    LeagueApiTestCase,
    add_tournament_phase,
    confirmed_map,
    duplicate_msg,
    make_completed_season,
    make_conference,
    make_league,
    make_open_season,
    make_teams,
    not_enrolled_msg,
)


class _ConferencesTestCase(TempMediaMixin, LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teams = make_teams("ApiConf", 4)
        self.ids = [t.id for t in self.teams]
        self.league = make_league("ApiConfL", manager=self.user_a)
        self.season = make_open_season(self.league, self.teams)
        self.url = f"/api/seasons/{self.season.id}/conferences/"

    def put(self, body, **kwargs):
        kwargs.setdefault("format", "json")
        return self.api.put(self.url, body, **kwargs)

    def two_confs(self, west_rotation=None, east_rotation=None) -> list[dict]:
        west = {"name": "West", "team_ids": self.ids[:2]}
        east = {"name": "East", "team_ids": self.ids[2:]}
        if west_rotation is not None:
            west["map_rotation_ids"] = west_rotation
        if east_rotation is not None:
            east["map_rotation_ids"] = east_rotation
        return [west, east]

    def messages(self, response) -> list[str]:
        errors = self.errors(response, 400)
        for entry in errors:
            self.assertEqual(entry["field"], "conferences")
            self.assertEqual(entry["code"], "invalid")
        return [entry["message"] for entry in errors]


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


class TestConferencesGet(_ConferencesTestCase):
    def test_get_state(self) -> None:
        conf = make_conference(self.season, "West", 1, self.teams[:2])
        data = self.ok(self.api.get(self.url))
        self.assertEqual(set(data), CONFERENCES_STATE_KEYS)
        self.assertEqual(
            data["season"], {"id": self.season.id, "name": self.season.name}
        )
        self.assertTrue(data["editable"])
        self.assertEqual(data["map_mode"], "none")
        self.assertEqual(
            [t["name"] for t in data["teams"]], sorted(t.name for t in self.teams)
        )
        for team in data["teams"]:
            self.assertEqual(set(team), REF_KEYS)
        (row,) = data["conferences"]
        self.assertEqual(set(row), CONFERENCE_STATE_ROW_KEYS)
        self.assertEqual(row["id"], conf.id)
        self.assertEqual(row["ordinal"], 1)
        self.assertEqual(row["team_ids"], sorted(self.ids[:2]))
        self.assertEqual(row["map_rotation_ids"], [])
        self.assertIsNone(row["starting_team_ids"])
        self.assertIsNone(row["starting_map_rotation_ids"])
        self.assertEqual(data["unassigned_team_ids"], sorted(self.ids[2:]))
        self.assert_session_untouched()

    def test_get_flat_season(self) -> None:
        data = self.ok(self.api.get(self.url))
        self.assertEqual(data["conferences"], [])
        self.assertEqual(data["unassigned_team_ids"], sorted(self.ids))

    def test_get_active_not_editable(self) -> None:
        active = make_open_season(
            make_league("ApiConfAct", manager=self.user_a), self.teams, state="active"
        )
        data = self.ok(self.api.get(f"/api/seasons/{active.id}/conferences/"))
        self.assertFalse(data["editable"])

    def test_foreign_is_404(self) -> None:
        season = make_open_season(self.foreign_league("ApiConfF"), self.teams)
        self.error(
            self.api.get(f"/api/seasons/{season.id}/conferences/"), 404, "not_found"
        )
        self.error(
            self.api.put(
                f"/api/seasons/{season.id}/conferences/",
                {"conferences": []},
                format="json",
            ),
            404,
            "not_found",
        )

    def test_anonymous_is_403(self) -> None:
        self.error(self.anon.get(self.url), 403, "not_authenticated")

    def test_other_methods_are_405(self) -> None:
        for method in ("post", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(self.url, {}, format="json")
                self.error(response, 405, "method_not_allowed")


# ---------------------------------------------------------------------------
# PUT happy paths
# ---------------------------------------------------------------------------


class TestConferencesPut(_ConferencesTestCase):
    def test_create_returns_saved_state(self) -> None:
        m = confirmed_map("ApiConfMap")
        data = self.ok(
            self.put(
                {
                    "conferences": [
                        {
                            "name": "  West ",
                            "team_ids": [self.ids[1], self.ids[0]],
                            "map_rotation_ids": [m.id, m.id],
                        },
                        {"name": "East", "team_ids": self.ids[2:]},
                    ]
                }
            )
        )
        self.assertEqual(set(data), CONFERENCES_STATE_KEYS)
        self.assertEqual([c["name"] for c in data["conferences"]], ["West", "East"])
        self.assertEqual([c["ordinal"] for c in data["conferences"]], [1, 2])
        self.assertEqual(data["conferences"][0]["team_ids"], sorted(self.ids[:2]))
        self.assertEqual(data["conferences"][0]["map_rotation_ids"], [m.id, m.id])
        self.assertEqual(data["conferences"][1]["map_rotation_ids"], [])
        self.assertEqual(data["unassigned_team_ids"], [])
        confs = list(self.season.conferences.order_by("ordinal"))
        self.assertEqual([c.name for c in confs], ["West", "East"])
        self.assertEqual(confs[0].map_rotation_ids_json, [m.id, m.id])
        # The PUT payload equals a subsequent GET.
        self.assertEqual(self.ok(self.api.get(self.url)), data)
        self.assert_session_untouched()

    def test_string_rotation_accepted(self) -> None:
        a = confirmed_map("ApiConfStrA")
        b = confirmed_map("ApiConfStrB")
        data = self.ok(self.put({"conferences": self.two_confs(f"{b.id},{a.id}")}))
        self.assertEqual(data["conferences"][0]["map_rotation_ids"], [b.id, a.id])

    def test_replace_existing(self) -> None:
        stale = make_conference(self.season, "Stale", 1, self.teams)
        self.ok(self.put({"conferences": self.two_confs()}))
        self.assertFalse(Conference.objects.filter(pk=stale.pk).exists())
        self.assertEqual(self.season.conferences.count(), 2)

    def test_empty_list_clears(self) -> None:
        make_conference(self.season, "Gone", 1, self.teams)
        data = self.ok(self.put({"conferences": []}))
        self.assertEqual(data["conferences"], [])
        self.assertEqual(self.season.conferences.count(), 0)

    def test_rotate_by_conference_with_maps_saves(self) -> None:
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        a = confirmed_map("ApiConfRbcA")
        b = confirmed_map("ApiConfRbcB")
        data = self.ok(self.put({"conferences": self.two_confs([a.id], [b.id])}))
        self.assertEqual(data["map_mode"], "rotate_by_conference")
        self.assertEqual(self.season.conferences.count(), 2)


# ---------------------------------------------------------------------------
# PUT conflicts / transport errors
# ---------------------------------------------------------------------------


class TestConferencesPutConflicts(_ConferencesTestCase):
    def _non_draft(self, state: str):
        league = make_league(f"ApiConf{state}", manager=self.user_a)
        if state == "completed":
            season = make_completed_season(league, self.teams, ranked=False)
        else:
            season = make_open_season(league, self.teams, state=state)
        return season

    def test_active_season_is_409_before_body_validation(self) -> None:
        for state in ("active", "completed"):
            with self.subTest(state=state):
                season = self._non_draft(state)
                url = f"/api/seasons/{season.id}/conferences/"
                for body in ({"conferences": []}, {"conferences": "garbage"}, {}):
                    entry = self.error(
                        self.api.put(url, body, format="json"), 409, "season_not_draft"
                    )
                    self.assertEqual(entry["message"], SEASON_NOT_DRAFT_MSG)
                self.assertEqual(season.conferences.count(), 0)

    def test_multipart_is_415(self) -> None:
        self.error(
            self.api.put(self.url, {"conferences": "x"}, format="multipart"),
            415,
            "unsupported_media_type",
        )

    def test_array_body_is_parse_error(self) -> None:
        self.error(self.put([1, 2]), 400, "parse_error")

    def test_invalid_json_is_parse_error(self) -> None:
        response = self.api.put(self.url, b"{not json", content_type="application/json")
        self.error(response, 400, "parse_error")


# ---------------------------------------------------------------------------
# PUT validation errors (400, field "conferences")
# ---------------------------------------------------------------------------


class TestConferencesPutValidation(_ConferencesTestCase):
    def test_missing_key_is_required(self) -> None:
        self.error(self.put({}), 400, "required", "conferences")
        self.error(self.put({"conferences": None}), 400, "required", "conferences")

    def test_shape_errors(self) -> None:
        cases = (
            {"name": "West"},
            "West",
            [{"name": "West"}],
            [{"name": "West", "team_ids": [True]}],
            [{"name": "West", "team_ids": self.ids, "extra": 1}],
            [{"name": "West", "team_ids": self.ids, "map_rotation_ids": 7}],
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    self.messages(self.put({"conferences": value})), [SHAPE_MSG]
                )
        self.assertEqual(self.season.conferences.count(), 0)

    def test_duplicate_team(self) -> None:
        confs = self.two_confs()
        confs[1]["team_ids"] = self.ids[2:] + [self.ids[0]]
        self.assertEqual(
            self.messages(self.put({"conferences": confs})),
            [duplicate_msg(self.ids[0])],
        )

    def test_non_enrolled_team(self) -> None:
        (outsider,) = make_teams("ApiConfOut", 1)
        confs = self.two_confs()
        confs[0]["team_ids"] = self.ids[:2] + [outsider.id]
        self.assertEqual(
            self.messages(self.put({"conferences": confs})),
            [not_enrolled_msg(outsider.id)],
        )

    def test_partition_errors(self) -> None:
        confs = [
            {"name": " ", "team_ids": [self.ids[0]]},
            {"name": "East", "team_ids": self.ids[2:]},
        ]
        self.assertEqual(
            self.messages(self.put({"conferences": confs})),
            [NAMES_EMPTY_MSG, UNASSIGNED_MSG, UNDER_TWO_MSG],
        )

    def test_bracket_floor(self) -> None:
        add_tournament_phase(self.season)
        self.assertEqual(
            self.messages(self.put({"conferences": self.two_confs()})),
            [BRACKET_FLOOR_MSG],
        )

    def test_rotation_errors(self) -> None:
        self.assertEqual(
            self.messages(
                self.put({"conferences": self.two_confs(["x", 999_999], "abc")})
            ),
            [ROTATION_INVALID_MSG, ROTATION_UNKNOWN_MSG, ROTATION_INVALID_MSG],
        )

    def test_rotate_by_conference_guard(self) -> None:
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        m = confirmed_map("ApiConfGuard")
        self.assertEqual(
            self.messages(self.put({"conferences": self.two_confs([m.id], [])})),
            [ROTATION_GUARD_MSG],
        )
        self.assertEqual(
            self.messages(self.put({"conferences": self.two_confs()})),
            [ROTATION_GUARD_MSG],
        )
        self.assertEqual(self.season.conferences.count(), 0)

    def test_failed_put_writes_nothing(self) -> None:
        keep = make_conference(self.season, "Keep", 1, self.teams)
        self.assertEqual(
            self.messages(
                self.put({"conferences": [{"name": "", "team_ids": self.ids}]})
            ),
            [NAMES_EMPTY_MSG],
        )
        self.assertTrue(Conference.objects.filter(pk=keep.pk).exists())
        self.assertEqual(self.season.conferences.count(), 1)
