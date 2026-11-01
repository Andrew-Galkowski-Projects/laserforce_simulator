"""CONF-05 / CONF-02 -- the pure Conference partition validator
(``league_services._validate_conference_partition``).

ANG-08: the HTML Manage Conferences page and its view tests went; the API
composer (``PUT /api/seasons/<id>/conferences/``) is covered by
``test_api_conferences.py`` and ``ConferencePartitionForm`` by
``test_league_services.py``. Schema-level assertions only.
"""

from __future__ import annotations

from django.test import TestCase

from matches.league_services import _validate_conference_partition


class TestValidateConferencePartition(TestCase):
    def test_empty_submission_is_zero_conference(self):
        errors, normalized = _validate_conference_partition([], {}, {1, 2, 3, 4})
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [])

    def test_blank_only_names_is_zero_conference(self):
        # No conference names at all (the composer submits none) => flat Season.
        errors, normalized = _validate_conference_partition(
            [], {1: None, 2: None}, {1, 2}
        )
        self.assertEqual((errors, normalized), ([], []))

    def test_valid_full_partition(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("West", [1, 2]), ("East", [3, 4])])

    def test_single_conference_with_all_teams_is_valid(self):
        errors, normalized = _validate_conference_partition(
            ["Only"], {1: 0, 2: 0, 3: 0}, {1, 2, 3}
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("Only", [1, 2, 3])])

    def test_unassigned_team_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: None}, {1, 2, 3, 4}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)

    def test_conference_under_two_teams_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: 0}, {1, 2, 3, 4}
        )
        self.assertIn("Each conference needs at least 2 teams.", errors)
        self.assertIsNone(normalized)

    def test_empty_name_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "  "], {1: 0, 2: 0, 3: 1, 4: 1}, {1, 2, 3, 4}
        )
        self.assertIn("Conference names cannot be empty.", errors)
        self.assertIsNone(normalized)

    def test_out_of_range_index_counts_as_unassigned(self):
        errors, normalized = _validate_conference_partition(
            ["West"], {1: 0, 2: 0, 3: 5}, {1, 2, 3}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)


class TestConf02PartitionBracketFloor(TestCase):
    """CONF-02 review fix - ``min_per_conference`` raises the floor to 4 when
    the Season composes a tournament phase (each Conference must field its own
    Regional playoff bracket)."""

    def test_two_per_conference_rejected_when_floor_is_four(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
            min_per_conference=4,
        )
        self.assertIsNone(normalized)
        self.assertTrue(any("at least 4 teams" in e for e in errors))

    def test_two_per_conference_allowed_at_the_default_floor(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(normalized)

    def test_four_per_conference_allowed_when_floor_is_four(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1},
            set(range(1, 9)),
            min_per_conference=4,
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(normalized)
