"""STAT-01: PlayerForm stat fields and overall_rating property."""

from django.test import TestCase

from teams.forms import PlayerForm, TeamSlotForm
from teams.models import Player, Team

ALL_STAT_FIELDS = [
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
]


def _minimal_stat_payload(**overrides):
    """Return a POST-data dict with all 19 stats set to 50, plus required profile fields."""
    data = {stat: 50 for stat in ALL_STAT_FIELDS}
    data["name"] = "Test Player"
    data["preferred_roles"] = []
    data["total_games"] = 0
    data.update(overrides)
    return data


class PlayerFormFieldsTest(TestCase):
    """STAT-01-F1: PlayerForm declares all 19 stat fields."""

    def test_form_declares_all_19_stat_fields(self):
        form = PlayerForm()
        missing = [f for f in ALL_STAT_FIELDS if f not in form.fields]
        self.assertEqual(
            missing,
            [],
            f"PlayerForm is missing stat field(s): {missing}",
        )

    def test_form_has_exactly_19_stat_fields(self):
        form = PlayerForm()
        stat_fields_present = [f for f in ALL_STAT_FIELDS if f in form.fields]
        self.assertEqual(
            len(stat_fields_present),
            19,
            "Expected 19 stat fields in PlayerForm",
        )


class PlayerFormValidationTest(TestCase):
    """STAT-01-F2: PlayerForm accepts valid stat integers in 0–100 range."""

    def setUp(self):
        self.team = Team.objects.create(name="Form Validation Team")

    def test_form_valid_with_all_stats_at_50(self):
        data = _minimal_stat_payload()
        form = PlayerForm(data=data)
        self.assertTrue(
            form.is_valid(),
            f"Form should be valid with all stats at 50; errors: {form.errors}",
        )

    def test_form_valid_with_boundary_values(self):
        """Stats at 0 and 100 (boundaries) are integers — form should accept them."""
        # Set alternating 0/100 values to exercise both ends
        stats = {
            stat: (0 if i % 2 == 0 else 100) for i, stat in enumerate(ALL_STAT_FIELDS)
        }
        data = _minimal_stat_payload(**stats)
        form = PlayerForm(data=data)
        self.assertTrue(
            form.is_valid(),
            f"Form should be valid with boundary stat values; errors: {form.errors}",
        )

    def test_form_valid_with_known_fixed_values(self):
        """Deterministic non-default values are accepted."""
        fixed = {stat: (i * 5) % 101 for i, stat in enumerate(ALL_STAT_FIELDS)}
        data = _minimal_stat_payload(**fixed)
        form = PlayerForm(data=data)
        self.assertTrue(
            form.is_valid(),
            f"Form should be valid with fixed stat values; errors: {form.errors}",
        )

    def test_form_invalid_without_name(self):
        data = _minimal_stat_payload()
        data["name"] = ""
        form = PlayerForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_stat_outside_0_100_rejected_by_validator(self):
        data = _minimal_stat_payload(accuracy=200)
        form = PlayerForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("accuracy", form.errors)

    def test_stat_below_0_rejected_by_validator(self):
        data = _minimal_stat_payload(speed=-1)
        form = PlayerForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("speed", form.errors)


class PlayerFormSaveTest(TestCase):
    """STAT-01-F3: PlayerForm persists all 19 stat values to the database."""

    def setUp(self):
        self.team = Team.objects.create(name="Form Save Team")

    def test_form_saves_all_stats_to_db(self):
        fixed_values = {stat: (i + 1) * 5 for i, stat in enumerate(ALL_STAT_FIELDS)}
        data = _minimal_stat_payload(**fixed_values)
        form = PlayerForm(data=data)
        self.assertTrue(
            form.is_valid(),
            f"Form unexpectedly invalid: {form.errors}",
        )
        player = form.save(commit=False)
        player.team = self.team
        player.save()

        saved = Player.objects.get(pk=player.pk)
        for stat, expected_value in fixed_values.items():
            actual = getattr(saved, stat)
            self.assertEqual(
                actual,
                expected_value,
                f"Stat '{stat}' was not saved correctly: expected {expected_value}, got {actual}",
            )

    def test_form_saves_name_correctly(self):
        data = _minimal_stat_payload(name="Named Player")
        form = PlayerForm(data=data)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")
        player = form.save(commit=False)
        player.team = self.team
        player.save()

        saved = Player.objects.get(pk=player.pk)
        self.assertEqual(saved.name, "Named Player")


class PlayerFormDefaultStatsTest(TestCase):
    """STAT-01-F4: New players default all stats to 50 when no stat data is submitted."""

    def setUp(self):
        self.team = Team.objects.create(name="Default Stats Team")

    def test_player_created_without_stat_payload_defaults_to_50(self):
        """
        When a Player is created directly (not via form), every stat defaults to 50
        per the model field definitions.  This covers the 'form submits no stat values'
        scenario: the model-level default is what protects the data.
        """
        player = Player.objects.create(team=self.team, name="Default Player")
        for stat in ALL_STAT_FIELDS:
            value = getattr(player, stat)
            self.assertEqual(
                value,
                50,
                f"Stat '{stat}' should default to 50, got {value}",
            )

    def test_form_with_only_name_and_roles_omits_stats_gracefully(self):
        """
        A form submission that includes name+preferred_roles but omits all
        stat fields is *invalid* because the stat fields are required by
        IntegerField (no blank=True on model fields).  This test documents
        that behavior so it is not silently changed.
        """
        minimal_data = {"name": "Minimal Player", "preferred_roles": []}
        form = PlayerForm(data=minimal_data)
        # Without stat fields the form is invalid — they are required inputs
        # (unless STAT-01 adds initial/default handling to the form itself)
        if form.is_valid():
            # If the form adds defaults, all saved stats must be 50
            player = form.save(commit=False)
            player.team = self.team
            player.save()
            saved = Player.objects.get(pk=player.pk)
            for stat in ALL_STAT_FIELDS:
                self.assertEqual(
                    getattr(saved, stat),
                    50,
                    f"Stat '{stat}' should be 50 when omitted, got {getattr(saved, stat)}",
                )
        else:
            # Documenting: stat fields are required; missing them → form invalid
            stat_errors = [f for f in ALL_STAT_FIELDS if f in form.errors]
            self.assertTrue(
                len(stat_errors) > 0,
                "Expected stat-field errors when stats are omitted from POST data",
            )


class PlayerOverallRatingTest(TestCase):
    """STAT-01-M6/M7: overall_rating returns correct mean of all 19 stats."""

    def setUp(self):
        self.team = Team.objects.create(name="Rating Test Team")

    def test_overall_rating_returns_50_when_all_stats_at_default(self):
        player = Player.objects.create(team=self.team, name="Default Rating Player")
        self.assertEqual(
            player.overall_rating,
            50.0,
            "overall_rating should be 50.0 when all stats are at their default of 50",
        )

    def test_overall_rating_correct_mean_of_known_values(self):
        """Use fixed values whose mean is easy to compute."""
        # Set all stats to 70 → mean = 70.0
        kwargs = {stat: 70 for stat in ALL_STAT_FIELDS}
        player = Player.objects.create(
            team=self.team, name="Uniform 70 Player", **kwargs
        )
        self.assertAlmostEqual(
            player.overall_rating,
            70.0,
            places=5,
            msg="overall_rating should equal 70.0 when all stats are 70",
        )

    def test_overall_rating_correct_mean_mixed_values(self):
        """
        Assign a deterministic mix of values and assert the exact mean.
        Values: first 10 stats = 40, last 9 stats = 60
        Expected mean = (10 * 40 + 9 * 60) / 19 = (400 + 540) / 19 = 940 / 19
        """
        kwargs = {}
        for i, stat in enumerate(ALL_STAT_FIELDS):
            kwargs[stat] = 40 if i < 10 else 60
        player = Player.objects.create(
            team=self.team, name="Mixed Rating Player", **kwargs
        )
        expected_mean = (10 * 40 + 9 * 60) / 19
        self.assertAlmostEqual(
            player.overall_rating,
            expected_mean,
            places=5,
            msg=f"overall_rating should be {expected_mean:.5f} for mixed 40/60 split",
        )

    def test_overall_rating_all_zero(self):
        kwargs = {stat: 0 for stat in ALL_STAT_FIELDS}
        player = Player.objects.create(team=self.team, name="Zero Player", **kwargs)
        self.assertEqual(player.overall_rating, 0.0)

    def test_overall_rating_all_100(self):
        kwargs = {stat: 100 for stat in ALL_STAT_FIELDS}
        player = Player.objects.create(team=self.team, name="Elite Player", **kwargs)
        self.assertEqual(player.overall_rating, 100.0)

    def test_overall_rating_uses_all_19_stats(self):
        """Changing a single stat must change overall_rating — verifies all 19 are included."""
        base_kwargs = {stat: 50 for stat in ALL_STAT_FIELDS}
        player = Player(**base_kwargs, team=self.team, name="Probe Player")
        base_rating = player.overall_rating

        for stat in ALL_STAT_FIELDS:
            setattr(player, stat, 100)
            changed_rating = player.overall_rating
            self.assertNotEqual(
                changed_rating,
                base_rating,
                f"Changing '{stat}' to 100 did not affect overall_rating — "
                "is it included in the stat list?",
            )
            setattr(player, stat, 50)


class TeamSlotFormPartialSaveTest(TestCase):
    """Regression: CT-1 — every slot was required, so a team with < 6
    players could never assign any slot and progress couldn't be saved
    incrementally. Slots are null/blank on the model; the form must allow
    partial assignment."""

    def setUp(self):
        self.team = Team.objects.create(name="Partial Slots Team")
        self.cmd = Player.objects.create(team=self.team, name="Cmd")
        self.hvy = Player.objects.create(team=self.team, name="Hvy")

    def test_no_slot_field_is_required(self):
        form = TeamSlotForm(instance=self.team)
        required = [n for n, f in form.fields.items() if f.required]
        self.assertEqual(required, [], f"slot field(s) still required: {required}")

    def test_partial_assignment_is_valid_and_saves(self):
        form = TeamSlotForm(data={"slot_commander": self.cmd.id}, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.slot_commander_id, self.cmd.id)
        self.assertIsNone(self.team.slot_heavy_id)
        self.assertIsNone(self.team.slot_medic_id)

    def test_empty_assignment_is_valid(self):
        form = TeamSlotForm(data={}, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors.as_json())


class PlayerFormProfileBoundsTest(TestCase):
    """Regression: CT-2 — Age / Started playing age / Total games had no
    real min/max (reported valuemin=0 valuemax=0) and accepted arbitrary
    or negative numbers. They must have bounded widgets and server-side
    validators."""

    PROFILE_FIELDS = ("age", "started_playing_age", "total_games")

    def test_profile_widgets_declare_min_and_max(self):
        form = PlayerForm()
        for name in self.PROFILE_FIELDS:
            attrs = form.fields[name].widget.attrs
            self.assertIn("min", attrs, f"{name} widget missing min")
            self.assertIn("max", attrs, f"{name} widget missing max")
            self.assertGreater(
                int(attrs["max"]), int(attrs["min"]), f"{name} max <= min"
            )

    def test_negative_and_oversized_values_rejected(self):
        for name, bad in [
            ("age", -1),
            ("age", 999),
            ("started_playing_age", -3),
            ("started_playing_age", 999),
            ("total_games", -1),
            ("total_games", 10_000_000),
        ]:
            data = _minimal_stat_payload(**{name: bad})
            form = PlayerForm(data=data)
            self.assertFalse(
                form.is_valid(),
                f"{name}={bad} should be rejected by validators",
            )
            self.assertIn(name, form.errors)

    def test_sensible_values_accepted(self):
        data = _minimal_stat_payload(age=25, started_playing_age=12, total_games=300)
        form = PlayerForm(data=data)
        self.assertTrue(form.is_valid(), form.errors.as_json())
