"""RV-02 — Pure unit tests for ``build_highlights``.

Describes the contract pinned by the RV-02 seam contract: the highlight record
shape (seven keys), the seven ``kind`` derivations, the scoring-burst window,
and id->name / id->team resolution. The module under test is pure Python — this
suite stays DB-free and Django-free.
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
