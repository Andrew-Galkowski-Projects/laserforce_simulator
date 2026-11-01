"""LG-06e -- pure-unit tests for ``matches/stat_feats.py`` (per-game feed).

One sortable row per (Player, Round) that achieved a feat, carrying that
Round's box-score line + Opp / Result / Season; comeback wins are a SEPARATE
Team-feats section. Hand-built dict fixtures, no DB, asserting against the
``scan_feats`` ``(feat_rows, team_feats)`` return + the ``FeatRow`` /
``FeatBadge`` / ``TeamFeatRecord`` dataclasses, plus the
``TestNoDjangoImportsLeaked`` subprocess purity check.

The HTML Statistical Feats screen's view tests went with the screen (ANG-08);
the API serves the same feed (``GET /api/leagues/<id>/statistical-feats/``).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches import stat_feats
from laserforce_simulator.testing import run_project_python

# ===========================================================================
# Pure-unit fixture builders for matches/stat_feats.py
# ===========================================================================

# The 13 box-score keys carried on every FeatRow.
_BOX_SCORE_DEFAULTS = {
    "points_scored": 0,
    "mvp": 0.0,
    "tags_made": 0,
    "times_tagged": 0,
    "accuracy": 0.0,
    "final_lives": 0,
    "resupplies_given": 0,
    "missiles_landed": 0,
    "specials_used": 0,
    "follow_up_shots": 0,
    "reaction_shots": 0,
    "combo_resupply_count": 0,
    "nuke_detonations": 0,
}


def _pr(**overrides) -> dict:
    """Build a per-(player,round) seam dict with sane all-zero defaults.

    Identity + descriptor + box-score keys. Defaults keep a row BELOW every
    threshold so a test opts a single stat over its bar explicitly.
    """
    base = {
        # identity / deep-link
        "round_id": 1,
        "match_id": 1,
        "player_id": 1,
        "player_name": "Alice",
        "role": "scout",
        "team_id": 10,
        "team_name": "Red Team",
        # descriptors (caller-computed)
        "opp_team_name": "Blue Team",
        "result": "W",
        "season_id": 100,
        "season_name": "S1",
        # box-score line
        "shots_missed": 0,
    }
    base.update(_BOX_SCORE_DEFAULTS)
    base.update(overrides)
    return base


def _match(**overrides) -> dict:
    base = {
        "match_id": 1,
        "round_id": 2,
        "winner_team_id": 10,
        "winner_team_name": "Red Team",
        "red_team_id": 10,
        "blue_team_id": 20,
        "red_round1_points": 5,
        "blue_round1_points": 10,
    }
    base.update(overrides)
    return base


def _kinds_of(row) -> set[str]:
    """The SET of badge kinds on a FeatRow (badge order is internal)."""
    return {b.kind for b in row.feats}


def _badge(row, kind: str):
    """The single FeatBadge of ``kind`` on ``row`` (after per-kind collapse)."""
    matches_ = [b for b in row.feats if b.kind == kind]
    assert len(matches_) == 1, f"expected exactly one {kind!r} badge, got {matches_}"
    return matches_[0]


# ===========================================================================
# Threshold qualification + edges
# ===========================================================================


class TestThresholdQualification(SimpleTestCase):
    def test_triple_nuke_three_qualifies_two_does_not(self) -> None:
        rows, _ = stat_feats.scan_feats([_pr(nuke_detonations=3)], [])
        self.assertEqual(len(rows), 1)
        self.assertIn("triple_nuke", _kinds_of(rows[0]))
        self.assertFalse(_badge(rows[0], "triple_nuke").is_season_best)

        rows, _ = stat_feats.scan_feats([_pr(nuke_detonations=2)], [])
        self.assertEqual(rows, [])

    def _row_for(self, rows, pid: int):
        matches_ = [r for r in rows if r.player_id == pid]
        self.assertEqual(len(matches_), 1, f"expected one row for pid {pid}")
        return matches_[0]

    def test_high_tags_edge_twenty_in_nineteen_out(self) -> None:
        # A higher decoy (pid 2) holds the tags season-best so the boundary row
        # (pid 1, 20 tags) carries a THRESHOLD badge (is_season_best False).
        decoy = _pr(player_id=2, round_id=9, tags_made=30)
        edge = _pr(player_id=1, round_id=1, tags_made=20)
        rows, _ = stat_feats.scan_feats([edge, decoy], [])
        edge_row = self._row_for(rows, 1)
        self.assertIn("high_tags", _kinds_of(edge_row))
        self.assertFalse(_badge(edge_row, "high_tags").is_season_best)

        # 19 tags, below threshold, NOT the leader (decoy higher) -> no row.
        below = _pr(player_id=1, round_id=1, tags_made=19)
        rows, _ = stat_feats.scan_feats([below, decoy], [])
        self.assertNotIn(1, {r.player_id for r in rows})

    def test_high_points_edge(self) -> None:
        decoy = _pr(player_id=2, round_id=9, points_scored=20000)
        edge = _pr(player_id=1, round_id=1, points_scored=12000)
        rows, _ = stat_feats.scan_feats([edge, decoy], [])
        edge_row = self._row_for(rows, 1)
        self.assertIn("high_points", _kinds_of(edge_row))
        self.assertFalse(_badge(edge_row, "high_points").is_season_best)

        below = _pr(player_id=1, round_id=1, points_scored=11999)
        rows, _ = stat_feats.scan_feats([below, decoy], [])
        self.assertNotIn(1, {r.player_id for r in rows})

    def test_high_mvp_edge(self) -> None:
        decoy = _pr(player_id=2, round_id=9, mvp=50.0)
        edge = _pr(player_id=1, round_id=1, mvp=15.0)
        rows, _ = stat_feats.scan_feats([edge, decoy], [])
        edge_row = self._row_for(rows, 1)
        self.assertIn("high_mvp", _kinds_of(edge_row))
        self.assertFalse(_badge(edge_row, "high_mvp").is_season_best)

        below = _pr(player_id=1, round_id=1, mvp=14.0)
        rows, _ = stat_feats.scan_feats([below, decoy], [])
        self.assertNotIn(1, {r.player_id for r in rows})

    def test_high_resupplies_edge(self) -> None:
        decoy = _pr(player_id=2, round_id=9, resupplies_given=40)
        edge = _pr(player_id=1, round_id=1, resupplies_given=20)
        rows, _ = stat_feats.scan_feats([edge, decoy], [])
        edge_row = self._row_for(rows, 1)
        self.assertIn("high_resupplies", _kinds_of(edge_row))
        self.assertFalse(_badge(edge_row, "high_resupplies").is_season_best)

        below = _pr(player_id=1, round_id=1, resupplies_given=19)
        rows, _ = stat_feats.scan_feats([below, decoy], [])
        self.assertNotIn(1, {r.player_id for r in rows})

    def test_high_missiles_edge(self) -> None:
        decoy = _pr(player_id=2, round_id=9, missiles_landed=20)
        edge = _pr(player_id=1, round_id=1, missiles_landed=8)
        rows, _ = stat_feats.scan_feats([edge, decoy], [])
        edge_row = self._row_for(rows, 1)
        self.assertIn("high_missiles", _kinds_of(edge_row))
        self.assertFalse(_badge(edge_row, "high_missiles").is_season_best)

        below = _pr(player_id=1, round_id=1, missiles_landed=7)
        rows, _ = stat_feats.scan_feats([below, decoy], [])
        self.assertNotIn(1, {r.player_id for r in rows})


class TestBooleanFeats(SimpleTestCase):
    def test_medic_shutout_qualifies(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="medic", times_tagged=0, tags_made=2)], []
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("medic_shutout", _kinds_of(rows[0]))
        self.assertFalse(_badge(rows[0], "medic_shutout").is_season_best)

    def test_tagged_medic_not_shutout(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="medic", times_tagged=1, tags_made=2)], []
        )
        # times_tagged=1 -> no shutout; tags_made=2 is still the lone leader so a
        # season-best high_tags badge keeps it on the feed, but NOT medic_shutout.
        for r in rows:
            self.assertNotIn("medic_shutout", _kinds_of(r))

    def test_non_medic_not_shutout(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="heavy", times_tagged=0, tags_made=2)], []
        )
        for r in rows:
            self.assertNotIn("medic_shutout", _kinds_of(r))

    def test_perfect_heavy_qualifies(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="heavy", shots_missed=0, tags_made=4)], []
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("perfect_heavy", _kinds_of(rows[0]))
        self.assertFalse(_badge(rows[0], "perfect_heavy").is_season_best)

    def test_perfect_heavy_with_misses_excluded(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="heavy", shots_missed=1, tags_made=4)], []
        )
        for r in rows:
            self.assertNotIn("perfect_heavy", _kinds_of(r))

    def test_perfect_heavy_zero_tags_excluded(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="heavy", shots_missed=0, tags_made=0)], []
        )
        # 0 tags -> not perfect_heavy; also not season-best for anything (all
        # zero pool max) -> no row emitted.
        for r in rows:
            self.assertNotIn("perfect_heavy", _kinds_of(r))

    def test_non_heavy_not_perfect_heavy(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(role="scout", shots_missed=0, tags_made=4)], []
        )
        for r in rows:
            self.assertNotIn("perfect_heavy", _kinds_of(r))


class TestBelowAllThresholdsNotEmitted(SimpleTestCase):
    def test_row_below_all_and_not_leader_excluded(self) -> None:
        # The clear leader for every season-best stat.
        leader = _pr(
            player_id=1,
            round_id=1,
            tags_made=10,
            points_scored=5000,
            mvp=10.0,
            resupplies_given=5,
            missiles_landed=3,
        )
        # A nobody -- below every threshold AND below the leader on every stat.
        nobody = _pr(
            player_id=2,
            round_id=2,
            tags_made=1,
            points_scored=10,
            mvp=1.0,
            resupplies_given=0,
            missiles_landed=0,
        )
        rows, _ = stat_feats.scan_feats([leader, nobody], [])
        emitted_pids = {r.player_id for r in rows}
        self.assertIn(1, emitted_pids)
        self.assertNotIn(2, emitted_pids)


# ===========================================================================
# Season-best selection + tiebreak + all-zero skip
# ===========================================================================


class TestSeasonBest(SimpleTestCase):
    def test_below_threshold_leader_listed_as_season_best(self) -> None:
        # Only one row; tags_made below 20, but it's the season's most tags.
        rows, _ = stat_feats.scan_feats([_pr(tags_made=7)], [])
        self.assertEqual(len(rows), 1)
        self.assertTrue(_badge(rows[0], "high_tags").is_season_best)

    def test_highest_value_wins_season_best(self) -> None:
        a = _pr(player_id=1, round_id=1, tags_made=5)
        b = _pr(player_id=2, round_id=2, tags_made=9)
        rows, _ = stat_feats.scan_feats([a, b], [])
        # The tags season-best belongs to player 2 (9 tags).
        sb_holders = [
            r.player_id
            for r in rows
            for bd in r.feats
            if bd.kind == "high_tags" and bd.is_season_best
        ]
        self.assertEqual(sb_holders, [2])

    def test_tiebreak_value_then_round_id_desc_then_player_id_asc(self) -> None:
        # Three rows tied on tags=5; tiebreak: highest round_id, then lowest pid.
        r1 = _pr(player_id=3, round_id=5, tags_made=5)
        r2 = _pr(player_id=1, round_id=9, tags_made=5)  # highest round_id -> wins
        r3 = _pr(player_id=2, round_id=9, tags_made=5)  # same round_id, higher pid
        rows, _ = stat_feats.scan_feats([r1, r2, r3], [])
        sb_holders = [
            (r.player_id, r.round_id)
            for r in rows
            for bd in r.feats
            if bd.kind == "high_tags" and bd.is_season_best
        ]
        self.assertEqual(sb_holders, [(1, 9)])

    def test_all_zero_max_stat_no_season_best_badge(self) -> None:
        # Nobody landed a missile; the missile season-best badge must NOT show.
        a = _pr(player_id=1, round_id=1, tags_made=5, missiles_landed=0)
        b = _pr(player_id=2, round_id=2, tags_made=9, missiles_landed=0)
        rows, _ = stat_feats.scan_feats([a, b], [])
        missile_sb = [bd for r in rows for bd in r.feats if bd.kind == "high_missiles"]
        self.assertEqual(missile_sb, [])

    def test_each_season_best_stat_yields_a_row(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [
                _pr(player_id=1, round_id=1, mvp=3.0),
                _pr(player_id=2, round_id=2, points_scored=100),
                _pr(player_id=3, round_id=3, tags_made=4),
                _pr(player_id=4, round_id=4, resupplies_given=2),
                _pr(player_id=5, round_id=5, missiles_landed=1),
            ],
            [],
        )
        kinds = {bd.kind for r in rows for bd in r.feats if bd.is_season_best}
        self.assertEqual(
            kinds,
            {
                "high_mvp",
                "high_points",
                "high_tags",
                "high_resupplies",
                "high_missiles",
            },
        )


# ===========================================================================
# Badge stacking + per-kind collapse
# ===========================================================================


class TestBadgeStacking(SimpleTestCase):
    def test_threshold_and_season_best_same_kind_collapse_to_one_season_best(
        self,
    ) -> None:
        # A round with 25 tags: both >= 20 (threshold) AND the season leader.
        rows, _ = stat_feats.scan_feats([_pr(tags_made=25)], [])
        self.assertEqual(len(rows), 1)
        tag_badges = [b for b in rows[0].feats if b.kind == "high_tags"]
        self.assertEqual(len(tag_badges), 1, "collapse to ONE badge per kind")
        self.assertTrue(tag_badges[0].is_season_best, "season-best wins the collapse")

    def test_multiple_different_kinds_stack_on_one_row(self) -> None:
        # One round crosses tags AND points AND is a perfect heavy.
        rows, _ = stat_feats.scan_feats(
            [
                _pr(
                    role="heavy",
                    shots_missed=0,
                    tags_made=21,
                    points_scored=13000,
                )
            ],
            [],
        )
        self.assertEqual(len(rows), 1)
        kinds = _kinds_of(rows[0])
        self.assertIn("high_tags", kinds)
        self.assertIn("high_points", kinds)
        self.assertIn("perfect_heavy", kinds)

    def test_one_row_per_player_round(self) -> None:
        # A single qualifying round must collapse to ONE FeatRow even with
        # several badges.
        rows, _ = stat_feats.scan_feats(
            [_pr(tags_made=22, points_scored=13000, mvp=16.0)], []
        )
        self.assertEqual(len(rows), 1)


# ===========================================================================
# Feat-kind vocabulary
# ===========================================================================


class TestFeatVocabulary(SimpleTestCase):
    def test_feat_kinds_constant_shape(self) -> None:
        kinds = {k for k, _ in stat_feats.FEAT_KINDS}
        self.assertEqual(
            kinds,
            {
                "triple_nuke",
                "medic_shutout",
                "perfect_heavy",
                "high_tags",
                "high_points",
                "high_mvp",
                "high_resupplies",
                "high_missiles",
            },
        )

    def test_every_emitted_badge_kind_in_vocabulary(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [
                _pr(role="heavy", shots_missed=0, tags_made=25, round_id=1),
                _pr(role="medic", times_tagged=0, tags_made=3, round_id=2, player_id=2),
                _pr(nuke_detonations=4, round_id=3, player_id=3),
                _pr(
                    points_scored=20000,
                    mvp=30.0,
                    missiles_landed=10,
                    resupplies_given=25,
                    round_id=4,
                    player_id=4,
                ),
            ],
            [],
        )
        vocab = {k for k, _ in stat_feats.FEAT_KINDS}
        for r in rows:
            for b in r.feats:
                self.assertIn(b.kind, vocab)

    def test_badge_label_matches_vocabulary_base(self) -> None:
        label_by_kind = dict(stat_feats.FEAT_KINDS)
        rows, _ = stat_feats.scan_feats([_pr(nuke_detonations=3)], [])
        badge = _badge(rows[0], "triple_nuke")
        self.assertEqual(badge.label, label_by_kind["triple_nuke"])


# ===========================================================================
# Deterministic order
# ===========================================================================


class TestDeterministicOrder(SimpleTestCase):
    def test_feat_rows_ordered_round_desc_then_player_asc(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [
                _pr(player_id=2, round_id=1, tags_made=21),
                _pr(player_id=1, round_id=3, tags_made=21),
                _pr(player_id=3, round_id=3, tags_made=21),
            ],
            [],
        )
        keys = [(r.round_id, r.player_id) for r in rows]
        self.assertEqual(keys, [(3, 1), (3, 3), (1, 2)])

    def test_repeated_calls_equal(self) -> None:
        data = [
            _pr(player_id=2, round_id=1, tags_made=21),
            _pr(player_id=1, round_id=3, tags_made=22),
        ]
        first = stat_feats.scan_feats(list(data), [])
        second = stat_feats.scan_feats(list(data), [])
        self.assertEqual(first, second)


# ===========================================================================
# FeatRow shape / box-score carry
# ===========================================================================


class TestFeatRowShape(SimpleTestCase):
    def test_row_carries_descriptors_and_box_score(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [
                _pr(
                    tags_made=21,
                    points_scored=9000,
                    accuracy=75.0,
                    mvp=12.5,
                    opp_team_name="Blue Team",
                    result="L",
                    season_name="S2",
                    season_id=7,
                    team_name="Red Team",
                    role="scout",
                    player_name="Alice",
                    round_id=42,
                )
            ],
            [],
        )
        row = rows[0]
        self.assertEqual(row.player_name, "Alice")
        self.assertEqual(row.role, "scout")
        self.assertEqual(row.team_name, "Red Team")
        self.assertEqual(row.opp_team_name, "Blue Team")
        self.assertEqual(row.result, "L")
        self.assertEqual(row.season_name, "S2")
        self.assertEqual(row.round_id, 42)
        # Every BOX_SCORE_KEYS key present in stats.
        for key in stat_feats.BOX_SCORE_KEYS:
            self.assertIn(key, row.stats)
        self.assertEqual(row.stats["tags_made"], 21)
        self.assertEqual(row.stats["points_scored"], 9000)
        self.assertEqual(row.stats["accuracy"], 75.0)
        self.assertEqual(row.stats["mvp"], 12.5)

    def test_every_emitted_row_has_at_least_one_badge(self) -> None:
        rows, _ = stat_feats.scan_feats(
            [_pr(tags_made=21), _pr(player_id=2, round_id=2, mvp=4.0)], []
        )
        for r in rows:
            self.assertGreaterEqual(len(r.feats), 1)


# ===========================================================================
# Comeback (Team feats) -- find_comeback_win
# ===========================================================================


class TestFindComebackWin(SimpleTestCase):
    def test_winner_lost_round_one_qualifies(self) -> None:
        recs = stat_feats.find_comeback_win([_match()])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec.kind, "comeback_win")
        self.assertEqual(rec.team_name, "Red Team")
        self.assertEqual(rec.round_id, 2)

    def test_winner_won_round_one_excluded(self) -> None:
        m = _match(red_round1_points=20, blue_round1_points=5)
        self.assertEqual(stat_feats.find_comeback_win([m]), [])

    def test_tie_match_excluded(self) -> None:
        m = _match(winner_team_id=None, winner_team_name="")
        self.assertEqual(stat_feats.find_comeback_win([m]), [])

    def test_last_qualifier_chosen(self) -> None:
        m1 = _match(match_id=1, round_id=2)
        m2 = _match(match_id=2, round_id=4, winner_team_name="Red Team")
        recs = stat_feats.find_comeback_win([m1, m2])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].round_id, 4)

    def test_returns_list_type(self) -> None:
        self.assertIsInstance(stat_feats.find_comeback_win([]), list)


class TestScanFeatsTeamFeats(SimpleTestCase):
    def test_scan_feats_returns_team_feats_tuple(self) -> None:
        rows, team_feats = stat_feats.scan_feats([_pr(tags_made=21)], [_match()])
        self.assertEqual(len(team_feats), 1)
        self.assertEqual(team_feats[0].kind, "comeback_win")
        # Comeback is NOT a per-player FeatRow.
        for r in rows:
            self.assertNotIn("comeback_win", _kinds_of(r))

    def test_empty_inputs_return_two_empty_lists(self) -> None:
        self.assertEqual(stat_feats.scan_feats([], []), ([], []))


# ===========================================================================
# Purity -- no Django imports leaked
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """stat_feats.py must stay pure -- no Django in sys.modules after import."""

    def test_no_django_imported(self) -> None:
        code = (
            "import sys; import matches.stat_feats; "
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]; "
            "assert not leaked, leaked; print('OK')"
        )
        result = run_project_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)
