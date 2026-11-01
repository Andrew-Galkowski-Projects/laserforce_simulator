"""LG-01b / LG-01j / CAR-01 / FIN-03 / FIN-05 / SUB-01 / CONF-02 / CONF-05 /
CONF-06 -- ``matches.forms.CreateLeagueForm`` and the FIN-03 budget seeder.

ANG-08: the HTML create views went. Every create-path case that asserted
persisted rows (enrolment, free-agent pool, rollback, ``current_team``, map
modes, the phase composer, tournament mode / cut / sub-config, baseline
ratings and potential, the named manager team, FIN-03 budgets, the challenge
luxury tax, the rotation order and the Conference split) is pinned through
``POST /api/leagues/`` in ``test_api_leagues.py::TestLeagueCreate``. This file
keeps the form-level validation and the pure seeding helper.
"""

from __future__ import annotations

import io as _lg01j_io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
from matches.development import STAT_FIELDS
from matches.forms import CreateLeagueForm
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Reusable valid payload
# ---------------------------------------------------------------------------


def _valid_payload(**overrides) -> dict:
    payload = {
        "league_name": "Spring 2026",
        "season_name": "Season 1",
        "start_date": "2026-06-01",
        "num_teams": "4",
        "schedule_format": "single_round_robin",
        "mean": "50",
        "std_dev": "15",
        # LG-01j -- ``map_mode`` is a required field on ``CreateLeagueForm``.
        "map_mode": "none",
    }
    payload.update(overrides)
    return payload


def _png_bytes() -> bytes:
    from PIL import Image as _PILImage

    buf = _lg01j_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(0, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _make_map_with_config(name: str = "Map") -> ArenaMap:
    """An ArenaMap with a confirmed MapZoneConfig + both bases, so it
    surfaces in ``_maps_with_confirmed_config()``."""
    arena_map = ArenaMap.objects.create(
        name=name,
        image=SimpleUploadedFile(f"{name}.png", _png_bytes(), content_type="image/png"),
        img_width=10,
        img_height=10,
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        zone_data={"zones": [[1, 1], [1, 1]]},
        confirmed=True,
    )
    # UXP-10 - a simulation-ready map also needs a red and a blue base.
    for base_type in ("red", "blue"):
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type=base_type, x_px=5, y_px=5
        )
    return arena_map


def _make_map_without_config(name: str = "MapNoCfg") -> ArenaMap:
    """ArenaMap with NO confirmed MapZoneConfig -- NOT in the picker queryset."""
    return ArenaMap.objects.create(
        name=name,
        image=SimpleUploadedFile(f"{name}.png", _png_bytes(), content_type="image/png"),
        img_width=10,
        img_height=10,
    )


def _rotation_wire(ids: list[int]) -> str:
    """Serialize an ordered id list to the ``map_rotation`` wire-format
    (comma-joined, author order preserved)."""
    return ",".join(str(i) for i in ids)


# ---------------------------------------------------------------------------
# LG-01j -- map_mode / map_pool
# ---------------------------------------------------------------------------


class TestLeagueCreateMapMode(TestCase):
    """LG-01j -- ``CreateLeagueForm.map_mode``: choices, initial, and the
    per-mode-vs-pool ``clean()`` rules."""

    def test_form_has_map_mode_field(self) -> None:
        self.assertIn("map_mode", CreateLeagueForm().fields)

    def test_map_mode_choices_match_five_locked_tuples(self) -> None:
        choices = list(CreateLeagueForm().fields["map_mode"].choices)
        # SUB-01 widened the LG-01j 3-tuple to 4 with ``rotate_by_matchday``;
        # CONF-06 appends ``rotate_by_conference`` as the FIFTH and LAST tuple.
        # ``required=True`` + ``initial="none"`` => no blank choice.
        self.assertEqual(
            choices,
            [
                ("none", "3-zone fallback"),
                ("single", "Single map"),
                ("random_per_round", "Random per Round"),
                ("rotate_by_matchday", "Rotate by matchday"),
                ("rotate_by_conference", "Rotate by Conference"),
            ],
        )

    def test_map_mode_initial_is_none_string(self) -> None:
        self.assertEqual(CreateLeagueForm().fields["map_mode"].initial, "none")

    def test_map_mode_required_true(self) -> None:
        self.assertTrue(CreateLeagueForm().fields["map_mode"].required)

    def test_clean_accepts_mode_none_with_empty_pool(self) -> None:
        form = CreateLeagueForm(_valid_payload(map_mode="none"))
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_single_with_one_map(self) -> None:
        m = _make_map_with_config("S1")
        payload = _valid_payload(map_mode="single")
        payload["map_pool"] = [str(m.id)]
        form = CreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_random_per_round_with_one_map(self) -> None:
        m = _make_map_with_config("R1")
        payload = _valid_payload(map_mode="random_per_round")
        payload["map_pool"] = [str(m.id)]
        form = CreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_accepts_mode_random_per_round_with_five_maps(self) -> None:
        ms = [_make_map_with_config(f"R5_{i}") for i in range(5)]
        payload = _valid_payload(map_mode="random_per_round")
        payload["map_pool"] = [str(m.id) for m in ms]
        form = CreateLeagueForm(payload)
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_clean_rejects_bogus_mode(self) -> None:
        form = CreateLeagueForm(_valid_payload(map_mode="bogus"))
        self.assertFalse(form.is_valid())
        # Django field-level enum error keyed on ``map_mode``.
        self.assertIn("map_mode", form.errors)


class TestLeagueCreateMapPool(TestCase):
    """LG-01j -- ``CreateLeagueForm.map_pool``: queryset is
    ``_maps_with_confirmed_config()``; the 3 mode-vs-pool rules."""

    def test_form_has_map_pool_field(self) -> None:
        self.assertIn("map_pool", CreateLeagueForm().fields)

    def test_map_pool_required_false(self) -> None:
        self.assertFalse(CreateLeagueForm().fields["map_pool"].required)

    def test_queryset_excludes_maps_without_confirmed_config(self) -> None:
        live = _make_map_with_config("LiveMap")
        skipped = _make_map_without_config("SkippedMap")
        qs = CreateLeagueForm().fields["map_pool"].queryset
        qs_ids = set(qs.values_list("id", flat=True))
        self.assertIn(live.id, qs_ids)
        self.assertNotIn(skipped.id, qs_ids)

    def test_clean_rejects_mode_none_with_non_empty_pool(self) -> None:
        m = _make_map_with_config("NoneWithPool")
        payload = _valid_payload(map_mode="none")
        payload["map_pool"] = [str(m.id)]
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must be empty when Map mode is '3-zone fallback'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_single_with_empty_pool(self) -> None:
        form = CreateLeagueForm(_valid_payload(map_mode="single"))
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain exactly 1 map when Map mode is 'Single map'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_single_with_two_maps(self) -> None:
        m1 = _make_map_with_config("S2A")
        m2 = _make_map_with_config("S2B")
        payload = _valid_payload(map_mode="single")
        payload["map_pool"] = [str(m1.id), str(m2.id)]
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain exactly 1 map when Map mode is 'Single map'.",
            form.errors.get("map_pool", []),
        )

    def test_clean_rejects_mode_random_per_round_with_empty_pool(self) -> None:
        form = CreateLeagueForm(_valid_payload(map_mode="random_per_round"))
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Map pool must contain at least 1 map when Map mode is 'Random per Round'.",
            form.errors.get("map_pool", []),
        )


# ---------------------------------------------------------------------------
# CAR-01 -- the manager names their own team
# ---------------------------------------------------------------------------


class TestCar01ManagerTeamName(TestCase):
    """CAR-01 -- ``manager_team_name`` is optional and round-trips."""

    def test_form_valid_with_blank_manager_team_name(self) -> None:
        form = CreateLeagueForm(_valid_payload())
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())

    def test_form_round_trips_populated_manager_team_name(self) -> None:
        form = CreateLeagueForm(_valid_payload(manager_team_name="Galkowski FC"))
        self.assertTrue(form.is_valid(), msg=form.errors.as_json())
        self.assertEqual(form.cleaned_data["manager_team_name"], "Galkowski FC")


# ---------------------------------------------------------------------------
# FIN-03 -- the AI-budget seeder
# ---------------------------------------------------------------------------


class TestFin03SeedBudgetsByStrengthHelper(TestCase):
    """``_seed_team_budgets_by_strength(teams)`` ranks by mean active-roster
    overall DESC (team_id ASC tiebreak), assigns the rank-linear band
    ``[20, 90]`` to all 3 budget fields, and bulk-updates. Exercised directly so
    the strength ordering is deterministic."""

    def _pin_roster_overall(self, team, value: int) -> None:
        for p in team.active_players:
            for name in STAT_FIELDS:
                setattr(p, name, value)
            p.save()

    def test_consts_exist_with_locked_values(self) -> None:
        from matches.league_services import (
            SEED_BUDGET_MAX,
            SEED_BUDGET_MIN,
            SEED_BUDGET_SINGLE,
        )

        self.assertEqual(SEED_BUDGET_MIN, 20)
        self.assertEqual(SEED_BUDGET_MAX, 90)
        self.assertEqual(SEED_BUDGET_SINGLE, 55)

    def test_strongest_gets_max_weakest_gets_min(self) -> None:
        from matches.league_services import _seed_team_budgets_by_strength

        strong, _ = make_team_with_slots("Fin03Strong")
        weak, _ = make_team_with_slots("Fin03Weak")
        self._pin_roster_overall(strong, 90)
        self._pin_roster_overall(weak, 20)

        _seed_team_budgets_by_strength([strong, weak])

        strong.refresh_from_db()
        weak.refresh_from_db()
        # Strongest roster -> SEED_BUDGET_MAX (90), weakest -> SEED_BUDGET_MIN (20).
        self.assertEqual(strong.budget_scouting, 90)
        self.assertEqual(weak.budget_scouting, 20)

    def test_all_three_budget_fields_equal_per_team(self) -> None:
        from matches.league_services import _seed_team_budgets_by_strength

        a, _ = make_team_with_slots("Fin03TripA")
        b, _ = make_team_with_slots("Fin03TripB")
        c, _ = make_team_with_slots("Fin03TripC")
        self._pin_roster_overall(a, 80)
        self._pin_roster_overall(b, 50)
        self._pin_roster_overall(c, 30)

        _seed_team_budgets_by_strength([a, b, c])

        for team in (a, b, c):
            team.refresh_from_db()
            self.assertEqual(team.budget_scouting, team.budget_coaching)
            self.assertEqual(team.budget_scouting, team.budget_facilities)

    def test_single_team_gets_seed_budget_single(self) -> None:
        from matches.league_services import _seed_team_budgets_by_strength

        solo, _ = make_team_with_slots("Fin03Solo")
        self._pin_roster_overall(solo, 60)

        _seed_team_budgets_by_strength([solo])

        solo.refresh_from_db()
        self.assertEqual(solo.budget_scouting, 55)
        self.assertEqual(solo.budget_coaching, 55)
        self.assertEqual(solo.budget_facilities, 55)

    def test_rank_linear_spread_strongest_above_weakest(self) -> None:
        from matches.league_services import _seed_team_budgets_by_strength

        strong, _ = make_team_with_slots("Fin03SpreadStrong")
        mid, _ = make_team_with_slots("Fin03SpreadMid")
        weak, _ = make_team_with_slots("Fin03SpreadWeak")
        self._pin_roster_overall(strong, 95)
        self._pin_roster_overall(mid, 55)
        self._pin_roster_overall(weak, 15)

        _seed_team_budgets_by_strength([strong, mid, weak])

        strong.refresh_from_db()
        mid.refresh_from_db()
        weak.refresh_from_db()
        self.assertEqual(strong.budget_scouting, 90)
        self.assertEqual(weak.budget_scouting, 20)
        # The middle team lands strictly between the band ends (rank-linear).
        self.assertGreater(mid.budget_scouting, weak.budget_scouting)
        self.assertLess(mid.budget_scouting, strong.budget_scouting)


# ---------------------------------------------------------------------------
# FIN-05 -- the challenge luxury-tax field
# ---------------------------------------------------------------------------


class TestCreateLeagueChallengeLuxuryTax(TestCase):
    """The ``challenge_fired_luxury_tax`` create-form field."""

    def test_form_has_challenge_field(self) -> None:
        self.assertIn("challenge_fired_luxury_tax", CreateLeagueForm().fields)

    def test_challenge_field_not_required(self) -> None:
        self.assertFalse(
            CreateLeagueForm().fields["challenge_fired_luxury_tax"].required
        )

    def test_unchecked_payload_is_valid_and_false(self) -> None:
        form = CreateLeagueForm(data=_valid_payload())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["challenge_fired_luxury_tax"])

    def test_checked_payload_is_valid_and_true(self) -> None:
        form = CreateLeagueForm(data=_valid_payload(challenge_fired_luxury_tax="on"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["challenge_fired_luxury_tax"])


# ---------------------------------------------------------------------------
# SUB-01 -- the ``rotate_by_matchday`` cross-guard matrix
# ---------------------------------------------------------------------------


class TestLeagueCreateRotateByMatchdayValidationMatrix(TestCase):
    """SUB-01 -- the FULL 4x2 cross-guard matrix, driven through the REAL form.

    Rotation-rule errors attach to the ``map_rotation`` field; the rotate-mode
    pool rule attaches to ``map_pool``. Asserts on locked error SUBSTRINGS.
    """

    def _errors(self, payload: dict, field: str) -> list:
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        return form.errors.get(field, [])

    # ---- rotation must be empty for the three non-rotate modes ----

    def test_mode_none_with_non_empty_rotation_rejected(self) -> None:
        m = _make_map_with_config("NoneRot")
        payload = _valid_payload(map_mode="none", league_name="NoneRotL")
        payload["map_rotation"] = _rotation_wire([m.id])
        errs = self._errors(payload, "map_rotation")
        self.assertTrue(
            any("must be empty when Map mode is '3-zone fallback'." in e for e in errs),
            f"map_rotation errors={errs!r}",
        )

    def test_mode_single_with_non_empty_rotation_rejected(self) -> None:
        m1 = _make_map_with_config("SingleRotPool")
        m2 = _make_map_with_config("SingleRotRot")
        payload = _valid_payload(map_mode="single", league_name="SingleRotL")
        payload["map_pool"] = [str(m1.id)]
        payload["map_rotation"] = _rotation_wire([m2.id])
        errs = self._errors(payload, "map_rotation")
        self.assertTrue(
            any("must be empty when Map mode is 'Single map'." in e for e in errs),
            f"map_rotation errors={errs!r}",
        )

    def test_mode_random_per_round_with_non_empty_rotation_rejected(self) -> None:
        m1 = _make_map_with_config("RandRotPool")
        m2 = _make_map_with_config("RandRotRot")
        payload = _valid_payload(map_mode="random_per_round", league_name="RandRotL")
        payload["map_pool"] = [str(m1.id)]
        payload["map_rotation"] = _rotation_wire([m2.id])
        errs = self._errors(payload, "map_rotation")
        self.assertTrue(
            any(
                "must be empty when Map mode is 'Random per Round'." in e for e in errs
            ),
            f"map_rotation errors={errs!r}",
        )

    # ---- rotate mode: pool must be empty, rotation >= 1 ----

    def test_mode_rotate_with_non_empty_pool_rejected(self) -> None:
        m_pool = _make_map_with_config("RotateWithPoolPool")
        m_rot = _make_map_with_config("RotateWithPoolRot")
        payload = _valid_payload(
            map_mode="rotate_by_matchday", league_name="RotateWithPoolL"
        )
        payload["map_pool"] = [str(m_pool.id)]  # must be empty for rotate
        payload["map_rotation"] = _rotation_wire([m_rot.id])
        errs = self._errors(payload, "map_pool")
        self.assertTrue(
            any(
                "Map pool must be empty when Map mode is 'Rotate by matchday'." in e
                for e in errs
            ),
            f"map_pool errors={errs!r}",
        )

    def test_mode_rotate_with_empty_rotation_rejected(self) -> None:
        payload = _valid_payload(
            map_mode="rotate_by_matchday", league_name="RotateEmptyRotL"
        )
        # No map_rotation key => empty rotation.
        errs = self._errors(payload, "map_rotation")
        self.assertTrue(
            any(
                "must contain at least 1 map when Map mode is 'Rotate by matchday'."
                in e
                for e in errs
            ),
            f"map_rotation errors={errs!r}",
        )

    def test_rotation_id_without_confirmed_config_rejected(self) -> None:
        """An id NOT in ``_maps_with_confirmed_config()`` is rejected at the
        form layer."""
        good = _make_map_with_config("RotConfGood")
        skipped = _make_map_without_config("RotConfSkipped")
        payload = _valid_payload(map_mode="rotate_by_matchday", league_name="RotConfL")
        payload["map_rotation"] = _rotation_wire([good.id, skipped.id])
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        # The error attaches to map_rotation (the field carrying the bad id).
        self.assertIn("map_rotation", form.errors)


# ---------------------------------------------------------------------------
# CONF-05 / CONF-02 -- the Conference count
# ---------------------------------------------------------------------------


class TestConf05CreateLeagueConferences(TestCase):
    """CONF-05 -- the form's ``number_of_conferences`` rejects too-few teams."""

    def test_form_rejects_too_few_teams(self):
        form = CreateLeagueForm(
            _valid_payload(num_teams="4", number_of_conferences="3")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("number_of_conferences", form.errors)

    def test_form_accepts_enough_teams(self):
        form = CreateLeagueForm(
            _valid_payload(num_teams="8", number_of_conferences="3")
        )
        self.assertTrue(form.is_valid(), form.errors)


class TestConf02ConferenceTournamentTeamFloor(TestCase):
    """CONF-02 review fix - a tournament phase raises the per-Conference floor.

    Found by manual browser testing: 4 teams across 2 Conferences with a
    trailing tournament phase produced two 2-team brackets, and the build
    raised out of the atomic activation, rolling back the play-week that
    triggered it. CONF-05's rule only required 2 teams per Conference, which
    is below the bracket engine's 4-participant floor.
    """

    def test_rejects_four_teams_across_two_conferences_with_a_tournament(self):
        form = CreateLeagueForm(
            data=_valid_payload(
                num_teams="4",
                number_of_conferences="2",
                phases="round_robin,tournament",
            )
        )
        self.assertFalse(form.is_valid())
        self.assertIn("number_of_conferences", form.errors)
        self.assertIn("8 teams", str(form.errors["number_of_conferences"]))

    def test_allows_four_teams_across_two_conferences_without_a_tournament(self):
        """No bracket to field, so CONF-05's 2-per-Conference floor still applies."""
        form = CreateLeagueForm(
            data=_valid_payload(
                num_teams="4",
                number_of_conferences="2",
                phases="round_robin",
            )
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_allows_eight_teams_across_two_conferences_with_a_tournament(self):
        form = CreateLeagueForm(
            data=_valid_payload(
                num_teams="8",
                number_of_conferences="2",
                phases="round_robin,tournament",
            )
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_rejects_eight_teams_across_three_conferences_with_a_tournament(self):
        """8 / 3 would leave 2-3 per Conference - under the engine floor."""
        form = CreateLeagueForm(
            data=_valid_payload(
                num_teams="8",
                number_of_conferences="3",
                phases="round_robin,tournament",
            )
        )
        self.assertFalse(form.is_valid())
        self.assertIn("number_of_conferences", form.errors)


# ---------------------------------------------------------------------------
# CONF-06 -- ``rotate_by_conference`` create guards
# ---------------------------------------------------------------------------
#
# The block uses ``add_error`` (NOT ``raise``) so all three guards can surface
# together in one submission. The Season-level pool/rotation must both be
# EMPTY because the rotation lives one level down, on each Conference.

_CONF06_POOL_ERROR = "Map pool must be empty when Map mode is 'Rotate by Conference'."
_CONF06_ROTATION_ERROR = (
    "Map rotation must be empty when Map mode is 'Rotate by Conference'."
)
_CONF06_CONFERENCES_ERROR = (
    "Map mode 'Rotate by Conference' requires at least 2 conferences."
)


class TestConf06CreateLeagueRotateByConferenceGuards(TestCase):
    """The three locked error strings under their exact field keys, and a valid
    ``rotate_by_conference`` submission."""

    def _payload(self, **overrides) -> dict:
        payload = _valid_payload(
            map_mode="rotate_by_conference",
            num_teams="4",
            number_of_conferences="2",
            league_name="Conf06CreateL",
        )
        payload.update(overrides)
        return payload

    def _errors(self, payload: dict, field: str) -> list:
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        return [str(e) for e in form.errors.get(field, [])]

    def test_valid_submission_is_valid(self) -> None:
        form = CreateLeagueForm(self._payload())
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_valid_submission_with_four_conferences_is_valid(self) -> None:
        form = CreateLeagueForm(self._payload(num_teams="8", number_of_conferences="4"))
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_non_empty_pool_rejected_on_map_pool(self) -> None:
        m = _make_map_with_config("Conf06PoolMap")
        errs = self._errors(self._payload(map_pool=[str(m.id)]), "map_pool")
        self.assertIn(_CONF06_POOL_ERROR, errs, f"map_pool errors={errs!r}")

    def test_non_empty_rotation_rejected_on_map_rotation(self) -> None:
        m = _make_map_with_config("Conf06RotMap")
        errs = self._errors(
            self._payload(map_rotation=_rotation_wire([m.id])), "map_rotation"
        )
        self.assertIn(_CONF06_ROTATION_ERROR, errs, f"map_rotation errors={errs!r}")

    def test_zero_conferences_rejected_on_number_of_conferences(self) -> None:
        errs = self._errors(
            self._payload(number_of_conferences="0"), "number_of_conferences"
        )
        self.assertIn(
            _CONF06_CONFERENCES_ERROR, errs, f"number_of_conferences errors={errs!r}"
        )

    def test_missing_conferences_field_rejected(self) -> None:
        payload = self._payload()
        payload.pop("number_of_conferences")
        errs = self._errors(payload, "number_of_conferences")
        self.assertIn(
            _CONF06_CONFERENCES_ERROR, errs, f"number_of_conferences errors={errs!r}"
        )

    def test_one_conference_rejected(self) -> None:
        errs = self._errors(
            self._payload(number_of_conferences="1"), "number_of_conferences"
        )
        self.assertIn(
            _CONF06_CONFERENCES_ERROR, errs, f"number_of_conferences errors={errs!r}"
        )

    def test_all_three_guards_surface_together(self) -> None:
        """``add_error``, not ``raise`` -- one submission reports every
        offending field rather than only the first."""
        m_pool = _make_map_with_config("Conf06AllPool")
        m_rot = _make_map_with_config("Conf06AllRot")
        payload = self._payload(
            map_pool=[str(m_pool.id)],
            map_rotation=_rotation_wire([m_rot.id]),
            number_of_conferences="0",
        )
        form = CreateLeagueForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn(
            _CONF06_POOL_ERROR, [str(e) for e in form.errors.get("map_pool", [])]
        )
        self.assertIn(
            _CONF06_ROTATION_ERROR,
            [str(e) for e in form.errors.get("map_rotation", [])],
        )
        self.assertIn(
            _CONF06_CONFERENCES_ERROR,
            [str(e) for e in form.errors.get("number_of_conferences", [])],
        )

    def test_other_modes_unaffected_by_the_new_guard(self) -> None:
        """The four pre-existing per-mode blocks are untouched: a plain
        ``none`` create with 2 Conferences still validates."""
        form = CreateLeagueForm(
            _valid_payload(
                map_mode="none",
                num_teams="4",
                number_of_conferences="2",
                league_name="Conf06Untouched",
            )
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
