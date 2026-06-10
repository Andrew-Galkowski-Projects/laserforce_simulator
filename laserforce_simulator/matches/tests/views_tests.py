import json

import pytest
from unittest.mock import patch
from django.test import Client
from django.urls import reverse, NoReverseMatch

from matches.models import GameEvent, GameRound, Match, PlayerRoundState
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestSingleRoundRemoval:
    def test_single_round_detail_url_removed(self):
        """The legacy /matches/round/<id>/ URL should not exist."""
        with pytest.raises(NoReverseMatch):
            reverse("single_round_detail", kwargs={"round_id": 1})

    def test_create_single_round_always_creates_game_round(self):
        """POST to create_single_round must always produce a GameRound."""
        red, _ = make_team_with_slots("Red")
        blue, _ = make_team_with_slots("Blue")
        client = Client()
        before = GameRound.objects.count()
        # SIM-09: view now drives BatchSimulator (was RBS); ROUND_TICKS=40
        # keeps the integration test fast.
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            response = client.post(
                reverse("create_single_round"),
                {"team_red": red.id, "team_blue": blue.id},
            )
        assert GameRound.objects.count() == before + 1
        assert response.status_code == 302

    def test_create_single_round_same_team_returns_form(self):
        """Submitting the same team for both sides must return the form (200), no GameRound."""
        team, _ = make_team_with_slots("SameTeam")
        client = Client()
        before = GameRound.objects.count()
        response = client.post(
            reverse("create_single_round"),
            {"team_red": team.id, "team_blue": team.id},
        )
        assert response.status_code == 200
        assert GameRound.objects.count() == before

    def test_match_list_has_no_single_rounds_context(self):
        """match_list view must not pass single_rounds to the template context."""
        client = Client()
        response = client.get(reverse("match_list"))
        assert response.status_code == 200
        assert "single_rounds" not in response.context

    def test_team_match_history_has_no_single_rounds_context(self):
        """team_match_history view must not pass single_rounds to the template context."""
        team, _ = make_team_with_slots("History")
        client = Client()
        response = client.get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.status_code == 200
        assert "single_rounds" not in response.context


@pytest.mark.django_db
class TestTeamMatchHistoryRoster:
    """The team history page surfaces a role-breakdown Roster + a Wins/Losses
    chart derived from PlayerRoundState — so DRAW teams (whose borrowed players
    keep Player.team = Free Agents and so never appear in team.players) still
    show a roster.
    """

    def test_draw_team_roster_built_from_player_round_states(self):
        from teams.models import Team, Player, get_free_agents_team
        from matches.models import TournamentPlayerEntry

        # A draw team owns no players directly; the pool lives on Free Agents.
        draw_team = Team.objects.create(name="Team Turkey So Ez", is_draw_team=True)
        opponent, _ = make_team_with_slots("Opp")
        pool = get_free_agents_team()
        drawn_player = Player.objects.create(team=pool, name="Odin's Fist")

        # Minimal Tournament so the entry FK is satisfiable.
        from matches.models import Tournament

        tourney = Tournament.objects.create(name="Loveland Random Draw 2026")
        TournamentPlayerEntry.objects.create(
            tournament=tourney, player=drawn_player, drawn_team=draw_team
        )

        # The drawn player plays commander in one round and scout in another,
        # wearing red both times.
        gr1 = _make_round(draw_team, opponent, round_number=1)
        gr2 = _make_round(draw_team, opponent, round_number=2)
        _make_state(
            gr1, drawn_player, team_color="red", role="commander", points_scored=8000
        )
        _make_state(
            gr2, drawn_player, team_color="red", role="scout", points_scored=5000
        )

        client = Client()
        response = client.get(
            reverse("team_match_history", kwargs={"team_id": draw_team.id})
        )
        assert response.status_code == 200

        roster_rows = response.context["roster_rows"]
        assert len(roster_rows) == 1
        row = roster_rows[0]
        assert row["player"].id == drawn_player.id
        assert row["games_played"] == 2
        assert row["is_merc"] is False  # rostered via TournamentPlayerEntry

        # roles list follows role_columns order: commander, heavy, scout, ammo, medic
        commander_cell, _heavy, scout_cell, _ammo, _medic = row["roles"]
        assert commander_cell["games"] == 1
        assert commander_cell["avg_score"] == 8000
        assert scout_cell["games"] == 1
        assert scout_cell["avg_score"] == 5000

        body = response.content.decode()
        assert 'id="roster-table"' in body
        assert "Odin&#x27;s Fist" in body or "Odin's Fist" in body
        assert 'id="winloss-chart"' in body

    def test_player_on_opposite_color_not_counted(self):
        """A PlayerRoundState whose team_color != the team's colour that round
        belongs to the opponent and must be excluded from the roster."""
        from teams.models import Player

        team, players = make_team_with_slots("Mine")
        opponent, _ = make_team_with_slots("Other")
        ringer = Player.objects.create(team=opponent, name="Ringer")

        gr = _make_round(team, opponent, round_number=1)
        # team wears red; the ringer plays blue (the opponent's side)
        _make_state(gr, ringer, team_color="blue", role="commander", points_scored=999)

        client = Client()
        response = client.get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.status_code == 200
        roster_ids = {r["player"].id for r in response.context["roster_rows"]}
        assert ringer.id not in roster_ids


@pytest.mark.django_db
class TestTeamMatchHistoryScopeFilter:
    """Overall / Free-form / per-tournament toggles scope the whole page."""

    def _setup(self):
        from teams.models import Team
        from matches.models import Tournament, BracketNode, SeriesMatch

        team = Team.objects.create(name="Scoped")
        opp = Team.objects.create(name="Foe")
        freeform_match = Match.objects.create(team_red=team, team_blue=opp, winner=team)
        tourney_match = Match.objects.create(team_red=team, team_blue=opp, winner=opp)
        tourney = Tournament.objects.create(name="Spring Cup")
        node = BracketNode.objects.create(
            tournament=tourney, bracket_round=1, position=0
        )
        SeriesMatch.objects.create(node=node, match=tourney_match, game_number=1)
        return team, freeform_match, tourney_match, tourney

    def test_default_shows_all_scopes_selected(self):
        team, freeform_match, tourney_match, tourney = self._setup()
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.status_code == 200
        assert response.context["show_scope_filter"] is True
        assert response.context["overall_checked"] is True
        keys = {opt["key"] for opt in response.context["scope_options"]}
        assert keys == {"freeform", f"t{tourney.id}"}
        match_ids = {m.id for m in response.context["matches"]}
        assert match_ids == {freeform_match.id, tourney_match.id}

    def test_freeform_scope_excludes_tournament_match(self):
        team, freeform_match, tourney_match, tourney = self._setup()
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id}),
            {"applied": "1", "scope": "freeform"},
        )
        match_ids = {m.id for m in response.context["matches"]}
        assert match_ids == {freeform_match.id}
        assert response.context["overall_checked"] is False

    def test_single_tournament_scope_excludes_freeform(self):
        team, freeform_match, tourney_match, tourney = self._setup()
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id}),
            {"applied": "1", "scope": f"t{tourney.id}"},
        )
        match_ids = {m.id for m in response.context["matches"]}
        assert match_ids == {tourney_match.id}

    def test_all_off_shows_nothing(self):
        team, freeform_match, tourney_match, tourney = self._setup()
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id}),
            {"applied": "1"},
        )
        assert list(response.context["matches"]) == []
        assert list(response.context["detailed_rounds"]) == []
        assert response.context["has_any_games"] is True
        body = response.content.decode()
        assert "No games match the current filter" in body
        assert "No matches played yet" not in body

    def test_genuinely_empty_team_shows_no_history_message(self):
        from teams.models import Team

        team = Team.objects.create(name="Brand New")
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.context["has_any_games"] is False
        body = response.content.decode()
        assert "No matches played yet" in body
        assert "No games match the current filter" not in body

    def test_no_filter_panel_without_multiple_scopes(self):
        """A team with only free-form games has a single scope -> no panel."""
        from teams.models import Team

        team = Team.objects.create(name="Lonely")
        opp = Team.objects.create(name="Foe2")
        Match.objects.create(team_red=team, team_blue=opp, winner=team)
        response = Client().get(
            reverse("team_match_history", kwargs={"team_id": team.id})
        )
        assert response.context["show_scope_filter"] is False


@pytest.mark.django_db
class TestCreateMatchView:
    def test_create_match_same_team_returns_form(self):
        """Submitting the same team for both sides must return the form (200), no Match created."""
        team, _ = make_team_with_slots("SameMatch")
        client = Client()
        before = Match.objects.count()
        response = client.post(
            reverse("create_match"),
            {"team_red": team.id, "team_blue": team.id, "match_type": "friendly"},
        )
        assert response.status_code == 200
        assert Match.objects.count() == before


@pytest.mark.django_db
class TestSim08BatchSideAdvantageView:
    """SIM-08: the batch-simulate view exposes the physical-side advantage
    panel alongside the de-flipped team-position results.
    """

    def test_batch_simulate_renders_side_advantage(self):
        """POST drives a job through to ``complete`` and the partial
        aggregate surfaces ``side_advantage``.

        API-03 (ADR-0013) replaced the SIM-10 in-process daemon-thread Job
        dict with Celery tasks; under ``CELERY_TASK_ALWAYS_EAGER`` the task
        runs synchronously in the POST handler so the immediately-following
        status GET sees ``status=complete``. The SIM-08 contract (the
        side-advantage sub-dict is present and consistent) still holds.
        """
        red, _ = make_team_with_slots("Sim08ViewRed")
        blue, _ = make_team_with_slots("Sim08ViewBlue")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            post_resp = client.post(
                reverse("simulate_batch"),
                {"team_red": red.id, "team_blue": blue.id, "n": "10"},
            )

        assert post_resp.status_code == 200, post_resp.content
        body = json.loads(post_resp.content.decode())
        job_id = body["job_id"]

        status_resp = client.get(
            reverse("batch_simulate_status", args=[job_id])
            + f"?team_red_id={red.id}&team_blue_id={blue.id}&arena_map_id="
        )
        assert status_resp.status_code == 200, status_resp.content
        job = json.loads(status_resp.content.decode())
        assert job["status"] == "complete", job
        results = job["partial"]
        assert results is not None and "side_advantage" in results, (
            "partial aggregate must include the SIM-08 'side_advantage' "
            "physical-side panel data"
        )
        sa = results["side_advantage"]
        for key in (
            "red_side_wins",
            "blue_side_wins",
            "side_ties",
            "red_side_win_pct",
            "blue_side_win_pct",
            "avg_red_side_score",
            "avg_blue_side_score",
            "n",
        ):
            assert key in sa, f"side_advantage missing documented key {key!r}"
        assert sa["n"] == 10
        assert sa["red_side_wins"] + sa["blue_side_wins"] + sa["side_ties"] == 10
        # Team-position aggregates remain present and consistent.
        assert results["red_wins"] + results["blue_wins"] + results["ties"] == 10


@pytest.mark.django_db
class TestM1EventLogWindowing:
    """M-1: the HTML event log no longer server-renders one DOM row per
    event (the ~20k-node blow-up). Events and players are emitted once as
    JSON; the timeline is windowed client-side. These tests pin the
    server-side contract the client windowing depends on.
    """

    # Keys every serialized event dict must carry (the client reads only
    # these — see game_round_events.html). Keeping this list in a test
    # makes the JSON contract explicit and fails loudly if a key is
    # renamed out from under the template.
    _EVENT_KEYS = {
        "type",
        "ts",
        "tf",
        "icon",
        "desc",
        "pts",
        "aid",
        "an",
        "at",
        "tid",
        "tn",
        "tt",
        "meta",
    }
    _PLAYER_KEYS = {"id", "name", "team", "role", "sl", "ss"}

    def _round_with_events(self):
        red, _ = make_team_with_slots("M1Red")
        blue, _ = make_team_with_slots("M1Blue")
        # SIM-09: view-path now drives BatchSimulator. Short round for speed.
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            gr = BatchSimulator().simulate_single_round_detailed(red, blue)
        return gr

    def test_view_emits_json_not_per_event_rows(self):
        """The response carries the JSON script blocks and does NOT
        server-render a ``data-event-type`` div per event."""
        gr = self._round_with_events()
        assert gr.events.count() > 0, "fixture round produced no events"

        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200

        body = resp.content.decode()
        assert 'id="events-data"' in body, "events JSON script block missing"
        assert 'id="players-data"' in body, "players JSON script block missing"
        # The old design emitted one `data-event-type="..."` attribute per
        # event into the server HTML. The windowed design renders rows in
        # JS, so no such attribute should appear in the served markup.
        assert "data-event-type=" not in body, (
            "events are still being server-rendered as DOM rows — the M-1 "
            "DOM blow-up is not fixed"
        )

    def test_events_data_matches_db_and_has_full_shape(self):
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        events_data = resp.context["events_data"]
        assert len(events_data) == gr.events.count()
        # Events are emitted in chronological (timestamp) order.
        ts = [e["ts"] for e in events_data]
        assert ts == sorted(ts)
        sample = events_data[0]
        assert (
            set(sample) == self._EVENT_KEYS
        ), f"event JSON shape drifted: {set(sample) ^ self._EVENT_KEYS}"
        assert isinstance(sample["meta"], dict)
        # A targetless event must use the -1 sentinel the client expects.
        assert all(e["tid"] == -1 or isinstance(e["tid"], int) for e in events_data)

    def test_players_data_matches_round_states(self):
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        players_data = resp.context["players_data"]
        assert len(players_data) == gr.player_states.count()
        for p in players_data:
            assert set(p) == self._PLAYER_KEYS
            assert p["team"] in ("red", "blue")

    def test_highlights_json_context_is_a_list(self):
        """RV-02: the events view exposes ``highlights_json`` as a list
        (coalesced from the round's persisted field)."""
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200
        assert isinstance(resp.context["highlights_json"], list)

    def test_highlights_tab_rendered(self):
        """RV-02: the Highlights tab + json_script block + DOM ids render."""
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        body = resp.content.decode()
        assert 'id="highlights-data"' in body, "highlights JSON script block missing"
        assert 'id="highlights-section"' in body
        assert 'id="highlights-list"' in body
        assert 'id="highlights-empty"' in body

    def test_empty_round_renders_without_error(self):
        """A round with zero events still renders 200 with an empty list
        (the client must handle the no-events path)."""
        gr = self._round_with_events()
        gr.events.all().delete()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200
        assert resp.context["events_data"] == []
        assert "data-event-type=" not in resp.content.decode()

    # ------------------------------------------------------------------ #
    # RES-02b — universal per-player snapshot contract on events_data.   #
    # ------------------------------------------------------------------ #
    #
    # RES-02b SUPERSEDES the original RES-02 "MUST NOT carry sp" partition
    # (`miss`, `resupply_ammo`, `resupply_lives`, `combo_resupply`,
    # `movement`, `elimination`). EVERY event with an actor now carries the
    # universal actor block (`actor_role` + `actor_shots`/`actor_lives`/
    # `actor_points` + `sp`), and every event with a target carries the
    # target block. The view layer must mirror that universal contract on
    # each `events_data` row.
    #
    # Seam contract: `.claude/worktrees/res02b-parity-contract.md`.

    # Event types that historically carry ``sp`` reliably — kept as a
    # coverage hint for the sanity assertion below (NOT as a partition).
    _RES02_SP_TYPES = {"tag", "missile", "special", "base_capture"}

    # Required key sets on `meta` per the universal contract.
    _ACTOR_BLOCK_KEYS = (
        "actor_role",
        "actor_shots",
        "actor_lives",
        "actor_points",
        "sp",
    )
    _TARGET_BLOCK_KEYS = (
        "target_role",
        "target_shots",
        "target_lives",
        "target_points",
    )

    def test_events_data_carries_sp_on_sp_changing_types_only(self):
        """RES-02b: every ``events_data`` row carries the universal actor
        block (``meta.actor_role`` + ``actor_shots``/``actor_lives``/
        ``actor_points`` + ``sp``) because every row has a non-null actor
        (``aid`` is always an int, never -1, since ``GameEvent.actor`` is a
        non-nullable FK). Every row whose ``tid != -1`` additionally
        carries the target block. ``base_capture`` rows do NOT carry
        ``meta.special_points`` (RES-02 rename preserved).
        """
        gr = self._round_with_events()
        client = Client()
        resp = client.get(reverse("game_round_events", kwargs={"round_id": gr.id}))
        assert resp.status_code == 200
        events_data = resp.context["events_data"]
        assert events_data, "fixture round produced no events_data rows"

        sp_seen = {t: 0 for t in self._RES02_SP_TYPES}
        targeted_rows = 0

        for row in events_data:
            etype = row["type"]
            meta = row["meta"]
            aid = row["aid"]
            tid = row["tid"]

            # Universal actor block — every row's `aid` is a real int
            # (GameEvent.actor is a non-nullable FK), so the actor block
            # is required on every row.
            assert isinstance(aid, int) and aid != -1, (
                f"events_data row must carry a real actor id; got aid={aid!r} "
                f"on row {row!r}"
            )
            for key in self._ACTOR_BLOCK_KEYS:
                assert key in meta, (
                    f"events_data row of type {etype!r} must carry meta[{key!r}]"
                    f" per the RES-02b seam contract; meta={meta!r}"
                )
            assert isinstance(meta["actor_role"], str), (
                f"meta.actor_role on row {etype!r} must be str, got "
                f"{type(meta['actor_role']).__name__}"
            )
            assert (
                isinstance(meta["actor_shots"], int) and meta["actor_shots"] >= 0
            ), f"meta.actor_shots={meta['actor_shots']!r} must be int >= 0"
            assert (
                isinstance(meta["actor_lives"], int) and meta["actor_lives"] >= 0
            ), f"meta.actor_lives={meta['actor_lives']!r} must be int >= 0"
            assert isinstance(
                meta["actor_points"], int
            ), f"meta.actor_points={meta['actor_points']!r} must be int"
            assert isinstance(meta["sp"], int), (
                f"meta.sp on row {etype!r} must be int, got "
                f"{type(meta['sp']).__name__}"
            )
            assert (
                0 <= meta["sp"] <= 99
            ), f"meta.sp={meta['sp']!r} out of [0, 99] on {etype!r}"

            # Universal target block — only when tid is a real id.
            if tid != -1:
                targeted_rows += 1
                for key in self._TARGET_BLOCK_KEYS:
                    assert key in meta, (
                        f"events_data row of type {etype!r} with tid={tid!r} "
                        f"must carry meta[{key!r}] per the RES-02b seam "
                        f"contract; meta={meta!r}"
                    )
                assert isinstance(meta["target_role"], str)
                assert (
                    isinstance(meta["target_shots"], int) and meta["target_shots"] >= 0
                )
                assert (
                    isinstance(meta["target_lives"], int) and meta["target_lives"] >= 0
                )
                assert isinstance(meta["target_points"], int)

            if etype in self._RES02_SP_TYPES:
                sp_seen[etype] += 1
                if etype == "base_capture":
                    assert "special_points" not in meta, (
                        "base_capture row must rename 'special_points' to "
                        f"'sp' with no alias; got meta={meta!r}"
                    )

        # Sanity: the fixture should produce at least one SP-carrying row
        # and at least one targeted row so the universal assertions above
        # have bite.
        assert sum(sp_seen.values()) > 0, (
            "fixture round produced no SP-carrying events — adjust ROUND_TICKS"
            " or seed so this assertion has bite"
        )
        assert targeted_rows > 0, (
            "fixture round produced no rows with a target — adjust fixture so "
            "the target-block assertion has bite"
        )


# ---------------------------------------------------------------------------
# RV-01 — Compare two rounds side by side.
#
# Read-only view at /matches/compare/?round_a=<id>&round_b=<id> rendering a
# per-player stat-delta table plus a Points-Over-Time overlay for two
# GameRounds sharing >= 1 team. Seam contract (names are stable):
#
#   URL name `compare_rounds`, path `/matches/compare/`, reads `round_a` /
#   `round_b` from the GET query string.
#
#   Helpers in matches/views.py:
#     - _shared_team_ids(round_a, round_b) -> list[int]
#     - _player_stat_deltas(round_a, round_b, team_ids) -> list[dict]
#     - _cumulative_team_points(game_round, team_id) -> list[list]
#
# These tests build minimal DB fixtures directly (GameRound +
# PlayerRoundState + GameEvent) — no simulator runs, no RNG — so the derived
# values are exact and assertable. They are EXPECTED to fail until the RV-01
# production code lands (the view/url/helpers do not yet exist).
# ---------------------------------------------------------------------------


# The 12 per-player stat keys the delta table is contracted to expose.
_RV01_STAT_KEYS = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)


def _make_round(team_red, team_blue, round_number=1):
    """Create a bare completed GameRound for two teams (no simulator run)."""
    return GameRound.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        is_completed=True,
    )


def _make_state(game_round, player, *, team_color, role, **stats):
    """Create a PlayerRoundState row with explicit stat values.

    Any keyword in ``stats`` is forwarded straight to the model so callers can
    pin ``points_scored=100``, ``tags_made=5``, etc. without RNG.
    """
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color=team_color,
        role=role,
        **stats,
    )


@pytest.mark.django_db
class TestBs1BatchFormLabels:
    """BS-1: every visible field on the batch-sim form must have a label
    programmatically associated via ``for=`` (a11y — "No label associated
    with a form field"). The four fields are team_red, team_blue, arena_map,
    and n.
    """

    def test_batch_form_labels_have_for_attribute(self):
        client = Client()
        resp = client.get(reverse("simulate_batch"))
        assert resp.status_code == 200
        html = resp.content.decode()
        for field_id in ("id_team_red", "id_team_blue", "id_arena_map", "id_n"):
            assert f'for="{field_id}"' in html, (
                f"batch-form label for {field_id} must use for= to associate "
                f"with its field (BS-1 a11y)"
            )


@pytest.mark.django_db
class TestRd1RoundDetailMissileLink:
    """RD-1: the round-detail page must link to the missile log, alongside the
    existing Event Log and Movement Heatmap links (the link was wired into the
    heatmap template but never into round detail when RES-03 shipped).
    """

    def test_round_detail_links_to_missile_log(self):
        red, _ = make_team_with_slots("Rd1Red")
        blue, _ = make_team_with_slots("Rd1Blue")
        game_round = _make_round(red, blue)

        client = Client()
        resp = client.get(
            reverse("game_round_detail", kwargs={"round_id": game_round.id})
        )
        assert resp.status_code == 200
        missile_url = reverse("missile_log", kwargs={"round_id": game_round.id})
        assert (
            missile_url.encode() in resp.content
        ), "round-detail page must link to the missile log (RD-1)"


@pytest.mark.django_db
class TestRv01CompareRounds:
    """RV-01 — compare two rounds side by side (read-only view + helpers)."""

    # ------------------------------------------------------------------ #
    # 1. URL / view routing — compare mode renders a delta table.        #
    # ------------------------------------------------------------------ #
    def test_compare_rounds_url_resolves(self):
        # Should not raise NoReverseMatch.
        assert reverse("compare_rounds") == "/matches/compare/"

    def test_compare_mode_both_params_shared_team_renders_table(self):
        red, players_a = make_team_with_slots("Rv01Shared")
        blue_a, _ = make_team_with_slots("Rv01BlueA")
        blue_b, _ = make_team_with_slots("Rv01BlueB")

        round_a = _make_round(red, blue_a)
        round_b = _make_round(red, blue_b)
        # `red` plays in both rounds → shared team.
        _make_state(round_a, players_a["commander"], team_color="red", role="commander")
        _make_state(round_b, players_a["commander"], team_color="red", role="commander")

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": round_a.id, "round_b": round_b.id},
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        # Compare mode renders the delta table (and not just the picker).
        assert (
            "compare-deltas" in body or 'id="compare-delta-table"' in body
        ), "compare mode did not render the per-player delta table"

    def test_match_list_links_to_compare_page(self):
        # The match list is the entry point to the compare page.
        client = Client()
        resp = client.get(reverse("match_list"))
        assert resp.status_code == 200
        assert reverse("compare_rounds") in resp.content.decode()

    # ------------------------------------------------------------------ #
    # 2. Picker mode — no/one param → selects, no delta table.           #
    # ------------------------------------------------------------------ #
    def test_picker_mode_no_params_renders_selects(self):
        client = Client()
        resp = client.get(reverse("compare_rounds"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'id="compare-select-a"' in body
        assert 'id="compare-select-b"' in body
        assert (
            'id="compare-delta-table"' not in body
        ), "picker mode must not render the delta table"

    def test_picker_mode_only_one_param_renders_selects(self):
        red, _ = make_team_with_slots("Rv01OnlyOne")
        blue, _ = make_team_with_slots("Rv01OnlyOneBlue")
        round_a = _make_round(red, blue)

        client = Client()
        resp = client.get(reverse("compare_rounds"), {"round_a": round_a.id})
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'id="compare-select-a"' in body
        assert 'id="compare-select-b"' in body
        assert 'id="compare-delta-table"' not in body

    # ------------------------------------------------------------------ #
    # 3. 404 — a round id that does not exist.                           #
    # ------------------------------------------------------------------ #
    def test_missing_round_a_returns_404(self):
        red, players = make_team_with_slots("Rv01Missing")
        blue, _ = make_team_with_slots("Rv01MissingBlue")
        round_b = _make_round(red, blue)
        _make_state(round_b, players["commander"], team_color="red", role="commander")

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": 9_999_999, "round_b": round_b.id},
        )
        assert resp.status_code == 404

    def test_non_numeric_round_id_returns_404(self):
        # A hand-crafted ?round_a=abc must be a clean 404, not a 500 from the
        # int() coercion failing inside the ORM query.
        red, players = make_team_with_slots("Rv01NonNum")
        blue, _ = make_team_with_slots("Rv01NonNumBlue")
        round_b = _make_round(red, blue)
        _make_state(round_b, players["commander"], team_color="red", role="commander")

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": "abc", "round_b": round_b.id},
        )
        assert resp.status_code == 404

    # ------------------------------------------------------------------ #
    # 4. Same round (round_a == round_b) → 200, error banner, no table.  #
    # ------------------------------------------------------------------ #
    def test_same_round_shows_error_banner_no_table(self):
        red, players = make_team_with_slots("Rv01Same")
        blue, _ = make_team_with_slots("Rv01SameBlue")
        round_a = _make_round(red, blue)
        _make_state(round_a, players["commander"], team_color="red", role="commander")

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": round_a.id, "round_b": round_a.id},
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert (
            'id="compare-delta-table"' not in body
        ), "comparing a round against itself must not render a delta table"
        assert (
            "alert-warning" in body or "compare-error" in body
        ), "same-round comparison must surface an error banner"

    # ------------------------------------------------------------------ #
    # 5. No shared team — four distinct teams → 200, error banner.       #
    # ------------------------------------------------------------------ #
    def test_no_shared_team_shows_error_banner_no_table(self):
        red_a, players_a = make_team_with_slots("Rv01NoShareRA")
        blue_a, _ = make_team_with_slots("Rv01NoShareBA")
        red_b, players_b = make_team_with_slots("Rv01NoShareRB")
        blue_b, _ = make_team_with_slots("Rv01NoShareBB")

        round_a = _make_round(red_a, blue_a)
        round_b = _make_round(red_b, blue_b)
        _make_state(round_a, players_a["commander"], team_color="red", role="commander")
        _make_state(round_b, players_b["commander"], team_color="red", role="commander")

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": round_a.id, "round_b": round_b.id},
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert (
            'id="compare-delta-table"' not in body
        ), "rounds with no shared team must not render a delta table"
        assert (
            "alert-warning" in body or "compare-error" in body
        ), "no-shared-team comparison must surface an error banner"

    # ------------------------------------------------------------------ #
    # 9. json_script ids present in compare-mode HTML.                   #
    # ------------------------------------------------------------------ #
    def test_compare_mode_emits_json_script_ids(self):
        red, players = make_team_with_slots("Rv01Json")
        blue_a, _ = make_team_with_slots("Rv01JsonBA")
        blue_b, _ = make_team_with_slots("Rv01JsonBB")
        round_a = _make_round(red, blue_a)
        round_b = _make_round(red, blue_b)
        _make_state(
            round_a,
            players["commander"],
            team_color="red",
            role="commander",
            points_scored=100,
        )
        _make_state(
            round_b,
            players["commander"],
            team_color="red",
            role="commander",
            points_scored=250,
        )

        client = Client()
        resp = client.get(
            reverse("compare_rounds"),
            {"round_a": round_a.id, "round_b": round_b.id},
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert (
            'id="compare-points-series"' in body
        ), "compare mode must emit the Points-Over-Time series json_script block"
        assert (
            'id="compare-deltas"' in body
        ), "compare mode must emit the per-player delta json_script block"

    # ------------------------------------------------------------------ #
    # 6. _shared_team_ids is Side-agnostic.                              #
    # ------------------------------------------------------------------ #
    def test_shared_team_ids_is_side_agnostic(self):
        from matches.views import _shared_team_ids

        team_x, _ = make_team_with_slots("Rv01ShareX")
        team_y, _ = make_team_with_slots("Rv01ShareY")
        team_z, _ = make_team_with_slots("Rv01ShareZ")

        # Round A: X red, Y blue.  Round B: X blue, Z red.
        round_a = _make_round(team_x, team_y)
        round_b = _make_round(team_z, team_x)

        shared = _shared_team_ids(round_a, round_b)
        assert shared == [team_x.id], (
            "X is shared across both rounds even though it played different "
            f"Sides; got {shared!r}"
        )

    def test_shared_team_ids_distinct_teams_empty(self):
        from matches.views import _shared_team_ids

        red_a, _ = make_team_with_slots("Rv01DistRA")
        blue_a, _ = make_team_with_slots("Rv01DistBA")
        red_b, _ = make_team_with_slots("Rv01DistRB")
        blue_b, _ = make_team_with_slots("Rv01DistBB")

        round_a = _make_round(red_a, blue_a)
        round_b = _make_round(red_b, blue_b)

        assert _shared_team_ids(round_a, round_b) == []

    # ------------------------------------------------------------------ #
    # 7. _player_stat_deltas row shape + delta == b - a.                 #
    # ------------------------------------------------------------------ #
    def test_player_stat_deltas_row_shape_for_player_in_both_rounds(self):
        from matches.round_comparison import player_stat_deltas
        from matches.views import _comparison_row

        team_x, players = make_team_with_slots("Rv01DeltaX")
        blue_a, _ = make_team_with_slots("Rv01DeltaBA")
        blue_b, _ = make_team_with_slots("Rv01DeltaBB")

        round_a = _make_round(team_x, blue_a)
        round_b = _make_round(team_x, blue_b)
        commander = players["commander"]

        # Same player, both rounds, different points (100 → 250 ⇒ delta 150).
        _make_state(
            round_a,
            commander,
            team_color="red",
            role="commander",
            points_scored=100,
            tags_made=4,
        )
        _make_state(
            round_b,
            commander,
            team_color="blue",
            role="commander",
            points_scored=250,
            tags_made=10,
        )

        team_id_set = {team_x.id}
        rows_a = [
            _comparison_row(ps)
            for ps in round_a.player_states.select_related("player").all()
            if ps.player.team_id in team_id_set
        ]
        rows_b = [
            _comparison_row(ps)
            for ps in round_b.player_states.select_related("player").all()
            if ps.player.team_id in team_id_set
        ]
        rows = player_stat_deltas(rows_a, rows_b)
        assert len(rows) == 1, f"expected one shared-team player row, got {rows!r}"
        row = rows[0]

        for key in (
            "player_id",
            "name",
            "role_a",
            "role_b",
            "side_a",
            "side_b",
            "stats",
        ):
            assert key in row, f"delta row missing top-level key {key!r}: {row!r}"

        assert row["player_id"] == commander.id
        assert row["name"] == commander.name
        assert row["role_a"] == "commander"
        assert row["role_b"] == "commander"
        assert row["side_a"] == "red"
        assert row["side_b"] == "blue"

        stats = row["stats"]
        assert set(stats) == set(
            _RV01_STAT_KEYS
        ), f"stat key set drifted: {set(stats) ^ set(_RV01_STAT_KEYS)}"
        for key, cell in stats.items():
            assert set(cell) == {
                "a",
                "b",
                "delta",
            }, f"stat cell {key!r} must have keys a/b/delta; got {set(cell)!r}"

        # points_scored: 100 in A, 250 in B → delta 150.
        ps = stats["points_scored"]
        assert ps["a"] == 100
        assert ps["b"] == 250
        assert ps["delta"] == 150

        # Every present-in-both stat: delta == b - a.
        for key, cell in stats.items():
            if cell["a"] is not None and cell["b"] is not None:
                assert cell["delta"] == cell["b"] - cell["a"], (
                    f"stat {key!r}: delta {cell['delta']!r} != b - a "
                    f"({cell['b']!r} - {cell['a']!r})"
                )

    def test_player_stat_deltas_player_in_only_one_round(self):
        from matches.round_comparison import player_stat_deltas
        from matches.views import _comparison_row

        team_x, players = make_team_with_slots("Rv01OneSide")
        blue_a, _ = make_team_with_slots("Rv01OneSideBA")
        blue_b, _ = make_team_with_slots("Rv01OneSideBB")

        round_a = _make_round(team_x, blue_a)
        round_b = _make_round(team_x, blue_b)

        # Heavy appears only in round A.
        heavy = players["heavy"]
        _make_state(
            round_a,
            heavy,
            team_color="red",
            role="heavy",
            points_scored=80,
            tags_made=3,
        )

        team_id_set = {team_x.id}
        rows_a = [
            _comparison_row(ps)
            for ps in round_a.player_states.select_related("player").all()
            if ps.player.team_id in team_id_set
        ]
        rows_b = [
            _comparison_row(ps)
            for ps in round_b.player_states.select_related("player").all()
            if ps.player.team_id in team_id_set
        ]
        rows = player_stat_deltas(rows_a, rows_b)
        matching = [r for r in rows if r["player_id"] == heavy.id]
        assert len(matching) == 1, (
            "a player present in only one round must still appear in the "
            f"delta table; rows={rows!r}"
        )
        row = matching[0]

        # Present in A only → side_b / role_b are None.
        assert row["role_a"] == "heavy"
        assert row["side_a"] == "red"
        assert row["role_b"] is None
        assert row["side_b"] is None

        # The missing side's stat values + deltas are None.
        for key, cell in row["stats"].items():
            assert (
                cell["b"] is None
            ), f"stat {key!r}: side B absent so 'b' must be None; got {cell['b']!r}"
            assert cell["delta"] is None, (
                f"stat {key!r}: side B absent so 'delta' must be None; "
                f"got {cell['delta']!r}"
            )
        # The present side keeps its real value.
        assert row["stats"]["points_scored"]["a"] == 80

    # ------------------------------------------------------------------ #
    # 8. _cumulative_team_points — monotonic running sum, null coalesce. #
    # ------------------------------------------------------------------ #
    def test_cumulative_team_points_running_sum_with_null_coalesce(self):
        from matches.views import _cumulative_team_points

        red, players = make_team_with_slots("Rv01Cum")
        blue, _ = make_team_with_slots("Rv01CumBlue")
        game_round = _make_round(red, blue)

        commander = players["commander"]
        heavy = players["heavy"]
        # Red-team states so the actors belong to the team being summed.
        _make_state(game_round, commander, team_color="red", role="commander")
        _make_state(game_round, heavy, team_color="red", role="heavy")

        # GameEvents at increasing ticks with points_awarded; one None to
        # confirm coalesce-to-0.
        GameEvent.objects.create(
            game_round=game_round,
            timestamp=10,
            event_type="tag",
            actor=commander,
            points_awarded=100,
        )
        # A zero-point event (points_awarded == 0). GameEvent.points_awarded
        # is NOT NULL with default 0 — the simulator never writes a true NULL —
        # so the coalesce-to-0 contract is exercised here by a 0-point row that
        # must not perturb the running cumulative sum.
        GameEvent.objects.create(
            game_round=game_round,
            timestamp=20,
            event_type="tag",
            actor=heavy,
            points_awarded=0,
        )
        GameEvent.objects.create(
            game_round=game_round,
            timestamp=30,
            event_type="tag",
            actor=commander,
            points_awarded=50,
        )

        series = _cumulative_team_points(game_round, red.id)
        assert (
            isinstance(series, list) and series
        ), f"expected non-empty list, got {series!r}"
        # Shape: [[tick, cumulative], ...].
        for point in series:
            assert (
                isinstance(point, list) and len(point) == 2
            ), f"each series entry must be a [tick, cum] pair; got {point!r}"

        # Cumulative column is monotonic non-decreasing.
        cumulative = [pt[1] for pt in series]
        for prev, cur in zip(cumulative, cumulative[1:]):
            assert cur >= prev, f"cumulative regressed: {prev} → {cur}"

        # Final running total = 100 + 0 (coalesced) + 50 = 150.
        assert cumulative[-1] == 150, (
            f"cumulative total should coalesce the null award to 0 and sum to "
            f"150; got {cumulative!r}"
        )

    def test_cumulative_team_points_empty_round_returns_empty(self):
        from matches.views import _cumulative_team_points

        red, _ = make_team_with_slots("Rv01CumEmpty")
        blue, _ = make_team_with_slots("Rv01CumEmptyBlue")
        game_round = _make_round(red, blue)

        assert _cumulative_team_points(game_round, red.id) == []


@pytest.mark.django_db
class TestBs2BatchChartReuse:
    """BS-2: re-running a batch without reloading must not throw Chart.js
    "Canvas is already in use" — the prior Chart bound to ``#scoreChart``
    has to be destroyed before a new run re-instantiates on the same canvas.

    Pure-markup regression: the batch template nulls ``scoreChart`` on a new
    run but, pre-fix, never ``.destroy()``-ed the live instance, so the next
    ``new Chart(...)`` on the still-bound canvas threw. The robust guard is
    ``Chart.getChart(<canvas>)?.destroy()`` so any chart attached to the
    canvas (not just one we tracked) is torn down before reuse.
    """

    def test_batch_template_destroys_existing_chart_before_reuse(self):
        client = Client()
        resp = client.get(reverse("simulate_batch"))
        assert resp.status_code == 200
        html = resp.content.decode()

        # The canvas-reuse guard must be present so a second run on the same
        # page tears down any chart still bound to #scoreChart.
        assert "Chart.getChart(" in html, (
            "batch template must call Chart.getChart() to find and destroy a "
            "live chart on #scoreChart before re-instantiating (BS-2)"
        )
        assert ".destroy()" in html, (
            "batch template must .destroy() the existing Chart.js instance "
            "before reusing the #scoreChart canvas (BS-2)"
        )


@pytest.mark.django_db
class TestRv03ExportRoundReport:
    """RV-03 — ``export_round_report`` view: GET returns an attachment PDF,
    missing id 404s, non-GET 405s, and both ``is_simulated`` branches render.

    Pins the §3 / §8 / §10b view contract from
    ``.claude/worktrees/rv-03-seam-contract.md``. These build a real saved
    ``GameRound`` with a handful of ``PlayerRoundState`` rows via the shared
    ``_make_round`` / ``_make_state`` helpers (no simulator run — DB-cheap and
    deterministic). The view assembles ``report_data`` from the ORM and hands
    it to the pure ``build_round_report`` builder; until both the view and the
    builder land, these tests fail (expected, spec-first).
    """

    def _saved_round_with_players(self, prefix: str, *, is_simulated: bool = True):
        """A completed round with two red + two blue players, explicit stats.

        ``is_simulated`` is set after creation so the test does not depend on
        the field being a ``create()`` kwarg (it has a ``default=True``).
        """
        red, red_players = make_team_with_slots(f"{prefix}R")
        blue, blue_players = make_team_with_slots(f"{prefix}B")
        game_round = _make_round(red, blue)
        game_round.red_points = 9000
        game_round.blue_points = 8500
        game_round.is_simulated = is_simulated
        game_round.save()
        _make_state(
            game_round,
            red_players["commander"],
            team_color="red",
            role="commander",
            points_scored=5000,
            tags_made=10,
            final_lives=3,
        )
        _make_state(
            game_round,
            red_players["heavy"],
            team_color="red",
            role="heavy",
            points_scored=4000,
            tags_made=8,
            final_lives=2,
        )
        _make_state(
            game_round,
            blue_players["scout"],
            team_color="blue",
            role="scout",
            points_scored=4500,
            tags_made=7,
            final_lives=1,
        )
        _make_state(
            game_round,
            blue_players["medic"],
            team_color="blue",
            role="medic",
            points_scored=4000,
            tags_made=2,
            final_lives=0,
        )
        return game_round

    def test_export_url_resolves(self):
        """The route reverses with a round id (no NoReverseMatch)."""
        url = reverse("export_round_report", args=[1])
        assert url == "/matches/game-round/1/export/"

    def test_get_returns_pdf_attachment(self):
        game_round = self._saved_round_with_players("Rv03Get")
        client = Client()
        resp = client.get(reverse("export_round_report", args=[game_round.id]))

        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        disposition = resp["Content-Disposition"]
        assert disposition.startswith('attachment; filename="round-'), (
            f"Content-Disposition must be an attachment named round-<id>-...; "
            f"got {disposition!r}"
        )
        assert disposition.rstrip().endswith(
            '.pdf"'
        ), f"filename must end with .pdf; got {disposition!r}"
        # The id appears in the filename: round-<id>-...
        assert f"round-{game_round.id}-" in disposition
        assert resp.content.startswith(
            b"%PDF"
        ), f"response body must be a PDF; got {resp.content[:8]!r}"

    def test_missing_round_id_returns_404(self):
        client = Client()
        resp = client.get(reverse("export_round_report", args=[999999]))
        assert resp.status_code == 404

    def test_post_returns_405(self):
        game_round = self._saved_round_with_players("Rv03Post")
        client = Client()
        resp = client.post(reverse("export_round_report", args=[game_round.id]))
        assert resp.status_code == 405

    def test_simulated_round_renders_pdf(self):
        """is_simulated=True (watermark branch) -> 200 + PDF."""
        game_round = self._saved_round_with_players("Rv03Sim", is_simulated=True)
        client = Client()
        resp = client.get(reverse("export_round_report", args=[game_round.id]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_non_simulated_round_renders_pdf(self):
        """is_simulated=False (no-watermark branch) -> 200 + PDF."""
        game_round = self._saved_round_with_players("Rv03Real", is_simulated=False)
        client = Client()
        resp = client.get(reverse("export_round_report", args=[game_round.id]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# HX-03 — head-to-head record view
# ---------------------------------------------------------------------------


import json as _json
from datetime import datetime as _datetime

from django.test import TestCase as _TestCase
from django.utils import timezone as _timezone

from core.models import ArenaMap as _ArenaMap


class TestHx03HeadToHead(_TestCase):
    """Coverage for ``matches/views.py::head_to_head`` at
    ``GET /matches/h2h/``.

    Mirrors RV-01's ``compare_rounds`` 4-mode pattern (picker / 404 /
    error / results); the locked seam contract lives at
    ``.claude/worktrees/hx-03-seam-contract.md``. Locked test names below
    are pinned by the **Tests** section of that contract verbatim.

    Each test sets up the rosters via the existing
    ``conftest.py::make_team_with_slots`` helper and writes real
    ``Match`` / ``GameRound`` / ``PlayerRoundState`` rows through the
    ORM (Django ``TestCase`` ⇒ test DB).
    """

    # ------------------------------------------------------------------ setup

    def _make_two_teams(self):
        """Return (team_a, players_a, team_b, players_b)."""
        team_a, players_a = make_team_with_slots("H2hA")
        team_b, players_b = make_team_with_slots("H2hB")
        return team_a, players_a, team_b, players_b

    def _make_round(
        self,
        *,
        team_red,
        team_blue,
        red_points: int = 100,
        blue_points: int = 50,
        match=None,
        round_number: int = 1,
        arena_map=None,
        is_simulated: bool = True,
    ) -> GameRound:
        """Create one GameRound and mark it completed so winner resolves."""
        gr = GameRound.objects.create(
            match=match,
            round_number=round_number,
            team_red=team_red,
            team_blue=team_blue,
            red_points=red_points,
            blue_points=blue_points,
            arena_map=arena_map,
            is_simulated=is_simulated,
            is_completed=True,
        )
        return gr

    def _make_match(
        self,
        *,
        team_red,
        team_blue,
        red_r1: int = 100,
        blue_r1: int = 50,
        red_r2: int = 100,
        blue_r2: int = 50,
        is_completed: bool = True,
    ) -> Match:
        """Create a Match with two completed Rounds (red wins by default).

        The Match's ``winner`` is computed in ``Match.save()`` from the
        per-round point fields when ``is_completed`` is True.
        """
        match = Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            red_round1_points=red_r1,
            blue_round1_points=blue_r1,
            red_round2_points=red_r2,
            blue_round2_points=blue_r2,
            is_completed=is_completed,
        )
        return match

    def _make_player_state(
        self,
        *,
        game_round: GameRound,
        player,
        team_color: str,
        role: str = "commander",
        final_lives: int = 3,
    ) -> PlayerRoundState:
        return PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=team_color,
            role=role,
            final_lives=final_lives,
        )

    # ------------------------------------------------------------------ §1 picker

    def test_picker_mode_both_params_missing_renders_form_200(self):
        response = self.client.get(reverse("head_to_head"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "picker")
        # Picker form DOM id present.
        self.assertIn(b"h2h-picker-form", response.content)

    def test_picker_mode_only_team_a_param_renders_form_with_a_preselected_200(
        self,
    ):
        team_a, _, _team_b, _ = self._make_two_teams()
        response = self.client.get(reverse("head_to_head") + f"?team_a={team_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "picker")
        # team_a should be carried into the context so the picker re-renders
        # with the user's prior selection.
        ctx_team_a = response.context.get("team_a")
        self.assertIsNotNone(ctx_team_a)
        self.assertEqual(ctx_team_a.id, team_a.id)

    # ------------------------------------------------------------------ §2 404

    def test_404_when_team_a_id_does_not_resolve(self):
        _team_a, _, team_b, _ = self._make_two_teams()
        response = self.client.get(
            reverse("head_to_head") + f"?team_a=9999999&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 404)

    def test_404_when_team_b_id_does_not_resolve(self):
        team_a, _, _team_b, _ = self._make_two_teams()
        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b=9999999"
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------ §3 error

    def test_error_mode_when_team_a_equals_team_b_200_with_error_banner(self):
        team_a, _, _team_b, _ = self._make_two_teams()
        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_a.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "error")
        self.assertIn(b"h2h-error-banner", response.content)

    # ------------------------------------------------------------------ §4 empty history

    def test_empty_history_results_mode_with_h2h_no_games_notice_200(self):
        """Two valid distinct Teams that have never played each other →
        results mode with all-zero aggregates and the no-games notice."""
        team_a, _, team_b, _ = self._make_two_teams()
        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "results")
        self.assertEqual(response.context["match_record"]["n"], 0)
        self.assertEqual(response.context["round_record"]["n"], 0)
        self.assertIn(b"h2h-no-games-notice", response.content)

    # ------------------------------------------------------------------ §5 full results

    def test_full_results_renders_match_record_round_record_margin_survivors_dom_ids(
        self,
    ):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        # One match: team_a wins both rounds → team_a wins the Match.
        match = self._make_match(
            team_red=team_a,
            team_blue=team_b,
            red_r1=200,
            blue_r1=100,
            red_r2=150,
            blue_r2=120,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=200,
            blue_points=100,
            match=match,
            round_number=1,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=150,
            blue_points=120,
            match=match,
            round_number=2,
        )
        # Drop a couple PlayerRoundStates so survivor math is non-zero.
        gr1 = match.game_rounds.first()
        self._make_player_state(
            game_round=gr1,
            player=players_a["commander"],
            team_color="red",
            role="commander",
            final_lives=5,
        )
        self._make_player_state(
            game_round=gr1,
            player=players_b["commander"],
            team_color="blue",
            role="commander",
            final_lives=0,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "results")
        # 1 Match, team_a won.
        self.assertEqual(response.context["match_record"]["wins"], 1)
        self.assertEqual(response.context["match_record"]["n"], 1)
        # 2 Rounds, team_a won both.
        self.assertEqual(response.context["round_record"]["wins"], 2)
        self.assertEqual(response.context["round_record"]["n"], 2)
        # All four headline DOM ids present.
        for dom_id in (
            b"h2h-match-record",
            b"h2h-round-record",
            b"h2h-score-margin",
            b"h2h-team-a-survivors",
            b"h2h-team-b-survivors",
        ):
            self.assertIn(dom_id, response.content)

    def test_full_results_renders_per_map_breakdown_table_with_no_map_3_zone_row(
        self,
    ):
        """Three Rounds across map A, map B, and no-map: all 3 rows present
        and the no-map row is labelled ``No map (3-zone)``."""
        team_a, _, team_b, _ = self._make_two_teams()
        map_a = _ArenaMap.objects.create(
            name="Alpha Arena", img_width=200, img_height=200
        )
        map_b = _ArenaMap.objects.create(
            name="Beta Arena", img_width=200, img_height=200
        )
        match = self._make_match(team_red=team_a, team_blue=team_b)
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=match,
            round_number=1,
            arena_map=map_a,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=200,
            blue_points=10,
            match=match,
            round_number=2,
            arena_map=map_b,
        )
        # Plus a standalone (no match) Round on no-map.
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=70,
            blue_points=70,
            match=None,
            round_number=1,
            arena_map=None,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        breakdown = response.context["per_map_breakdown"]
        self.assertEqual(len(breakdown), 3)
        # The body contains the per-map-breakdown table DOM id.
        self.assertIn(b"h2h-per-map-table", response.content)
        # The no-map row label is the locked literal.
        labels = {row["arena_map_name"] for row in breakdown}
        self.assertIn("No map (3-zone)", labels)

    def test_full_results_renders_detail_list_with_unified_match_and_standalone_rounds(
        self,
    ):
        team_a, _, team_b, _ = self._make_two_teams()
        match = self._make_match(team_red=team_a, team_blue=team_b)
        # 2 match-rounds + 1 standalone Round.
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=10,
            match=match,
            round_number=1,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=50,
            blue_points=80,
            match=match,
            round_number=2,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=20,
            blue_points=20,
            match=None,
            round_number=1,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        detail = response.context["detail_list"]
        # 1 Match entry + 1 standalone Round entry = 2 rows minimum.
        self.assertGreaterEqual(len(detail), 2)
        kinds = {row["kind"] for row in detail}
        self.assertIn("match", kinds)
        self.assertIn("round", kinds)
        self.assertIn(b"h2h-detail-list", response.content)

    def test_full_results_renders_top_impactful_per_team_dom_ids(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        match = self._make_match(team_red=team_a, team_blue=team_b)
        gr = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=match,
            round_number=1,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=50,
            blue_points=50,
            match=match,
            round_number=2,
        )
        # One player per team, both with non-zero MVP-relevant counters.
        self._make_player_state(
            game_round=gr,
            player=players_a["commander"],
            team_color="red",
            role="commander",
            final_lives=5,
        )
        self._make_player_state(
            game_round=gr,
            player=players_b["commander"],
            team_color="blue",
            role="commander",
            final_lives=3,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # Both per-team DOM ids present (template renders the lines even when
        # the value is "—" / None — the surrounding wrapper carries the id).
        self.assertIn(b"h2h-top-impactful-a", response.content)
        self.assertIn(b"h2h-top-impactful-b", response.content)

    def test_charts_render_canvas_and_json_script_blocks(self):
        team_a, _, team_b, _ = self._make_two_teams()
        match = self._make_match(team_red=team_a, team_blue=team_b)
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=40,
            match=match,
            round_number=1,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=60,
            blue_points=60,
            match=match,
            round_number=2,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # The two json_script DOM ids must be present...
        self.assertIn("h2h-margin-series", body)
        self.assertIn("h2h-cumulative-wl-series", body)
        # ...and the canvas DOM ids for the rendered charts.
        self.assertIn("h2h-margin-chart", body)
        self.assertIn("h2h-cumulative-wl-chart", body)
        # And the json_script payloads must be valid JSON.
        # ``json_script`` wraps content in <script id="...">...</script>.
        for series_id in ("h2h-margin-series", "h2h-cumulative-wl-series"):
            marker = f'id="{series_id}"'
            idx = body.find(marker)
            self.assertGreater(idx, -1, f"json_script id {series_id} missing")
            # Locate the JSON body between the tag's end and the closing tag.
            tag_end = body.find(">", idx) + 1
            close = body.find("</script>", tag_end)
            payload = body[tag_end:close].strip()
            # Should be valid JSON (list or null).
            _json.loads(payload)

    # ------------------------------------------------------------------ §6 provenance

    def test_provenance_param_real_filters_to_is_simulated_false(self):
        team_a, _, team_b, _ = self._make_two_teams()
        # One simulated, one real Round.
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&provenance=real"
        )
        self.assertEqual(response.status_code, 200)
        # Only the real Round survives.
        self.assertEqual(response.context["round_record"]["n"], 1)
        # And the surviving Round is the loss (red_points 10 < blue_points 80
        # from team_a's perspective when team_a is on red).
        self.assertEqual(response.context["round_record"]["losses"], 1)

    def test_provenance_param_sim_filters_to_is_simulated_true(self):
        team_a, _, team_b, _ = self._make_two_teams()
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&provenance=sim"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["round_record"]["n"], 1)
        # The simulated Round is the win.
        self.assertEqual(response.context["round_record"]["wins"], 1)

    def test_provenance_param_invalid_falls_back_to_all(self):
        team_a, _, team_b, _ = self._make_two_teams()
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&provenance=garbage"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["provenance"], "all")
        # Both Rounds counted.
        self.assertEqual(response.context["round_record"]["n"], 2)

    # ------------------------------------------------------------------ §7 dates

    def test_from_and_to_date_filter_applied_to_rounds_and_matches(self):
        """Only Rounds whose ``date_played`` falls in the window are kept.

        ``date_played`` is ``auto_now_add``; we cannot easily set it on
        create, so we update it after the fact. Two Rounds: one we
        relocate to 2020, one we leave at "now"; the filter
        ``?from=2025-01-01`` should keep only the "now" Round.
        """
        team_a, _, team_b, _ = self._make_two_teams()
        old = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=10,
        )
        new = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
        )
        # Force the old Round's date_played into the past.
        GameRound.objects.filter(pk=old.pk).update(
            date_played=_timezone.make_aware(_datetime(2020, 1, 1, 12, 0, 0))
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&from=2025-01-01"
        )
        self.assertEqual(response.status_code, 200)
        # Only the "new" Round survives the filter.
        self.assertEqual(response.context["round_record"]["n"], 1)
        # Sanity: the only surviving Round is "new" (team_a wins).
        self.assertEqual(response.context["round_record"]["wins"], 1)
        # And the surviving Round's id matches the expected one.
        # (We don't assert on detail_list ordering here; the n=1 above
        # already pins the filter behaviour. Reference ``new`` to keep
        # the linter quiet.)
        self.assertIsNotNone(new.pk)

    def test_invalid_from_date_silently_ignored(self):
        """Invalid ``from`` date is treated as unbounded on that side."""
        team_a, _, team_b, _ = self._make_two_teams()
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&from=not-a-date"
        )
        # No 400 / 500; the Round is still counted.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["round_record"]["n"], 1)

    # ------------------------------------------------------------------ §8 side-agnostic pairing

    def test_side_agnostic_pairing_team_a_red_in_one_match_blue_in_other(self):
        """team_a plays red in Match 1 and blue in Match 2 — both Matches
        count toward the H2H record."""
        team_a, _, team_b, _ = self._make_two_teams()
        # Match 1: team_a on red.
        match1 = self._make_match(team_red=team_a, team_blue=team_b)
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=match1,
            round_number=1,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=20,
            match=match1,
            round_number=2,
        )
        # Match 2: team_a on blue (sides flipped). The Match's stored
        # per-round point fields must match the GameRound rows so
        # Match.calculate_winner picks team_blue (team_a) as winner.
        match2 = self._make_match(
            team_red=team_b,
            team_blue=team_a,
            red_r1=10,
            blue_r1=200,
            red_r2=30,
            blue_r2=200,
        )
        self._make_round(
            team_red=team_b,
            team_blue=team_a,
            red_points=10,
            blue_points=200,
            match=match2,
            round_number=1,
        )
        self._make_round(
            team_red=team_b,
            team_blue=team_a,
            red_points=30,
            blue_points=200,
            match=match2,
            round_number=2,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # Both Matches counted.
        self.assertEqual(response.context["match_record"]["n"], 2)
        # team_a wins both Matches (in match1 they were red and won 100>50
        # and 80>20; in match2 they were blue and won 200>10 and 200>30).
        self.assertEqual(response.context["match_record"]["wins"], 2)
        # All four Rounds counted in the unified basket.
        self.assertEqual(response.context["round_record"]["n"], 4)

    # ------------------------------------------------------------------ §9 player who switched teams

    def test_player_who_switched_teams_appears_in_both_team_pools_per_round_attribution(
        self,
    ):
        """Same Player has PlayerRoundState rows on both sides across two
        Rounds — they appear in both per-team most-impactful pools."""
        team_a, players_a, team_b, _ = self._make_two_teams()
        # Two Rounds: one with the switcher on team_a's side, one on team_b's.
        gr_a_side = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
        )
        gr_b_side = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=50,
            blue_points=100,
        )
        # The same Player (single id) appears as "red" in gr_a_side and as
        # "blue" in gr_b_side.
        switcher = players_a["commander"]
        self._make_player_state(
            game_round=gr_a_side,
            player=switcher,
            team_color="red",
            role="commander",
            final_lives=5,
        )
        self._make_player_state(
            game_round=gr_b_side,
            player=switcher,
            team_color="blue",
            role="commander",
            final_lives=2,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # The view computes top_impactful per team — with one player only on
        # each side, both per-team entries should resolve to that player id.
        top = response.context["top_impactful"]
        self.assertIsNotNone(top["team_a"])
        self.assertIsNotNone(top["team_b"])
        self.assertEqual(top["team_a"]["player_id"], switcher.id)
        self.assertEqual(top["team_b"]["player_id"], switcher.id)

    # ------------------------------------------------------------------ §10 match filters

    def test_match_filter_is_completed_true_only(self):
        """A Match with ``is_completed=False`` is excluded from match_record."""
        team_a, _, team_b, _ = self._make_two_teams()
        # One completed Match: team_a wins both rounds.
        self._make_match(
            team_red=team_a,
            team_blue=team_b,
            red_r1=100,
            blue_r1=10,
            red_r2=80,
            blue_r2=20,
            is_completed=True,
        )
        # One in-progress Match: leave is_completed=False.
        self._make_match(
            team_red=team_a,
            team_blue=team_b,
            red_r1=0,
            blue_r1=0,
            red_r2=0,
            blue_r2=0,
            is_completed=False,
        )

        response = self.client.get(
            reverse("head_to_head") + f"?team_a={team_a.id}&team_b={team_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # Only the completed Match contributes.
        self.assertEqual(response.context["match_record"]["n"], 1)

    def test_match_record_excludes_match_when_provenance_real_and_either_round_is_simulated(
        self,
    ):
        """``provenance=real`` requires BOTH of a Match's Rounds to be real;
        a Match with one simulated and one real Round is excluded from the
        match_record."""
        team_a, _, team_b, _ = self._make_two_teams()
        # Match A: BOTH rounds real → kept.
        match_a = self._make_match(team_red=team_a, team_blue=team_b)
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=10,
            match=match_a,
            round_number=1,
            is_simulated=False,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=20,
            match=match_a,
            round_number=2,
            is_simulated=False,
        )
        # Match B: one real, one simulated → excluded.
        match_b = self._make_match(team_red=team_a, team_blue=team_b)
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=10,
            match=match_b,
            round_number=1,
            is_simulated=False,
        )
        self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=20,
            match=match_b,
            round_number=2,
            is_simulated=True,
        )

        response = self.client.get(
            reverse("head_to_head")
            + f"?team_a={team_a.id}&team_b={team_b.id}&provenance=real"
        )
        self.assertEqual(response.status_code, 200)
        # Only Match A survives in match_record.
        self.assertEqual(response.context["match_record"]["n"], 1)


# ---------------------------------------------------------------------------
# HX-04 — player head-to-head record view
# ---------------------------------------------------------------------------


class TestHx04PlayerHeadToHead(_TestCase):
    """Coverage for ``matches/views.py::player_head_to_head`` at
    ``GET /matches/h2h/player/``.

    Mirrors HX-03 ``TestHx03HeadToHead`` patterns and RV-01's 4-mode
    layout (picker / 404 / error / results). The locked seam contract
    lives at ``.claude/worktrees/hx-04-seam-contract.md``. Locked test
    names below are pinned by the **Tests** section of that contract
    verbatim.
    """

    # ------------------------------------------------------------------ setup

    def _make_two_teams(self):
        """Return (team_a, players_a, team_b, players_b)."""
        team_a, players_a = make_team_with_slots("Hx04A")
        team_b, players_b = make_team_with_slots("Hx04B")
        return team_a, players_a, team_b, players_b

    def _make_round(
        self,
        *,
        team_red,
        team_blue,
        red_points: int = 100,
        blue_points: int = 50,
        match=None,
        round_number: int = 1,
        arena_map=None,
        is_simulated: bool = True,
    ) -> GameRound:
        gr = GameRound.objects.create(
            match=match,
            round_number=round_number,
            team_red=team_red,
            team_blue=team_blue,
            red_points=red_points,
            blue_points=blue_points,
            arena_map=arena_map,
            is_simulated=is_simulated,
            is_completed=True,
        )
        return gr

    def _make_match(
        self,
        *,
        team_red,
        team_blue,
        red_r1: int = 100,
        blue_r1: int = 50,
        red_r2: int = 100,
        blue_r2: int = 50,
        is_completed: bool = True,
    ) -> Match:
        return Match.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            red_round1_points=red_r1,
            blue_round1_points=blue_r1,
            red_round2_points=red_r2,
            blue_round2_points=blue_r2,
            is_completed=is_completed,
        )

    def _make_player_state(
        self,
        *,
        game_round: GameRound,
        player,
        team_color: str,
        role: str = "commander",
        final_lives: int = 3,
    ) -> PlayerRoundState:
        return PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=team_color,
            role=role,
            final_lives=final_lives,
        )

    def _make_tag_event(
        self,
        *,
        game_round: GameRound,
        actor,
        target,
        timestamp: int = 100,
        points: int = 100,
    ) -> GameEvent:
        """Create a tag GameEvent — the row the single-iterate tag query reads."""
        return GameEvent.objects.create(
            game_round=game_round,
            timestamp=timestamp,
            event_type="tag",
            actor=actor,
            target=target,
            points_awarded=points,
        )

    # ------------------------------------------------------------------ §1 picker

    def test_picker_mode_both_params_missing_renders_form_200(self):
        response = self.client.get(reverse("player_head_to_head"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "picker")
        self.assertIn(b"player-h2h-picker-form", response.content)

    def test_picker_mode_only_player_a_param_renders_form_with_a_preselected_200(
        self,
    ):
        _team_a, players_a, _team_b, _ = self._make_two_teams()
        player_a = players_a["commander"]
        response = self.client.get(
            reverse("player_head_to_head") + f"?player_a={player_a.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "picker")
        ctx_player_a = response.context.get("player_a")
        self.assertIsNotNone(ctx_player_a)
        self.assertEqual(ctx_player_a.id, player_a.id)

    # ------------------------------------------------------------------ §2 404

    def test_404_when_player_a_id_does_not_resolve(self):
        _team_a, _, _team_b, players_b = self._make_two_teams()
        player_b = players_b["commander"]
        response = self.client.get(
            reverse("player_head_to_head") + f"?player_a=9999999&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 404)

    def test_404_when_player_b_id_does_not_resolve(self):
        _team_a, players_a, _team_b, _ = self._make_two_teams()
        player_a = players_a["commander"]
        response = self.client.get(
            reverse("player_head_to_head") + f"?player_a={player_a.id}&player_b=9999999"
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------ §3 error

    def test_error_mode_when_player_a_equals_player_b_200_with_error_banner(self):
        _team_a, players_a, _team_b, _ = self._make_two_teams()
        player_a = players_a["commander"]
        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_a.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "error")
        self.assertIn(b"player-h2h-error-banner", response.content)

    # ------------------------------------------------------------------ §4 empty history

    def test_empty_history_results_mode_with_no_games_notice_200(self):
        """Two valid distinct Players who have never been on opposite teams →
        results mode with all-zero aggregates and the no-games notice."""
        _team_a, players_a, _team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "results")
        self.assertEqual(response.context["round_record"]["n"], 0)
        self.assertIn(b"player-h2h-no-games-notice", response.content)

    # ------------------------------------------------------------------ §5 full results

    def test_full_results_renders_round_record_score_margin_tag_stats_dom_ids(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        match = self._make_match(
            team_red=team_a,
            team_blue=team_b,
            red_r1=200,
            blue_r1=100,
            red_r2=150,
            blue_r2=120,
        )
        gr1 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=200,
            blue_points=100,
            match=match,
            round_number=1,
        )
        gr2 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=150,
            blue_points=120,
            match=match,
            round_number=2,
        )
        # PlayerRoundStates on opposite team_colors → both rounds qualify.
        self._make_player_state(
            game_round=gr1, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr1, player=player_b, team_color="blue", role="commander"
        )
        self._make_player_state(
            game_round=gr2, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr2, player=player_b, team_color="blue", role="commander"
        )
        # A couple of tag events both directions.
        self._make_tag_event(game_round=gr1, actor=player_a, target=player_b)
        self._make_tag_event(game_round=gr1, actor=player_b, target=player_a)

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "results")
        # 2 Rounds, player_a's team won both.
        self.assertEqual(response.context["round_record"]["wins"], 2)
        self.assertEqual(response.context["round_record"]["n"], 2)
        for dom_id in (
            b"player-h2h-round-record",
            b"player-h2h-score-margin",
            b"player-h2h-tags-a-to-b",
            b"player-h2h-tags-b-to-a",
        ):
            self.assertIn(dom_id, response.content)

    def test_full_results_renders_per_role_breakdown_table(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        match = self._make_match(team_red=team_a, team_blue=team_b)
        gr1 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=match,
            round_number=1,
        )
        gr2 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=80,
            match=match,
            round_number=2,
        )
        # Both rounds: player_a as commander, player_b as commander.
        for gr in (gr1, gr2):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        breakdown = response.context["per_role_breakdown"]
        self.assertGreaterEqual(len(breakdown), 1)
        self.assertIn(b"player-h2h-per-role-table", response.content)

    def test_full_results_renders_per_map_breakdown_table_with_no_map_3_zone_row(
        self,
    ):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        map_a = _ArenaMap.objects.create(
            name="Hx04 Arena Alpha", img_width=200, img_height=200
        )
        map_b = _ArenaMap.objects.create(
            name="Hx04 Arena Beta", img_width=200, img_height=200
        )
        match = self._make_match(team_red=team_a, team_blue=team_b)
        gr1 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=match,
            round_number=1,
            arena_map=map_a,
        )
        gr2 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=200,
            blue_points=10,
            match=match,
            round_number=2,
            arena_map=map_b,
        )
        gr_nomap = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=70,
            blue_points=70,
            match=None,
            round_number=1,
            arena_map=None,
        )
        for gr in (gr1, gr2, gr_nomap):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        breakdown = response.context["per_map_breakdown"]
        self.assertEqual(len(breakdown), 3)
        self.assertIn(b"player-h2h-per-map-table", response.content)
        labels = {row["arena_map_name"] for row in breakdown}
        self.assertIn("No map (3-zone)", labels)

    def test_full_results_renders_detail_list_reverse_chronological(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        # Two rounds, both qualify.
        gr1 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=10,
        )
        gr2 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=50,
            blue_points=80,
        )
        for gr in (gr1, gr2):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        detail = response.context["detail_list"]
        # 2 rounds in basket.
        self.assertGreaterEqual(len(detail), 2)
        self.assertIn(b"player-h2h-detail-list", response.content)
        # Reverse chronological: first row's date_played should be >= last row's.
        first_date = detail[0].get("date_played")
        last_date = detail[-1].get("date_played")
        self.assertIsNotNone(first_date)
        self.assertIsNotNone(last_date)
        self.assertGreaterEqual(first_date, last_date)

    def test_charts_render_canvas_and_json_script_blocks(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        match = self._make_match(team_red=team_a, team_blue=team_b)
        gr1 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=80,
            blue_points=40,
            match=match,
            round_number=1,
        )
        gr2 = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=60,
            blue_points=60,
            match=match,
            round_number=2,
        )
        for gr in (gr1, gr2):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("player-h2h-margin-series", body)
        self.assertIn("player-h2h-cumulative-wl-series", body)
        self.assertIn("player-h2h-margin-chart", body)
        self.assertIn("player-h2h-cumulative-wl-chart", body)
        # And the json_script payloads must be valid JSON.
        for series_id in (
            "player-h2h-margin-series",
            "player-h2h-cumulative-wl-series",
        ):
            marker = f'id="{series_id}"'
            idx = body.find(marker)
            self.assertGreater(idx, -1, f"json_script id {series_id} missing")
            tag_end = body.find(">", idx) + 1
            close = body.find("</script>", tag_end)
            payload = body[tag_end:close].strip()
            _json.loads(payload)

    # ------------------------------------------------------------------ §6 opposite-teams gate

    def test_opposite_teams_gate_excludes_same_team_rounds_from_basket(self):
        """A Round where both PRSes share team_color must NOT count.

        A Round where the two are on opposite team_colors MUST count.
        """
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        # Round 1: SAME team_color (both on "red") — excluded.
        gr_same = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        self._make_player_state(
            game_round=gr_same, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr_same, player=player_b, team_color="red", role="commander"
        )
        # Round 2: OPPOSITE team_colors — included.
        gr_opp = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=80, blue_points=80
        )
        self._make_player_state(
            game_round=gr_opp, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr_opp, player=player_b, team_color="blue", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # Only the opposite-team Round contributes.
        self.assertEqual(response.context["round_record"]["n"], 1)

    # ------------------------------------------------------------------ §7 role filter

    def test_role_param_both_semantic_filters_to_rounds_where_both_played_role(
        self,
    ):
        """``?role=commander`` keeps only Rounds where BOTH players played
        commander."""
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        # Round 1: both commander → kept.
        gr_both = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        self._make_player_state(
            game_round=gr_both, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr_both, player=player_b, team_color="blue", role="commander"
        )
        # Round 2: both heavy → dropped (not commander).
        gr_heavy = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=10, blue_points=10
        )
        self._make_player_state(
            game_round=gr_heavy, player=player_a, team_color="red", role="heavy"
        )
        self._make_player_state(
            game_round=gr_heavy, player=player_b, team_color="blue", role="heavy"
        )
        # Round 3: only one is commander → dropped.
        gr_mixed = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=20, blue_points=30
        )
        self._make_player_state(
            game_round=gr_mixed, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr_mixed, player=player_b, team_color="blue", role="heavy"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&role=commander"
        )
        self.assertEqual(response.status_code, 200)
        # Only the both-commander Round survives.
        self.assertEqual(response.context["round_record"]["n"], 1)

    def test_role_param_invalid_silently_ignored(self):
        """An unrecognised ``?role=...`` value is treated as no role filter."""
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        self._make_player_state(
            game_round=gr, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="blue", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&role=not-a-role"
        )
        self.assertEqual(response.status_code, 200)
        # The single round still counts (filter no-ops on invalid role).
        self.assertEqual(response.context["round_record"]["n"], 1)

    # ------------------------------------------------------------------ §8 provenance

    def test_provenance_param_real_filters_to_is_simulated_false(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr_sim = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        gr_real = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )
        for gr in (gr_sim, gr_real):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&provenance=real"
        )
        self.assertEqual(response.status_code, 200)
        # Only the real Round survives.
        self.assertEqual(response.context["round_record"]["n"], 1)
        # And that Round is the loss (player_a on red, 10 < 80).
        self.assertEqual(response.context["round_record"]["losses"], 1)

    def test_provenance_param_sim_filters_to_is_simulated_true(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr_sim = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        gr_real = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )
        for gr in (gr_sim, gr_real):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&provenance=sim"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["round_record"]["n"], 1)
        self.assertEqual(response.context["round_record"]["wins"], 1)

    def test_provenance_param_invalid_falls_back_to_all(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr_sim = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            is_simulated=True,
        )
        gr_real = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=10,
            blue_points=80,
            is_simulated=False,
        )
        for gr in (gr_sim, gr_real):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&provenance=garbage"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["provenance"], "all")
        self.assertEqual(response.context["round_record"]["n"], 2)

    # ------------------------------------------------------------------ §9 dates

    def test_from_and_to_date_filter_applied_to_rounds(self):
        """Only Rounds whose ``date_played`` falls in the window are kept."""
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        old = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=10, blue_points=10
        )
        new = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        for gr in (old, new):
            self._make_player_state(
                game_round=gr, player=player_a, team_color="red", role="commander"
            )
            self._make_player_state(
                game_round=gr, player=player_b, team_color="blue", role="commander"
            )
        # Force the old Round's date_played into the past.
        GameRound.objects.filter(pk=old.pk).update(
            date_played=_timezone.make_aware(_datetime(2020, 1, 1, 12, 0, 0))
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&from=2025-01-01"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["round_record"]["n"], 1)
        self.assertEqual(response.context["round_record"]["wins"], 1)
        self.assertIsNotNone(new.pk)

    def test_invalid_from_date_silently_ignored(self):
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        self._make_player_state(
            game_round=gr, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="blue", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}&from=not-a-date"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["round_record"]["n"], 1)

    # ------------------------------------------------------------------ §10 tag direction

    def test_tags_a_to_b_counted_independently_of_tags_b_to_a(self):
        """An asymmetric tag distribution must surface as asymmetric totals."""
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        self._make_player_state(
            game_round=gr, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="blue", role="commander"
        )
        # 3 tags a→b, 1 tag b→a.
        for ts in (10, 20, 30):
            self._make_tag_event(
                game_round=gr, actor=player_a, target=player_b, timestamp=ts
            )
        self._make_tag_event(
            game_round=gr, actor=player_b, target=player_a, timestamp=40
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        tag_stats = response.context["tag_stats"]
        self.assertEqual(tag_stats["total_tags_a_to_b"], 3)
        self.assertEqual(tag_stats["total_tags_b_to_a"], 1)

    # ------------------------------------------------------------------ §11 side-agnostic

    def test_side_agnostic_attribution_via_team_color_not_team_red_blue_ids(self):
        """A Round where the Team-side relationship is inverted from
        player_a's stored team must still attribute scores by team_color.

        Setup: team_red = team_a, but player_a played as blue and player_b
        played as red. The seam dict's ``player_a_team_score`` must equal
        ``game_round.blue_points`` (NOT red_points) because
        ``prs_a.team_color == 'blue'``.
        """
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        # Red wins big; player_a is actually on blue (the losing side).
        gr = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=200, blue_points=10
        )
        # Inverted: player_a on blue, player_b on red.
        self._make_player_state(
            game_round=gr, player=player_a, team_color="blue", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="red", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # 1 Round in basket, and it must be a LOSS for player_a (10 < 200).
        self.assertEqual(response.context["round_record"]["n"], 1)
        self.assertEqual(response.context["round_record"]["losses"], 1)
        self.assertEqual(response.context["round_record"]["wins"], 0)

    # ------------------------------------------------------------------ §12 career-page anchor

    def test_career_page_renders_player_h2h_link_anchor_with_player_a_prefilled(
        self,
    ):
        """The HX-01 ``/players/<id>/stats/`` page renders a
        ``player-h2h-link`` anchor whose href carries
        ``player_a={{ player_a.id }}``."""
        _team_a, players_a, _team_b, _ = self._make_two_teams()
        player_a = players_a["commander"]
        response = self.client.get(
            reverse("player_career_stats", kwargs={"player_id": player_a.id})
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("player-h2h-link", body)
        self.assertIn(f"player_a={player_a.id}", body)

    # ------------------------------------------------------------------ §13 standalone match_id

    def test_match_id_none_for_standalone_rounds_in_seam_dicts(self):
        """A Round with ``match=None`` (standalone) crossing the seam has
        ``match_id=None`` — surfaced via the detail list row's match_id."""
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        # Standalone Round (no match parent).
        gr = self._make_round(
            team_red=team_a,
            team_blue=team_b,
            red_points=100,
            blue_points=50,
            match=None,
        )
        self._make_player_state(
            game_round=gr, player=player_a, team_color="red", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="blue", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        detail = response.context["detail_list"]
        self.assertGreaterEqual(len(detail), 1)
        # The single row in the basket should reflect a None match_id.
        # The detail-list shape carries match_id explicitly.
        match_ids = [row.get("match_id") for row in detail]
        self.assertIn(None, match_ids)

    # ------------------------------------------------------------------ §14 player who switched teams

    def test_player_who_switched_teams_still_pairs_when_on_opposite_team_color(
        self,
    ):
        """player_a originally belongs to team_a. We create a Round where
        team_red = team_a / team_blue = team_b but player_a (a "team_a"
        player) actually played as BLUE (the team_b side) and player_b
        (a "team_b" player) played as RED — i.e. both players switched
        teams for that Round.

        Side-agnostic-by-team_color: this Round STILL counts, because the
        gate is "opposite team_colors", not "different home Team FK".
        """
        team_a, players_a, team_b, players_b = self._make_two_teams()
        player_a = players_a["commander"]
        player_b = players_b["commander"]
        gr = self._make_round(
            team_red=team_a, team_blue=team_b, red_points=100, blue_points=50
        )
        # Both players "switched sides" for the round: player_a as blue,
        # player_b as red. They're still on opposite team_colors → counts.
        self._make_player_state(
            game_round=gr, player=player_a, team_color="blue", role="commander"
        )
        self._make_player_state(
            game_round=gr, player=player_b, team_color="red", role="commander"
        )

        response = self.client.get(
            reverse("player_head_to_head")
            + f"?player_a={player_a.id}&player_b={player_b.id}"
        )
        self.assertEqual(response.status_code, 200)
        # The round still pairs by team_color, regardless of Team FK home.
        self.assertEqual(response.context["round_record"]["n"], 1)


# ===========================================================================
# LG-01d — Play Season views (Start Season + Play One Week + Play Two Months
# + Play Until End + Play Status).
#
# Seam contract: ``.claude/worktrees/lg-01d-seam-contract.md`` §11c.
# Five new test classes appended below. Tests run under
# ``CELERY_TASK_ALWAYS_EAGER=True`` (set by the project ``conftest.py``)
# so ``play_two_months`` / ``play_until_end`` `.delay()` calls execute
# synchronously in the request thread.
# ===========================================================================


from datetime import date as _lg01d_date
from django.test import TestCase as _Lg01dTestCase
from matches.models import League as _Lg01dLeague, Season as _Lg01dSeason


def _lg01d_active_season(prefix: str, n_teams: int = 2):
    """Build an ``active`` Season with ``n_teams`` enrolled."""
    league = _Lg01dLeague.objects.create(name=f"L{prefix}")
    season = _Lg01dSeason.objects.create(
        league=league, name="S1", start_date=_lg01d_date.today()
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _lg01d_draft_season(prefix: str, n_teams: int = 2):
    league = _Lg01dLeague.objects.create(name=f"L{prefix}")
    season = _Lg01dSeason.objects.create(
        league=league, name="S1", start_date=_lg01d_date.today()
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    return season, teams


_LG01D_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# TestLg01dStartSeason
# ---------------------------------------------------------------------------


class TestLg01dStartSeason(_Lg01dTestCase):
    """POST ``/seasons/<id>/start-season/`` flips ``draft → active``.

    Idempotent on the "already active" double-submit race (the LG-01
    ``Season.clean()`` ``"non-completed"`` substring is swallowed and the
    view redirects). 400 + ``play_error`` on ``< 2`` teams.
    """

    def test_post_flips_draft_to_active_and_redirects_to_dashboard(self) -> None:
        season, _teams = _lg01d_draft_season("StartA", n_teams=2)
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[season.id]),
        )
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_post_with_less_than_two_teams_returns_400_with_play_error(
        self,
    ) -> None:
        league = _Lg01dLeague.objects.create(name="LStartTooFew")
        season = _Lg01dSeason.objects.create(
            league=league, name="S1", start_date=_lg01d_date.today()
        )
        # Zero teams enrolled.
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(response.context.get("play_error"))
        # Season state still draft (no flip).
        season.refresh_from_db()
        self.assertEqual(season.state, "draft")

    def test_post_on_already_active_season_returns_idempotent_302_no_play_error(
        self,
    ) -> None:
        """The LG-01 ``Season.clean()`` raises with the substring
        ``"non-completed"`` on a second activation attempt. The view
        catches it and redirects (idempotent).
        """
        season, _teams = _lg01d_active_season("StartActive", n_teams=2)
        # Second POST.
        response = self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[season.id]),
        )

    def test_get_returns_405(self) -> None:
        season, _teams = _lg01d_draft_season("StartGet", n_teams=2)
        response = self.client.get(reverse("start_season", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_on_missing_season_id_returns_404(self) -> None:
        response = self.client.post(reverse("start_season", args=[9_999_999]))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# TestLg01dPlayWeek
# ---------------------------------------------------------------------------


class TestLg01dPlayWeek(_Lg01dTestCase):
    """POST ``/seasons/<id>/play-week/`` plays exactly one matchday inside
    a single ``@transaction.atomic`` block. Idempotent 302 on a completed
    Season; 400 on non-active; full-matchday rollback on mid-loop error.
    """

    def test_post_plays_exactly_one_matchdays_worth_of_rounds(self) -> None:
        # N=3 ⇒ 6 fixtures / 6 matchdays (odd-N: 1 pair per matchday).
        season, _teams = _lg01d_active_season("PWWeek", n_teams=3)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            GameRound.objects.filter(match__season=season).count(),
            1,
            "play_week on N=3 first matchday should run exactly 1 fixture",
        )
        # Second POST — second matchday's 1 fixture.
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)

    def test_post_is_atomic_across_the_matchday(self) -> None:
        """Patch ``simulate_scheduled_round`` to raise mid-loop on a
        matchday containing > 1 fixture; assert NO ``GameRound`` rows
        persisted (full rollback).
        """
        # N=4 ⇒ matchday 1 has 2 fixtures.
        season, _teams = _lg01d_active_season("PWAtomic", n_teams=4)
        original = BatchSimulator.simulate_scheduled_round
        state = {"calls": 0}

        def _raises_on_second(self, season_, ta, tb, rnd, **kw):
            state["calls"] += 1
            if state["calls"] == 2:
                raise ValueError("mid-matchday failure")
            return original(self, season_, ta, tb, rnd, **kw)

        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            with patch.object(
                BatchSimulator,
                "simulate_scheduled_round",
                _raises_on_second,
            ):
                response = self.client.post(reverse("play_week", args=[season.id]))
        # View returns 400 (caught + re-rendered with play_error).
        self.assertEqual(response.status_code, 400)
        # Atomic block rolled back the first commit too.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 0)

    def test_post_on_completed_season_returns_idempotent_302_no_play_error(
        self,
    ) -> None:
        # Build a completed Season directly — no fixtures left to play.
        league = _Lg01dLeague.objects.create(name="LPWCompleted")
        season = _Lg01dSeason.objects.create(
            league=league,
            name="Done",
            start_date=_lg01d_date.today(),
            state="completed",
            starting_team_ids_json=[],
        )
        # The state guard rejects non-active ⇒ 400 with play_error per
        # the contract for ``play_week`` on non-active. The "completed +
        # no unplayed" idempotent 302 path applies only when the Season
        # is active and to_play is empty.
        response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 400)

    def test_post_on_non_active_season_returns_400_with_play_error(self) -> None:
        season, _teams = _lg01d_draft_season("PWDraft", n_teams=2)
        response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(response.context.get("play_error"))

    def test_get_returns_405(self) -> None:
        season, _teams = _lg01d_active_season("PWGet", n_teams=2)
        response = self.client.get(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_on_missing_season_id_returns_404(self) -> None:
        response = self.client.post(reverse("play_week", args=[9_999_999]))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# TestLg01dPlayTwoMonths
# ---------------------------------------------------------------------------


class TestLg01dPlayTwoMonths(_Lg01dTestCase):
    """POST ``/seasons/<id>/play-two-months/`` enqueues
    ``play_season_task.delay(season_id, max_matchdays=8)`` and returns
    202 + ``{job_id, season_id}`` JSON. Under EAGER the task body runs
    synchronously.
    """

    def test_post_returns_202_with_job_id_and_season_id_keys(self) -> None:
        season, _teams = _lg01d_active_season("P2MJob", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.content.decode())
        self.assertIn("job_id", payload)
        self.assertIn("season_id", payload)
        self.assertEqual(payload["season_id"], season.id)
        self.assertIsInstance(payload["job_id"], str)

    def test_under_eager_task_runs_to_completion_and_persists_rounds(
        self,
    ) -> None:
        # N=2 ⇒ 2 fixtures < 8 matchdays ⇒ Season completes.
        season, _teams = _lg01d_active_season("P2MRun", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        # Under EAGER, the task ran inline and rounds are persisted.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_post_on_non_active_season_returns_400_with_play_error(self) -> None:
        season, _teams = _lg01d_draft_season("P2MDraft", n_teams=2)
        response = self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(response.context.get("play_error"))

    def test_get_returns_405(self) -> None:
        season, _teams = _lg01d_active_season("P2MGet", n_teams=2)
        response = self.client.get(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# TestLg01dPlayUntilEnd
# ---------------------------------------------------------------------------


class TestLg01dPlayUntilEnd(_Lg01dTestCase):
    """POST ``/seasons/<id>/play-until-end/`` — async, identical to
    ``play_two_months`` except ``max_matchdays=None``.
    """

    def test_post_returns_202_with_job_id_and_season_id_keys(self) -> None:
        season, _teams = _lg01d_active_season("PUEJob", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_until_end", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.content.decode())
        self.assertIn("job_id", payload)
        self.assertIn("season_id", payload)
        self.assertEqual(payload["season_id"], season.id)

    def test_under_eager_task_runs_full_season_to_completion(self) -> None:
        season, _teams = _lg01d_active_season("PUERun", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_until_end", args=[season.id]))
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team)

    def test_post_on_non_active_season_returns_400_with_play_error(self) -> None:
        season, _teams = _lg01d_draft_season("PUEDraft", n_teams=2)
        response = self.client.post(reverse("play_until_end", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(response.context.get("play_error"))

    def test_get_returns_405(self) -> None:
        season, _teams = _lg01d_active_season("PUEGet", n_teams=2)
        response = self.client.get(reverse("play_until_end", args=[season.id]))
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# TestLg01dPlayStatus
# ---------------------------------------------------------------------------


class TestLg01dPlayStatus(_Lg01dTestCase):
    """GET ``/seasons/<id>/play-status/<job_id>/`` returns the locked
    5-key polling JSON. Mocks ``matches.league_views.AsyncResult`` to fake each
    Celery state.
    """

    def _make_async_result(self, state: str, info=None, result_payload=None):
        """Build a fake ``AsyncResult``-shaped object exposing
        ``.state`` / ``.info`` / ``.result`` for the polling view to
        read.
        """

        class _Fake:
            def __init__(self):
                self.state = state
                self.info = info
                self.result = result_payload

        return _Fake()

    def test_progress_state_returns_running_with_completed_total(self) -> None:
        season, _teams = _lg01d_active_season("PSProg", n_teams=2)
        fake = self._make_async_result(
            "PROGRESS",
            info={"completed": 5, "total": 12},
        )
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["completed"], 5)
        self.assertEqual(payload["total"], 12)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["season_id"], season.id)

    def test_success_state_returns_complete_with_completed_total(self) -> None:
        season, _teams = _lg01d_active_season("PSSucc", n_teams=2)
        fake = self._make_async_result(
            "SUCCESS",
            result_payload={"completed": 12, "total": 12},
        )
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["completed"], 12)
        self.assertEqual(payload["total"], 12)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["season_id"], season.id)

    def test_failure_state_returns_error_with_str_info(self) -> None:
        season, _teams = _lg01d_active_season("PSFail", n_teams=2)
        fake = self._make_async_result("FAILURE", info=Exception("boom"))
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "boom")
        self.assertEqual(payload["completed"], 0)
        self.assertEqual(payload["total"], 0)

    def test_unknown_job_id_returns_running_with_zero_zero(self) -> None:
        """A never-submitted ``job_id`` resolves to Celery ``PENDING`` ⇒
        the polling view maps that to ``"running"`` with 0/0 counts.
        """
        season, _teams = _lg01d_active_season("PSUnk", n_teams=2)
        fake = self._make_async_result("PENDING", info=None)
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={
                        "season_id": season.id,
                        "job_id": "never-submitted-id",
                    },
                )
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["completed"], 0)
        self.assertEqual(payload["total"], 0)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["season_id"], season.id)

    def test_get_returns_200_on_any_job_id(self) -> None:
        season, _teams = _lg01d_active_season("PSAny", n_teams=2)
        fake = self._make_async_result("PENDING", info=None)
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "anything"},
                )
            )
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        season, _teams = _lg01d_active_season("PSPost", n_teams=2)
        response = self.client.post(
            reverse(
                "play_status",
                kwargs={"season_id": season.id, "job_id": "anything"},
            )
        )
        self.assertEqual(response.status_code, 405)

    def test_season_id_query_param_echoed_in_response_when_provided(
        self,
    ) -> None:
        """The URL kwarg is authoritative — the query param is the carry
        pattern. The URL kwarg always wins in the response payload.
        """
        season, _teams = _lg01d_active_season("PSQP", n_teams=2)
        fake = self._make_async_result("PENDING", info=None)
        # Send a DIFFERENT ?season_id= query param — the response should
        # echo the URL kwarg, not the query param.
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "anything"},
                )
                + f"?season_id={season.id + 999}"
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["season_id"], season.id)


# ===========================================================================
# LG-01f — session-write extension tests for the LG-01d Play Season views.
#
# Seam contract: ``.claude/worktrees/lg-01f-seam-contract.md`` §9g — one
# session-write assertion per LG-01d view: start_season / play_week /
# play_two_months / play_until_end / play_status.
# ===========================================================================


class TestLg01fLg01dSessionWrites(_Lg01dTestCase):
    """LG-01f — each LG-01d view writes
    ``request.session["last_league_id"] = season.league_id`` after the
    404 guard. ``play_status`` writes on every poll.
    """

    def test_lg01f_start_season_writes_last_league_id(self) -> None:
        season, _teams = _lg01d_draft_season("LfStartS", n_teams=2)
        self.client.post(reverse("start_season", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_lg01f_play_week_writes_last_league_id(self) -> None:
        season, _teams = _lg01d_active_season("LfPWk", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_lg01f_play_two_months_writes_last_league_id(self) -> None:
        season, _teams = _lg01d_active_season("LfP2M", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_lg01f_play_until_end_writes_last_league_id(self) -> None:
        season, _teams = _lg01d_active_season("LfPUE", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_until_end", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_lg01f_play_status_writes_last_league_id(self) -> None:
        """``play_status`` is a polling endpoint — the session write
        fires on every poll so ``last_league_id`` stays fresh.
        """
        season, _teams = _lg01d_active_season("LfPSt", n_teams=2)

        class _Fake:
            state = "PENDING"
            info = None
            result = None

        with patch("matches.league_views.AsyncResult", return_value=_Fake()):
            self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "anything"},
                )
            )
        self.assertEqual(self.client.session["last_league_id"], season.league_id)


class TestPlayStatusSessionWriteGuard(_Lg01dTestCase):
    """SQLite write-contention fix — ``play_status`` only writes
    ``last_league_id`` when it changes. Polling every ~0.5s with an
    unconditional session write forced a ``django_session`` write on every
    poll that collided with the play loop and raised "database is locked".
    """

    class _Fake:
        state = "PENDING"
        info = None
        result = None

    def _call_play_status(self, season, *, preset_league_id):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from matches.league_views import play_status

        request = RequestFactory().get("/ignored/")
        request.session = SessionStore()
        if preset_league_id is not None:
            request.session["last_league_id"] = preset_league_id
        # Reset the modified flag so we only observe the view's own write.
        request.session.modified = False
        with patch("matches.league_views.AsyncResult", return_value=self._Fake()):
            play_status(request, season_id=season.id, job_id="anything")
        return request

    def test_no_session_write_when_value_unchanged(self) -> None:
        season, _teams = _lg01d_active_season("LfPStGuardSame", n_teams=2)
        request = self._call_play_status(season, preset_league_id=season.league_id)
        self.assertFalse(request.session.modified)

    def test_session_write_when_value_changes(self) -> None:
        season, _teams = _lg01d_active_season("LfPStGuardDiff", n_teams=2)
        request = self._call_play_status(
            season, preset_league_id=season.league_id + 999
        )
        self.assertTrue(request.session.modified)
        self.assertEqual(request.session["last_league_id"], season.league_id)


# ===========================================================================
# LG-02-Part2c-2 — play_week advances exactly one global matchday and crosses
# the RR1 -> RR2 boundary on the boundary click.
#
# Seam contract: ``.claude/worktrees/lg-02-part2c-2-seam-contract.md`` §5.2 + §7.
# Appended as a NEW class; no existing class is modified.
# ===========================================================================


from matches.models import SeasonPhase as _Lg02c2SeasonPhase  # noqa: E402


def _lg02c2_two_rr_active_season(prefix: str, n_teams: int = 2):
    """An active Season with two ordinal-ordered ``round_robin`` phases.

    Returns ``(season, teams, rr1, rr2)``.
    """
    league = _Lg01dLeague.objects.create(name=f"L{prefix}")
    season = _Lg01dSeason.objects.create(
        league=league, name="S1", start_date=_lg01d_date.today()
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr1 = _Lg02c2SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    rr2 = _Lg02c2SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="round_robin"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2


class TestLg02c2PlayWeekMultiRr(_Lg01dTestCase):
    """``play_week`` on a two-RR-phase Season advances exactly one global
    matchday and crosses the RR1->RR2 boundary on the boundary click."""

    def test_play_week_advances_one_global_matchday_attributed_to_rr1(
        self,
    ) -> None:
        # N=2 ⇒ RR1 spans 2 global matchdays (round-1, then round-2 mirror).
        season, _teams, rr1, rr2 = _lg02c2_two_rr_active_season("PWMrr1", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        # Exactly one matchday's worth of Rounds ran, all attributed to RR1.
        self.assertEqual(GameRound.objects.filter(match__season_phase=rr1).count(), 1)
        self.assertEqual(GameRound.objects.filter(match__season_phase=rr2).count(), 0)

    def test_play_week_crosses_boundary_into_rr2_after_rr1_exhausted(
        self,
    ) -> None:
        """Once every RR1 matchday is played, the next ``play_week`` click
        lands on the first RR2 matchday — the global calendar crosses the
        phase boundary.
        """
        season, _teams, rr1, rr2 = _lg02c2_two_rr_active_season("PWMrrX", n_teams=2)
        # N=2 ⇒ RR1 has 2 global matchdays. Click play_week until RR1 is
        # exhausted (defensively bounded).
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            for _ in range(2):
                self.client.post(reverse("play_week", args=[season.id]))
        season.refresh_from_db()
        # RR1 fully played; RR2 still untouched.
        self.assertTrue(season._phase_complete(rr1))
        self.assertEqual(GameRound.objects.filter(match__season_phase=rr2).count(), 0)

        # The boundary click now lands on RR2's first matchday.
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        self.assertGreater(GameRound.objects.filter(match__season_phase=rr2).count(), 0)

    def test_season_not_completed_mid_rr2(self) -> None:
        season, _teams, rr1, rr2 = _lg02c2_two_rr_active_season("PWMrrMid", n_teams=2)
        # Play just the first global matchday (RR1 round 1).
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            self.client.post(reverse("play_week", args=[season.id]))
        season.refresh_from_db()
        # Season stays active until the FINAL RR phase finishes.
        self.assertEqual(season.state, "active")
        self.assertIsNone(season.champion_team_id)


# ===========================================================================
# LG-02-Part2c-3f — phase-aware play_week (weekly playoff pacing)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3f-seam-contract.md`` §2.2 /
# §6.4: when the Season cursor is a built + active tournament phase, a POST to
# play_week drains EXACTLY ONE bracket STAGE (the lowest incomplete stage's
# nodes gain winner_id, the NEXT stage stays unresolved) then 302s; when the
# cursor is an RR phase the existing RR matchday path runs unchanged (RR Rounds
# created); 405 on GET; non-active → ``play_error`` re-render.
#
# Assertions are on the count of nodes whose winner_id is set + which stage
# advanced + status codes — NEVER on exact simulated point totals. Appended as
# a NEW class; no existing class is modified. These WILL fail until the Code
# agent lands the phase-aware play_week branch — the TDD red state.


def _lg3f_rr_then_tournament_season(prefix: str, n_teams: int = 4):
    """An active Season: ordinal-1 ``round_robin`` + ordinal-2 ``tournament``
    SeasonPhase, ``n_teams`` enrolled, started. Returns
    ``(season, teams, rr, tournament_phase)``.
    """
    league = _Lg01dLeague.objects.create(name=f"L{prefix}")
    season = _Lg01dSeason.objects.create(
        league=league, name="S1", start_date=_lg01d_date.today()
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr = _Lg02c2SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    tournament_phase = _Lg02c2SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr, tournament_phase


def _lg3f_play_rr(season, teams) -> None:
    """Play every RR fixture (auto-builds the tournament phase on completion)."""
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


class TestLg3fPlayWeekPhaseAware(_Lg01dTestCase):
    """``play_week`` drains exactly ONE bracket stage when the cursor is a
    built tournament phase; the existing RR path is unchanged on an RR cursor."""

    def _resolved_node_count(self, tournament) -> int:
        from matches.models import BracketNode

        return BracketNode.objects.filter(
            tournament=tournament, winner__isnull=False
        ).count()

    def test_post_on_tournament_cursor_drains_one_stage_then_302(self) -> None:
        from matches.models import BracketNode

        season, teams, _rr, tp = _lg3f_rr_then_tournament_season("PWPlayoff", n_teams=4)
        _lg3f_play_rr(season, teams)
        tp.refresh_from_db()
        self.assertIsNotNone(tp.tournament_id)
        tournament = tp.tournament

        # The lowest incomplete stage is winners round 1 (2 nodes for N=4).
        before = self._resolved_node_count(tournament)
        stage_size = BracketNode.objects.filter(
            tournament=tournament, bracket_type="winners", bracket_round=1
        ).count()
        self.assertEqual(stage_size, 2)

        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 302)

        tournament.refresh_from_db()
        after = self._resolved_node_count(tournament)
        # Exactly the lowest stage's nodes advanced (one whole stage).
        self.assertEqual(after - before, stage_size)
        # The NEXT stage (the final) is still unresolved.
        final = tournament.nodes.get(advances_to__isnull=True)
        self.assertIsNone(final.winner_id)

    def test_post_on_rr_cursor_runs_rr_matchday_path_unchanged(self) -> None:
        season, _teams, rr, _tp = _lg3f_rr_then_tournament_season("PWRr", n_teams=4)
        # RR not yet played ⇒ the cursor is the RR phase; play_week runs the
        # existing RR matchday path (creates RR Rounds attributed to rr).
        before = GameRound.objects.filter(match__season_phase=rr).count()
        self.assertEqual(before, 0)
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 302)
        after = GameRound.objects.filter(match__season_phase=rr).count()
        self.assertGreater(after, 0)

    def test_get_returns_405(self) -> None:
        season, _teams, _rr, _tp = _lg3f_rr_then_tournament_season(
            "PWGet405", n_teams=4
        )
        response = self.client.get(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_on_non_active_season_returns_400_with_play_error(self) -> None:
        league = _Lg01dLeague.objects.create(name="LPWNonActive")
        season = _Lg01dSeason.objects.create(
            league=league, name="Draft", start_date=_lg01d_date.today()
        )
        # Draft (non-active) → play_error re-render.
        response = self.client.post(reverse("play_week", args=[season.id]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(response.context.get("play_error"))

    def test_repeated_play_week_drains_bracket_stage_by_stage_to_champion(
        self,
    ) -> None:
        season, teams, _rr, tp = _lg3f_rr_then_tournament_season("PWDrain", n_teams=4)
        _lg3f_play_rr(season, teams)
        tp.refresh_from_db()
        # Two stages for an N=4 bracket: two play_week clicks crown a champion.
        with patch.object(BatchSimulator, "ROUND_TICKS", _LG01D_FAST_TICKS):
            for _ in range(6):
                tp.refresh_from_db()
                if tp.tournament.state == "completed":
                    break
                self.client.post(reverse("play_week", args=[season.id]))
        tp.refresh_from_db()
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)


@pytest.mark.django_db
class TestPlayerRowEliminationSemantic:
    """Regression coverage for the ``_player_row`` ``is_eliminated`` /
    ``eliminated_timestamp`` carryover, lifted from the deleted
    ``teams/tests/test_template_filters.py``.

    Pre-TIME-01 the survived sentinel was tick 900; a hardcoded ``val > 900``
    check on the round-detail page wrongly painted second-half eliminations as
    survivors. TIME-01 raised the sentinel to ``SURVIVED_SENTINEL = 1801``;
    the deleted template filter encoded the corrected rule; the rule now lives
    inside ``matches.views._player_row``. These tests pin the boundary cases
    against the dict the HTML template renders so a future regression on the
    sentinel value is caught at the view-builder layer, not at visual review.
    """

    def _state_with_eliminated_at(self, ts):
        from teams.models import Player, Team

        team = Team.objects.create(name=f"PlayerRowElim-{ts!r}")
        player = Player.objects.create(team=team, name="Test", height="6'0\"")
        gr = GameRound.objects.create(
            team_red=team, team_blue=team, round_number=1, is_completed=True
        )
        return _make_state(
            gr,
            player,
            team_color="red",
            role="scout",
            was_eliminated_at=ts,
            final_lives=0 if (ts < 1801 and ts != 0) else 5,
        )

    def test_survived_full_round_sentinel_carries_false(self):
        """``was_eliminated_at = 1801`` (SURVIVED_SENTINEL) ⇒ is_eliminated=False."""
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(1801)
        row = _player_row(ps)
        assert row["is_eliminated"] is False
        assert row["eliminated_timestamp"] == ""

    def test_eliminated_in_first_half_carries_true(self):
        """``was_eliminated_at = 478`` ⇒ is_eliminated=True with MM:SS string."""
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(478)
        row = _player_row(ps)
        assert row["is_eliminated"] is True
        assert row["eliminated_timestamp"] != ""

    def test_eliminated_in_second_half_carries_true(self):
        """Regression: pre-TIME-01 the ``> 900`` check wrongly survived this
        tick. ``was_eliminated_at = 1277`` is the broken-case fixture (Round 80
        Vipers Heavy).
        """
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(1277)
        row = _player_row(ps)
        assert row["is_eliminated"] is True

    def test_eliminated_at_old_threshold_carries_true(self):
        """Pre-fix boundary: ``val == 920`` was wrongly treated as survived
        because 920 > 900. Round 80 had a Vipers Scout-A at tick 920.
        """
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(920)
        row = _player_row(ps)
        assert row["is_eliminated"] is True

    def test_eliminated_just_below_sentinel_carries_true(self):
        """``was_eliminated_at = 1800`` is the last legal elimination tick of
        a 1800-tick round — must still be treated as eliminated.
        """
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(1800)
        row = _player_row(ps)
        assert row["is_eliminated"] is True

    def test_zero_eliminated_at_carries_false(self):
        """``was_eliminated_at = 0`` is the legacy "never set" sentinel — must
        be treated as not-eliminated to avoid false-positives on old rounds.
        """
        from matches.views import _player_row

        ps = self._state_with_eliminated_at(0)
        row = _player_row(ps)
        assert row["is_eliminated"] is False
        assert row["eliminated_timestamp"] == ""
