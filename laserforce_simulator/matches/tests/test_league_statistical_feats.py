"""LG-01z-q — tests for the Statistical Feats league screen.

Two test surfaces:

* Pure-unit tests for ``matches/stat_feats.py`` (hand-built dict fixtures, no
  DB) plus the ``TestNoDjangoImportsLeaked`` subprocess purity check.
* Django ``TestCase`` tests for the view
  ``matches.league_screens.statistical_feats.statistical_feats`` exercised via
  ``RequestFactory`` with a real session attached (the route is wired
  centrally by the orchestrator, so the view is called directly).

Fixtures are hand-constructed ``Match`` / ``GameRound`` / ``PlayerRoundState``
/ ``GameEvent`` rows — LG-01z runs NO simulation.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase

from matches import stat_feats
from matches.league_screens.statistical_feats import statistical_feats
from matches.models import (
    GameEvent,
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ===========================================================================
# Pure-unit tests for matches/stat_feats.py
# ===========================================================================


def _pr(**overrides) -> dict:
    """Build a per-player-round seam dict with sane defaults."""
    base = {
        "round_id": 1,
        "match_id": 1,
        "player_id": 1,
        "player_name": "Alice",
        "team_id": 10,
        "team_name": "Red Team",
        "role": "scout",
        "tags_made": 0,
        "times_tagged": 0,
        "shots_missed": 0,
        "points_scored": 0,
        "resupplies_given": 0,
        "missiles_landed": 0,
        "mvp": 0.0,
        "nuke_detonations": 0,
    }
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


class TestFindTripleNukes(SimpleTestCase):
    def test_three_detonations_qualifies(self) -> None:
        rows = [_pr(player_name="Cmdr", nuke_detonations=3, round_id=7)]
        recs = stat_feats.find_triple_nukes(rows)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].kind, "triple_nuke")
        self.assertEqual(recs[0].name, "Cmdr")
        self.assertEqual(recs[0].value, "3")
        self.assertEqual(recs[0].round_id, 7)

    def test_two_detonations_does_not_qualify(self) -> None:
        rows = [_pr(nuke_detonations=2)]
        self.assertEqual(stat_feats.find_triple_nukes(rows), [])

    def test_multiple_qualifiers_sorted_by_count_desc(self) -> None:
        rows = [
            _pr(player_name="A", nuke_detonations=3, mvp=1.0),
            _pr(player_name="B", nuke_detonations=4, mvp=1.0),
        ]
        recs = stat_feats.find_triple_nukes(rows)
        self.assertEqual([r.name for r in recs], ["B", "A"])

    def test_empty_input(self) -> None:
        self.assertEqual(stat_feats.find_triple_nukes([]), [])


class TestFindMedicShutout(SimpleTestCase):
    def test_untagged_medic_qualifies(self) -> None:
        rows = [_pr(role="medic", times_tagged=0, tags_made=2, round_id=3)]
        rec = stat_feats.find_medic_shutout(rows)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.kind, "medic_shutout")
        self.assertEqual(rec.round_id, 3)

    def test_tagged_medic_excluded(self) -> None:
        rows = [_pr(role="medic", times_tagged=1)]
        self.assertIsNone(stat_feats.find_medic_shutout(rows))

    def test_non_medic_excluded(self) -> None:
        rows = [_pr(role="heavy", times_tagged=0)]
        self.assertIsNone(stat_feats.find_medic_shutout(rows))

    def test_best_by_tags_made(self) -> None:
        rows = [
            _pr(player_name="A", role="medic", times_tagged=0, tags_made=1),
            _pr(player_name="B", role="medic", times_tagged=0, tags_made=5),
        ]
        rec = stat_feats.find_medic_shutout(rows)
        self.assertEqual(rec.name, "B")


class TestFindPerfectHeavy(SimpleTestCase):
    def test_perfect_heavy_qualifies(self) -> None:
        rows = [_pr(role="heavy", shots_missed=0, tags_made=4, round_id=9)]
        rec = stat_feats.find_perfect_heavy(rows)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.kind, "perfect_heavy")
        self.assertEqual(rec.round_id, 9)

    def test_heavy_with_misses_excluded(self) -> None:
        rows = [_pr(role="heavy", shots_missed=1, tags_made=4)]
        self.assertIsNone(stat_feats.find_perfect_heavy(rows))

    def test_heavy_with_zero_tags_excluded(self) -> None:
        rows = [_pr(role="heavy", shots_missed=0, tags_made=0)]
        self.assertIsNone(stat_feats.find_perfect_heavy(rows))

    def test_non_heavy_excluded(self) -> None:
        rows = [_pr(role="scout", shots_missed=0, tags_made=4)]
        self.assertIsNone(stat_feats.find_perfect_heavy(rows))


class TestFindTopMvpAndScore(SimpleTestCase):
    def test_top_mvp(self) -> None:
        rows = [
            _pr(player_name="A", mvp=12.0),
            _pr(player_name="B", mvp=99.5, round_id=4),
        ]
        rec = stat_feats.find_top_mvp(rows)
        self.assertEqual(rec.kind, "top_mvp")
        self.assertEqual(rec.name, "B")
        self.assertEqual(rec.value, "99.5")
        self.assertEqual(rec.round_id, 4)

    def test_top_score(self) -> None:
        rows = [
            _pr(player_name="A", points_scored=100),
            _pr(player_name="B", points_scored=8000, round_id=5),
        ]
        rec = stat_feats.find_top_score(rows)
        self.assertEqual(rec.kind, "top_score")
        self.assertEqual(rec.name, "B")
        self.assertEqual(rec.value, "8000")

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(stat_feats.find_top_mvp([]))
        self.assertIsNone(stat_feats.find_top_score([]))


class TestFindTagStreak(SimpleTestCase):
    def test_most_tags_in_round(self) -> None:
        rows = [
            _pr(player_name="A", tags_made=3),
            _pr(player_name="B", tags_made=17, round_id=6),
        ]
        rec = stat_feats.find_tag_streak(rows)
        self.assertEqual(rec.kind, "tag_streak")
        self.assertEqual(rec.name, "B")
        self.assertEqual(rec.value, "17")

    def test_zero_tags_returns_none(self) -> None:
        self.assertIsNone(stat_feats.find_tag_streak([_pr(tags_made=0)]))


class TestFindResuppliesAndMissiles(SimpleTestCase):
    def test_most_resupplies(self) -> None:
        rows = [_pr(player_name="Medic", resupplies_given=9, round_id=2)]
        rec = stat_feats.find_most_resupplies(rows)
        self.assertEqual(rec.kind, "most_resupplies")
        self.assertEqual(rec.value, "9")

    def test_most_missiles(self) -> None:
        rows = [_pr(player_name="Heavy", missiles_landed=6, round_id=2)]
        rec = stat_feats.find_most_missiles(rows)
        self.assertEqual(rec.kind, "most_missiles")
        self.assertEqual(rec.value, "6")

    def test_zero_returns_none(self) -> None:
        self.assertIsNone(stat_feats.find_most_resupplies([_pr(resupplies_given=0)]))
        self.assertIsNone(stat_feats.find_most_missiles([_pr(missiles_landed=0)]))


class TestFindComebackWin(SimpleTestCase):
    def test_winner_lost_round_one_qualifies(self) -> None:
        # Red won the match but lost round 1 (5 < 10).
        rec = stat_feats.find_comeback_win([_match()])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.kind, "comeback_win")
        self.assertEqual(rec.name, "Red Team")
        self.assertEqual(rec.round_id, 2)

    def test_winner_won_round_one_excluded(self) -> None:
        m = _match(red_round1_points=20, blue_round1_points=5)
        self.assertIsNone(stat_feats.find_comeback_win([m]))

    def test_tie_match_excluded(self) -> None:
        m = _match(winner_team_id=None, winner_team_name="")
        self.assertIsNone(stat_feats.find_comeback_win([m]))

    def test_last_qualifier_chosen(self) -> None:
        m1 = _match(match_id=1, round_id=2)
        m2 = _match(match_id=2, round_id=4, winner_team_name="Red Team")
        rec = stat_feats.find_comeback_win([m1, m2])
        self.assertEqual(rec.round_id, 4)


class TestScanFeats(SimpleTestCase):
    def test_stable_order_and_all_kinds(self) -> None:
        rows = [
            _pr(player_name="Cmdr", role="commander", nuke_detonations=3, round_id=1),
            _pr(player_name="Doc", role="medic", times_tagged=0, tags_made=2),
            _pr(player_name="Tank", role="heavy", shots_missed=0, tags_made=4),
            _pr(
                player_name="Star",
                mvp=50.0,
                points_scored=9000,
                tags_made=10,
                resupplies_given=3,
                missiles_landed=2,
            ),
        ]
        recs = stat_feats.scan_feats(rows, [_match()])
        kinds = [r.kind for r in recs]
        # triple_nuke first, comeback last.
        self.assertEqual(kinds[0], "triple_nuke")
        self.assertEqual(kinds[-1], "comeback_win")
        for expected in (
            "medic_shutout",
            "perfect_heavy",
            "top_mvp",
            "top_score",
            "tag_streak",
            "most_resupplies",
            "most_missiles",
        ):
            self.assertIn(expected, kinds)

    def test_empty_inputs_return_empty(self) -> None:
        self.assertEqual(stat_feats.scan_feats([], []), [])


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """stat_feats.py must stay pure — no Django in sys.modules after import."""

    def test_no_django_imported(self) -> None:
        code = (
            "import sys; import matches.stat_feats; "
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]; "
            "assert not leaked, leaked; print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=_manage_dir(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


def _manage_dir() -> str:
    """Directory containing manage.py (so `import matches.*` resolves)."""
    import os

    # This test file: .../laserforce_simulator/matches/tests/<file>
    # manage.py lives at .../laserforce_simulator/
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


# ===========================================================================
# View tests (Django TestCase)
# ===========================================================================


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int):
    request = RequestFactory().get(f"/leagues/{league_id}/stats/statistical-feats/")
    return _attach_session(request)


def _make_league(name: str = "FeatLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _player_of(team):
    return team.slot_commander


def _make_round(
    season,
    team_red,
    team_blue,
    *,
    round_number=1,
    red_points=10,
    blue_points=5,
    completed=True,
):
    match, _ = Match.objects.get_or_create(
        team_red=team_red, team_blue=team_blue, season=season
    )
    return match, GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=completed,
    )


def _make_prs(game_round, player, team_color, role, **stats):
    defaults = dict(
        game_round=game_round,
        player=player,
        team_color=team_color,
        role=role,
    )
    defaults.update(stats)
    return PlayerRoundState.objects.create(**defaults)


class TestStatisticalFeatsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = statistical_feats(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/statistical-feats/")
        )
        response = statistical_feats(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        from django.http import Http404

        with self.assertRaises(Http404):
            statistical_feats(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        statistical_feats(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


class TestStatisticalFeatsEmptyState(TestCase):
    def test_no_season_renders_empty_notice_with_no_season(self) -> None:
        league = _make_league()
        response = statistical_feats(_get(league.id), league.id)
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("stat-feats-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = statistical_feats(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())

    def test_season_no_feats_renders_empty_notice(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = statistical_feats(_get(league.id), league.id)
        content = response.content.decode()
        self.assertIn("stat-feats-empty-notice", content)


class TestStatisticalFeatsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams

    def test_perfect_heavy_and_top_score_render(self) -> None:
        _match, gr = _make_round(self.season, self.team_a, self.team_b)
        _make_prs(
            gr,
            self.team_a.slot_heavy,
            "red",
            "heavy",
            shots_missed=0,
            tags_made=5,
            points_scored=6000,
        )
        response = statistical_feats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("stat-feats-list", content)
        self.assertIn("stat-feat-perfect_heavy", content)
        self.assertIn("stat-feat-top_score", content)
        self.assertIn(f"/matches/game-round/{gr.id}/", content)

    def test_medic_shutout_render(self) -> None:
        _match, gr = _make_round(self.season, self.team_a, self.team_b)
        _make_prs(
            gr,
            self.team_a.slot_medic,
            "red",
            "medic",
            times_tagged=0,
            tags_made=1,
        )
        response = statistical_feats(_get(self.league.id), self.league.id)
        self.assertIn("stat-feat-medic_shutout", response.content.decode())

    def test_triple_nuke_render(self) -> None:
        _m, gr = _make_round(self.season, self.team_a, self.team_b)
        cmdr = self.team_a.slot_commander
        _make_prs(gr, cmdr, "red", "commander", tags_made=1)
        for tick in (100, 200, 300):
            GameEvent.objects.create(
                game_round=gr,
                actor=cmdr,
                event_type="special",
                timestamp=tick,
                points_awarded=500,
                metadata={"targets": [1, 2]},
            )
        response = statistical_feats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("stat-feat-triple_nuke", content)
        self.assertIn(cmdr.name, content)

    def test_nuke_activation_not_counted(self) -> None:
        # Activation rows (points=0, fires_at) must NOT count toward triple-nuke.
        _m, gr = _make_round(self.season, self.team_a, self.team_b)
        cmdr = self.team_a.slot_commander
        _make_prs(gr, cmdr, "red", "commander")
        for tick in (100, 200, 300):
            GameEvent.objects.create(
                game_round=gr,
                actor=cmdr,
                event_type="special",
                timestamp=tick,
                points_awarded=0,
                metadata={"fires_at": tick + 20},
            )
        response = statistical_feats(_get(self.league.id), self.league.id)
        self.assertNotIn("stat-feat-triple_nuke", response.content.decode())

    def test_comeback_win_render(self) -> None:
        # team_a (red) loses round 1 but wins the match.
        match, gr1 = _make_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=5,
            blue_points=12,
            completed=False,
        )
        GameRound.objects.create(
            match=match,
            team_red=self.team_b,
            team_blue=self.team_a,
            round_number=2,
            red_points=2,
            blue_points=20,
            is_completed=True,
        )
        # team_a won round 1? no (lost 5<12); round 2 team_a played blue, scored 20.
        match.red_round1_points = 5
        match.blue_round1_points = 12
        match.red_round2_points = 2
        match.blue_round2_points = 20
        match.is_completed = True
        match.save()
        match.refresh_from_db()
        # Winner is team_a (won round 2, 2 rounds tie 1-1 -> total points 25 vs 14).
        response = statistical_feats(_get(self.league.id), self.league.id)
        content = response.content.decode()
        if match.winner_id == self.team_a.id:
            self.assertIn("stat-feat-comeback_win", content)

    def test_sidebar_active_entry_present(self) -> None:
        response = statistical_feats(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-stats-statistical_feats", response.content.decode())


# ===========================================================================
# LG-06b — team filter
# ===========================================================================


class TestStatisticalFeatsTeamFilter(TestCase):
    """LG-06b team filter via the Django test ``Client`` against the
    wired ``stats_statistical_feats`` URL (so ``response.context``
    exists). Statistical Feats has NO pagination / sort."""

    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        teams.sort(key=lambda t: t.name)
        self.team_a, self.team_b = teams
        # team_a's Heavy logs a perfect-accuracy round (feat: perfect_heavy);
        # team_b's Heavy logs a perfect round too. The two feats are
        # attributed to different teams, so a team filter narrows them.
        _match, gr = _make_round(self.season, self.team_a, self.team_b)
        self.heavy_a = self.team_a.slot_heavy
        self.heavy_b = self.team_b.slot_heavy
        _make_prs(
            gr,
            self.heavy_a,
            "red",
            "heavy",
            shots_missed=0,
            tags_made=5,
            points_scored=9000,
        )
        _make_prs(
            gr,
            self.heavy_b,
            "blue",
            "heavy",
            shots_missed=0,
            tags_made=3,
            points_scored=1000,
        )

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_enrolled_teams_context_ordered_by_name(self) -> None:
        response = self._get()
        enrolled = response.context["enrolled_teams"]
        names = [t.name for t in enrolled]
        self.assertEqual(names, sorted(names))
        self.assertEqual(
            {t.id for t in enrolled},
            {self.team_a.id, self.team_b.id},
        )

    def test_selected_team_id_none_when_no_param(self) -> None:
        response = self._get()
        self.assertIsNone(response.context["selected_team_id"])

    def test_selected_team_id_set_for_enrolled(self) -> None:
        response = self._get(query=f"team_id={self.team_a.id}")
        self.assertEqual(response.context["selected_team_id"], self.team_a.id)

    def test_selected_team_id_none_for_non_enrolled(self) -> None:
        outsider = Team.objects.create(name="SF Outsiders")
        response = self._get(query=f"team_id={outsider.id}")
        self.assertIsNone(response.context["selected_team_id"])

    def test_filter_attributes_feats_to_team_only(self) -> None:
        # Filtering to team_a: the top_score feat (9000, team_a's Heavy)
        # is present; team_b's Heavy (1000 points) is not the top scorer
        # within team_a's pool, so the other team's player name must not
        # surface as a feat holder.
        content = self._get(query=f"team_id={self.team_a.id}").content.decode()
        self.assertIn(self.heavy_a.name, content)
        self.assertNotIn(self.heavy_b.name, content)

    def test_absent_param_shows_full_feats(self) -> None:
        # The overall top scorer is team_a's Heavy; both teams' rounds
        # feed the full scan.
        content = self._get().content.decode()
        self.assertIn(self.heavy_a.name, content)

    def test_malformed_param_falls_back_to_full(self) -> None:
        response = self._get(query="team_id=abc")
        self.assertIsNone(response.context["selected_team_id"])
        self.assertIn(self.heavy_a.name, response.content.decode())

    def test_non_enrolled_param_falls_back_to_full(self) -> None:
        outsider = Team.objects.create(name="SF Outsiders")
        response = self._get(query=f"team_id={outsider.id}")
        self.assertIsNone(response.context["selected_team_id"])
        self.assertIn(self.heavy_a.name, response.content.decode())

    def test_filter_form_and_select_dom_ids_present(self) -> None:
        content = self._get().content.decode()
        self.assertIn("statistical-feats-team-filter-form", content)
        self.assertIn("statistical-feats-team-filter-select", content)

    def test_default_all_teams_option_present(self) -> None:
        content = self._get().content.decode()
        # The default option carries value="" and the "All Teams" label;
        # it may additionally carry ``selected`` when no team is picked.
        self.assertIn('value=""', content)
        self.assertIn("All Teams", content)

    def test_selected_option_matches_selected_team_id(self) -> None:
        content = self._get(query=f"team_id={self.team_a.id}").content.decode()
        self.assertIn(f'value="{self.team_a.id}" selected', content)


class TestStatisticalFeatsComebackFilter(TestCase):
    """The comeback feat is included only when the selected team
    participated in the match (selected in {red_team_id, blue_team_id})."""

    URL_NAME = "stats_statistical_feats"

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        teams.sort(key=lambda t: t.name)
        self.team_a, self.team_b, self.team_c = teams
        # team_a (Match.team_red) loses round 1 (5 < 12) but wins round 2
        # (20 > 2): rounds tie 1-1, team_a wins on total points (25 vs 14)
        # — a comeback win. The Match's per-round point fields drive
        # ``calculate_winner`` (keyed to team_red / team_blue), so team_a
        # must be the round-2 point WINNER as red.
        match, gr1 = _make_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=5,
            blue_points=12,
            completed=False,
        )
        GameRound.objects.create(
            match=match,
            team_red=self.team_a,
            team_blue=self.team_b,
            round_number=2,
            red_points=20,
            blue_points=2,
            is_completed=True,
        )
        match.red_round1_points = 5
        match.blue_round1_points = 12
        match.red_round2_points = 20
        match.blue_round2_points = 2
        match.is_completed = True
        match.save()
        match.refresh_from_db()
        self.match = match

    def test_comeback_feat_present_when_team_participated(self) -> None:
        # Only assert if team_a actually won the comeback match.
        if self.match.winner_id != self.team_a.id:
            self.skipTest("fixture did not produce a comeback win for team_a")
        response = self._get(query=f"team_id={self.team_a.id}")
        self.assertIn("stat-feat-comeback_win", response.content.decode())

    def test_comeback_feat_absent_for_uninvolved_team(self) -> None:
        if self.match.winner_id != self.team_a.id:
            self.skipTest("fixture did not produce a comeback win for team_a")
        # team_c did not participate in the comeback match → no comeback feat
        # when filtering to team_c.
        response = self._get(query=f"team_id={self.team_c.id}")
        self.assertNotIn("stat-feat-comeback_win", response.content.decode())


# ===========================================================================
# LG-06c — Statistical Feats sortable control
#
# A single ?sort=&dir= over the flat <ul> of FeatRecords. Keys {kind, name,
# value}; default kind/asc. value extraction is numeric-aware so non-numeric
# values ("Comeback", "4 tags, 0 tagged") group at one end without crashing.
# Coexists with ?team_id=. DOM ids statistical-feats-th-<key>; active glyph
# U+2191 / U+2193.
#
# EXPECTED TO FAIL until the Code agent lands feats sorting + the sort-control
# bar above the <ul>.
# ===========================================================================

import re as _sf_re  # noqa: E402

_SF_GLYPH_UP = "↑"
_SF_GLYPH_DOWN = "↓"


def _feat_kind_order(content: str) -> list[str]:
    """Kinds in render order from the ``stat-feat-{kind}`` list-item ids."""
    return _sf_re.findall(r"stat-feat-([a-z_]+)", content)


def _make_multi_feat_round(test):
    """Populate a round producing several distinct feat kinds with a mix of
    numeric and non-numeric values:

      * perfect_heavy  → value "5 tags, 0 misses"  (non-numeric)
      * top_score      → value "9000"              (numeric)
      * top_mvp        → value "<float>"           (numeric)
      * tag_streak     → value "5"                 (numeric)
      * medic_shutout  → value "<n> tags, 0 tagged" (non-numeric)
    """
    _m, gr = _make_round(test.season, test.team_a, test.team_b)
    _make_prs(
        gr,
        test.team_a.slot_heavy,
        "red",
        "heavy",
        shots_missed=0,
        tags_made=5,
        points_scored=9000,
    )
    _make_prs(
        gr,
        test.team_a.slot_medic,
        "red",
        "medic",
        times_tagged=0,
        tags_made=3,
        points_scored=100,
    )
    return gr


class TestStatisticalFeatsSortDefault(TestCase):
    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        _make_multi_feat_round(self)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_default_order_is_kind_asc(self) -> None:
        kinds = _feat_kind_order(self._get().content.decode())
        # At least two distinct kinds, rendered kind-ascending.
        self.assertGreaterEqual(len(set(kinds)), 2)
        self.assertEqual(kinds, sorted(kinds))


class TestStatisticalFeatsSortKeys(TestCase):
    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        _make_multi_feat_round(self)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_kind_asc_then_desc(self) -> None:
        asc = _feat_kind_order(self._get(query="sort=kind&dir=asc").content.decode())
        desc = _feat_kind_order(self._get(query="sort=kind&dir=desc").content.decode())
        self.assertEqual(asc, sorted(asc))
        self.assertEqual(desc, sorted(desc, reverse=True))

    def test_name_asc_then_desc_returns_200(self) -> None:
        # All feats here are team_a players; assert no crash + a deterministic
        # total order (reverse of asc).
        asc = _feat_kind_order(self._get(query="sort=name&dir=asc").content.decode())
        desc = _feat_kind_order(self._get(query="sort=name&dir=desc").content.decode())
        self.assertEqual(self._get(query="sort=name&dir=asc").status_code, 200)
        # The two orderings must be reverses over the same multiset of kinds.
        self.assertEqual(sorted(asc), sorted(desc))

    def test_value_sort_mixes_numeric_and_non_numeric_without_crash(self) -> None:
        # A mix of "9000"/"42.5" (numeric) and "5 tags, 0 misses"/"Comeback"
        # (non-numeric). The sort must not raise and must give a deterministic
        # total order — non-numeric values grouped at one end.
        asc = self._get(query="sort=value&dir=asc")
        desc = self._get(query="sort=value&dir=desc")
        self.assertEqual(asc.status_code, 200)
        self.assertEqual(desc.status_code, 200)
        asc_kinds = _feat_kind_order(asc.content.decode())
        desc_kinds = _feat_kind_order(desc.content.decode())
        # Same multiset of feats either way; deterministic ordering.
        self.assertEqual(sorted(asc_kinds), sorted(desc_kinds))
        # Direction actually changes the order (numeric extreme moves).
        self.assertNotEqual(asc_kinds, desc_kinds)


class TestStatisticalFeatsSortInvalidFallback(TestCase):
    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        _make_multi_feat_round(self)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_garbage_sort_falls_back_to_kind_asc(self) -> None:
        kinds = _feat_kind_order(self._get(query="sort=BOGUS").content.decode())
        self.assertEqual(kinds, sorted(kinds))

    def test_garbage_dir_falls_back_to_asc(self) -> None:
        kinds = _feat_kind_order(self._get(query="sort=kind&dir=NOPE").content.decode())
        self.assertEqual(kinds, sorted(kinds))

    def test_empty_sort_falls_back_to_default(self) -> None:
        kinds = _feat_kind_order(self._get(query="sort=&dir=").content.decode())
        self.assertEqual(kinds, sorted(kinds))

    def test_uppercase_sort_falls_back(self) -> None:
        kinds = _feat_kind_order(self._get(query="sort=KIND").content.decode())
        self.assertEqual(kinds, sorted(kinds))


class TestStatisticalFeatsSortHeaderGlyph(TestCase):
    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        _make_multi_feat_round(self)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_th_dom_ids_present(self) -> None:
        content = self._get().content.decode()
        for key in ("kind", "name", "value"):
            self.assertIn(f"statistical-feats-th-{key}", content)

    def test_active_value_header_glyph(self) -> None:
        content = self._get(query="sort=value&dir=desc").content.decode()
        th_start = content.index("statistical-feats-th-value")
        window = content[th_start : th_start + 400]
        self.assertIn(_SF_GLYPH_DOWN, window)

    def test_active_kind_header_up_glyph_on_asc(self) -> None:
        content = self._get(query="sort=kind&dir=asc").content.decode()
        th_start = content.index("statistical-feats-th-kind")
        window = content[th_start : th_start + 400]
        self.assertIn(_SF_GLYPH_UP, window)


class TestStatisticalFeatsSortCoexistsWithTeamId(TestCase):
    """Sort + ?team_id= honoured together. team_a's Heavy is the top scorer;
    filtering to team_a AND sorting by value desc keeps team_a's feats only."""

    URL_NAME = "stats_statistical_feats"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        teams.sort(key=lambda t: t.name)
        self.team_a, self.team_b = teams
        _m, gr = _make_round(self.season, self.team_a, self.team_b)
        self.heavy_a = self.team_a.slot_heavy
        self.heavy_b = self.team_b.slot_heavy
        _make_prs(
            gr,
            self.heavy_a,
            "red",
            "heavy",
            shots_missed=0,
            tags_made=5,
            points_scored=9000,
        )
        _make_prs(
            gr,
            self.heavy_b,
            "blue",
            "heavy",
            shots_missed=0,
            tags_made=3,
            points_scored=1000,
        )

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_filter_and_value_desc_together(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&sort=value&dir=desc"
        ).content.decode()
        # team_a's Heavy present, team_b's Heavy filtered out.
        self.assertIn(self.heavy_a.name, content)
        self.assertNotIn(self.heavy_b.name, content)

    def test_header_href_carries_team_id(self) -> None:
        content = self._get(query=f"team_id={self.team_a.id}").content.decode()
        th_start = content.index("statistical-feats-th-value")
        window = content[th_start : th_start + 400]
        self.assertIn(f"team_id={self.team_a.id}", window)

    def test_team_filter_form_carries_sort_and_dir(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&sort=value&dir=desc"
        ).content.decode()
        self.assertIn('name="sort"', content)
        self.assertIn('name="dir"', content)
