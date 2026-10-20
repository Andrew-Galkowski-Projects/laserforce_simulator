"""ANG-01f-3 -- ``POST /api/tournaments/`` (spec #190, ticket #193).

Create binds the strict ``TournamentCreateForm`` and calls
``create_tournament``; a 201 returns the ``build_tournament_detail`` payload.
Bad values are 400s with field codes (never defaulted), team choices are the
requester's own (plus Unmanaged) valid-Roster Teams, and the new row is stamped
with the requester as Manager. Assertions are on the envelope body.
"""

from __future__ import annotations

from matches.models import Tournament
from matches.tests.league_api_fixtures import LeagueApiTestCase, make_teams

LIST_URL = "/api/tournaments/"

FORMATS = (
    "single_elimination",
    "double_elimination",
    "round_robin",
    "round_robin_double_elim",
    "swiss",
)


class TournamentCreatePresetTests(LeagueApiTestCase):
    def _team_ids(self, n: int = 4, prefix: str = "C") -> list[int]:
        return [t.id for t in make_teams(prefix, n, manager=self.user_a)]

    def test_preset_single_elimination_201_returns_detail_and_stamps_manager(
        self,
    ) -> None:
        ids = self._team_ids()
        data = self.ok(
            self.api.post(
                LIST_URL,
                {"name": "API Cup", "format": "single_elimination", "teams": ids},
                format="json",
            ),
            201,
        )
        t = Tournament.objects.get(name="API Cup")
        self.assertEqual(data["id"], t.id)
        self.assertEqual(data["state"], "setup")
        self.assertEqual(data["format"], "single_elimination")
        self.assertEqual(data["manager"], self.user_a.id)
        self.assertEqual(t.manager, self.user_a)
        self.assertEqual(
            sorted(p["team"]["id"] for p in data["participants"]), sorted(ids)
        )
        self.assertEqual(sorted(p["seed"] for p in data["participants"]), [1, 2, 3, 4])

    def test_each_format_preset_201(self) -> None:
        ids = self._team_ids()
        for fmt in FORMATS:
            with self.subTest(format=fmt):
                data = self.ok(
                    self.api.post(
                        LIST_URL,
                        {"name": f"Cup {fmt}", "format": fmt, "teams": ids},
                        format="json",
                    ),
                    201,
                )
                self.assertEqual(data["format"], fmt)
                self.assertEqual(data["team_assembly"], "preset")
                self.assertEqual(len(data["participants"]), 4)

    def test_options_persist(self) -> None:
        ids = self._team_ids()
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "Options Cup",
                    "format": "round_robin_double_elim",
                    "teams": ids,
                    "rrde_combo": "8/4",
                    "final_series_length": 5,
                    "semifinal_series_length": "3",
                    "quarterfinal_series_length": 1,
                    "earlier_series_length": 3,
                },
                format="json",
            ),
            201,
        )
        self.assertEqual((data["wb_advancers"], data["lb_advancers"]), (8, 4))
        self.assertEqual(
            (
                data["final_series_length"],
                data["semifinal_series_length"],
                data["quarterfinal_series_length"],
                data["earlier_series_length"],
            ),
            (5, 3, 1, 3),
        )

    def test_swiss_rounds_persist(self) -> None:
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "Swiss Cup",
                    "format": "swiss",
                    "teams": self._team_ids(),
                    "swiss_rounds": 2,
                },
                format="json",
            ),
            201,
        )
        self.assertEqual(data["swiss_rounds"], 2)

    def test_absent_options_take_defaults(self) -> None:
        data = self.ok(
            self.api.post(
                LIST_URL,
                {"name": "Defaults Cup", "teams": self._team_ids()},
                format="json",
            ),
            201,
        )
        self.assertEqual(data["format"], "single_elimination")
        self.assertEqual(data["team_assembly"], "preset")
        self.assertEqual(data["role_assignment_mode"], "random")
        self.assertEqual((data["wb_advancers"], data["lb_advancers"]), (0, 0))
        self.assertEqual(data["swiss_rounds"], 0)
        self.assertEqual(data["final_series_length"], 1)

    def test_rrde_without_combo_uses_first_combo(self) -> None:
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "RRDE Default",
                    "format": "round_robin_double_elim",
                    "teams": self._team_ids(),
                },
                format="json",
            ),
            201,
        )
        self.assertEqual((data["wb_advancers"], data["lb_advancers"]), (4, 0))

    def test_generated_teams_pad_the_field_and_are_owned(self) -> None:
        ids = self._team_ids(2)
        data = self.ok(
            self.api.post(
                LIST_URL,
                {"name": "Padded", "teams": ids, "generate_count": 2},
                format="json",
            ),
            201,
        )
        self.assertEqual(len(data["participants"]), 4)
        t = Tournament.objects.get(name="Padded")
        generated = t.participants.exclude(team_id__in=ids)
        self.assertEqual(generated.count(), 2)
        for p in generated.select_related("team"):
            self.assertEqual(p.team.manager, self.user_a)
            self.assertTrue(p.team.is_valid_roster)

    def test_form_encoded_multi_value_teams(self) -> None:
        ids = self._team_ids()
        data = self.ok(self.api.post(LIST_URL, {"name": "Form Cup", "teams": ids}), 201)
        self.assertEqual(len(data["participants"]), 4)

    def test_unmanaged_team_is_a_valid_choice(self) -> None:
        ids = self._team_ids(3) + [make_teams("U", 1)[0].id]
        self.ok(
            self.api.post(LIST_URL, {"name": "Mixed", "teams": ids}, format="json"),
            201,
        )


class TournamentCreatePlayerPoolTests(LeagueApiTestCase):
    def test_player_pool_rrde_is_random_draw_with_empty_pool(self) -> None:
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "Draw Cup",
                    "format": "round_robin_double_elim",
                    "team_assembly": "player_pool",
                    "role_assignment_mode": "per_tier",
                    "rrde_combo": "4/2",
                },
                format="json",
            ),
            201,
        )
        self.assertEqual(data["team_assembly"], "player_pool")
        self.assertEqual(data["format_label"], "Random Draw")
        self.assertEqual(data["role_assignment_mode"], "per_tier")
        self.assertEqual(data["participants"], [])
        self.assertEqual(data["pool_size"], 0)
        self.assertFalse(data["is_drawn"])
        self.assertEqual(data["manager"], self.user_a.id)

    def test_player_pool_single_elimination(self) -> None:
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "Draw SE",
                    "format": "single_elimination",
                    "team_assembly": "player_pool",
                },
                format="json",
            ),
            201,
        )
        self.assertEqual(data["format"], "single_elimination")
        self.assertEqual(data["format_label"], "Single elimination")
        self.assertEqual(data["participants"], [])

    def test_player_pool_ignores_teams_and_generate(self) -> None:
        ids = [t.id for t in make_teams("PP", 2, manager=self.user_a)]
        data = self.ok(
            self.api.post(
                LIST_URL,
                {
                    "name": "Draw Ignore",
                    "team_assembly": "player_pool",
                    "teams": ids,
                    "generate_count": 3,
                },
                format="json",
            ),
            201,
        )
        self.assertEqual(data["participants"], [])


class TournamentCreateRejectionTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids = [t.id for t in make_teams("R", 4, manager=self.user_a)]

    def _post(self, **overrides):
        body = {"name": "Bad Cup", "teams": self.ids, **overrides}
        return self.api.post(LIST_URL, body, format="json")

    def _assert_nothing_created(self) -> None:
        self.assertFalse(Tournament.objects.exists())

    def test_unknown_format(self) -> None:
        self.error(self._post(format="not_a_format"), 400, "invalid_choice", "format")
        self._assert_nothing_created()

    def test_series_length_outside_1_3_5(self) -> None:
        for field in (
            "final_series_length",
            "semifinal_series_length",
            "quarterfinal_series_length",
            "earlier_series_length",
        ):
            for bad in (4, "abc"):
                with self.subTest(field=field, value=bad):
                    self.error(self._post(**{field: bad}), 400, "invalid_choice", field)
        self._assert_nothing_created()

    def test_unknown_rrde_combo(self) -> None:
        self.error(
            self._post(format="round_robin_double_elim", rrde_combo="7/3"),
            400,
            "invalid_choice",
            "rrde_combo",
        )
        self._assert_nothing_created()

    def test_negative_swiss_rounds(self) -> None:
        self.error(
            self._post(format="swiss", swiss_rounds=-1),
            400,
            "min_value",
            "swiss_rounds",
        )
        self._assert_nothing_created()

    def test_non_integer_swiss_rounds(self) -> None:
        self.error(
            self._post(format="swiss", swiss_rounds="abc"),
            400,
            "invalid",
            "swiss_rounds",
        )

    def test_unknown_team_assembly_and_role_mode(self) -> None:
        response = self._post(team_assembly="nope", role_assignment_mode="nope")
        self.error(response, 400, "invalid_choice", "team_assembly")
        self.error(response, 400, "invalid_choice", "role_assignment_mode")

    def test_negative_generate_count(self) -> None:
        self.error(self._post(generate_count=-1), 400, "min_value", "generate_count")

    def test_generate_ppt_below_six_is_min_value(self) -> None:
        # ANG-01f review (P4): rejected, never raised to 6.
        for bad in (0, 3, 5):
            with self.subTest(value=bad):
                self.error(
                    self._post(generate_count=2, generate_ppt=bad),
                    400,
                    "min_value",
                    "generate_ppt",
                )
        self._assert_nothing_created()

    def test_generate_ppt_six_or_absent_is_accepted(self) -> None:
        self.ok(self._post(name="Six", generate_count=1, generate_ppt=6), 201)
        self.ok(self._post(name="Absent", generate_count=1), 201)

    def test_missing_name(self) -> None:
        response = self.api.post(LIST_URL, {"teams": self.ids}, format="json")
        entry = self.error(response, 400, "required", "name")
        self.assertEqual(entry["message"], "Tournament name is required.")
        self._assert_nothing_created()

    def test_blank_name(self) -> None:
        self.error(self._post(name="   "), 400, "required", "name")

    def test_fewer_than_four_preset_teams(self) -> None:
        self.error(self._post(teams=self.ids[:3]), 400, "too_few_teams", "teams")
        self._assert_nothing_created()

    def test_invalid_roster_team(self) -> None:
        bad = make_teams("Incomplete", 1, manager=self.user_a)[0]
        bad.slot_ammo = None
        bad.save()
        self.error(
            self._post(teams=self.ids[:3] + [bad.id]), 400, "invalid_choice", "teams"
        )
        self._assert_nothing_created()

    def test_another_accounts_team(self) -> None:
        foreign = make_teams("Foreign", 1, manager=self.user_b)[0]
        self.error(
            self._post(teams=self.ids[:3] + [foreign.id]),
            400,
            "invalid_choice",
            "teams",
        )
        self._assert_nothing_created()

    def test_non_object_body_is_parse_error(self) -> None:
        self.error(
            self.api.post(LIST_URL, [1, 2], format="json"), 400, "parse_error", None
        )

    def test_anonymous_is_403(self) -> None:
        self.error(
            self.anon.post(
                LIST_URL, {"name": "Anon", "teams": self.ids}, format="json"
            ),
            403,
            "not_authenticated",
            None,
        )
        self._assert_nothing_created()
