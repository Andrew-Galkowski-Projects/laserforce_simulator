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


def _entries_for_phase(response, phase) -> list:
    """CONF-04 — the Playoffs-screen bracket entries of ONE phase.

    Since ADR-0037 the screen also renders the DERIVED Worlds phase's entry, so
    a bare ``context["brackets"]`` is no longer the same list as the
    qualification phase's brackets. Filtering by phase keeps the CONF-02 /
    CONF-03 assertions below pinned to exactly what they were written to pin
    — it narrows the scope of each guard rather than weakening it.
    """
    return [
        entry for entry in response.context["brackets"] if entry["phase"].pk == phase.pk
    ]


def _worlds_entry(response):
    """CONF-04 — the single Worlds bracket entry, or ``None``."""
    for entry in response.context["brackets"]:
        if entry["phase"].tournament_mode == "worlds":
            return entry
    return None


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
        self.assertEqual(len(_entries_for_phase(self.response, self.phase)), 2)

    def test_the_worlds_phase_adds_exactly_one_further_entry(self) -> None:
        # CONF-04 — the derived Worlds phase renders its own (pending) entry,
        # so the screen now carries 2 + 1. Nothing else is added.
        self.assertEqual(len(self.response.context["brackets"]), 3)
        self.assertIsNotNone(_worlds_entry(self.response))

    def test_entries_are_in_conference_ordinal_order(self) -> None:
        brackets = _entries_for_phase(self.response, self.phase)
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
        brackets = _entries_for_phase(self.response, self.phase)
        self.assertEqual(
            [entry["key"] for entry in brackets],
            [
                f"{self.phase.ordinal}-{self.conferences[0].ordinal}",
                f"{self.phase.ordinal}-{self.conferences[1].ordinal}",
            ],
        )

    def test_keys_do_not_collide(self) -> None:
        keys = [entry["key"] for entry in _entries_for_phase(self.response, self.phase)]
        self.assertEqual(len(set(keys)), 2)

    def test_neither_entry_is_pending(self) -> None:
        # CONF-04 — scoped to this phase. The derived Worlds phase's entry IS
        # pending here (its bracket only builds once qualification completes),
        # which is asserted separately below.
        for entry in _entries_for_phase(self.response, self.phase):
            self.assertFalse(entry["pending"])
            self.assertTrue(entry["rounds"])

    def test_the_worlds_entry_is_pending_while_qualification_is_unfinished(
        self,
    ) -> None:
        entry = _worlds_entry(self.response)
        self.assertIsNotNone(entry)
        self.assertTrue(entry["pending"])
        self.assertEqual(entry["rounds"], [])

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


# ===========================================================================
# CONF-03 — the Last-chance bracket section and the Worlds qualification panel
# ===========================================================================
#
# Seam contract ``.claude/worktrees/conf-03-seam-contract.md`` §7 + §9.4 items
# 30-33; rationale in
# [ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md).
#
# A Conference of 9+ Teams appends a SECOND ``brackets`` entry for its
# Last-chance qualifier, discriminated by the locked ``-lc`` key suffix and
# labelled by the new ``stage`` / ``stage_label`` keys. Every CONF-02 key
# (``"<phase ordinal>-<conference ordinal>"``) and every 0/1-Conference key
# (``str(phase.ordinal)``) is therefore byte-identical, and ``stage_label`` is
# empty for every non-Last-chance bracket so NO new element renders on a
# CONF-02 Season — the two regression pins below are what prove that.
#
# The Worlds panel is a read-only table driven by the single new context key
# ``worlds_qualifiers``. ``{% if worlds_qualifiers %}`` IS the readiness test:
# ``[]`` means the panel is absent entirely — no section, no heading, no
# empty-state text, no new DOM id.
#
# Fixtures are hand-built (no simulation, contract §9.1): the round-robin is a
# deterministic set of completed Match + GameRound rows and brackets are
# drained by STAMPING the persisted rows. Appended as NEW classes; no existing
# class above is modified.


from matches.tests.test_regional_playoffs import (  # noqa: E402
    _conf_season as _conf03_conf_season,
    _stamp_bracket_completed as _conf03_stamp_bracket_completed,
)


def _conf03_built_season(prefix: str, sizes, **kwargs):
    """``_built_regional_season`` under a CONF-03-local alias."""
    return _conf02_built_regional_season(prefix, list(sizes), **kwargs)


def _conf03_last_chance_row(phase, conference=None):
    rows = phase.regional_tournaments.filter(qualifier_stage="last_chance")
    if conference is not None:
        rows = rows.filter(conference=conference)
    return rows.first()


def _conf03_regional_row(phase, conference):
    """The §3.2 read rule — a Regional playoff is Conference-scoped and NOT a
    Last-chance row (never a positive test on ``"regional_playoff"``)."""
    return (
        phase.regional_tournaments.filter(conference=conference)
        .exclude(qualifier_stage="last_chance")
        .first()
    )


def _conf03_entry_by_key(response, key: str):
    for entry in response.context["brackets"]:
        if entry["key"] == key:
            return entry
    return None


# ---------------------------------------------------------------------------
# 30 + 32. The N+1 labelled sections and the unseeded pending alert
# ---------------------------------------------------------------------------


class TestLeaguePlayoffsLastChanceSection(TestCase):
    """A 9-Team Conference + a 4-Team one yields THREE bracket entries: two
    Regional playoffs and one Last-chance qualifier."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf03_built_season("LcScreen", (9, 4))
        self.big, self.small = self.conferences
        self.league = self.season.league
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.league.id})
        )
        self.lc_key = f"{self.phase.ordinal}-{self.big.ordinal}-lc"

    def test_three_bracket_entries_for_one_phase(self) -> None:
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(len(_entries_for_phase(self.response, self.phase)), 3)

    def test_the_last_chance_entry_sorts_after_its_regional_sibling(self) -> None:
        keys = [entry["key"] for entry in _entries_for_phase(self.response, self.phase)]
        self.assertEqual(
            keys,
            [
                f"{self.phase.ordinal}-{self.big.ordinal}",
                self.lc_key,
                f"{self.phase.ordinal}-{self.small.ordinal}",
            ],
        )

    def test_the_last_chance_entry_carries_its_conference(self) -> None:
        entry = _conf03_entry_by_key(self.response, self.lc_key)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["conference"].id, self.big.id)
        self.assertEqual(entry["tournament"].conference_id, self.big.id)

    def test_the_last_chance_entry_carries_stage_and_stage_label(self) -> None:
        entry = _conf03_entry_by_key(self.response, self.lc_key)
        self.assertEqual(entry["stage"], "last_chance")
        self.assertEqual(entry["stage_label"], "Last Chance Qualifier")

    def test_regional_entries_carry_the_regional_stage_and_no_label(self) -> None:
        for conference in self.conferences:
            key = f"{self.phase.ordinal}-{conference.ordinal}"
            entry = _conf03_entry_by_key(self.response, key)
            self.assertEqual(entry["stage"], "regional_playoff")
            self.assertEqual(
                entry["stage_label"],
                "",
                "an empty stage_label is what keeps CONF-02 markup unchanged",
            )

    def test_the_stage_badge_renders_for_the_last_chance_bracket(self) -> None:
        self.assertContains(self.response, f'id="league-playoffs-stage-{self.lc_key}"')
        self.assertContains(self.response, "Last Chance Qualifier")

    def test_no_stage_badge_renders_for_a_regional_bracket(self) -> None:
        for conference in self.conferences:
            self.assertNotContains(
                self.response,
                f'id="league-playoffs-stage-{self.phase.ordinal}-'
                f'{conference.ordinal}"',
            )

    def test_the_last_chance_section_has_its_own_dom_id(self) -> None:
        self.assertContains(self.response, f'id="league-playoffs-phase-{self.lc_key}"')

    def test_the_keys_do_not_collide(self) -> None:
        keys = [entry["key"] for entry in _entries_for_phase(self.response, self.phase)]
        self.assertEqual(len(set(keys)), 3)

    # -- 32. the unseeded Last-chance section renders the pending alert -----

    def test_the_unseeded_entry_is_pending_with_no_rounds(self) -> None:
        entry = _conf03_entry_by_key(self.response, self.lc_key)
        self.assertIs(entry["pending"], True)
        self.assertEqual(entry["rounds"], [])
        self.assertIsNone(entry["champion"])

    def test_no_empty_bracket_grid_is_rendered_for_the_unseeded_entry(self) -> None:
        self.assertNotContains(
            self.response, f'id="league-playoffs-bracket-{self.lc_key}"'
        )

    def test_the_pending_alert_carries_the_last_chance_message(self) -> None:
        self.assertContains(self.response, "The field is not set yet")

    def test_the_built_regional_entries_are_not_pending(self) -> None:
        for conference in self.conferences:
            key = f"{self.phase.ordinal}-{conference.ordinal}"
            entry = _conf03_entry_by_key(self.response, key)
            self.assertIs(entry["pending"], False)
            self.assertTrue(entry["rounds"])

    def test_a_seeded_last_chance_bracket_stops_being_pending(self) -> None:
        _conf03_stamp_bracket_completed(
            _conf03_regional_row(self.phase, self.big), self.groups[0][0]
        )
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 1)
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.league.id})
        )
        entry = _conf03_entry_by_key(response, self.lc_key)
        self.assertIs(entry["pending"], False)
        self.assertTrue(entry["rounds"])
        self.assertContains(response, f'id="league-playoffs-bracket-{self.lc_key}"')
        # The badge stays put once the bracket is playable.
        self.assertContains(response, f'id="league-playoffs-stage-{self.lc_key}"')


# ---------------------------------------------------------------------------
# 31. CONF-02 DOM ids are byte-identical
# ---------------------------------------------------------------------------


class TestLeaguePlayoffsConf02DomIdsUnchanged(TestCase):
    """Invariants 2 + 3 — a 2-Conference Season with no 9+-Team Conference
    renders exactly the CONF-02 markup: no ``-lc`` key, no stage element."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf03_built_season("LcNoLc", (5, 8))
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )

    def test_two_entries_only(self) -> None:
        self.assertEqual(len(_entries_for_phase(self.response, self.phase)), 2)

    def test_keys_are_exactly_phase_ordinal_dash_conference_ordinal(self) -> None:
        self.assertEqual(
            [entry["key"] for entry in _entries_for_phase(self.response, self.phase)],
            [
                f"{self.phase.ordinal}-{self.conferences[0].ordinal}",
                f"{self.phase.ordinal}-{self.conferences[1].ordinal}",
            ],
        )

    def test_no_lc_substring_anywhere_in_the_response(self) -> None:
        self.assertNotContains(self.response, "-lc")

    def test_no_stage_element_is_present(self) -> None:
        # CONF-04 — narrowed from "no stage element anywhere" to "no stage
        # element on a CONF-02 REGIONAL key". The invariant this pins is that a
        # regional bracket still renders no badge; the derived Worlds entry has
        # its own badge on its own key, which cannot collide with these.
        for conference in self.conferences:
            key = f"{self.phase.ordinal}-{conference.ordinal}"
            self.assertNotContains(self.response, f'id="league-playoffs-stage-{key}"')

    def test_every_stage_label_is_empty(self) -> None:
        # CONF-04 — scoped to this phase's entries; the Worlds entry carries
        # "Worlds" by design (ADR-0037).
        for entry in _entries_for_phase(self.response, self.phase):
            self.assertEqual(entry["stage_label"], "")

    def test_the_conf02_section_and_bracket_ids_still_render(self) -> None:
        for conference in self.conferences:
            key = f"{self.phase.ordinal}-{conference.ordinal}"
            self.assertContains(self.response, f'id="league-playoffs-phase-{key}"')
            self.assertContains(self.response, f'id="league-playoffs-bracket-{key}"')
            self.assertContains(self.response, f'id="league-playoffs-conference-{key}"')

    def test_the_existing_pending_message_is_verbatim_on_an_unbuilt_phase(
        self,
    ) -> None:
        season, _conferences, _groups, _rr, phase = _conf03_conf_season(
            "LcPendMsg", [5, 5]
        )
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": season.league_id})
        )
        self.assertContains(response, "The bracket is not seeded yet")
        self.assertNotContains(response, "The field is not set yet")
        self.assertEqual(response.context["brackets"][0]["stage"], "")
        self.assertEqual(response.context["brackets"][0]["stage_label"], "")


class TestLeaguePlayoffsZeroConferenceStageKeysUnchanged(TestCase):
    """Invariant 1 — the 0-Conference Season keeps the bare-ordinal key and
    gains only the two empty new keys."""

    def setUp(self) -> None:
        self.season, self.teams, self.phase = _conf02_built_flat_season("LcFlat")
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )

    def test_key_is_the_bare_phase_ordinal(self) -> None:
        entry = self.response.context["brackets"][0]
        self.assertEqual(entry["key"], str(self.phase.ordinal))

    def test_stage_and_stage_label_are_empty(self) -> None:
        entry = self.response.context["brackets"][0]
        self.assertEqual(entry["stage"], "")
        self.assertEqual(entry["stage_label"], "")

    def test_no_stage_element_and_no_lc_suffix(self) -> None:
        self.assertNotContains(self.response, "league-playoffs-stage-")
        self.assertNotContains(self.response, "-lc")


# ---------------------------------------------------------------------------
# 33. The Worlds qualification panel
# ---------------------------------------------------------------------------


class TestLeaguePlayoffsWorldsPanelAbsent(TestCase):
    """``{% if worlds_qualifiers %}`` is the readiness test — an empty list
    renders NO section, NO heading and NO empty-state text."""

    def test_absent_for_a_zero_conference_season(self) -> None:
        season, _teams, _phase = _conf02_built_flat_season("LcWorldsFlat")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": season.league_id})
        )
        self.assertEqual(response.context["worlds_qualifiers"], [])
        self.assertNotContains(response, 'id="league-playoffs-worlds"')
        self.assertNotContains(response, "Worlds qualification")

    def test_absent_for_a_one_conference_season(self) -> None:
        season, _conferences, groups, rr_phase, phase = _conf03_conf_season(
            "LcWorldsOne", [9]
        )
        _conf02_hand_play_rr(season, rr_phase, _conf02_ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": season.league_id})
        )
        self.assertEqual(response.context["worlds_qualifiers"], [])
        self.assertNotContains(response, 'id="league-playoffs-worlds"')

    def test_absent_for_an_incomplete_multi_conference_season(self) -> None:
        season, _conferences, _groups, _rr, _phase = _conf03_built_season(
            "LcWorldsMid", (9, 4)
        )
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": season.league_id})
        )
        self.assertEqual(response.context["worlds_qualifiers"], [])
        self.assertNotContains(response, 'id="league-playoffs-worlds"')
        self.assertNotContains(response, 'id="league-playoffs-worlds-table"')


class TestLeaguePlayoffsWorldsPanelPresent(TestCase):
    """Once every bracket of the final tournament phase has a champion the
    panel renders exactly M rows with the locked DOM ids of §7.3."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf03_built_season("LcWorldsDone", (9, 4), cut=4)
        self.big, self.small = self.conferences
        # Rank 1 wins each Regional playoff, so the tier-2 slots are rank 2 and
        # the Last-chance field starts at rank 3.
        for conference, group in zip(self.conferences, self.groups):
            _conf03_stamp_bracket_completed(
                _conf03_regional_row(self.phase, conference), group[0]
            )
        self.season.seed_pending_last_chance_brackets(self.phase)
        _conf03_stamp_bracket_completed(
            _conf03_last_chance_row(self.phase, self.big), self.groups[0][2]
        )
        self.response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        self.field = self.response.context["worlds_qualifiers"]

    def test_the_context_key_holds_the_ordered_field(self) -> None:
        # 3 from the 9-Team Conference + 1 from the 4-Team one.
        self.assertEqual(len(self.field), 4)
        self.assertEqual([q.seed for q in self.field], [1, 2, 3, 4])

    def test_the_panel_and_table_render(self) -> None:
        self.assertContains(self.response, 'id="league-playoffs-worlds"')
        self.assertContains(self.response, 'id="league-playoffs-worlds-table"')
        self.assertContains(self.response, "Worlds qualification")

    def test_one_row_per_qualifier_with_the_locked_ids(self) -> None:
        for seed in range(1, len(self.field) + 1):
            self.assertContains(
                self.response, f'id="league-playoffs-worlds-row-{seed}"'
            )
            self.assertContains(
                self.response, f'id="league-playoffs-worlds-team-{seed}"'
            )
            self.assertContains(
                self.response, f'id="league-playoffs-worlds-conference-{seed}"'
            )
            self.assertContains(
                self.response, f'id="league-playoffs-worlds-provenance-{seed}"'
            )

    def test_no_extra_row_beyond_the_field_size(self) -> None:
        self.assertNotContains(
            self.response,
            f'id="league-playoffs-worlds-row-{len(self.field) + 1}"',
        )

    def test_every_team_name_is_rendered(self) -> None:
        for qualifier in self.field:
            self.assertContains(self.response, qualifier.team_name)

    def test_every_conference_name_is_rendered(self) -> None:
        for conference in self.conferences:
            self.assertContains(self.response, conference.name)

    def test_every_provenance_label_is_rendered(self) -> None:
        for qualifier in self.field:
            self.assertNotEqual(qualifier.provenance_label, "")
            self.assertContains(self.response, qualifier.provenance_label)

    def test_the_three_provenances_are_all_present(self) -> None:
        labels = {q.provenance_label for q in self.field}
        self.assertEqual(
            labels,
            {"Conference champion", "Regular season", "Last-chance qualifier"},
        )

    def test_the_last_chance_winner_is_the_last_seed(self) -> None:
        last = self.field[-1]
        self.assertEqual(last.team_id, self.groups[0][2].id)
        self.assertEqual(last.provenance_label, "Last-chance qualifier")

    def test_the_panel_renders_alongside_the_bracket_sections(self) -> None:
        # The panel lives INSIDE the ``{% else %}`` of the no-brackets guard.
        # CONF-04 — 3 qualification brackets + the derived Worlds entry.
        self.assertEqual(len(_entries_for_phase(self.response, self.phase)), 3)
        self.assertEqual(len(self.response.context["brackets"]), 4)
        self.assertNotContains(self.response, 'id="league-playoffs-empty-notice"')
