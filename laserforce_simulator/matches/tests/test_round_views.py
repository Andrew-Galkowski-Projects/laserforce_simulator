"""Tests for the single-round review surfaces: auto-flagged highlights (RV-02),
the round-report PDF (RV-03), side-by-side round comparison (RV-01), and the
round summary view.
"""

from __future__ import annotations

import unittest

from matches.sim_helpers.highlights import build_highlights

# Stable maps reused across tests.
NAMES = {1: "RedHvy", 2: "BluSct", 3: "RedCmd", 4: "BluMed", 5: "BluCmd"}
TEAMS = {1: "red", 2: "blue", 3: "red", 4: "blue", 5: "blue"}

RECORD_KEYS = {"kind", "tick", "team", "actor", "target", "points", "label"}

NO_WIPE = {"red_eliminated": False, "blue_eliminated": False, "eliminated_at": 1801}


def _ev(
    event_type, *, actor_id=1, target_id=None, timestamp=0, points=0, metadata=None
):
    return {
        "event_type": event_type,
        "actor_id": actor_id,
        "target_id": target_id,
        "timestamp": timestamp,
        "points_awarded": points,
        "description": "",
        "metadata": metadata or {},
    }


def _build(events, result=None):
    return build_highlights(
        events,
        result if result is not None else dict(NO_WIPE),
        round_ticks=1800,
        name_by_id=NAMES,
        team_by_id=TEAMS,
    )


class TestRecordShape(unittest.TestCase):
    def test_every_record_has_exactly_seven_keys(self):
        events = [
            _ev("nuke_cancelled", actor_id=3, timestamp=10),
            _ev("medic_reset", actor_id=4, timestamp=20),
            _ev("base_capture", actor_id=1, timestamp=30, points=1001),
        ]
        hl = _build(events)
        self.assertTrue(hl)
        for rec in hl:
            self.assertEqual(set(rec.keys()), RECORD_KEYS)

    def test_empty_events_no_wipe_returns_empty_list(self):
        self.assertEqual(_build([]), [])


class TestNukeDetonation(unittest.TestCase):
    def test_detonation_flagged(self):
        ev = _ev(
            "special",
            actor_id=1,
            timestamp=100,
            points=500,
            metadata={"targets": [{"pid": 2}]},
        )
        # (the detonation is also the only point event → a scoring_burst is
        # produced too; isolate the detonation record.)
        (rec,) = [h for h in _build([ev]) if h["kind"] == "nuke_detonation"]
        self.assertEqual(rec["kind"], "nuke_detonation")
        self.assertEqual(rec["tick"], 100)
        self.assertEqual(rec["team"], "red")
        self.assertEqual(rec["actor"], "RedHvy")
        self.assertEqual(rec["points"], 500)

    def test_activation_not_flagged(self):
        ev = _ev(
            "special",
            actor_id=1,
            timestamp=50,
            points=0,
            metadata={"fires_at": 60},
        )
        self.assertEqual(_build([ev]), [])

    def test_non_detonation_special_not_flagged(self):
        # rapid fire / other specials carry no "targets" and award 0 pts.
        ev = _ev("special", actor_id=1, timestamp=50, points=0, metadata={})
        self.assertEqual(_build([ev]), [])


class TestNukeCancelledAndMedicReset(unittest.TestCase):
    def test_nuke_cancelled(self):
        (rec,) = _build([_ev("nuke_cancelled", actor_id=3, timestamp=70)])
        self.assertEqual(rec["kind"], "nuke_cancelled")
        self.assertEqual(rec["actor"], "RedCmd")
        self.assertEqual(rec["team"], "red")
        self.assertEqual(rec["points"], 0)

    def test_medic_reset(self):
        (rec,) = _build([_ev("medic_reset", actor_id=4, timestamp=80)])
        self.assertEqual(rec["kind"], "medic_reset")
        self.assertEqual(rec["actor"], "BluMed")
        self.assertEqual(rec["team"], "blue")
        self.assertIsNone(rec["points"])


class TestBaseCaptureNotAHighlight(unittest.TestCase):
    def test_base_capture_never_flagged(self):
        # Base captures are routine point-grabs — they are NOT highlights
        # (they remain visible in the event log). The capture's points still
        # feed the scoring_burst, but no base_capture record is ever emitted.
        ev = _ev("base_capture", actor_id=1, timestamp=200, points=1001, metadata={})
        kinds = [h["kind"] for h in _build([ev])]
        self.assertNotIn("base_capture", kinds)


class TestFirstElimination(unittest.TestCase):
    def test_only_earliest_elimination_flagged(self):
        events = [
            _ev("elimination", actor_id=1, target_id=2, timestamp=500),
            _ev("elimination", actor_id=3, target_id=4, timestamp=120),
            _ev("elimination", actor_id=1, target_id=5, timestamp=900),
        ]
        recs = [h for h in _build(events) if h["kind"] == "first_elimination"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["tick"], 120)
        self.assertEqual(recs[0]["actor"], "RedCmd")
        self.assertEqual(recs[0]["target"], "BluMed")

    def test_no_elimination_no_record(self):
        kinds = [h["kind"] for h in _build([_ev("tag", timestamp=1, points=100)])]
        self.assertNotIn("first_elimination", kinds)


class TestTeamElimination(unittest.TestCase):
    def test_red_eliminated(self):
        result = {
            "red_eliminated": True,
            "blue_eliminated": False,
            "eliminated_at": 640,
        }
        (rec,) = [h for h in _build([], result) if h["kind"] == "team_elimination"]
        self.assertEqual(rec["team"], "red")
        self.assertEqual(rec["tick"], 640)

    def test_blue_eliminated(self):
        result = {
            "red_eliminated": False,
            "blue_eliminated": True,
            "eliminated_at": 700,
        }
        (rec,) = [h for h in _build([], result) if h["kind"] == "team_elimination"]
        self.assertEqual(rec["team"], "blue")

    def test_neither_eliminated_no_record(self):
        kinds = [h["kind"] for h in _build([], dict(NO_WIPE))]
        self.assertNotIn("team_elimination", kinds)

    def test_both_eliminated_prefers_red_single_record(self):
        result = {"red_eliminated": True, "blue_eliminated": True, "eliminated_at": 800}
        recs = [h for h in _build([], result) if h["kind"] == "team_elimination"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["team"], "red")


class TestScoringBurst(unittest.TestCase):
    def test_single_team_max_window(self):
        # Red scores 100 at t10, 500 at t20 → window [10,70) sums 600.
        # Blue scores 100 at t10 only.
        events = [
            _ev("tag", actor_id=1, target_id=2, timestamp=10, points=100),
            _ev(
                "special",
                actor_id=1,
                timestamp=20,
                points=500,
                metadata={"targets": [{"pid": 2}]},
            ),
            _ev("tag", actor_id=2, target_id=1, timestamp=10, points=100),
        ]
        (rec,) = [h for h in _build(events) if h["kind"] == "scoring_burst"]
        self.assertEqual(rec["team"], "red")
        self.assertEqual(rec["tick"], 10)
        self.assertEqual(rec["points"], 600)

    def test_no_point_events_no_burst(self):
        events = [
            _ev("miss", actor_id=1, target_id=2, timestamp=5, points=0),
            _ev("medic_reset", actor_id=4, timestamp=6),
        ]
        kinds = [h["kind"] for h in _build(events)]
        self.assertNotIn("scoring_burst", kinds)

    def test_window_is_forward_and_exclusive_of_end(self):
        # Two red tags 60 ticks apart: tick 0 window [0,60) excludes the t60 tag,
        # so each window sees exactly one 100-pt tag → burst points == 100.
        events = [
            _ev("tag", actor_id=1, target_id=2, timestamp=0, points=100),
            _ev("tag", actor_id=1, target_id=2, timestamp=60, points=100),
        ]
        (rec,) = [h for h in _build(events) if h["kind"] == "scoring_burst"]
        self.assertEqual(rec["points"], 100)

    def test_window_includes_within_60(self):
        # Two red tags 59 ticks apart fall in one [0,60) window → 200.
        events = [
            _ev("tag", actor_id=1, target_id=2, timestamp=0, points=100),
            _ev("tag", actor_id=1, target_id=2, timestamp=59, points=100),
        ]
        (rec,) = [h for h in _build(events) if h["kind"] == "scoring_burst"]
        self.assertEqual(rec["points"], 200)

    def test_actor_without_team_ignored(self):
        # actor_id 99 has no team mapping → its points never form a burst.
        events = [_ev("tag", actor_id=99, target_id=2, timestamp=10, points=100)]
        kinds = [h["kind"] for h in _build(events)]
        self.assertNotIn("scoring_burst", kinds)


class TestSortingAndResolution(unittest.TestCase):
    def test_output_sorted_by_tick(self):
        events = [
            _ev("nuke_cancelled", actor_id=3, timestamp=300),
            _ev("medic_reset", actor_id=4, timestamp=100),
            _ev("base_capture", actor_id=1, timestamp=200, points=1001),
        ]
        ticks = [h["tick"] for h in _build(events)]
        self.assertEqual(ticks, sorted(ticks))

    def test_unknown_id_resolves_to_none(self):
        # actor 42 is in neither map → name/team None.
        ev = _ev("nuke_cancelled", actor_id=42, timestamp=10)
        (rec,) = _build([ev])
        self.assertIsNone(rec["actor"])
        self.assertIsNone(rec["team"])


class TestPurity(unittest.TestCase):
    def test_pure_no_django_imports(self):
        import matches.sim_helpers.highlights as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))


if __name__ == "__main__":
    unittest.main()


# ===== Round-report PDF export =====
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
        ``test_heatmap.py``.
        """
        import matches.sim_helpers.pdf_report as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))


if __name__ == "__main__":
    unittest.main()


# ===== Side-by-side round comparison =====
import unittest

from matches.round_comparison import (
    COMPARE_FIELD_STAT_KEYS,
    COMPARE_STAT_KEYS,
    cumulative_team_points,
    player_stat_deltas,
    stat_values,
)


def _row(
    *,
    player_id: int = 1,
    name: str = "Alice",
    role: str = "scout",
    team_color: str = "red",
    points_scored: int = 0,
    mvp: float = 0.0,
    tags_made: int = 0,
    times_tagged: int = 0,
    accuracy: int = 0,
    final_lives: int = 0,
    resupplies_given: int = 0,
    missiles_landed: int = 0,
    specials_used: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    combo_resupply_count: int = 0,
) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "role": role,
        "team_color": team_color,
        "points_scored": points_scored,
        "mvp": mvp,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "final_lives": final_lives,
        "resupplies_given": resupplies_given,
        "missiles_landed": missiles_landed,
        "specials_used": specials_used,
        "follow_up_shots": follow_up_shots,
        "reaction_shots": reaction_shots,
        "combo_resupply_count": combo_resupply_count,
    }


class TestStatValues(unittest.TestCase):
    def test_returns_12_keys_in_order(self) -> None:
        out = stat_values(_row())
        self.assertEqual(list(out.keys()), list(COMPARE_STAT_KEYS))

    def test_mvp_and_accuracy_carry_through(self) -> None:
        out = stat_values(_row(mvp=42.5, accuracy=80))
        self.assertEqual(out["mvp"], 42.5)
        self.assertEqual(out["accuracy"], 80)

    def test_field_keys_subset_of_stat_keys(self) -> None:
        # COMPARE_FIELD_STAT_KEYS is the IntegerField subset (no mvp/accuracy)
        self.assertTrue(set(COMPARE_FIELD_STAT_KEYS).issubset(set(COMPARE_STAT_KEYS)))
        self.assertNotIn("mvp", COMPARE_FIELD_STAT_KEYS)
        self.assertNotIn("accuracy", COMPARE_FIELD_STAT_KEYS)


class TestPlayerStatDeltas(unittest.TestCase):
    def test_empty_inputs_yield_empty(self) -> None:
        self.assertEqual(player_stat_deltas([], []), [])

    def test_both_sides_present_delta_math(self) -> None:
        a = [_row(player_id=1, name="Alice", points_scored=100, tags_made=10)]
        b = [_row(player_id=1, name="Alice", points_scored=150, tags_made=12)]
        rows = player_stat_deltas(a, b)
        self.assertEqual(len(rows), 1)
        cell_pts = rows[0]["stats"]["points_scored"]
        self.assertEqual(cell_pts, {"a": 100, "b": 150, "delta": 50})
        cell_tags = rows[0]["stats"]["tags_made"]
        self.assertEqual(cell_tags, {"a": 10, "b": 12, "delta": 2})

    def test_player_only_in_a_yields_none_b(self) -> None:
        a = [_row(player_id=1, name="Alice", role="scout", team_color="red")]
        rows = player_stat_deltas(a, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["role_a"], "scout")
        self.assertIsNone(row["role_b"])
        self.assertEqual(row["side_a"], "red")
        self.assertIsNone(row["side_b"])
        self.assertIsNone(row["stats"]["points_scored"]["delta"])
        self.assertIsNone(row["stats"]["points_scored"]["b"])

    def test_player_only_in_b_yields_none_a(self) -> None:
        b = [_row(player_id=2, name="Bob", role="medic", team_color="blue")]
        rows = player_stat_deltas([], b)
        row = rows[0]
        self.assertIsNone(row["role_a"])
        self.assertEqual(row["role_b"], "medic")
        self.assertIsNone(row["stats"]["tags_made"]["a"])
        self.assertIsNone(row["stats"]["tags_made"]["delta"])

    def test_sorted_by_name_asc(self) -> None:
        a = [
            _row(player_id=1, name="Charlie"),
            _row(player_id=2, name="Alice"),
            _row(player_id=3, name="Bob"),
        ]
        b = [
            _row(player_id=1, name="Charlie"),
            _row(player_id=2, name="Alice"),
            _row(player_id=3, name="Bob"),
        ]
        rows = player_stat_deltas(a, b)
        self.assertEqual([r["name"] for r in rows], ["Alice", "Bob", "Charlie"])

    def test_cells_array_is_template_friendly_ordered_view(self) -> None:
        a = [_row(player_id=1, points_scored=10, mvp=1.0)]
        b = [_row(player_id=1, points_scored=20, mvp=2.0)]
        rows = player_stat_deltas(a, b)
        cells = rows[0]["cells"]
        self.assertEqual(len(cells), len(COMPARE_STAT_KEYS))
        # First entry corresponds to points_scored (first in COMPARE_STAT_KEYS).
        self.assertEqual(cells[0], rows[0]["stats"]["points_scored"])


class TestCumulativeTeamPoints(unittest.TestCase):
    def test_empty_yields_empty(self) -> None:
        self.assertEqual(cumulative_team_points([]), [])

    def test_null_points_coalesce_to_zero(self) -> None:
        events = [(10, None), (20, 5), (30, None)]
        series = cumulative_team_points(events)
        self.assertEqual(series, [[10, 0], [20, 5], [30, 5]])

    def test_running_sum(self) -> None:
        events = [(10, 100), (20, 50), (30, 25)]
        series = cumulative_team_points(events)
        self.assertEqual(series, [[10, 100], [20, 150], [30, 175]])

    def test_accepts_iterable(self) -> None:
        gen = ((tick, 10) for tick in (1, 2, 3))
        series = cumulative_team_points(gen)
        self.assertEqual(series, [[1, 10], [2, 20], [3, 30]])


class TestRoundComparisonNoDjangoImportsLeaked(unittest.TestCase):
    def test_clean_import_in_subprocess(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.round_comparison  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()


# ===== Round summary view =====
import unittest

from matches.round_summary import (
    PLAYER_ROW_KEYS,
    survivor_count,
    team_eliminated,
    team_totals,
)


def _player_row_dict(
    *,
    name: str = "Alice",
    role: str = "scout",
    team_color: str = "red",
    was_eliminated_at: int = 1801,
    eliminated_timestamp: str = "",
    is_eliminated: bool = False,
    final_lives: int = 5,
    points_scored: int = 0,
    mvp: float = 0.0,
    tags_made: int = 0,
    times_tagged: int = 0,
    accuracy: int = 0,
    final_shots: int = 0,
    final_special: int = 0,
    shots_used: int = 0,
    missiles_used: int = 0,
    starting_missiles: int = 0,
    missiles_landed: int = 0,
    times_missiled: int = 0,
    final_medic_hits: int = 0,
    medic_lives_removed_from_nuke: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    resupplies_given: int = 0,
    specials_used: int = 0,
    combo_resupply_count: int = 0,
    specific_tags_count: int = 0,
    special_cost: int = 10,
) -> dict:
    return {
        "name": name,
        "role": role,
        "team_color": team_color,
        "was_eliminated_at": was_eliminated_at,
        "eliminated_timestamp": eliminated_timestamp,
        "is_eliminated": is_eliminated,
        "final_lives": final_lives,
        "points_scored": points_scored,
        "mvp": mvp,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "final_shots": final_shots,
        "final_special": final_special,
        "shots_used": shots_used,
        "missiles_used": missiles_used,
        "starting_missiles": starting_missiles,
        "missiles_landed": missiles_landed,
        "times_missiled": times_missiled,
        "final_medic_hits": final_medic_hits,
        "medic_lives_removed_from_nuke": medic_lives_removed_from_nuke,
        "follow_up_shots": follow_up_shots,
        "reaction_shots": reaction_shots,
        "resupplies_given": resupplies_given,
        "specials_used": specials_used,
        "combo_resupply_count": combo_resupply_count,
        "specific_tags_count": specific_tags_count,
        "special_cost": special_cost,
    }


class TestPlayerRowKeys(unittest.TestCase):
    def test_28_keys_in_pinned_order(self) -> None:
        self.assertEqual(len(PLAYER_ROW_KEYS), 28)
        self.assertEqual(PLAYER_ROW_KEYS[0], "name")
        self.assertEqual(PLAYER_ROW_KEYS[-1], "special_cost")

    def test_fixture_dict_covers_every_key(self) -> None:
        row = _player_row_dict()
        for key in PLAYER_ROW_KEYS:
            self.assertIn(key, row, msg=f"fixture missing pinned key {key!r}")

    def test_no_extra_keys_in_fixture(self) -> None:
        row = _player_row_dict()
        extras = set(row) - set(PLAYER_ROW_KEYS)
        self.assertEqual(extras, set(), msg=f"fixture has extras: {extras}")


class TestTeamTotals(unittest.TestCase):
    def test_empty_zeros(self) -> None:
        out = team_totals([], team_points=0)
        self.assertEqual(
            out,
            {
                "resupplies_given": 0,
                "missiles_landed": 0,
                "specials_used": 0,
                "tags_made": 0,
                "survivors": 0,
                "team_points": 0,
            },
        )

    def test_sums_across_rows(self) -> None:
        rows = [
            _player_row_dict(
                resupplies_given=3, missiles_landed=1, specials_used=2, tags_made=10
            ),
            _player_row_dict(
                resupplies_given=2, missiles_landed=4, specials_used=1, tags_made=5
            ),
        ]
        out = team_totals(rows, team_points=12_345)
        self.assertEqual(out["resupplies_given"], 5)
        self.assertEqual(out["missiles_landed"], 5)
        self.assertEqual(out["specials_used"], 3)
        self.assertEqual(out["tags_made"], 15)
        self.assertEqual(out["team_points"], 12_345)

    def test_survivors_from_final_lives(self) -> None:
        rows = [
            _player_row_dict(final_lives=3),
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        out = team_totals(rows, team_points=0)
        self.assertEqual(out["survivors"], 2)

    def test_accepts_iterable_not_just_list(self) -> None:
        gen = (_player_row_dict(tags_made=n) for n in (1, 2, 3))
        out = team_totals(gen, team_points=0)
        self.assertEqual(out["tags_made"], 6)


class TestSurvivorCount(unittest.TestCase):
    def test_empty_zero(self) -> None:
        self.assertEqual(survivor_count([]), 0)

    def test_all_survive(self) -> None:
        rows = [_player_row_dict(final_lives=n) for n in (1, 2, 3)]
        self.assertEqual(survivor_count(rows), 3)

    def test_all_eliminated(self) -> None:
        rows = [_player_row_dict(final_lives=0) for _ in range(4)]
        self.assertEqual(survivor_count(rows), 0)

    def test_mixed(self) -> None:
        rows = [
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=2),
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        self.assertEqual(survivor_count(rows), 2)


class TestTeamEliminated(unittest.TestCase):
    def test_empty_team_counts_as_eliminated(self) -> None:
        self.assertTrue(team_eliminated([]))

    def test_one_survivor_not_eliminated(self) -> None:
        rows = [
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        self.assertFalse(team_eliminated(rows))

    def test_all_zero_eliminated(self) -> None:
        rows = [_player_row_dict(final_lives=0) for _ in range(6)]
        self.assertTrue(team_eliminated(rows))


class TestRoundSummaryNoDjangoImportsLeaked(unittest.TestCase):
    """Importing ``matches.round_summary`` in a fresh subprocess must not
    pull in ``django.*`` or ``matches.models``.
    """

    def test_clean_import_in_subprocess(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.round_summary  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
