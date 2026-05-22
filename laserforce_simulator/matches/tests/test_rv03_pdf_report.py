"""RV-03 — Pure unit tests for the Round-report PDF builder.

These tests describe the contract pinned by the RV-03 seam contract
(``.claude/worktrees/rv-03-seam-contract.md``, §2 / §6 / §10a). The module
under test, ``matches/sim_helpers/pdf_report.py``, is a pure-Python helper:
imports here are limited to ``stdlib`` plus the helper itself so the suite
stays DB-free and Django-free.

Until the Code agent lands ``matches/sim_helpers/pdf_report.py`` every test in
this module will fail at import time with an ``ImportError`` — that is expected
and the seam-contract precedent for spec-first tests (mirrors
``test_res04_cell_occupancy.py``).

``report_data`` is built here as hand-written dict literals matching the
contract's §2a / §2b / §2c schema EXACTLY (top-level keys, ``player_row`` keys,
``team_totals`` keys). No ORM, no Django ``TestCase``.
"""

from __future__ import annotations

import unittest

from matches.sim_helpers.pdf_report import build_round_report, should_watermark


def _player_row(name: str, role: str) -> dict:
    """A fully-populated ``player_row`` matching the §2b frozen key set.

    Fixed key order per the contract. Values are arbitrary but deterministic.
    """
    return {
        "name": name,
        "role": role,
        "points_scored": 1234,
        "mvp": 56.7,
        "tags_made": 12,
        "times_tagged": 8,
        "accuracy": 73,
        "final_lives": 2,
        "resupplies_given": 4,
        "missiles_landed": 3,
        "specials_used": 1,
        "follow_up_shots": 5,
        "reaction_shots": 2,
        "combo_resupply_count": 1,
    }


def _team_totals(team_points: int) -> dict:
    """A §2c ``team_totals`` dict with the frozen key set."""
    return {
        "resupplies_given": 8,
        "missiles_landed": 6,
        "specials_used": 2,
        "tags_made": 24,
        "survivors": 3,
        "team_points": team_points,
    }


def _full_report_data() -> dict:
    """A populated ``report_data`` dict matching the §2a frozen top-level keys."""
    return {
        "round_id": 42,
        "round_label": "Round 1 of 2",
        "date_played": "2026-05-22 14:30",
        "map_name": "Test Arena",
        "red_team_name": "Red Squad",
        "blue_team_name": "Blue Squad",
        "red_points": 9000,
        "blue_points": 8500,
        "red_eliminated": False,
        "blue_eliminated": True,
        "winner_name": "Red Squad",
        "red_players": [
            _player_row("Alice", "commander"),
            _player_row("Bob", "heavy"),
        ],
        "blue_players": [
            _player_row("Carol", "scout"),
            _player_row("Dave", "medic"),
        ],
        "red_totals": _team_totals(9000),
        "blue_totals": _team_totals(8500),
    }


def _empty_report_data() -> dict:
    """Zeroed / early-eliminated edge case (§8, §10a).

    Empty player lists, ``map_name=None``, ``winner_name=None`` (tie), all-zero
    points. Must render without crashing and still start with ``b"%PDF"``.
    """
    zero_totals = {
        "resupplies_given": 0,
        "missiles_landed": 0,
        "specials_used": 0,
        "tags_made": 0,
        "survivors": 0,
        "team_points": 0,
    }
    return {
        "round_id": 1,
        "round_label": "Single Round",
        "date_played": "2026-05-22 09:00",
        "map_name": None,
        "red_team_name": "Red Squad",
        "blue_team_name": "Blue Squad",
        "red_points": 0,
        "blue_points": 0,
        "red_eliminated": False,
        "blue_eliminated": False,
        "winner_name": None,
        "red_players": [],
        "blue_players": [],
        "red_totals": dict(zero_totals),
        "blue_totals": dict(zero_totals),
    }


class TestBuildRoundReport(unittest.TestCase):
    """§10a — pure-unit coverage of ``build_round_report`` / ``should_watermark``."""

    def test_returns_pdf_bytes_with_watermark(self) -> None:
        """watermark=True -> non-empty bytes starting with the PDF magic."""
        pdf = build_round_report(_full_report_data(), watermark=True)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf, "PDF bytes must be non-empty")
        self.assertTrue(
            pdf.startswith(b"%PDF"),
            f"output must start with the PDF magic; got {pdf[:8]!r}",
        )

    def test_returns_pdf_bytes_without_watermark(self) -> None:
        """watermark=False -> still non-empty bytes starting with the PDF magic."""
        pdf = build_round_report(_full_report_data(), watermark=False)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf, "PDF bytes must be non-empty")
        self.assertTrue(
            pdf.startswith(b"%PDF"),
            f"output must start with the PDF magic; got {pdf[:8]!r}",
        )

    def test_should_watermark_true(self) -> None:
        """§6: the watermark decision seam returns True for a simulated round."""
        self.assertIs(should_watermark(True), True)

    def test_should_watermark_false(self) -> None:
        """§6: the watermark decision seam returns False for a non-simulated round."""
        self.assertIs(should_watermark(False), False)

    def test_empty_report_renders_without_crashing(self) -> None:
        """§8 / §10a: zeroed/empty report_data (empty player lists,
        map_name=None, winner_name=None, zero points) renders without crashing
        and still starts with the PDF magic."""
        pdf = build_round_report(_empty_report_data(), watermark=True)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf, "PDF bytes must be non-empty even for an empty round")
        self.assertTrue(
            pdf.startswith(b"%PDF"),
            f"empty-round output must start with the PDF magic; got {pdf[:8]!r}",
        )

    def test_empty_report_renders_without_watermark(self) -> None:
        """The zeroed edge case must also render with watermark=False."""
        pdf = build_round_report(_empty_report_data(), watermark=False)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pure_no_django_imports(self) -> None:
        """§10a defensive check: the builder module must not leak ``django``
        or ``models`` names — it imports cleanly without Django setup.

        Mirrors the RES-04 "no Django imports leaked" guard in
        ``test_res04_cell_occupancy.py``.
        """
        import matches.sim_helpers.pdf_report as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))


if __name__ == "__main__":
    unittest.main()
