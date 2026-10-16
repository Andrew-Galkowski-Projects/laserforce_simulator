"""ANG-01d-1 -- ``matches/league_services.py`` + ``ConferencePartitionForm``.

Seam contract ``.claude/worktrees/ang-01d-1-seam-contract.md`` sections 2 / 3.1 / 8.

* The new public service surface (``season_mode_for``, ``latest_completed_season``,
  ``start_next_season`` + its three domain exceptions, ``reassign_and_roll``,
  ``ensure_owner_evaluation``, the ``build_*`` readers, ``history_seasons``,
  ``replace_conferences``).
* The moved-name re-export block in ``matches.league_views`` (section 2.3).
* ``ConferencePartitionForm`` -- every step-1/2 message, the partition strings
  + bracket floor, str vs list rotation, the once-only rotate-by-conference
  guard, and the combined error order == today's HTML list.

Hand-built fixtures only; no simulated point totals are asserted.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.map_test_fixtures import TempMediaMixin
from matches import league_services, league_views
from matches.forms import ConferencePartitionForm
from matches.models import Conference, OwnerEvaluation, Season
from matches.tests.league_api_fixtures import (
    BRACKET_FLOOR_MSG,
    NAMES_EMPTY_MSG,
    ROTATION_GUARD_MSG,
    ROTATION_INVALID_MSG,
    ROTATION_UNKNOWN_MSG,
    SHAPE_MSG,
    STANDINGS_FIELDS,
    UNASSIGNED_MSG,
    UNDER_TWO_MSG,
    add_round_states,
    add_tournament_phase,
    confirmed_map,
    duplicate_msg,
    fired_evaluation,
    make_completed_season,
    make_conference,
    make_league,
    make_open_season,
    make_teams,
    not_enrolled_msg,
    ranked_career_league,
)

# ---------------------------------------------------------------------------
# Re-exports (section 2.3)
# ---------------------------------------------------------------------------

_RENAMED_ALIASES = {
    "_template_to_form_data": "template_to_form_data",
    "_create_league_and_season": "create_league_and_season",
    "_is_career_league": "is_career_league",
    "_run_season_rollover": "run_season_rollover",
    "_eligible_new_teams": "eligible_new_teams",
    "_teardown_league": "teardown_league",
    "_build_dashboard_context": "build_dashboard_context",
    "_pick_displayed_season": "pick_displayed_season",
    "_compute_season_award_set": "compute_season_award_set",
    "_build_history_row": "build_history_row",
    "_STANDINGS_SORT_KEYS": "STANDINGS_SORT_KEYS",
}

_SAME_NAME_MOVES = (
    "SEED_BUDGET_MIN",
    "SEED_BUDGET_MAX",
    "SEED_BUDGET_SINGLE",
    "_compute_team_overall",
    "_rank_teams_by_strength",
    "_pick_manager_team",
    "_seed_team_budgets_by_strength",
    "_write_baseline_ratings",
    "_developing_players",
    "_develop_league_for_new_season",
    "_classify_playoffs_for_team",
    "_ensure_team_finances",
    "_ensure_owner_evaluations",
    "WORST_N_ELIGIBLE",
    "_delete_team_if_orphaned",
    "_league_teardown_targets",
    "_league_match_q",
    "_playoff_cursor_keys",
    "_build_map_config_label",
    "_member_night_games_for_season",
    "_member_night_appearances_for_team",
    "_build_team_schedule_rows",
    "_validate_conference_partition",
    "_standings_sort_value",
)


class TestReExports(TestCase):
    def test_renamed_names_alias_the_service_objects(self) -> None:
        for old, new in _RENAMED_ALIASES.items():
            with self.subTest(old=old):
                self.assertTrue(hasattr(league_services, new), new)
                self.assertIs(getattr(league_views, old), getattr(league_services, new))

    def test_same_name_moves_are_re_exported(self) -> None:
        for name in _SAME_NAME_MOVES:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(league_views, name), getattr(league_services, name)
                )

    def test_new_public_surface_exists(self) -> None:
        for name in (
            "SeasonInProgressError",
            "NoCompletedSeasonError",
            "ManagerFiredError",
            "season_mode_for",
            "latest_completed_season",
            "start_next_season",
            "reassign_and_roll",
            "ensure_owner_evaluation",
            "build_season_standings",
            "build_season_schedule",
            "build_team_schedule",
            "history_seasons",
            "build_season_awards",
            "build_conferences_state",
            "replace_conferences",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(league_services, name))

    def test_services_module_does_not_import_league_views(self) -> None:
        import inspect

        source = inspect.getsource(league_services)
        self.assertNotIn("from matches.league_views", source)
        self.assertNotIn("from .league_views", source)
        self.assertNotIn("import matches.league_views", source)


# ---------------------------------------------------------------------------
# season_mode_for / latest_completed_season
# ---------------------------------------------------------------------------


class TestSeasonMode(TestCase):
    def test_ladder(self) -> None:
        teams = make_teams("Mode", 2)
        league = make_league("ModeL")
        self.assertEqual(league_services.season_mode_for(None), "none")
        draft = make_open_season(league, teams, state="draft")
        self.assertEqual(league_services.season_mode_for(draft), "draft")
        draft.state = "active"
        self.assertEqual(league_services.season_mode_for(draft), "active")
        draft.state = "completed"
        self.assertEqual(league_services.season_mode_for(draft), "completed")

    def test_latest_completed_season(self) -> None:
        teams = make_teams("Latest", 2)
        league = make_league("LatestL")
        self.assertIsNone(league_services.latest_completed_season(league))
        s1 = make_completed_season(league, teams, ranked=False)
        s2 = make_completed_season(
            league, teams, name="Season 2", start_date=date(2026, 1, 1), ranked=False
        )
        make_open_season(league, teams, name="Season 3", start_date=date(2027, 1, 1))
        self.assertEqual(league_services.latest_completed_season(league), s2)
        self.assertNotEqual(s1, s2)


# ---------------------------------------------------------------------------
# start_next_season / reassign_and_roll
# ---------------------------------------------------------------------------


class TestStartNextSeason(TestCase):
    def test_in_progress_raises_with_season(self) -> None:
        league, _season, teams = ranked_career_league("SnsProg", 4)
        draft = make_open_season(league, teams)
        before = league.seasons.count()
        with self.assertRaises(league_services.SeasonInProgressError) as ctx:
            league_services.start_next_season(league)
        self.assertEqual(ctx.exception.season, draft)
        self.assertEqual(league.seasons.count(), before)

    def test_no_completed_raises(self) -> None:
        league = make_league("SnsNone")
        with self.assertRaises(league_services.NoCompletedSeasonError):
            league_services.start_next_season(league)
        self.assertEqual(league.seasons.count(), 0)

    def test_fired_raises_with_evaluation(self) -> None:
        league, season, teams = ranked_career_league("SnsFired", 4)
        evaluation = fired_evaluation(league, season, teams[0])
        before = league.seasons.count()
        with self.assertRaises(league_services.ManagerFiredError) as ctx:
            league_services.start_next_season(league)
        self.assertEqual(ctx.exception.evaluation, evaluation)
        self.assertEqual(league.seasons.count(), before)

    def test_fired_but_reassigned_rolls(self) -> None:
        league, season, teams = ranked_career_league("SnsReas", 4)
        fired_evaluation(league, season, teams[0])
        league.current_team = teams[-1]
        league.save(update_fields=["current_team"])
        new = league_services.start_next_season(league)
        self.assertEqual(new.state, "draft")

    def test_success_returns_draft_season(self) -> None:
        league, _season, teams = ranked_career_league("SnsOk", 4)
        before = league.seasons.count()
        new = league_services.start_next_season(league)
        self.assertIsInstance(new, Season)
        self.assertEqual(new.state, "draft")
        self.assertEqual(new.league_id, league.id)
        self.assertEqual(new.name, "Season 2")
        self.assertEqual(league.seasons.count(), before + 1)
        self.assertEqual(
            set(new.teams.values_list("id", flat=True)), {t.id for t in teams}
        )


class TestReassignAndRoll(TestCase):
    def test_sets_current_team_and_rolls(self) -> None:
        league, season, teams = ranked_career_league("RarOk", 8)
        new = league_services.reassign_and_roll(league, teams[-1], season)
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, teams[-1].id)
        self.assertEqual(new.state, "draft")
        self.assertEqual(new.league_id, league.id)
        self.assertEqual(league.seasons.count(), 2)


# ---------------------------------------------------------------------------
# ensure_owner_evaluation
# ---------------------------------------------------------------------------


class TestEnsureOwnerEvaluation(TestCase):
    def test_none_for_draft(self) -> None:
        league, _season, teams = ranked_career_league("EoeDraft", 4)
        draft = make_open_season(league, teams)
        self.assertIsNone(league_services.ensure_owner_evaluation(draft))

    def test_row_for_completed_career(self) -> None:
        league, season, _teams = ranked_career_league("EoeOk", 4)
        self.assertEqual(OwnerEvaluation.objects.count(), 0)
        evaluation = league_services.ensure_owner_evaluation(season)
        self.assertIsInstance(evaluation, OwnerEvaluation)
        self.assertEqual(evaluation.season_id, season.id)
        self.assertEqual(evaluation.league_id, league.id)
        self.assertEqual(OwnerEvaluation.objects.count(), 1)
        # Idempotent.
        league_services.ensure_owner_evaluation(season)
        self.assertEqual(OwnerEvaluation.objects.count(), 1)

    def test_none_for_non_career(self) -> None:
        _league, season, _teams = ranked_career_league("EoeMp", 4, mode="multiplayer")
        self.assertIsNone(league_services.ensure_owner_evaluation(season))
        self.assertEqual(OwnerEvaluation.objects.count(), 0)


# ---------------------------------------------------------------------------
# build_season_standings
# ---------------------------------------------------------------------------


class TestBuildSeasonStandings(TestCase):
    def test_key_order_and_single_group_without_conferences(self) -> None:
        league, season, teams = ranked_career_league("BssKeys", 4)
        result = league_services.build_season_standings(season)
        self.assertEqual(
            list(result),
            ["is_draft_preview", "standings_groups", "rows", "rows_with_teams"],
        )
        self.assertFalse(result["is_draft_preview"])
        self.assertEqual(len(result["standings_groups"]), 1)
        group = result["standings_groups"][0]
        self.assertEqual(set(group), {"conference", "name", "rows_with_teams"})
        self.assertIsNone(group["conference"])
        self.assertIsNone(group["name"])
        self.assertEqual(len(result["rows"]), 4)
        for row in result["rows"]:
            for field in STANDINGS_FIELDS:
                self.assertTrue(hasattr(row, field), field)
        # Default sort == rank order; strongest team is rank 1.
        ranked = [row for row, _team in group["rows_with_teams"]]
        self.assertEqual([r.rank for r in ranked], [1, 2, 3, 4])
        self.assertEqual(ranked[0].team_id, teams[0].id)

    def test_sort_by_team_desc_keeps_rank_frozen(self) -> None:
        _league, season, teams = ranked_career_league("BssSort", 4)
        default = league_services.build_season_standings(season)
        rank_by_team = {r.team_id: r.rank for r in default["rows"]}
        result = league_services.build_season_standings(
            season, sort="team", direction="desc"
        )
        pairs = result["standings_groups"][0]["rows_with_teams"]
        names = [team.name for _row, team in pairs]
        self.assertEqual(names, sorted(names, reverse=True))
        for row, _team in pairs:
            self.assertEqual(row.rank, rank_by_team[row.team_id])

    def test_draft_rows_are_17_key_dicts(self) -> None:
        teams = make_teams("BssDraft", 4)
        league = make_league("BssDraftL")
        draft = make_open_season(league, teams)
        result = league_services.build_season_standings(draft)
        self.assertTrue(result["is_draft_preview"])
        self.assertEqual(len(result["rows"]), 4)
        for row in result["rows"]:
            self.assertEqual(set(row), set(STANDINGS_FIELDS))

    def test_one_group_per_conference(self) -> None:
        teams = make_teams("BssConf", 4)
        league = make_league("BssConfL")
        draft = make_open_season(league, teams)
        make_conference(draft, "West", 1, teams[:2])
        make_conference(draft, "East", 2, teams[2:])
        result = league_services.build_season_standings(draft)
        groups = result["standings_groups"]
        self.assertEqual([g["name"] for g in groups], ["West", "East"])
        self.assertEqual([len(g["rows_with_teams"]) for g in groups], [2, 2])
        self.assertEqual(len(result["rows_with_teams"]), 4)


# ---------------------------------------------------------------------------
# build_season_schedule / build_team_schedule
# ---------------------------------------------------------------------------

_FIXTURE_KEYS = {
    "matchday",
    "round_number",
    "team_a_id",
    "team_b_id",
    "team_a",
    "team_b",
    "played",
    "game_round_id",
    "red_score",
    "blue_score",
    "date",
}


class TestBuildSeasonSchedule(TestCase):
    def test_draft_schedule_shape(self) -> None:
        teams = make_teams("BsSch", 4)
        league = make_league("BsSchL")
        draft = make_open_season(league, teams)
        result = league_services.build_season_schedule(draft)
        self.assertEqual(set(result), {"matchdays", "member_night_games"})
        self.assertTrue(result["matchdays"])
        self.assertEqual(result["member_night_games"], [])
        for matchday in result["matchdays"]:
            self.assertEqual(set(matchday), {"matchday", "date", "fixtures"})
            for fixture in matchday["fixtures"]:
                self.assertEqual(set(fixture), _FIXTURE_KEYS)
                self.assertFalse(fixture["played"])

    def test_completed_schedule_marks_played(self) -> None:
        _league, season, _teams = ranked_career_league("BsSchDone", 4)
        result = league_services.build_season_schedule(season)
        fixtures = [f for md in result["matchdays"] for f in md["fixtures"]]
        self.assertTrue(any(f["played"] for f in fixtures))
        played = next(f for f in fixtures if f["played"])
        self.assertIsNotNone(played["game_round_id"])

    def test_under_two_teams_is_empty(self) -> None:
        teams = make_teams("BsSchOne", 1)
        league = make_league("BsSchOneL")
        draft = make_open_season(league, teams)
        result = league_services.build_season_schedule(draft)
        self.assertEqual(result["matchdays"], [])


class TestBuildTeamSchedule(TestCase):
    def test_completed_rows(self) -> None:
        _league, season, teams = ranked_career_league("BtsDone", 4)
        result = league_services.build_team_schedule(season, teams[0])
        self.assertEqual(
            set(result), {"upcoming", "completed", "member_night_appearances"}
        )
        self.assertTrue(result["completed"])
        for row in result["completed"]:
            self.assertEqual(len(row), 11)
            self.assertIn("outcome", row)
        self.assertEqual(result["member_night_appearances"], [])

    def test_upcoming_rows_for_draft(self) -> None:
        teams = make_teams("BtsDraft", 4)
        league = make_league("BtsDraftL")
        draft = make_open_season(league, teams)
        result = league_services.build_team_schedule(draft, teams[0])
        self.assertTrue(result["upcoming"])
        for row in result["upcoming"]:
            self.assertEqual(len(row), 7)
        self.assertEqual(result["completed"], [])


# ---------------------------------------------------------------------------
# history_seasons / build_season_awards
# ---------------------------------------------------------------------------


class TestHistorySeasons(TestCase):
    def test_in_progress_completed_and_teams(self) -> None:
        teams = make_teams("HsTeams", 2)
        league = make_league("HsL")
        s1 = make_completed_season(league, teams, ranked=False)
        s2 = make_completed_season(
            league, teams, name="Season 2", start_date=date(2026, 1, 1), ranked=False
        )
        draft = make_open_season(
            league, teams, name="Season 3", start_date=date(2027, 1, 1)
        )
        in_progress, completed, teams_by_id = league_services.history_seasons(league)
        self.assertEqual(in_progress, draft)
        self.assertEqual(completed, [s2, s1])
        for team in teams:
            self.assertIn(team.id, teams_by_id)

    def test_no_in_progress(self) -> None:
        teams = make_teams("HsNone", 2)
        league = make_league("HsNoneL")
        make_completed_season(league, teams, ranked=False)
        in_progress, completed, _teams_by_id = league_services.history_seasons(league)
        self.assertIsNone(in_progress)
        self.assertEqual(len(completed), 1)


class TestBuildSeasonAwards(TestCase):
    def test_key_set_without_rounds(self) -> None:
        teams = make_teams("BsaNone", 2)
        league = make_league("BsaNoneL")
        season = make_completed_season(league, teams, ranked=False)
        result = league_services.build_season_awards(season)
        self.assertEqual(set(result), {"awards", "kd_rows", "has_rounds"})
        self.assertFalse(result["has_rounds"])
        self.assertEqual(len(result["kd_rows"]), 5)

    def test_has_rounds_with_player_states(self) -> None:
        teams = make_teams("BsaRounds", 2)
        league = make_league("BsaRoundsL")
        season = make_completed_season(league, teams, ranked=False)
        add_round_states(season, teams[0], teams[1])
        result = league_services.build_season_awards(season)
        self.assertTrue(result["has_rounds"])
        self.assertIsNotNone(result["awards"].most_points)
        roles = [role for role, _label, _winner in result["kd_rows"]]
        self.assertEqual(roles, ["commander", "heavy", "scout", "medic", "ammo"])


# ---------------------------------------------------------------------------
# build_conferences_state / replace_conferences
# ---------------------------------------------------------------------------


class TestConferencesState(TestCase):
    def test_state_shape(self) -> None:
        teams = make_teams("BcsState", 4)
        league = make_league("BcsStateL")
        draft = make_open_season(league, teams)
        conf = make_conference(draft, "West", 1, [teams[1], teams[0]])
        state = league_services.build_conferences_state(draft)
        self.assertEqual(
            set(state),
            {"editable", "map_mode", "teams", "conferences", "unassigned_team_ids"},
        )
        self.assertTrue(state["editable"])
        self.assertEqual(state["map_mode"], "none")
        self.assertEqual(
            [t.name for t in state["teams"]], sorted(t.name for t in teams)
        )
        self.assertEqual(
            state["unassigned_team_ids"], sorted([teams[2].id, teams[3].id])
        )
        (row,) = state["conferences"]
        self.assertEqual(
            set(row),
            {
                "id",
                "name",
                "ordinal",
                "team_ids",
                "map_rotation_ids",
                "starting_team_ids",
                "starting_map_rotation_ids",
            },
        )
        self.assertEqual(row["id"], conf.id)
        self.assertEqual(row["team_ids"], sorted([teams[0].id, teams[1].id]))
        self.assertEqual(row["map_rotation_ids"], [])
        self.assertIsNone(row["starting_team_ids"])

    def test_not_editable_once_active(self) -> None:
        teams = make_teams("BcsActive", 4)
        league = make_league("BcsActiveL")
        active = make_open_season(league, teams, state="active")
        state = league_services.build_conferences_state(active)
        self.assertFalse(state["editable"])


class TestReplaceConferences(TestCase):
    def test_replace_then_clear(self) -> None:
        teams = make_teams("RcRep", 4)
        league = make_league("RcRepL")
        draft = make_open_season(league, teams)
        stale = make_conference(draft, "Stale", 1, teams)
        league_services.replace_conferences(
            draft,
            [
                ("West", [teams[0].id, teams[1].id], []),
                ("East", [teams[2].id, teams[3].id], []),
            ],
        )
        self.assertFalse(Conference.objects.filter(pk=stale.pk).exists())
        confs = list(draft.conferences.order_by("ordinal"))
        self.assertEqual([c.name for c in confs], ["West", "East"])
        self.assertEqual([c.ordinal for c in confs], [1, 2])
        self.assertEqual(
            set(confs[1].teams.values_list("id", flat=True)),
            {teams[2].id, teams[3].id},
        )
        self.assertEqual(confs[0].map_rotation_ids_json, [])
        league_services.replace_conferences(draft, [])
        self.assertEqual(draft.conferences.count(), 0)


# ---------------------------------------------------------------------------
# ConferencePartitionForm (section 3.1)
# ---------------------------------------------------------------------------


def _messages(form: ConferencePartitionForm) -> list[str]:
    return [m for e in form.errors.as_data()["conferences"] for m in e.messages]


class TestConferencePartitionForm(TempMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teams = make_teams("CpfT", 4)
        self.league = make_league("CpfL")
        self.season = make_open_season(self.league, self.teams)
        self.ids = [t.id for t in self.teams]

    def form(self, conferences, season=None, **data) -> ConferencePartitionForm:
        payload = {"conferences": conferences, **data}
        return ConferencePartitionForm(data=payload, season=season or self.season)

    def valid_two(self) -> list[dict]:
        return [
            {"name": "West", "team_ids": self.ids[:2]},
            {"name": "East", "team_ids": self.ids[2:]},
        ]

    # --- required / empty --------------------------------------------------

    def test_absent_is_required(self) -> None:
        form = ConferencePartitionForm(data={}, season=self.season)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["conferences"][0].code, "required")

    def test_none_is_required(self) -> None:
        form = self.form(None)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["conferences"][0].code, "required")

    def test_empty_list_cleans_to_empty(self) -> None:
        form = self.form([])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["conferences"], [])

    # --- happy path --------------------------------------------------------

    def test_valid_partition_normalises(self) -> None:
        m = confirmed_map("CpfMap")
        form = self.form(
            [
                {
                    "name": "  West ",
                    "team_ids": [self.ids[1], self.ids[0]],
                    "map_rotation_ids": [m.id, m.id],
                },
                {"name": "East", "team_ids": self.ids[2:], "map_rotation_ids": None},
            ]
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["conferences"],
            [
                ("West", sorted(self.ids[:2]), [m.id, m.id]),
                ("East", sorted(self.ids[2:]), []),
            ],
        )

    def test_string_rotation_accepted(self) -> None:
        a = confirmed_map("CpfStrA")
        b = confirmed_map("CpfStrB")
        confs = self.valid_two()
        confs[0]["map_rotation_ids"] = f"{b.id},{a.id}"
        form = self.form(confs)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["conferences"][0][2], [b.id, a.id])

    # --- step 1: shape -----------------------------------------------------

    def test_shape_violations_raise_exactly_one_error(self) -> None:
        t0 = self.ids[0]
        cases = {
            "dict": {"name": "A", "team_ids": [t0]},
            "string": "abc",
            "int": 5,
            "item_not_dict": ["West"],
            "missing_name": [{"team_ids": [t0]}],
            "name_not_str": [{"name": 5, "team_ids": [t0]}],
            "missing_team_ids": [{"name": "A"}],
            "team_ids_not_list": [{"name": "A", "team_ids": "1,2"}],
            "team_ids_bool": [{"name": "A", "team_ids": [True]}],
            "team_ids_str": [{"name": "A", "team_ids": [str(t0)]}],
            "team_ids_float": [{"name": "A", "team_ids": [1.0]}],
            "extra_key": [{"name": "A", "team_ids": [t0], "colour": "red"}],
            "rotation_int": [{"name": "A", "team_ids": [t0], "map_rotation_ids": 5}],
            "rotation_dict": [
                {"name": "A", "team_ids": [t0], "map_rotation_ids": {"a": 1}}
            ],
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                form = self.form(value)
                self.assertFalse(form.is_valid())
                self.assertEqual(_messages(form), [SHAPE_MSG])

    # --- step 2: membership ------------------------------------------------

    def test_duplicate_team(self) -> None:
        form = self.form(
            [
                {"name": "West", "team_ids": [self.ids[0], self.ids[1]]},
                {"name": "East", "team_ids": [self.ids[0], self.ids[2], self.ids[3]]},
            ]
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [duplicate_msg(self.ids[0])])

    def test_duplicates_listed_ascending(self) -> None:
        low, high = sorted(self.ids[:2])
        form = self.form(
            [
                {"name": "West", "team_ids": [high, low]},
                {"name": "East", "team_ids": [high, low, self.ids[2], self.ids[3]]},
            ]
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [duplicate_msg(low), duplicate_msg(high)])

    def test_not_enrolled_team(self) -> None:
        (outsider,) = make_teams("CpfOut", 1)
        confs = self.valid_two()
        confs[1]["team_ids"] = self.ids[2:] + [outsider.id]
        form = self.form(confs)
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [not_enrolled_msg(outsider.id)])

    def test_membership_errors_stop_before_partition(self) -> None:
        (outsider,) = make_teams("CpfStop", 1)
        form = self.form(
            [
                {"name": "", "team_ids": [self.ids[0], outsider.id]},
                {"name": "East", "team_ids": [self.ids[0]]},
            ]
        )
        self.assertFalse(form.is_valid())
        self.assertCountEqual(
            _messages(form),
            [duplicate_msg(self.ids[0]), not_enrolled_msg(outsider.id)],
        )

    # --- step 3: partition --------------------------------------------------

    def test_empty_name(self) -> None:
        confs = self.valid_two()
        confs[1]["name"] = "   "
        form = self.form(confs)
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [NAMES_EMPTY_MSG])

    def test_unassigned_team(self) -> None:
        form = self.form(
            [
                {"name": "West", "team_ids": self.ids[:2]},
                {"name": "East", "team_ids": [self.ids[2]]},
            ]
        )
        self.assertFalse(form.is_valid())
        self.assertIn(UNASSIGNED_MSG, _messages(form))

    def test_under_two_per_conference(self) -> None:
        form = self.form(
            [
                {"name": "West", "team_ids": self.ids[:3]},
                {"name": "East", "team_ids": [self.ids[3]]},
            ]
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [UNDER_TWO_MSG])

    def test_bracket_floor_variant(self) -> None:
        add_tournament_phase(self.season)
        form = self.form(self.valid_two())
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [BRACKET_FLOOR_MSG])

    # --- step 4: rotation ----------------------------------------------------

    def test_list_rotation_bad_values(self) -> None:
        for bad in (True, 1.5, "5", None):
            with self.subTest(value=bad):
                confs = self.valid_two()
                confs[0]["map_rotation_ids"] = [bad]
                form = self.form(confs)
                self.assertFalse(form.is_valid())
                self.assertEqual(_messages(form), [ROTATION_INVALID_MSG])

    def test_list_rotation_unknown_id(self) -> None:
        confs = self.valid_two()
        confs[0]["map_rotation_ids"] = [999_999]
        form = self.form(confs)
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [ROTATION_UNKNOWN_MSG])

    def test_rotation_errors_in_conference_then_item_order(self) -> None:
        confs = self.valid_two()
        confs[0]["map_rotation_ids"] = ["x", 999_999]
        confs[1]["map_rotation_ids"] = "abc"
        form = self.form(confs)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            _messages(form),
            [ROTATION_INVALID_MSG, ROTATION_UNKNOWN_MSG, ROTATION_INVALID_MSG],
        )

    # --- step 5: guard -------------------------------------------------------

    def test_rotate_by_conference_guard_once(self) -> None:
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        form = self.form(self.valid_two())
        self.assertFalse(form.is_valid())
        self.assertEqual(_messages(form), [ROTATION_GUARD_MSG])

    def test_guard_silent_under_other_modes(self) -> None:
        form = self.form(self.valid_two())
        self.assertTrue(form.is_valid(), form.errors)

    def test_guard_skipped_for_empty_partition(self) -> None:
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        form = self.form([])
        self.assertTrue(form.is_valid(), form.errors)

    # --- combined order == today's HTML list ---------------------------------

    def _combined_inputs(self) -> list[dict]:
        return [
            {"name": " ", "team_ids": [self.ids[0]], "map_rotation_ids": "abc"},
            {"name": "East", "team_ids": self.ids[2:], "map_rotation_ids": [999_999]},
        ]

    def test_combined_error_order(self) -> None:
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        form = self.form(self._combined_inputs())
        self.assertFalse(form.is_valid())
        self.assertEqual(
            _messages(form),
            [
                NAMES_EMPTY_MSG,
                UNASSIGNED_MSG,
                UNDER_TWO_MSG,
                ROTATION_INVALID_MSG,
                ROTATION_UNKNOWN_MSG,
                ROTATION_GUARD_MSG,
            ],
        )

    def test_combined_errors_match_html_manage_conferences(self) -> None:
        """The HTML POST (indexed parse) and the API body (JSON) report the
        identical error list for the equivalent submission."""
        self.season.map_mode = "rotate_by_conference"
        self.season.save(update_fields=["map_mode"])
        form = self.form(self._combined_inputs())
        self.assertFalse(form.is_valid())
        response = self.client.post(
            reverse("manage_conferences", args=[self.season.id]),
            {
                "conference_name": [" ", "East"],
                "conference_rotation": ["abc", "999999"],
                f"team_{self.ids[0]}_conference": "0",
                f"team_{self.ids[2]}_conference": "1",
                f"team_{self.ids[3]}_conference": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["errors"], _messages(form))
        self.assertEqual(self.season.conferences.count(), 0)
