"""HX-02 — Django ``TestCase`` view tests for role benchmarks.

Covers the new ``/players/benchmarks/`` page and the HX-02 overlay
additions to ``/players/<id>/stats/``. Both views share the same query
params (``?threshold=``, ``?display=``) and the same cache backend.

Each class is wrapped in ``@override_settings`` to pin a clean local-
memory cache LOCATION so the cache-version increments from
``invalidate_role_benchmarks`` don't bleed across test classes — and
``cache.clear()`` runs in ``setUp`` to wipe anything signal handlers
might have created during fixture setup.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from matches.models import GameRound, PlayerRoundState
from teams.models import Player, Team


def _make_player(team_name: str, player_name: str) -> Player:
    """Create a team with one Player attached to the commander slot."""
    team = Team.objects.create(name=team_name)
    player = Player.objects.create(team=team, name=player_name)
    team.slot_commander = player
    team.save()
    return team, player


def _make_round_state(
    player: Player,
    team: Team,
    *,
    role: str = "commander",
    points_scored: int = 500,
    tags_made: int = 5,
    times_tagged: int = 3,
    shots_missed: int = 4,
    final_special: int = 2,
    specials_used: int = 1,
    was_eliminated_at: int = 1500,
    final_lives: int = 5,
    resupplies_given: int = 0,
    missiles_landed: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    combo_resupply_count: int = 0,
) -> PlayerRoundState:
    """Create a real ``GameRound`` + ``PlayerRoundState`` row."""
    game_round = GameRound.objects.create(
        round_number=1,
        team_red=team,
        team_blue=team,
    )
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color="red",
        role=role,
        points_scored=points_scored,
        tags_made=tags_made,
        times_tagged=times_tagged,
        shots_missed=shots_missed,
        final_special=final_special,
        specials_used=specials_used,
        was_eliminated_at=was_eliminated_at,
        final_lives=final_lives,
        resupplies_given=resupplies_given,
        missiles_landed=missiles_landed,
        follow_up_shots=follow_up_shots,
        reaction_shots=reaction_shots,
        combo_resupply_count=combo_resupply_count,
    )


# ---------------------------------------------------------------------------
# /players/benchmarks/ view
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-view",
        }
    }
)
class TestRoleBenchmarksView(TestCase):
    """GET ``/players/benchmarks/`` smoke + query-param coverage."""

    def setUp(self) -> None:
        cache.clear()

    def test_200_with_five_locked_context_keys(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        for key in ("min_rounds", "display", "roles", "benchmarks", "stat_keys"):
            self.assertIn(key, response.context, f"context key {key!r} missing")
        # `roles` is the locked ROLES tuple.
        from teams.role_benchmarks import ROLES, STAT_KEYS

        self.assertEqual(tuple(response.context["roles"]), ROLES)
        self.assertEqual(tuple(response.context["stat_keys"]), STAT_KEYS)

    def test_threshold_query_param_parsed(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url + "?threshold=10&display=median")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["min_rounds"], 10)
        self.assertEqual(response.context["display"], "median")

    def test_threshold_non_int_falls_back_to_default(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url + "?threshold=abc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["min_rounds"], 5)

    def test_threshold_negative_clamps_to_zero(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url + "?threshold=-3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["min_rounds"], 0)

    def test_display_invalid_falls_back_to_mean(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url + "?display=garbage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["display"], "mean")

    def test_empty_db_renders_no_data_notice(self) -> None:
        url = reverse("role_benchmarks")
        response = self.client.get(url)
        body = response.content.decode()
        self.assertIn("benchmark-no-data-notice", body)
        self.assertIn("no benchmark data yet", body.lower())

    def test_seeded_db_renders_five_role_tables_and_mean(self) -> None:
        team, player = _make_player("Bench Team", "Cmdr A")
        _make_round_state(player, team, role="commander", points_scored=1000)
        _make_round_state(player, team, role="commander", points_scored=2000)

        url = reverse("role_benchmarks")
        response = self.client.get(url + "?threshold=0")  # don't filter
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for role in ("commander", "heavy", "scout", "medic", "ammo"):
            self.assertIn(f"benchmark-table-{role}", body)
        # The commander/points_scored row id is present.
        self.assertIn("benchmark-row-commander-points_scored", body)
        # And the computed mean (1500.0 = (1000+2000)/2) is rendered in
        # the page body — formatting may vary but the integer prefix
        # "1500" should appear in some form.
        self.assertIn("1500", body)


# ---------------------------------------------------------------------------
# /players/<id>/stats/ — HX-01 extended with HX-02 overlay
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-view-stats",
        }
    }
)
class TestPlayerCareerStatsExtended(TestCase):
    """HX-02 extends the HX-01 view additively (existing keys + 4 new)."""

    def setUp(self) -> None:
        cache.clear()

    def test_200_with_hx01_and_hx02_context_keys(self) -> None:
        team, player = _make_player("Career Team", "Career Player")
        _make_round_state(player, team, role="commander", points_scored=500)
        _make_round_state(player, team, role="commander", points_scored=700)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        self.assertEqual(response.status_code, 200)
        # HX-01 six keys still present.
        for key in (
            "player",
            "total_rounds",
            "career",
            "per_role",
            "trend",
            "has_rounds",
        ):
            self.assertIn(key, response.context)
        # HX-02 additive four.
        for key in (
            "min_rounds",
            "display",
            "stat_keys",
            "per_role_with_benchmarks",
        ):
            self.assertIn(key, response.context)

    def test_per_role_with_benchmarks_contains_benchmarks_by_stat(self) -> None:
        team, player = _make_player("BBS Team", "BBS Player")
        _make_round_state(player, team, role="commander", points_scored=500)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        self.assertTrue(len(rows) >= 1)
        # Find the commander row.
        cmdr = next(r for r in rows if r["role"] == "commander")
        self.assertIn("benchmarks_by_stat", cmdr)
        self.assertIsInstance(cmdr["benchmarks_by_stat"], dict)

    def test_role_benchmarks_link_rendered(self) -> None:
        team, player = _make_player("Link Team", "Link Player")
        _make_round_state(player, team, role="commander")

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url)
        body = response.content.decode()
        self.assertIn("role-benchmarks-link", body)
        self.assertIn("Role benchmarks", body)

    def test_below_threshold_shows_need_n_rounds_substring(self) -> None:
        """One-round-in-heavy player at threshold=5 renders the need-N-rounds copy.

        The seam locks the substring shape ``"need N+ rounds"`` for an
        unqualified cell — the active threshold (5) appears in the
        rendered cell text.
        """
        team, player = _make_player("Heavy Team", "Heavy Player")
        # Build at least a couple rounds so the page renders the per-role
        # table at all (HX-01 hides it when has_rounds=False).
        _make_round_state(player, team, role="heavy", points_scored=200)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=5")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The page must mention the threshold-related "need" copy.
        self.assertIn("need ", body.lower())
        self.assertIn("5", body)

    # ----------------------------------------------------------------------
    # HX-01b — extend the per-role table from 5 rows to 15 (5 HX-01 + 10
    # STAT_KEYS net-new). Pinned to
    # `.claude/worktrees/hx-01b-seam-contract.md`.
    # ----------------------------------------------------------------------

    def test_stat_rows_is_15_entry_ordered_list_per_role(self) -> None:
        """Each per_role_with_benchmarks row carries a 15-entry stat_rows list."""
        team, player = _make_player("HX01b A", "HX01b A Player")
        _make_round_state(player, team, role="commander", points_scored=500)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        self.assertTrue(len(rows) >= 1)
        cmdr = next(r for r in rows if r["role"] == "commander")
        self.assertIn("stat_rows", cmdr)
        self.assertIsInstance(cmdr["stat_rows"], list)
        self.assertEqual(len(cmdr["stat_rows"]), 15)

    def test_stat_rows_order_is_locked(self) -> None:
        """The 15-entry key sequence is pinned verbatim by the seam contract."""
        team, player = _make_player("HX01b B", "HX01b B Player")
        _make_round_state(player, team, role="commander", points_scored=500)

        expected_keys = [
            "avg_points",
            "tag_ratio",
            "avg_survival_ticks",
            "avg_accuracy_pct",
            "avg_sp_earned",
            "mvp",
            "tags_made",
            "times_tagged",
            "final_lives",
            "resupplies_given",
            "missiles_landed",
            "specials_used",
            "follow_up_shots",
            "reaction_shots",
            "combo_resupply_count",
        ]

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        cmdr = next(r for r in rows if r["role"] == "commander")
        self.assertEqual([s["key"] for s in cmdr["stat_rows"]], expected_keys)

    def test_stat_rows_labels_are_locked(self) -> None:
        """The 15-entry user-visible labels are pinned by the seam contract."""
        team, player = _make_player("HX01b Labels", "HX01b Labels Player")
        _make_round_state(player, team, role="commander", points_scored=500)

        expected_labels = [
            "Avg points",
            "Tag ratio",
            "Avg survival",
            "Avg accuracy",
            "Avg SP earned",
            "MVP score",
            "Tags made",
            "Times tagged",
            "Final lives",
            "Resupplies given",
            "Missiles landed",
            "Specials used",
            "Follow-up shots",
            "Reaction shots",
            "Combo resupplies",
        ]

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        cmdr = next(r for r in rows if r["role"] == "commander")
        self.assertEqual([s["label"] for s in cmdr["stat_rows"]], expected_labels)

    def test_hx01_only_rows_carry_none_benchmark(self) -> None:
        """Rows 1, 2, 4 (tag_ratio, avg_survival_ticks, avg_sp_earned) → benchmark=None.

        These 3 HX-01-only derived stats have no STAT_KEYS counterpart and
        render `—` placeholder cells in the template.
        """
        team, player = _make_player("HX01b C", "HX01b C Player")
        _make_round_state(player, team, role="commander", points_scored=500)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        cmdr = next(r for r in rows if r["role"] == "commander")
        stat_rows = cmdr["stat_rows"]
        # Index 1 = tag_ratio; index 2 = avg_survival_ticks; index 4 = avg_sp_earned.
        for idx, expected_key in (
            (1, "tag_ratio"),
            (2, "avg_survival_ticks"),
            (4, "avg_sp_earned"),
        ):
            self.assertEqual(stat_rows[idx]["key"], expected_key)
            self.assertIsNone(stat_rows[idx]["benchmark"])

    def test_net_new_rows_subject_value_matches_compute_career_stat_for_role(
        self,
    ) -> None:
        """Player value for the 10 net-new rows equals compute_career_stat_for_role.

        Uses the view's `_round_dict_from_state` to build the same 18-key
        round-dict the view feeds the pure module, then asserts each
        stat_rows[i]["player_value"] matches a direct call to
        compute_career_stat_for_role on the same dicts.
        """
        from teams.role_benchmarks import compute_career_stat_for_role
        from teams.views import _round_dict_from_state

        team, player = _make_player("HX01b D", "HX01b D Player")
        _make_round_state(
            player,
            team,
            role="heavy",
            points_scored=400,
            tags_made=10,
            times_tagged=2,
            shots_missed=3,
            final_lives=8,
            missiles_landed=2,
            follow_up_shots=1,
        )
        _make_round_state(
            player,
            team,
            role="heavy",
            points_scored=600,
            tags_made=15,
            times_tagged=4,
            shots_missed=5,
            final_lives=6,
            missiles_landed=1,
            follow_up_shots=2,
        )

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        rows = response.context["per_role_with_benchmarks"]
        heavy = next(r for r in rows if r["role"] == "heavy")
        stat_rows = heavy["stat_rows"]

        # Build the same round-dict list the view used.
        states = PlayerRoundState.objects.filter(
            player=player, role="heavy"
        ).select_related("game_round")
        player_role_rounds = [_round_dict_from_state(s) for s in states]

        # Rows 5-14 are the 10 STAT_KEYS net-new rows.
        for idx in range(5, 15):
            stat = stat_rows[idx]
            expected = float(
                compute_career_stat_for_role(player_role_rounds, stat["key"])
            )
            self.assertAlmostEqual(
                stat["player_value"],
                expected,
                places=5,
                msg=f"stat_rows[{idx}] (key={stat['key']}) player_value drift",
            )

    def test_below_threshold_subject_renders_need_n_rounds_on_all_12_benchmarked_rows(
        self,
    ) -> None:
        """Single-round subject at threshold=5 → 12 'need 5+ rounds' substrings.

        The 15-row table for the one role played has 12 benchmark-backed
        rows (5 HX-01 minus the 3 HX-01-only + 10 STAT_KEYS = 12); each
        unqualified cell renders the locked substring once.
        """
        team, player = _make_player("HX01b E", "HX01b E Player")
        _make_round_state(player, team, role="commander", points_scored=300)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=5")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # 12 benchmark-backed rows × 1 role played × 1 occurrence each.
        self.assertGreaterEqual(body.count("need 5+ rounds"), 12)

    def test_per_role_table_dom_ids_present_per_role_played(self) -> None:
        """Section wrapper + one <table id=...> per role actually played."""
        team, player = _make_player("HX01b F", "HX01b F Player")
        _make_round_state(player, team, role="commander", points_scored=400)
        _make_round_state(player, team, role="heavy", points_scored=600)

        url = reverse("player_career_stats", args=[player.id])
        response = self.client.get(url + "?threshold=0")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Wrapper section preserves the HX-01-locked id.
        self.assertIn('id="career-per-role-table"', body)
        # One per-role table per role the subject played.
        self.assertIn('id="career-per-role-table-commander"', body)
        self.assertIn('id="career-per-role-table-heavy"', body)
        # Roles NOT played → no table id.
        self.assertNotIn('id="career-per-role-table-scout"', body)
        self.assertNotIn('id="career-per-role-table-medic"', body)
        self.assertNotIn('id="career-per-role-table-ammo"', body)
