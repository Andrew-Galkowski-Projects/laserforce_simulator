import json

import pytest
from unittest.mock import patch
from django.test import Client
from django.urls import reverse, NoReverseMatch

from matches.models import GameRound, Match
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots


def _run_thread_inline(captured: dict | None = None):
    """Build a ``_FakeThread`` class that runs its ``target(*args)`` inline on
    ``.start()``.

    Used to drive ``_run_batch_job`` (and similar background-thread runners)
    synchronously in tests, side-stepping the cross-thread race a real
    ``threading.Thread`` would introduce. Originally introduced for SIM-10
    view tests; reused by the SIM-08 / SIM-09 blast-radius updates after
    SIM-10 made the batch-POST asynchronous.
    """

    class _FakeThread:
        def __init__(self, *, target, args, daemon=True):
            if captured is not None:
                captured["target"] = target
                captured["args"] = args
                captured["daemon"] = daemon
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    return _FakeThread


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
        """POST drives a job through to ``complete`` and the partial
        aggregate surfaces ``side_advantage``.

        SIM-10 reshaped this view: POST returns ``{job_id, ...}`` JSON and
        the aggregate is no longer in ``response.context["results"]`` — it
        is in ``_BATCH_JOBS[job_id]["partial"]`` and surfaces to the client
        through the polling endpoint. The SIM-08 contract (the side-advantage
        sub-dict is present and consistent) still holds; we exercise it
        through the new job-status surface.
        """
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim08ViewRed")
        blue, _ = make_team_with_slots("Sim08ViewBlue")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                post_resp = client.post(
                    reverse("simulate_batch"),
                    {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                )

        assert post_resp.status_code == 200, post_resp.content
        body = json.loads(post_resp.content.decode())
        job_id = body["job_id"]

        status_resp = client.get(reverse("batch_simulate_status", args=[job_id]))
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
        """POST with ``arena_map`` → the batch-job thread calls
        ``BatchSimulator.run_incremental`` with that ``ArenaMap``, the
        ``_BATCH_JOBS`` entry records the resolved id, and a subsequent
        ``batch_simulate_status`` poll on ``complete`` stashes
        ``arena_map_id`` into the session.

        SIM-10 reshaped this path: POST is now async, ``run`` is no longer
        called by the view (``run_incremental`` is, on the background
        thread), and the session is populated by the first ``complete``-
        observing poll rather than inline on POST.
        """
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim09BatchR")
        blue, _ = make_team_with_slots("Sim09BatchB")
        arena_map = _make_minimal_arena_map("Sim09BatchMap")
        client = Client()

        original_run_incremental = BatchSimulator.run_incremental
        captured: dict = {}

        def _spy_run_incremental(
            self, team_red, team_blue, n=100, *, arena_map=None, **kwargs
        ):
            captured["arena_map"] = arena_map
            # Run a tiny real batch so the job's downstream aggregation /
            # session-stash code paths exercise real snapshots.
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                yield from original_run_incremental(
                    self, team_red, team_blue, n=2, arena_map=arena_map, **kwargs
                )

        with patch.object(BatchSimulator, "run_incremental", _spy_run_incremental):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                response = client.post(
                    reverse("simulate_batch"),
                    {
                        "team_red": red.id,
                        "team_blue": blue.id,
                        "n": "10",
                        "arena_map": arena_map.id,
                    },
                )

        assert response.status_code == 200, response.content
        body = json.loads(response.content.decode())
        assert (
            body.get("arena_map_id") == arena_map.id
        ), "POST JSON did not carry arena_map_id"
        assert (
            captured.get("arena_map") == arena_map
        ), "simulate_batch did not forward arena_map to BatchSimulator.run_incremental"

        # Poll the status endpoint once to trigger the SIM-10 session
        # handover. The first complete-observing poll writes batch_seeds.
        status_resp = client.get(
            reverse("batch_simulate_status", args=[body["job_id"]])
        )
        assert status_resp.status_code == 200
        session_seeds = client.session.get("batch_seeds")
        assert (
            session_seeds is not None
        ), "first complete-poll did not stash batch_seeds"
        assert (
            session_seeds.get("arena_map_id") == arena_map.id
        ), "batch_seeds['arena_map_id'] missing or wrong after complete-poll"

    def test_simulate_batch_no_arena_map_stashes_none(self):
        """Omitting ``arena_map`` from the form keeps the 3-zone fallback —
        ``BatchSimulator.run_incremental`` is called with ``arena_map=None``
        and the session stash records ``arena_map_id=None`` after a
        complete-observing poll.
        """
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim09BatchNoMapR")
        blue, _ = make_team_with_slots("Sim09BatchNoMapB")
        client = Client()

        original_run_incremental = BatchSimulator.run_incremental
        captured: dict = {}

        def _spy_run_incremental(
            self, team_red, team_blue, n=100, *, arena_map=None, **kwargs
        ):
            captured["arena_map"] = arena_map
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                yield from original_run_incremental(
                    self, team_red, team_blue, n=2, arena_map=arena_map, **kwargs
                )

        with patch.object(BatchSimulator, "run_incremental", _spy_run_incremental):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                response = client.post(
                    reverse("simulate_batch"),
                    {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                )

        assert response.status_code == 200, response.content
        body = json.loads(response.content.decode())
        assert body.get("arena_map_id") is None
        assert captured.get("arena_map") is None

        status_resp = client.get(
            reverse("batch_simulate_status", args=[body["job_id"]])
        )
        assert status_resp.status_code == 200
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

        SIM-10 made the simulate POST async, so the session is now populated
        by the first ``complete``-observing poll of ``batch_simulate_status``
        — not inline on the simulate POST. We drive the batch thread inline,
        poll once to populate ``batch_seeds`` in the session, then capture
        ``save_batch_games``'s ``threading.Thread`` construction so the
        ``arena_map_id`` seam is pinned independent of polling indirection.
        """
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim09ThreadArgR")
        blue, _ = make_team_with_slots("Sim09ThreadArgB")
        arena_map = _make_minimal_arena_map("Sim09ThreadArgMap")
        client = Client()

        # Drive a real batch job to ``complete`` synchronously via the inline
        # ``_FakeThread``, then poll status once so the SIM-10 session
        # handover writes ``batch_seeds`` into the session.
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                post_resp = client.post(
                    reverse("simulate_batch"),
                    {
                        "team_red": red.id,
                        "team_blue": blue.id,
                        "n": "10",
                        "arena_map": arena_map.id,
                    },
                )
        assert post_resp.status_code == 200, post_resp.content
        body = json.loads(post_resp.content.decode())
        client.get(reverse("batch_simulate_status", args=[body["job_id"]]))
        assert (
            client.session.get("batch_seeds") is not None
        ), "complete-poll did not populate batch_seeds; cannot test save flow"

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


# ---------------------------------------------------------------------------
# SIM-10 — progressive batch simulation: view surface tests.
#
# Pinned by `.claude/worktrees/sim-10-seam-contract.md` §2 (view surface) and
# §5.2 (test boundary). The synchronous render-with-`results`-context path is
# replaced by a JSON POST that dispatches a background thread plus a polling
# endpoint, so these tests use a `_FakeThread` that runs the target inline
# (mirrors the existing `TestSim09BatchArenaMapPlumbing._FakeThread` pattern)
# to drive the job to completion without `time.sleep` or real threads.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSim10SimulateBatchPostReturnsJson:
    """§5.2 — POST to ``simulate_batch`` returns 200 + JSON with exactly the
    locked key set. Form-validation failures keep returning HTML (existing
    behaviour preserved).
    """

    _POST_KEYS = {
        "job_id",
        "team_red_id",
        "team_red_name",
        "team_blue_id",
        "team_blue_name",
        "arena_map_id",
        "n",
    }

    def test_post_returns_json_with_locked_key_set(self):
        from matches import views as views_module

        red, _ = make_team_with_slots("Sim10PostR")
        blue, _ = make_team_with_slots("Sim10PostB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                response = client.post(
                    reverse("simulate_batch"),
                    {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                )

        assert response.status_code == 200, response.content
        assert response["Content-Type"].startswith(
            "application/json"
        ), f"POST must return JSON; got Content-Type={response['Content-Type']!r}"
        import json as _json

        body = _json.loads(response.content.decode())
        assert set(body.keys()) == self._POST_KEYS, (
            f"POST JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {self._POST_KEYS!r}"
        )
        assert isinstance(body["job_id"], str) and body["job_id"]
        assert isinstance(body["team_red_id"], int) and body["team_red_id"] == red.id
        assert (
            isinstance(body["team_red_name"], str) and body["team_red_name"] == red.name
        )
        assert isinstance(body["team_blue_id"], int) and body["team_blue_id"] == blue.id
        assert (
            isinstance(body["team_blue_name"], str)
            and body["team_blue_name"] == blue.name
        )
        assert body["arena_map_id"] is None or isinstance(body["arena_map_id"], int)
        assert isinstance(body["n"], int) and body["n"] == 10

    def test_form_validation_failure_returns_html_not_json(self):
        """Same-team validation failure still renders HTML (with a Django
        ``messages.error``) — the JSON branch only applies once form
        validation passes.
        """
        red, _ = make_team_with_slots("Sim10ValidR")
        client = Client()

        response = client.post(
            reverse("simulate_batch"),
            {"team_red": red.id, "team_blue": red.id, "n": "10"},
        )

        assert response.status_code == 200
        # HTML, not JSON. The exact body content is unstable so we only
        # assert on the content type — the JSON branch sets
        # application/json explicitly.
        ctype = response.get("Content-Type", "")
        assert "application/json" not in ctype, (
            "Form-validation failures must return HTML, not the JSON "
            f"dispatch response; got Content-Type={ctype!r}"
        )


@pytest.mark.django_db
class TestSim10BatchSimulateStatusShape:
    """§5.2 — polling ``batch_simulate_status`` returns a JSON body whose
    keys are exactly the locked job-dict set with the types from §2.3.
    """

    _STATUS_KEYS = {
        "status",
        "completed",
        "total",
        "partial",
        "error",
        "team_red_id",
        "team_blue_id",
        "arena_map_id",
    }

    def test_status_endpoint_returns_locked_shape(self):
        from matches import views as views_module
        import json as _json

        red, _ = make_team_with_slots("Sim10StatusR")
        blue, _ = make_team_with_slots("Sim10StatusB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                post_resp = client.post(
                    reverse("simulate_batch"),
                    {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                )
                assert post_resp.status_code == 200
                job_id = _json.loads(post_resp.content.decode())["job_id"]

                status_resp = client.get(
                    reverse("batch_simulate_status", args=[job_id])
                )

        assert status_resp.status_code == 200
        body = _json.loads(status_resp.content.decode())
        assert set(body.keys()) == self._STATUS_KEYS, (
            f"status JSON keys drifted: got {set(body.keys())!r}, "
            f"expected {self._STATUS_KEYS!r}"
        )
        assert isinstance(body["status"], str)
        assert body["status"] in ("running", "complete", "error")
        assert isinstance(body["completed"], int)
        assert isinstance(body["total"], int)
        # partial: dict | None
        assert body["partial"] is None or isinstance(body["partial"], dict)
        # error: str | None
        assert body["error"] is None or isinstance(body["error"], str)
        assert isinstance(body["team_red_id"], int)
        assert isinstance(body["team_blue_id"], int)
        assert body["arena_map_id"] is None or isinstance(body["arena_map_id"], int)


@pytest.mark.django_db
class TestSim10BatchSimulateStatusLifecycle:
    """§5.2 — driving the job synchronously (via inline ``_FakeThread``), the
    observed ``status`` sequence is a prefix of
    ``["running", ..., "running", "complete"]``, ``completed`` is monotonic
    non-decreasing, and the final poll has ``completed == total`` and
    ``partial`` not None.
    """

    def test_lifecycle_running_then_complete(self):
        from matches import views as views_module
        import json as _json

        red, _ = make_team_with_slots("Sim10LifeR")
        blue, _ = make_team_with_slots("Sim10LifeB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            # Force ≥ 2 snapshots even on small n by pinning chunk size to 1.
            with patch("matches.simulation._chunk_size_for", return_value=1):
                with patch.object(
                    views_module.threading, "Thread", _run_thread_inline()
                ):
                    post_resp = client.post(
                        reverse("simulate_batch"),
                        {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                    )
                    job_id = _json.loads(post_resp.content.decode())["job_id"]

                    # Inline thread already ran target to completion — polling
                    # observes the final ``complete`` state. We poll twice to
                    # exercise the GET path and verify it stays consistent
                    # (no mutation on repeat poll beyond the documented
                    # session-handover guard, asserted separately).
                    polls = []
                    for _ in range(2):
                        r = client.get(reverse("batch_simulate_status", args=[job_id]))
                        polls.append(_json.loads(r.content.decode()))

        statuses = [p["status"] for p in polls]
        # Every observed status is a valid lifecycle value, and the last
        # observed status must be `complete` (target ran inline).
        for st in statuses:
            assert st in (
                "running",
                "complete",
            ), f"unexpected status in lifecycle: {st!r}"
        assert statuses[-1] == "complete"

        # Monotonic non-decreasing `completed`.
        completed = [p["completed"] for p in polls]
        for prev, cur in zip(completed, completed[1:]):
            assert cur >= prev, f"completed regressed: {prev} → {cur}"

        # Final poll: `completed == total`, `partial` populated.
        final = polls[-1]
        assert final["completed"] == final["total"] == 10
        assert isinstance(final["partial"], dict)
        # Partial is the final aggregate dict — it carries the documented
        # `_aggregate_batch` keys.
        for key in (
            "n",
            "red_wins",
            "blue_wins",
            "ties",
            "avg_red_score",
            "avg_blue_score",
            "avg_seeds",
            "outlier_seeds",
        ):
            assert key in final["partial"], f"partial aggregate missing key {key!r}"


@pytest.mark.django_db
class TestSim10BatchSimulateStatusErrorPath:
    """§5.2 — patching ``run_incremental`` to raise propagates to the
    job dict as ``status='error'`` with ``error == str(exc)``.
    """

    def test_error_status_propagates_exception_message(self):
        from matches import views as views_module
        import json as _json

        red, _ = make_team_with_slots("Sim10ErrR")
        blue, _ = make_team_with_slots("Sim10ErrB")
        client = Client()

        def _raises(*args, **kwargs):
            # Generator-shaped raise: yield nothing, then raise on consumption.
            if False:
                yield  # pragma: no cover - keep this a generator
            raise RuntimeError("contrived")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(BatchSimulator, "run_incremental", _raises):
                with patch.object(
                    views_module.threading, "Thread", _run_thread_inline()
                ):
                    post_resp = client.post(
                        reverse("simulate_batch"),
                        {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                    )
                    job_id = _json.loads(post_resp.content.decode())["job_id"]

                    status_resp = client.get(
                        reverse("batch_simulate_status", args=[job_id])
                    )

        body = _json.loads(status_resp.content.decode())
        assert (
            body["status"] == "error"
        ), f"expected status='error' after raise, got {body['status']!r}"
        assert (
            body["error"] == "contrived"
        ), f"expected error='contrived', got {body['error']!r}"


@pytest.mark.django_db
class TestSim10BatchSimulateStatusNotFound:
    """§5.2 — GET ``batch_simulate_status`` with an unknown job id returns
    404 with JSON ``{"status": "not_found"}`` (mirrors
    ``save_batch_status``).
    """

    def test_not_found_returns_404_json(self):
        import json as _json

        client = Client()
        resp = client.get(reverse("batch_simulate_status", args=["does-not-exist"]))
        assert resp.status_code == 404
        body = _json.loads(resp.content.decode())
        assert body == {"status": "not_found"}


@pytest.mark.django_db
class TestSim10SessionHandoverWritesOnceOnComplete:
    """§5.2 / §2.6 — on the FIRST poll observing ``status == 'complete'`` for
    a given ``job_id``, the view writes ``request.session["batch_seeds"]``.
    Subsequent polls observing ``complete`` skip the write (guard hit because
    ``request.session["batch_seeds"]["job_id"]`` already matches).
    ``save_batch_games`` continues to work end-to-end against the unchanged
    session entry — a regression check that the existing seed-handover flow
    is preserved.
    """

    def test_session_handover_writes_once_and_guard_holds(self):
        from matches import views as views_module
        import json as _json

        red, _ = make_team_with_slots("Sim10SessR")
        blue, _ = make_team_with_slots("Sim10SessB")
        client = Client()

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(views_module.threading, "Thread", _run_thread_inline()):
                post_resp = client.post(
                    reverse("simulate_batch"),
                    {"team_red": red.id, "team_blue": blue.id, "n": "10"},
                )
                job_id = _json.loads(post_resp.content.decode())["job_id"]

                # First poll observes `complete` → triggers single write.
                first = client.get(reverse("batch_simulate_status", args=[job_id]))

        assert first.status_code == 200
        first_body = _json.loads(first.content.decode())
        assert first_body["status"] == "complete"

        session_seeds = client.session.get("batch_seeds")
        assert (
            session_seeds is not None
        ), "first complete-poll must write request.session['batch_seeds']"
        for key in (
            "job_id",
            "team_red_id",
            "team_blue_id",
            "arena_map_id",
            "avg_seeds",
            "outlier_seeds",
        ):
            assert (
                key in session_seeds
            ), f"batch_seeds missing locked key {key!r}: {session_seeds!r}"
        assert session_seeds["job_id"] == job_id
        assert session_seeds["team_red_id"] == red.id
        assert session_seeds["team_blue_id"] == blue.id
        # avg_seeds + outlier_seeds match the final aggregate carried in
        # `partial`.
        partial = first_body["partial"]
        assert session_seeds["avg_seeds"] == partial["avg_seeds"]
        assert session_seeds["outlier_seeds"] == partial["outlier_seeds"]

        # Mutate the session between polls; the next complete-poll must NOT
        # overwrite (guard hit because job_id matches).
        session = client.session
        session["batch_seeds"]["avg_seeds"] = "SENTINEL"
        session.save()

        second = client.get(reverse("batch_simulate_status", args=[job_id]))
        assert second.status_code == 200
        assert _json.loads(second.content.decode())["status"] == "complete"

        session_after = client.session.get("batch_seeds")
        assert session_after is not None
        assert session_after["avg_seeds"] == "SENTINEL", (
            "second complete-poll overwrote session despite matching "
            "`job_id` guard; the §2.6 single-write contract is broken"
        )

        # Regression: `save_batch_games` still works end-to-end against the
        # unchanged session entry — restore the avg_seeds before saving so
        # the SENTINEL marker does not leak into the save path.
        session = client.session
        session["batch_seeds"]["avg_seeds"] = partial["avg_seeds"]
        session.save()

        # Spy on save_games so the save flow does not actually persist
        # rounds — we only need to assert the session was readable.
        captured: dict = {}

        def _spy_save_games(self, t_red, t_blue, seeds, n, *, arena_map=None):
            captured["seeds"] = seeds
            captured["n"] = n
            return []

        from matches.views import _run_save_job

        with patch.object(BatchSimulator, "save_games", _spy_save_games):
            # Drive the save target directly to skip the thread layer (the
            # save-thread plumbing is covered by TestSim09 — we only care
            # that the seeds are readable from the session).
            saved_session = client.session
            seeds = saved_session["batch_seeds"]["avg_seeds"][:1]
            _run_save_job(
                "sim10-save-job",
                saved_session["batch_seeds"]["team_red_id"],
                saved_session["batch_seeds"]["team_blue_id"],
                seeds,
                1,
                saved_session["batch_seeds"]["arena_map_id"],
            )

        assert "seeds" in captured, (
            "save_batch_games regression: save_games was never called "
            "from the unaltered session entry"
        )


# ---------------------------------------------------------------------------
# SIM-11 — wire ``workers=`` into the UI batch path.
#
# Pinned by `.claude/worktrees/sim-11-seam-contract.md` §2 (the 19-row
# ``(n, cpu_count, expected)`` table) and §4 (the test boundary). Two new
# classes:
#   - ``TestSim11WorkersFor`` — every row of the locked table is a
#     parametrised case against the pure ``_workers_for(n)`` helper.
#   - ``TestSim11RunBatchJobPassesWorkers`` — drives ``_run_batch_job``
#     synchronously with ``matches.views.BatchSimulator`` patched to a
#     ``MagicMock`` and asserts the call site adds ``workers=_workers_for(n)``
#     to ``run_incremental(...)``. The other kwargs (``arena_map`` /
#     ``master_seed``) are SIM-09 / SIM-10 contracts and are NOT re-pinned
#     here (see §2 of the seam contract).
# ---------------------------------------------------------------------------


class TestSim11WorkersFor:
    """SIM-11 §2 — every row of the locked ``(n, cpu_count, expected)`` table
    is a parametrised case against the module-level helper
    ``matches.views._workers_for``.

    The helper is a pure function of ``n`` and the live ``os.cpu_count()``;
    we patch ``os.cpu_count`` (which the helper reads via ``os.cpu_count()``)
    rather than ``matches.views.os.cpu_count`` — both targets are equivalent
    in CPython since ``import os`` binds the same module object, but
    patching ``os.cpu_count`` is the simpler form and is the one the seam
    contract names. No DB / no ``Client`` / no ``RequestFactory`` — pure unit.
    """

    @pytest.mark.parametrize(
        "n,cpu_count,expected",
        [
            # n < 50 → 1 regardless of cpu_count (early return, no
            # cpu_count read).
            (0, 1, 1),
            (0, 4, 1),
            (0, 16, 1),
            (1, 4, 1),
            (10, 4, 1),
            (49, 4, 1),
            (49, 16, 1),
            # n >= 50 → min(os.cpu_count() or 1, 4).
            (50, 1, 1),
            (50, 2, 2),
            (50, 4, 4),
            (50, 8, 4),
            (50, 16, 4),
            (100, 4, 4),
            (500, 8, 4),
            (1000, 64, 4),
            # os.cpu_count() is None → `or 1` fallback (CPython contract:
            # `os.cpu_count()` may return None on some platforms).
            (50, None, 1),
            (1000, None, 1),
            # Defensive: negative n behaves as small n (workers=1).
            # Production form-validation guards against negative n, but the
            # lock pins the helper's behaviour at the boundary.
            (-1, 4, 1),
            (-100, 16, 1),
        ],
    )
    def test_workers_for_table(self, monkeypatch, n, cpu_count, expected):
        import os as _os
        from matches.views import _workers_for

        monkeypatch.setattr(_os, "cpu_count", lambda: cpu_count)
        assert _workers_for(n) == expected, (
            f"_workers_for({n!r}) with os.cpu_count()={cpu_count!r} "
            f"returned {_workers_for(n)!r}, expected {expected!r}"
        )


@pytest.mark.django_db
class TestSim11RunBatchJobPassesWorkers:
    """SIM-11 §3 / §4 — ``_run_batch_job`` adds exactly one kwarg
    (``workers=_workers_for(n)``) to its
    ``BatchSimulator().run_incremental(...)`` call.

    We patch ``matches.views.BatchSimulator`` (NOT
    ``matches.simulation.BatchSimulator``) because ``_run_batch_job`` reads
    the symbol via ``from .simulation import BatchSimulator`` at module
    load, so the bound name at the call site is ``matches.views.BatchSimulator``.

    The patched simulator's ``run_incremental`` returns ``iter([])`` so the
    ``for snap in ...`` loop in ``_run_batch_job`` exits immediately and the
    function writes its terminal ``status="complete"`` block under
    ``_JOBS_LOCK``. We then sniff ``call_args.kwargs["workers"]`` — no other
    kwarg is asserted.
    """

    def _populate_initial_job(self, job_id, n, team_red_id, team_blue_id):
        """Mirror the initial-write shape ``simulate_batch`` performs before
        spawning the daemon thread (SIM-10 contract — see
        ``matches/views.py:115`` comment "Initial entry was inserted under
        the lock in ``simulate_batch`` before this thread started").
        """
        from matches.views import _BATCH_JOBS, _JOBS_LOCK

        with _JOBS_LOCK:
            _BATCH_JOBS[job_id] = {
                "status": "running",
                "completed": 0,
                "total": n,
                "partial": None,
                "error": None,
                "team_red_id": team_red_id,
                "team_blue_id": team_blue_id,
                "arena_map_id": None,
            }

    def test_run_batch_job_passes_workers_one_for_small_n(self):
        """For ``n = 10`` (< 50), ``_workers_for(n)`` is ``1`` regardless of
        CPU count — the call site passes ``workers=1``.
        """
        from unittest.mock import MagicMock
        from matches.views import _run_batch_job

        red, _ = make_team_with_slots("Sim11SmallR")
        blue, _ = make_team_with_slots("Sim11SmallB")
        job_id = "sim11-small-job"
        n = 10
        self._populate_initial_job(job_id, n, red.id, blue.id)

        mock_simulator_cls = MagicMock()
        mock_simulator_cls.return_value.run_incremental.return_value = iter([])

        with patch("matches.views.BatchSimulator", mock_simulator_cls):
            _run_batch_job(job_id, red.id, blue.id, n, None, None)

        run_incremental = mock_simulator_cls.return_value.run_incremental
        assert run_incremental.call_count == 1, (
            f"expected exactly one run_incremental call, got "
            f"{run_incremental.call_count}"
        )
        kwargs = run_incremental.call_args.kwargs
        assert (
            "workers" in kwargs
        ), f"_run_batch_job dropped the workers kwarg; kwargs={kwargs!r}"
        assert (
            kwargs["workers"] == 1
        ), f"for n={n} (< 50), workers must be 1; got {kwargs['workers']!r}"

    def test_run_batch_job_passes_workers_helper_value_for_large_n(self):
        """For ``n = 50`` (>= 50), ``_workers_for(n)`` resolves the live
        ``os.cpu_count()`` (capped at 4) — the call site passes that exact
        value. We import ``_workers_for`` and use it to compute the expected
        value rather than hard-coding ``4`` so the assertion passes on any
        CI box regardless of CPU count.
        """
        from unittest.mock import MagicMock
        from matches.views import _run_batch_job, _workers_for

        red, _ = make_team_with_slots("Sim11LargeR")
        blue, _ = make_team_with_slots("Sim11LargeB")
        job_id = "sim11-large-job"
        n = 50
        self._populate_initial_job(job_id, n, red.id, blue.id)

        mock_simulator_cls = MagicMock()
        mock_simulator_cls.return_value.run_incremental.return_value = iter([])

        with patch("matches.views.BatchSimulator", mock_simulator_cls):
            _run_batch_job(job_id, red.id, blue.id, n, None, None)

        run_incremental = mock_simulator_cls.return_value.run_incremental
        assert run_incremental.call_count == 1, (
            f"expected exactly one run_incremental call, got "
            f"{run_incremental.call_count}"
        )
        kwargs = run_incremental.call_args.kwargs
        assert (
            "workers" in kwargs
        ), f"_run_batch_job dropped the workers kwarg; kwargs={kwargs!r}"
        expected = _workers_for(n)
        assert kwargs["workers"] == expected, (
            f"for n={n} (>= 50), workers must equal _workers_for(n)="
            f"{expected!r}; got {kwargs['workers']!r}"
        )
