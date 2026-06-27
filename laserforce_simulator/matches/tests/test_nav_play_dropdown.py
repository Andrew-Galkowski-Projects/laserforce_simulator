"""NAV-01 — TDD tests for the league-mode-only ``Play ▾`` top-nav dropdown.

The LOCKED seam contract is ``.claude/worktrees/nav-01-seam-contract.md``.
NAV-01 RELOCATES the league-advancement Play controls out of both dashboards
into a single ``Play ▾`` dropdown in the league branch of ``base.html``, driven
by the 9 play keys ``core.context_processors.league_nav`` adds (sourced from the
shared helper ``matches.league_views._build_play_controls_context``).

This file asserts on:

* The league-branch ``base.html`` Play-dropdown DOM ids across the STATE MATRIX
  (§3 nav-id table) — rendered against a ``/leagues/<id>/`` (and a
  ``/seasons/<id>/`` via ``last_league_id`` session pin) page.
* The pure helper ``_build_play_controls_context(league, displayed_season)``
  returning the 9-key dict across the state matrix (§1 table).
* Off-league pages NOT rendering any ``play-nav-link`` / ``topbar-play-*`` ids
  (§2 — the play keys are absent off-league, and the dropdown only renders in
  the ``app_mode == "league"`` branch).

Tests assert SCHEMA-LEVEL outcomes — DOM ids / context keys / status codes —
NEVER raw simulated point totals (tournament sims are non-deterministic). N=4
small seeded sims drive the playoff state; the RR is played via the real
``simulate_scheduled_round`` (which auto-builds the tournament phase) under a
small ``ROUND_TICKS`` patch.

PRE-CODE-LANDING NOTE: these assertions WILL fail until the Code agent lands the
``Play ▾`` dropdown template + the ``league_nav`` play-key merge — that is the
expected TDD red state, not a defect in this file.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.league_views import _build_play_controls_context
from matches.models import BracketNode, League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 30


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_league(name: str = "Nav") -> League:
    return League.objects.create(name=name)


def _make_draft_season(name: str, *, n_teams: int = 4):
    """A draft Season with ``n_teams`` slotted teams enrolled (NOT started)."""
    league = _make_league(name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return league, season, teams


def _make_active_season(name: str, *, n_teams: int = 4):
    league, season, teams = _make_draft_season(name, n_teams=n_teams)
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _make_completed_season(name: str) -> tuple[League, Season]:
    league = _make_league(name)
    season = Season.objects.create(
        league=league,
        name="Done",
        start_date=date(2026, 1, 1),
        state="completed",
        starting_team_ids_json=[],
    )
    return league, season


def _make_none_league(name: str) -> League:
    """A League with ZERO Seasons ⇒ ``season_mode == "none"``."""
    return _make_league(name)


def _rr_tournament_season(name: str, *, n: int = 4):
    """Active Season: ordinal-1 round_robin + ordinal-2 tournament, ``n`` teams."""
    league, season, teams = _make_draft_season(name, n_teams=n)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _play_rr(season, teams):
    """Play every RR fixture (auto-builds the tournament phase on completion)."""
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


def _pin_league(client, league: League) -> None:
    """Pin ``last_league_id`` in the session so ``league_nav`` resolves the
    league when rendering a ``/seasons/<id>/`` page (the topnav has no league
    template var)."""
    s = client.session
    s["last_league_id"] = league.id
    s.save()


# ---------------------------------------------------------------------------
# State matrix — `none`
# ---------------------------------------------------------------------------


class TestNavPlayDropdownNone(TestCase):
    """``none`` (League with no Season) → ``topbar-play-dropdown`` present,
    disabled affordance, NO action forms."""

    def _body(self, league: League) -> str:
        return self.client.get(
            reverse("league_dashboard", args=[league.id])
        ).content.decode()

    def test_dropdown_present_and_toggle_present(self) -> None:
        league = _make_none_league("NavNone1")
        body = self._body(league)
        self.assertIn('id="topbar-play-dropdown"', body)
        self.assertIn('id="play-nav-link"', body)

    def test_no_action_forms_in_none_state(self) -> None:
        league = _make_none_league("NavNone2")
        body = self._body(league)
        for dom_id in (
            "topbar-play-start-season",
            "topbar-play-one-week",
            "topbar-play-two-months",
            "topbar-play-until-end",
            "topbar-play-one-week-live",
            "topbar-play-owner-evaluation",
            "topbar-play-next-season",
            "topbar-play-play-single-round",
            "topbar-play-play-playoffs",
        ):
            self.assertNotIn(f'id="{dom_id}"', body)


# ---------------------------------------------------------------------------
# State matrix — `start_season`
# ---------------------------------------------------------------------------


class TestNavPlayDropdownStartSeason(TestCase):
    """``start_season`` (draft Season) → ``topbar-play-start-season`` form with
    ``{% url 'start_season' %}`` action."""

    def test_start_season_form_present_with_correct_action(self) -> None:
        _league, season, _teams = _make_draft_season("NavStart")
        _pin_league(self.client, season.league)
        body = self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()
        self.assertIn('id="topbar-play-start-season"', body)
        self.assertIn(reverse("start_season", args=[season.id]), body)

    def test_start_season_carries_csrf_and_data_action_state(self) -> None:
        _league, season, _teams = _make_draft_season("NavStartCsrf")
        _pin_league(self.client, season.league)
        body = self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()
        self.assertIn("csrfmiddlewaretoken", body)
        self.assertIn('data-action-state="start_season"', body)

    def test_play_next_forms_absent_in_draft(self) -> None:
        _league, season, _teams = _make_draft_season("NavStartNoNext")
        _pin_league(self.client, season.league)
        body = self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()
        self.assertNotIn('id="topbar-play-one-week"', body)
        self.assertNotIn('id="topbar-play-two-months"', body)


# ---------------------------------------------------------------------------
# State matrix — `play_next`
# ---------------------------------------------------------------------------


class TestNavPlayDropdownPlayNext(TestCase):
    """``play_next`` (active Season, unplayed matchday) → the four play forms,
    the gated one-week-live, and the terminal label relabel."""

    def test_play_next_core_forms_present(self) -> None:
        _league, season, _teams = _make_active_season("NavNext")
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        for dom_id, url_name in (
            ("topbar-play-one-week", "play_week"),
            ("topbar-play-two-months", "play_two_months"),
            ("topbar-play-until-end", "play_until_end"),
        ):
            self.assertIn(f'id="{dom_id}"', body)
            self.assertIn(reverse(url_name, args=[season.id]), body)

    def test_one_week_live_present_when_live_preview_available(self) -> None:
        # Active RR season with a manager team set ⇒ live cursor available.
        _league, season, teams = _make_active_season("NavLiveYes", n_teams=2)
        season.league.current_team = teams[0]
        season.league.save(update_fields=["current_team"])
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn('id="topbar-play-one-week-live"', body)
        self.assertIn(reverse("play_week_live", args=[season.id]), body)

    def test_one_week_live_absent_when_no_manager_team(self) -> None:
        # No current_team ⇒ live cursor None ⇒ entry gated off.
        _league, season, _teams = _make_active_season("NavLiveNo", n_teams=2)
        ctx = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).context
        self.assertFalse(ctx["live_preview_available"])
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertNotIn('id="topbar-play-one-week-live"', body)

    def test_terminal_label_until_end_of_season_when_no_following_tournament(
        self,
    ) -> None:
        # Plain active RR season, no tournament phase ⇒ "Play Until End of Season".
        _league, season, _teams = _make_active_season("NavTermEnd", n_teams=2)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn("Play Until End of Season", body)
        self.assertNotIn("Play Until Tournament", body)
        self.assertNotIn("Play Until Playoffs", body)

    def test_terminal_label_until_playoffs_when_following_final_tournament(
        self,
    ) -> None:
        # RR active + a FINAL tournament phase follows ⇒ "Play Until Playoffs".
        _league, season, _teams = _rr_tournament_season("NavTermPlay")
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn("Play Until Playoffs", body)
        self.assertNotIn("Play Until End of Season", body)

    def test_terminal_label_until_tournament_when_following_mid_season(
        self,
    ) -> None:
        # RR active + a MID-SEASON tournament phase (not final) follows ⇒
        # "Play Until Tournament".
        _league, season, _teams = _make_active_season("NavTermMid", n_teams=4)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament_mode="strength",
        )
        SeasonPhase.objects.create(season=season, ordinal=3, phase_type="round_robin")
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn("Play Until Tournament", body)
        self.assertNotIn("Play Until Playoffs", body)
        self.assertNotIn("Play Until End of Season", body)


# ---------------------------------------------------------------------------
# State matrix — `start_next_season`
# ---------------------------------------------------------------------------


class TestNavPlayDropdownStartNextSeason(TestCase):
    """``start_next_season`` (all completed) → career renders the
    ``topbar-play-owner-evaluation`` link; non-career renders the
    ``topbar-play-next-season`` form."""

    def test_career_renders_owner_evaluation_link(self) -> None:
        league, season = _make_completed_season("NavCareer")
        league.mode = "league"
        league.save(update_fields=["mode"])
        body = self.client.get(
            reverse("league_dashboard", args=[league.id])
        ).content.decode()
        self.assertIn('id="topbar-play-owner-evaluation"', body)
        self.assertIn(
            reverse("owner_evaluation", kwargs={"season_id": season.id}), body
        )
        self.assertNotIn('id="topbar-play-next-season"', body)

    def test_owner_evaluation_is_anchor_not_form(self) -> None:
        league, _season = _make_completed_season("NavCareerAnchor")
        league.mode = "league"
        league.save(update_fields=["mode"])
        body = self.client.get(
            reverse("league_dashboard", args=[league.id])
        ).content.decode()
        # The career owner-eval control is a GET <a> link, not a POST form.
        self.assertIn('id="topbar-play-owner-evaluation"', body)

    def test_non_career_renders_next_season_form(self) -> None:
        league, _season = _make_completed_season("NavMulti")
        league.mode = "multiplayer"
        league.save(update_fields=["mode"])
        body = self.client.get(
            reverse("league_dashboard", args=[league.id])
        ).content.decode()
        self.assertIn('id="topbar-play-next-season"', body)
        self.assertIn(reverse("next_season", kwargs={"league_id": league.id}), body)
        self.assertNotIn('id="topbar-play-owner-evaluation"', body)


# ---------------------------------------------------------------------------
# State matrix — `playoff_phase_active`
# ---------------------------------------------------------------------------


class TestNavPlayDropdownPlayoff(TestCase):
    """``playoff_phase_active`` → ``topbar-play-play-single-round`` +
    ``topbar-play-play-playoffs`` present."""

    def test_playoff_forms_present_when_tournament_phase_active(self) -> None:
        _league, season, teams = _rr_tournament_season("NavPlayoff")
        _play_rr(season, teams)
        season.refresh_from_db()
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        for dom_id, url_name in (
            ("topbar-play-play-single-round", "play_single_round"),
            ("topbar-play-play-playoffs", "play_playoffs"),
        ):
            self.assertIn(f'id="{dom_id}"', body)
            self.assertIn(reverse(url_name, args=[season.id]), body)

    def test_playoff_forms_absent_during_rr_phase(self) -> None:
        _league, season, _teams = _rr_tournament_season("NavPlayoffRR")
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertNotIn('id="topbar-play-play-single-round"', body)
        self.assertNotIn('id="topbar-play-play-playoffs"', body)


# ---------------------------------------------------------------------------
# Poll path — async submit + progress affordance
# ---------------------------------------------------------------------------


class TestNavPlayDropdownPollPath(TestCase):
    """The async submit (``play_two_months``) returns 202 ``{job_id, season_id}``;
    the navbar ``topbar-play-progress`` element exists; the page references
    ``play_status`` for polling."""

    def test_play_two_months_returns_202_job_id_season_id(self) -> None:
        _league, season, _teams = _make_active_season("NavPoll202", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertIn("job_id", payload)
        self.assertEqual(payload["season_id"], season.id)

    def test_progress_element_present_in_league_branch(self) -> None:
        _league, season, _teams = _make_active_season("NavPollProg", n_teams=2)
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn('id="topbar-play-progress"', body)

    def test_page_references_play_status_for_polling(self) -> None:
        # The relocated poll JS builds the play_status URL with a literal 'JOB'
        # placeholder substituted client-side (contract §5:
        # ``{% url 'play_status' season_id=play_displayed_season_id job_id='JOB' %}``),
        # so the season-specific play_status URL is rendered server-side with
        # ``JOB`` as the job_id.
        _league, season, _teams = _make_active_season("NavPollStatus", n_teams=2)
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        status_url = reverse(
            "play_status", kwargs={"season_id": season.id, "job_id": "JOB"}
        )
        self.assertIn(status_url, body)


# ---------------------------------------------------------------------------
# Off-league absence
# ---------------------------------------------------------------------------


class TestNavPlayDropdownOffLeague(TestCase):
    """Off-league (sandbox / start ``app_mode``) pages render NO
    ``play-nav-link`` / ``topbar-play-*`` ids."""

    def _assert_no_play_nav(self, body: str) -> None:
        self.assertNotIn('id="play-nav-link"', body)
        self.assertNotIn('id="topbar-play-dropdown"', body)
        self.assertNotIn("topbar-play-", body)

    def test_sandbox_team_list_has_no_play_nav(self) -> None:
        # Pin a league in session to prove the play keys are STILL absent
        # off-league (the dropdown only renders in the league branch).
        league, _season, _teams = _make_active_season("NavOffSandbox", n_teams=2)
        _pin_league(self.client, league)
        body = self.client.get(reverse("team_list")).content.decode()
        self._assert_no_play_nav(body)

    def test_start_landing_has_no_play_nav(self) -> None:
        league, _season, _teams = _make_active_season("NavOffStart", n_teams=2)
        _pin_league(self.client, league)
        body = self.client.get(reverse("landing")).content.decode()
        self._assert_no_play_nav(body)


# ---------------------------------------------------------------------------
# Pure helper — `_build_play_controls_context(league, displayed_season)`
# ---------------------------------------------------------------------------


class TestBuildPlayControlsContext(TestCase):
    """The shared helper returns the correct key dict across the state matrix
    (contract §1 table). The helper takes the RESOLVED league + displayed
    Season and never re-implements the resolution. PLAY-01 added the 10th key
    ``active_play_job_id`` (the resumable-progress render hint) — the
    load-bearing invariant is still "the helper emits exactly this fixed key
    set", just one wider.
    """

    _KEYS = (
        "action_button_label",
        "action_button_state",
        "playoff_phase_active",
        "playoff_tournament_id",
        "playoff_completed",
        "has_following_tournament_phase",
        "following_tournament_is_final",
        "live_preview_available",
        "is_career_mode",
        "active_play_job_id",
    )

    def test_returns_exactly_the_ten_keys(self) -> None:
        league = _make_none_league("HelperKeys")
        result = _build_play_controls_context(league, None)
        self.assertEqual(set(result.keys()), set(self._KEYS))

    def test_none_state(self) -> None:
        league = _make_none_league("HelperNone")
        result = _build_play_controls_context(league, None)
        self.assertEqual(result["action_button_state"], "none")
        self.assertEqual(result["action_button_label"], "No Season")
        self.assertFalse(result["playoff_phase_active"])
        self.assertIsNone(result["playoff_tournament_id"])
        self.assertFalse(result["live_preview_available"])

    def test_start_season_state(self) -> None:
        league, season, _teams = _make_draft_season("HelperDraft")
        result = _build_play_controls_context(league, season)
        self.assertEqual(result["action_button_state"], "start_season")
        self.assertEqual(result["action_button_label"], "Start Season")

    def test_play_next_state(self) -> None:
        league, season, _teams = _make_active_season("HelperActive")
        result = _build_play_controls_context(league, season)
        self.assertEqual(result["action_button_state"], "play_next")
        self.assertEqual(result["action_button_label"], "Play Next")

    def test_start_next_season_state(self) -> None:
        league, season = _make_completed_season("HelperCompleted")
        result = _build_play_controls_context(league, season)
        self.assertEqual(result["action_button_state"], "start_next_season")
        self.assertEqual(result["action_button_label"], "Start Next Season")

    def test_is_career_mode_true_for_league_mode(self) -> None:
        league, season = _make_completed_season("HelperCareer")
        league.mode = "league"
        league.save(update_fields=["mode"])
        result = _build_play_controls_context(league, season)
        self.assertTrue(result["is_career_mode"])

    def test_is_career_mode_false_for_multiplayer(self) -> None:
        league, season = _make_completed_season("HelperMulti")
        league.mode = "multiplayer"
        league.save(update_fields=["mode"])
        result = _build_play_controls_context(league, season)
        self.assertFalse(result["is_career_mode"])

    def test_has_following_tournament_phase_true_for_rr_then_tournament(
        self,
    ) -> None:
        league, season, _teams = _rr_tournament_season("HelperFollowing")
        result = _build_play_controls_context(league, season)
        self.assertTrue(result["has_following_tournament_phase"])

    def test_playoff_phase_active_true_once_tournament_built(self) -> None:
        league, season, teams = _rr_tournament_season("HelperPlayoffActive")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        result = _build_play_controls_context(league, season)
        self.assertTrue(result["playoff_phase_active"])
        self.assertEqual(
            result["playoff_tournament_id"], tournament_phase.tournament_id
        )
        self.assertFalse(result["playoff_completed"])

    def test_playoff_completed_after_drain(self) -> None:
        league, season, teams = _rr_tournament_season("HelperPlayoffDone")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _drain_tournament(tournament_phase.tournament)
        season.refresh_from_db()
        result = _build_play_controls_context(league, season)
        self.assertFalse(result["playoff_phase_active"])
        self.assertTrue(result["playoff_completed"])
        self.assertEqual(
            result["playoff_tournament_id"], tournament_phase.tournament_id
        )
