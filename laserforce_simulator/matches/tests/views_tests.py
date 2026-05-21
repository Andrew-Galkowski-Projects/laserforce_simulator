import pytest
from unittest.mock import patch
from django.test import Client
from django.urls import reverse, NoReverseMatch

from matches.models import GameRound, Match
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
        """POST two valid rosters → 200 and ``side_advantage`` in results.

        The view stashes ``avg_seeds``/``outlier_seeds`` in the session and
        passes ``results`` (including the new ``side_advantage`` sub-dict) to
        the template context. Keep this light per existing view-test patterns.
        """
        red, _ = make_team_with_slots("Sim08ViewRed")
        blue, _ = make_team_with_slots("Sim08ViewBlue")
        client = Client()

        response = client.post(
            reverse("simulate_batch"),
            {"team_red": red.id, "team_blue": blue.id, "n": "10"},
        )

        assert response.status_code == 200
        assert (
            "results" in response.context
        ), "batch view must pass aggregate 'results' to the template context"
        results = response.context["results"]
        assert "side_advantage" in results, (
            "results must include the SIM-08 'side_advantage' physical-side "
            "panel data"
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
# SIM-09 — batch view threads arena_map through to BatchSimulator + save path
# ---------------------------------------------------------------------------


def _make_minimal_arena_map(name="Sim09BatchMap"):
    """Build a tiny but fully-configured ArenaMap usable by BatchSimulator.run.

    Mirrors the smallest-config fixtures in ``test_map.py`` so the view-test
    exercises the real form QuerySet + view → simulator plumbing.
    """
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )
    from core.map_processing import compute_sight_lines

    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = ArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data,
        confirmed=True,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        x_px=zone_size // 2,
        y_px=zone_size // 2,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
    )
    return arena_map


@pytest.mark.django_db
class TestSim09BatchArenaMapPlumbing:
    """SIM-09 view path: ``simulate_batch`` accepts an ``arena_map`` form
    field, threads it through to ``BatchSimulator.run(arena_map=...)``, and
    stashes the resolved map id in the session under
    ``batch_seeds["arena_map_id"]`` so ``save_batch_games`` /
    ``_run_save_job`` can reload it and pass it to ``save_games``.
    """

    def test_simulate_batch_threads_arena_map_to_run(self):
        """POST with ``arena_map`` → view calls ``BatchSimulator.run`` with
        that exact ArenaMap, and stashes ``arena_map_id`` in the session.
        """
        red, _ = make_team_with_slots("Sim09BatchR")
        blue, _ = make_team_with_slots("Sim09BatchB")
        arena_map = _make_minimal_arena_map("Sim09BatchMap")
        client = Client()

        # Spy on BatchSimulator.run so we capture the arena_map kwarg without
        # actually running 10 full simulations (and so the assertion does not
        # depend on round outcomes).
        original_run = BatchSimulator.run
        captured: dict = {}

        def _spy_run(self, team_red, team_blue, n=100, *, arena_map=None, **kwargs):
            captured["arena_map"] = arena_map
            # Run a tiny real batch so the view's downstream code (histogram,
            # session stash) sees a populated result dict.
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                return original_run(
                    self, team_red, team_blue, n=2, arena_map=arena_map, **kwargs
                )

        with patch.object(BatchSimulator, "run", _spy_run):
            response = client.post(
                reverse("simulate_batch"),
                {
                    "team_red": red.id,
                    "team_blue": blue.id,
                    "n": "10",
                    "arena_map": arena_map.id,
                },
            )

        assert response.status_code == 200
        assert captured.get("arena_map") == arena_map, (
            "simulate_batch view did not forward arena_map to " "BatchSimulator.run"
        )
        # Session stash records the arena_map_id so the save path can reload.
        session_seeds = client.session.get("batch_seeds")
        assert session_seeds is not None, "view did not stash batch_seeds"
        assert (
            session_seeds.get("arena_map_id") == arena_map.id
        ), "batch_seeds['arena_map_id'] missing or wrong"

    def test_simulate_batch_no_arena_map_stashes_none(self):
        """Omitting ``arena_map`` from the form keeps the 3-zone fallback —
        ``BatchSimulator.run`` is called with ``arena_map=None`` and the
        session stash records ``arena_map_id=None``.
        """
        red, _ = make_team_with_slots("Sim09BatchNoMapR")
        blue, _ = make_team_with_slots("Sim09BatchNoMapB")
        client = Client()

        original_run = BatchSimulator.run
        captured: dict = {}

        def _spy_run(self, team_red, team_blue, n=100, *, arena_map=None, **kwargs):
            captured["arena_map"] = arena_map
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                return original_run(
                    self, team_red, team_blue, n=2, arena_map=arena_map, **kwargs
                )

        with patch.object(BatchSimulator, "run", _spy_run):
            response = client.post(
                reverse("simulate_batch"),
                {"team_red": red.id, "team_blue": blue.id, "n": "10"},
            )

        assert response.status_code == 200
        assert captured.get("arena_map") is None
        session_seeds = client.session.get("batch_seeds")
        assert session_seeds is not None
        assert session_seeds.get("arena_map_id") is None

    def test_run_save_job_threads_arena_map_to_save_games(self):
        """``_run_save_job(arena_map_id=...)`` resolves the id to an
        ``ArenaMap`` and passes it as the ``arena_map=`` kwarg to
        ``BatchSimulator.save_games``. Calls ``_run_save_job`` synchronously
        to side-step the cross-thread patch.object race the threaded
        ``save_batch_games`` view would otherwise introduce — the threading
        layer is just ``threading.Thread(target=_run_save_job)``, so testing
        the target directly covers the load-bearing arena_map plumbing.
        """
        from matches.views import _run_save_job, _SAVE_JOBS

        red, _ = make_team_with_slots("Sim09SaveR")
        blue, _ = make_team_with_slots("Sim09SaveB")
        arena_map = _make_minimal_arena_map("Sim09SaveMap")

        captured: dict = {}

        def _spy_save_games(self, t_red, t_blue, seeds, n, *, arena_map=None):
            captured["arena_map"] = arena_map
            captured["team_red"] = t_red
            captured["team_blue"] = t_blue
            return []

        # _run_save_job's seeds arg is a list of (seed, flipped) pairs;
        # the spy doesn't care about content, only that the kwarg is forwarded.
        seeds = [(12345, False)]
        job_id = "sim09-arena-map-job"
        with patch.object(BatchSimulator, "save_games", _spy_save_games):
            _run_save_job(job_id, red.id, blue.id, seeds, 1, arena_map.id)

        assert (
            "arena_map" in captured
        ), "_run_save_job never called save_games with an arena_map kwarg"
        assert captured["arena_map"] == arena_map, (
            "save_games received the wrong ArenaMap; expected the one "
            "stashed under arena_map_id"
        )
        assert captured["team_red"] == red
        assert captured["team_blue"] == blue
        # Job status reaches "done".
        assert _SAVE_JOBS[job_id]["status"] == "done"

    def test_run_save_job_none_arena_map_id_passes_none(self):
        """``arena_map_id=None`` (the 3-zone fallback) is forwarded as
        ``arena_map=None`` — the no-map path is preserved end-to-end.
        """
        from matches.views import _run_save_job

        red, _ = make_team_with_slots("Sim09SaveNoneR")
        blue, _ = make_team_with_slots("Sim09SaveNoneB")

        captured: dict = {}

        def _spy_save_games(self, t_red, t_blue, seeds, n, *, arena_map=None):
            captured["arena_map"] = arena_map
            return []

        seeds = [(99999, False)]
        with patch.object(BatchSimulator, "save_games", _spy_save_games):
            _run_save_job("none-job", red.id, blue.id, seeds, 1, None)

        assert "arena_map" in captured
        assert captured["arena_map"] is None

    def test_save_batch_games_view_threads_arena_map_id_into_worker_args(self):
        """``save_batch_games`` constructs ``threading.Thread`` with
        ``arena_map_id`` (read from the session stash) in the args tuple.
        Spies on ``threading.Thread`` to capture the args without actually
        spawning a thread — pins the seam between the synchronous view layer
        and the asynchronous worker so a future refactor that drops the
        arena_map_id arg fails here rather than silently falling back to
        no-map.
        """
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim09ThreadArgR")
        blue, _ = make_team_with_slots("Sim09ThreadArgB")
        arena_map = _make_minimal_arena_map("Sim09ThreadArgMap")
        client = Client()

        # First seed the session via a real batch POST so save_batch_games
        # has avg_seeds + arena_map_id in the session.
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            client.post(
                reverse("simulate_batch"),
                {
                    "team_red": red.id,
                    "team_blue": blue.id,
                    "n": "10",
                    "arena_map": arena_map.id,
                },
            )

        captured_args: dict = {}

        class _FakeThread:
            def __init__(self, *, target, args, daemon):
                captured_args["target"] = target
                captured_args["args"] = args
                captured_args["daemon"] = daemon

            def start(self):
                # Do not actually spawn — we only want the constructor args.
                pass

        with patch.object(views_module.threading, "Thread", _FakeThread):
            response = client.post(
                reverse("save_batch_games"), {"game_type": "avg", "n": "1"}
            )

        assert response.status_code == 200, response.content
        assert captured_args["target"] is views_module._run_save_job
        # _run_save_job(job_id, team_red_id, team_blue_id, seeds, n, arena_map_id)
        # — arena_map_id is the 6th positional arg.
        assert (
            len(captured_args["args"]) == 6
        ), "save_batch_games no longer passes arena_map_id positionally"
        assert (
            captured_args["args"][5] == arena_map.id
        ), "save_batch_games dropped the session-stashed arena_map_id"

    def test_run_save_job_stale_arena_map_id_treated_as_none(self):
        """A stale ``arena_map_id`` (map deleted between simulation and save)
        is resolved to ``None`` rather than crashing the job.
        """
        from matches.views import _run_save_job, _SAVE_JOBS

        red, _ = make_team_with_slots("Sim09SaveStaleR")
        blue, _ = make_team_with_slots("Sim09SaveStaleB")

        captured: dict = {}

        def _spy_save_games(self, t_red, t_blue, seeds, n, *, arena_map=None):
            captured["arena_map"] = arena_map
            return []

        stale_id = 9_999_999  # no ArenaMap with this PK exists
        seeds = [(11111, False)]
        job_id = "stale-job"
        with patch.object(BatchSimulator, "save_games", _spy_save_games):
            _run_save_job(job_id, red.id, blue.id, seeds, 1, stale_id)

        assert captured.get("arena_map") is None
        assert _SAVE_JOBS[job_id]["status"] == "done"
