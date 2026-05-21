"""Tests for teams/templatetags/team_extras.py — display-layer filters."""

from types import SimpleNamespace

from teams.templatetags.team_extras import is_eliminated


class TestIsEliminatedFilter:
    """The `is_eliminated` filter drives the table-danger CSS class on the
    round detail page. Regression: pre-fix the threshold was hardcoded at
    900 (the pre-TIME-01 survived sentinel), so anyone eliminated between
    ticks 901 and 1800 (second half of the round) was wrongly shown as a
    survivor on /matches/game-round/<id>/.
    """

    def test_survived_full_round_sentinel_is_not_eliminated(self):
        """`was_eliminated_at = 1801` (SURVIVED_SENTINEL) → not red."""
        perf = SimpleNamespace(was_eliminated_at=1801, final_lives=8)
        assert is_eliminated(perf) is False

    def test_eliminated_in_first_half_is_red(self):
        """Pre-fix behaviour: this case already worked."""
        perf = SimpleNamespace(was_eliminated_at=478, final_lives=0)
        assert is_eliminated(perf) is True

    def test_eliminated_in_second_half_is_red(self):
        """Regression: this was the broken case (val > 900 → wrongly False).
        Round 80 had a Vipers Heavy eliminated at tick 1277 with 0 lives
        and the row was not highlighted.
        """
        perf = SimpleNamespace(was_eliminated_at=1277, final_lives=0)
        assert is_eliminated(perf) is True

    def test_eliminated_exactly_at_old_threshold_is_red(self):
        """Pre-fix boundary: `val == 920` was wrongly treated as survived
        because 920 > 900. Round 80 had a Vipers Scout-A at tick 920.
        """
        perf = SimpleNamespace(was_eliminated_at=920, final_lives=0)
        assert is_eliminated(perf) is True

    def test_eliminated_just_below_sentinel_is_red(self):
        """`was_eliminated_at = 1800` is the last legal elimination tick of
        a 1800-tick round — must still be treated as eliminated.
        """
        perf = SimpleNamespace(was_eliminated_at=1800, final_lives=0)
        assert is_eliminated(perf) is True

    def test_zero_eliminated_at_is_not_red(self):
        """`was_eliminated_at = 0` is a legacy "never set" sentinel — not
        red (mirrors the pre-fix behaviour to avoid false-positives on old
        rounds that predate the timestamp field).
        """
        perf = SimpleNamespace(was_eliminated_at=0, final_lives=10)
        assert is_eliminated(perf) is False

    def test_none_eliminated_at_falls_back_to_final_lives(self):
        """When the field is missing entirely, fall back to final_lives."""
        perf_dead = SimpleNamespace(was_eliminated_at=None, final_lives=0)
        perf_alive = SimpleNamespace(was_eliminated_at=None, final_lives=5)
        assert is_eliminated(perf_dead) is True
        assert is_eliminated(perf_alive) is False
