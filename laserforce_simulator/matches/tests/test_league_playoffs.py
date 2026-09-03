"""LG-02 — Django ``TestCase`` tests for the League Playoffs screen.

``GET /leagues/<int:league_id>/playoffs/`` (URL name ``league_playoffs``)
renders the viewed Season's ``tournament`` SeasonPhase bracket(s) inside the
league shell, replacing the LG-01h ``coming_soon`` placeholder. Read-only,
GET-only; follows the LG-01z shared-view contract.

The fixtures mirror the LG-02-Part2c-1 dashboard-test pattern: compose an
active Season with an ordinal-1 round_robin + ordinal-2 tournament phase, play
the RR to trigger the auto-build, then optionally drain the bracket. Round
ticks are patched small for speed; assertions are schema-level (DOM ids,
context keys) — never raw simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 30


def _rr_tournament_season(name: str = "Pl"):
    """An active Season: ordinal-1 round_robin + ordinal-2 tournament, 4 teams."""
    league = League.objects.create(name=name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(4):
        t, _ = make_team_with_slots(f"{name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _play_rr(season, teams):
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _drain_tournament(tournament):
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


class TestLeaguePlayoffsRouting(TestCase):
    def test_get_returns_200_and_uses_template(self) -> None:
        league, _season, _teams = _rr_tournament_season("Route")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/playoffs.html")

    def test_post_returns_405(self) -> None:
        league, _season, _teams = _rr_tournament_season("Post")
        response = self.client.post(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_stale_league_id_returns_404(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_get_writes_last_league_id(self) -> None:
        league, _season, _teams = _rr_tournament_season("Sess")
        self.client.get(reverse("league_playoffs", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_sidebar_rendered_with_playoffs_active(self) -> None:
        league, _season, _teams = _rr_tournament_season("Side")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-sidebar"')
        self.assertEqual(response.context["sidebar_active"], "playoffs")


class TestLeaguePlayoffsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = League.objects.create(name="NoSeason")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-empty-notice"')

    def test_single_rr_season_renders_empty_notice(self) -> None:
        league = League.objects.create(name="SingleRR")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 6, 1)
        )
        for i in range(4):
            t, _ = make_team_with_slots(f"SRT{i}")
            season.teams.add(t)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.start_season()
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-empty-notice"')
        self.assertEqual(response.context["brackets"], [])


class TestLeaguePlayoffsBracket(TestCase):
    def test_pending_phase_renders_section_without_grid(self) -> None:
        # Tournament phase exists but RR not yet played -> tournament not built.
        league, _season, _teams = _rr_tournament_season("Pend")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-phase-2"')
        self.assertNotContains(response, 'id="league-playoffs-bracket-2"')
        self.assertTrue(response.context["brackets"][0]["pending"])

    def test_built_bracket_renders_nodes(self) -> None:
        league, season, teams = _rr_tournament_season("Built")
        _play_rr(season, teams)
        season.refresh_from_db()
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-phase-2"')
        self.assertContains(response, 'id="league-playoffs-bracket-2"')
        self.assertFalse(response.context["brackets"][0]["pending"])

    def test_champion_banner_after_drain(self) -> None:
        league, season, teams = _rr_tournament_season("Champ")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _drain_tournament(tournament_phase.tournament)
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-champion-2"')
        self.assertIsNotNone(response.context["brackets"][0]["champion"])


class TestLeaguePlayoffsSeasonSelector(TestCase):
    def test_explicit_season_param_selected(self) -> None:
        league, season, _teams = _rr_tournament_season("Sel")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id}),
            {"season": season.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_season_id"], season.id)

    def test_invalid_season_param_falls_back_to_displayed(self) -> None:
        league, season, _teams = _rr_tournament_season("Fall")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id}),
            {"season": 999999},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_season_id"], season.id)


# ===========================================================================
# CONF-02 — the Playoffs screen renders N labelled regional brackets
# ===========================================================================
#
# Seam contract ``.claude/worktrees/conf-02-seam-contract.md`` §5 + §9.4 items
# 15-16; rationale in
# [ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md).
#
# A ``>= 2``-Conference tournament phase appends ONE ``brackets`` entry per
# regional Tournament instead of one per phase, each carrying its
# ``conference`` and a ``key`` DOM-id discriminator ``"<phase.ordinal>-<
# conference.ordinal>"`` so the N brackets of one phase cannot collide on
# ``phase.ordinal``. A 0-Conference Season keeps ``key == str(phase.ordinal)``,
# so every DOM id it renders is byte-identical to today — that regression pin
# is what proves the change is additive.
#
# Fixtures are shared with ``test_regional_playoffs.py`` (same slice, same
# ownership lane) and are hand-built: the round-robin is a deterministic set of
# completed Match + GameRound rows, so NO simulation runs here (contract §9.1)
# and the screen renders in milliseconds. Appended as NEW classes; no existing
# class above is modified.


from matches.tests.test_regional_playoffs import (  # noqa: E402
    _built_regional_season as _conf02_built_regional_season,
    _flat_season as _conf02_flat_season,
    _hand_play_rr as _conf02_hand_play_rr,
    _ids as _conf02_ids,
)


def _conf02_built_flat_season(prefix: str):
    """The 0-Conference regression shape with its single bracket built."""
    season, teams, rr_phase, phase = _conf02_flat_season(prefix)
    _conf02_hand_play_rr(season, rr_phase, _conf02_ids(teams))
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    phase.refresh_from_db()
    return season, teams, phase


class TestLeaguePlayoffsRegionalBrackets(TestCase):
    """A 2-Conference Season renders TWO labelled brackets under one phase."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf02_built_regional_season("Screen")
        self.league = self.season.league
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.league.id})
        )

    def test_two_bracket_entries_for_one_phase(self) -> None:
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(len(self.response.context["brackets"]), 2)

    def test_entries_are_in_conference_ordinal_order(self) -> None:
        brackets = self.response.context["brackets"]
        self.assertEqual(
            [entry["conference"].id for entry in brackets],
            [self.conferences[0].id, self.conferences[1].id],
        )

    def test_each_entry_carries_the_right_conference(self) -> None:
        for entry, conference in zip(
            self.response.context["brackets"], self.conferences
        ):
            self.assertEqual(entry["conference"].id, conference.id)
            self.assertEqual(entry["tournament"].conference_id, conference.id)

    def test_keys_are_phase_ordinal_dash_conference_ordinal(self) -> None:
        brackets = self.response.context["brackets"]
        self.assertEqual(
            [entry["key"] for entry in brackets],
            [
                f"{self.phase.ordinal}-{self.conferences[0].ordinal}",
                f"{self.phase.ordinal}-{self.conferences[1].ordinal}",
            ],
        )

    def test_keys_do_not_collide(self) -> None:
        keys = [entry["key"] for entry in self.response.context["brackets"]]
        self.assertEqual(len(set(keys)), 2)

    def test_neither_entry_is_pending(self) -> None:
        for entry in self.response.context["brackets"]:
            self.assertFalse(entry["pending"])
            self.assertTrue(entry["rounds"])

    def test_rendered_html_carries_both_conference_labels(self) -> None:
        self.assertContains(self.response, 'id="league-playoffs-conference-2-1"')
        self.assertContains(self.response, 'id="league-playoffs-conference-2-2"')
        for conference in self.conferences:
            self.assertContains(self.response, conference.name)

    def test_rendered_html_carries_both_bracket_sections(self) -> None:
        self.assertContains(self.response, 'id="league-playoffs-phase-2-1"')
        self.assertContains(self.response, 'id="league-playoffs-phase-2-2"')
        self.assertContains(self.response, 'id="league-playoffs-bracket-2-1"')
        self.assertContains(self.response, 'id="league-playoffs-bracket-2-2"')

    def test_bare_phase_ordinal_ids_are_not_rendered(self) -> None:
        # The regional keys REPLACE the bare ordinal on this Season; a bare
        # ``-2"`` id would mean two brackets collided on one DOM id.
        self.assertNotContains(self.response, 'id="league-playoffs-phase-2"')
        self.assertNotContains(self.response, 'id="league-playoffs-bracket-2"')


class TestLeaguePlayoffsZeroConferenceRegressionPin(TestCase):
    """The 0-Conference Season renders ONE unlabelled bracket with exactly the
    DOM ids the existing tests already assert — the additive proof."""

    def setUp(self) -> None:
        self.season, self.teams, self.phase = _conf02_built_flat_season("ScreenZero")
        self.league = self.season.league
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.league.id})
        )

    def test_single_bracket_entry(self) -> None:
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(len(self.response.context["brackets"]), 1)

    def test_entry_conference_is_none(self) -> None:
        self.assertIsNone(self.response.context["brackets"][0]["conference"])

    def test_key_is_the_bare_phase_ordinal(self) -> None:
        self.assertEqual(
            self.response.context["brackets"][0]["key"], str(self.phase.ordinal)
        )

    def test_rendered_dom_ids_are_unchanged(self) -> None:
        self.assertContains(self.response, 'id="league-playoffs-phase-2"')
        self.assertContains(self.response, 'id="league-playoffs-bracket-2"')

    def test_no_conference_heading_rendered(self) -> None:
        self.assertNotContains(self.response, "league-playoffs-conference-")
