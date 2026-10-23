"""ANG-06f T1 -- ``matches.playback.round_playback``: per-player Playback
snapshots read from the logged after-event values (ADR-0044, spec #305).

Unit cases use hand-built ``GameEvent`` rows; the final-state invariant runs
over seeded, fully simulated Rounds.
"""

from __future__ import annotations

import random

from django.test import TestCase

from matches.models import GameEvent, GameRound, PlayerRoundState
from matches.playback import build_playback_map, round_playback
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_playback_map import _make_arena_map
from matches.tests.test_playback_map import _round as _map_round


def _snap(tick: int, **overrides) -> dict:
    base = {
        "tick": tick,
        "score": 0,
        "lives": 0,
        "shots": 0,
        "missiles": 0,
        "sp": 0,
        "tags": 0,
        "misses": 0,
        "downed_at": None,
        "eliminated": False,
        "rapid_fire": False,
        "nuking": False,
    }
    base.update(overrides)
    return base


class _PlaybackCase(TestCase):
    """Red commander + scout vs blue heavy + medic, known starting values."""

    def setUp(self) -> None:
        super().setUp()
        self.red, red_players = make_team_with_slots("PbRed")
        self.blue, blue_players = make_team_with_slots("PbBlue")
        self.game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.cmd = red_players["commander"]
        self.scout = red_players["scout"]
        self.hvy = blue_players["heavy"]
        self.med = blue_players["medic"]
        for player, color, role, lives, shots, missiles in (
            (self.cmd, "red", "commander", 15, 30, 5),
            (self.scout, "red", "scout", 15, 30, 0),
            (self.hvy, "blue", "heavy", 10, 20, 5),
            (self.med, "blue", "medic", 20, 15, 0),
        ):
            PlayerRoundState.objects.create(
                game_round=self.game_round,
                player=player,
                team_color=color,
                role=role,
                starting_lives=lives,
                starting_shots=shots,
                starting_missiles=missiles,
            )

    def event(self, tick: int, event_type: str, actor, target=None, **kw) -> None:
        GameEvent.objects.create(
            game_round=self.game_round,
            timestamp=tick,
            event_type=event_type,
            actor=actor,
            target=target,
            points_awarded=kw.pop("points", 0),
            description=kw.pop("description", ""),
            metadata=kw.pop("metadata", {}),
        )

    def snapshots(self, player) -> list[dict]:
        data = round_playback(self.game_round)
        by_id = {p["id"]: p["snapshots"] for p in data["players"]}
        return by_id[player.pk]


class TestFinalStateInvariant(TestCase):
    """ADR-0044: each player's last snapshot equals their Round scoreboard
    row. An engine change that alters state without logging it fails here."""

    def test_last_snapshot_equals_player_round_state(self) -> None:
        for seed in (1, 7, 42):
            with self.subTest(seed=seed):
                # Seed BEFORE creating players, or the Round doesn't reproduce.
                random.seed(seed)
                red, _ = make_team_with_slots(f"InvR{seed}")
                blue, _ = make_team_with_slots(f"InvB{seed}")
                game_round = BatchSimulator().simulate_single_round_detailed(
                    red, blue, fidelity="full"
                )
                last = {
                    p["id"]: p["snapshots"][-1]
                    for p in round_playback(game_round)["players"]
                }
                states = PlayerRoundState.objects.filter(game_round=game_round)
                self.assertEqual(len(states), len(last))
                for ps in states:
                    snap = last[ps.player_id]
                    self.assertEqual(
                        {k: snap[k] for k in _INVARIANT_FIELDS},
                        {
                            "score": ps.points_scored,
                            "lives": ps.final_lives,
                            "shots": ps.final_shots,
                            "missiles": ps.final_missiles,
                            "sp": ps.final_special,
                            "eliminated": ps.was_eliminated_at < SURVIVED_SENTINEL,
                            "tags": ps.tags_made,
                            "misses": ps.shots_missed,
                        },
                        f"seed {seed}, {ps.role} {ps.player_id}",
                    )


class TestSpecialKindMatchesEngine(TestCase):
    """Rapid fire and nuke arming are recognised only by the description text
    ``_use_special`` writes. Pin that matching against the engine's own
    ``specials_used`` counter, so a wording change fails here instead of
    silently dropping the Playback flags (which the invariant doesn't cover)."""

    def test_every_scout_and_commander_special_is_recognised(self) -> None:
        from matches.playback import _special_kind

        checked = 0
        for seed in (1, 7, 42):
            random.seed(seed)
            red, _ = make_team_with_slots(f"SkR{seed}")
            blue, _ = make_team_with_slots(f"SkB{seed}")
            game_round = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )
            expected = {"scout": "rapid_fire", "commander": "nuke_armed"}
            for ps in PlayerRoundState.objects.filter(
                game_round=game_round, role__in=expected
            ):
                recognised = sum(
                    _special_kind(ev, ev.metadata or {}) == expected[ps.role]
                    for ev in game_round.events.filter(
                        event_type="special", actor_id=ps.player_id
                    )
                )
                with self.subTest(seed=seed, role=ps.role, player=ps.player_id):
                    self.assertEqual(recognised, ps.specials_used)
                checked += ps.specials_used
        self.assertGreater(checked, 0, "no Scout / Commander special in the seeds")


_INVARIANT_FIELDS = (
    "score",
    "lives",
    "shots",
    "missiles",
    "sp",
    "eliminated",
    "tags",
    "misses",
)


class TestBuildPlaybackMap(TestCase):
    def test_map_carries_arena_map_id_and_name(self) -> None:
        arena_map, zone_size = _make_arena_map("PbArenaT1")
        game_round, red_players, _ = _map_round(arena_map, zone_size, prefix="PbM")
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=red_players["commander"],
            team_color="red",
            role="commander",
            cell_row=0,
            cell_col=0,
        )
        payload = build_playback_map(game_round)
        self.assertEqual(
            payload["arena_map"], {"id": arena_map.pk, "name": "PbArenaT1"}
        )
        self.assertEqual(payload["zone_size"], zone_size)
        self.assertEqual(len(payload["players"]), 1)

    def test_views_reexports_the_moved_builder(self) -> None:
        from matches import views

        self.assertIs(views._build_playback_map, build_playback_map)


class TestOpeningSnapshot(_PlaybackCase):
    def test_tick_zero_snapshot_equals_starting_fields(self) -> None:
        self.assertEqual(
            self.snapshots(self.hvy), [_snap(0, lives=10, shots=20, missiles=5)]
        )
        self.assertEqual(
            self.snapshots(self.med), [_snap(0, lives=20, shots=15, missiles=0)]
        )


def _actor(lives: int, shots: int, points: int, sp: int) -> dict:
    return {
        "actor_lives": lives,
        "actor_shots": shots,
        "actor_points": points,
        "sp": sp,
    }


def _target(lives: int, shots: int, points: int) -> dict:
    return {"target_lives": lives, "target_shots": shots, "target_points": points}


class TestSnapshotsOnChange(_PlaybackCase):
    def test_values_are_read_from_logged_after_event_metadata(self) -> None:
        self.event(
            12,
            "resupply_ammo",
            self.med,
            self.hvy,
            metadata={**_actor(20, 15, 7, 2), **_target(10, 29, -3)},
        )
        self.assertEqual(
            self.snapshots(self.med)[-1],
            _snap(12, lives=20, shots=15, score=7, sp=2, missiles=0),
        )
        self.assertEqual(
            self.snapshots(self.hvy)[-1],
            _snap(12, lives=10, shots=29, score=-3, missiles=5, downed_at=12),
        )

    def test_same_tick_events_collapse_to_the_state_after_the_last(self) -> None:
        self.event(5, "miss", self.cmd, metadata=_actor(15, 29, 0, 0))
        self.event(5, "miss", self.cmd, metadata=_actor(15, 28, 0, 0))
        snaps = self.snapshots(self.cmd)
        self.assertEqual([s["tick"] for s in snaps], [0, 5])
        self.assertEqual(snaps[-1]["shots"], 28)
        self.assertEqual(snaps[-1]["misses"], 2)

    def test_no_snapshot_when_nothing_changes(self) -> None:
        """A base capture logging unchanged values, and an event for someone
        else, give the player no new snapshot."""
        self.event(7, "base_capture", self.hvy, metadata=_actor(10, 20, 0, 0))
        self.event(8, "miss", self.cmd, metadata=_actor(15, 29, 0, 0))
        self.event(9, "movement", self.hvy)
        self.assertEqual([s["tick"] for s in self.snapshots(self.hvy)], [0])


class TestDerivedValues(_PlaybackCase):
    def test_downed_at_on_life_loss(self) -> None:
        self.event(
            20,
            "tag",
            self.cmd,
            self.hvy,
            metadata={**_actor(15, 29, 100, 1), **_target(9, 20, -20)},
        )
        self.event(
            30,
            "tag",
            self.cmd,
            self.hvy,
            metadata={**_actor(15, 28, 200, 2), **_target(9, 20, -40)},
        )
        snaps = self.snapshots(self.hvy)
        self.assertEqual(snaps[1]["downed_at"], 20)
        # A tag that only takes shields (lives unchanged) is no Down.
        self.assertEqual(snaps[2]["downed_at"], 20)
        self.assertEqual(self.snapshots(self.cmd)[-1]["tags"], 2)

    def test_downed_at_on_received_resupply_not_given(self) -> None:
        self.event(
            40,
            "resupply_lives",
            self.med,
            self.hvy,
            metadata={**_actor(20, 15, 0, 0), **_target(13, 20, 0)},
        )
        self.assertEqual(self.snapshots(self.hvy)[-1]["downed_at"], 40)
        self.assertIsNone(self.snapshots(self.med)[-1]["downed_at"])

    def test_eliminated_from_the_targets_elimination_event(self) -> None:
        self.event(
            50,
            "elimination",
            self.cmd,
            self.hvy,
            metadata={**_actor(15, 29, 100, 1), **_target(0, 20, -20)},
        )
        self.assertTrue(self.snapshots(self.hvy)[-1]["eliminated"])
        self.assertFalse(self.snapshots(self.cmd)[-1]["eliminated"])

    def test_missiles_minus_one_on_lock_and_minus_one_more_on_a_hit(self) -> None:
        """MECH-16: the engine spends a hit missile twice; a missed missile
        costs only the lock."""
        meta = {**_actor(15, 30, 0, 0), **_target(10, 20, 0)}
        self.event(60, "locking", self.cmd, self.hvy, metadata=meta)
        self.event(
            64, "missiled", self.cmd, self.hvy, metadata={**meta, "result": "hit"}
        )
        self.event(70, "locking", self.cmd, self.med, metadata=meta)
        self.event(
            74, "missiled", self.cmd, self.med, metadata={**meta, "result": "miss"}
        )
        self.assertEqual(
            [(s["tick"], s["missiles"]) for s in self.snapshots(self.cmd)],
            [(0, 5), (60, 4), (64, 3), (70, 2)],
        )
        # The target's missiles never move.
        self.assertEqual(self.snapshots(self.hvy)[-1]["missiles"], 5)

    def test_special_targets_values_apply_to_teammates_and_opponents(self) -> None:
        # Medic team heal: a teammate (the heavy) in targets[].
        self.event(
            80,
            "special",
            self.med,
            description="PbBlue medic team heal special",
            metadata={
                **_actor(20, 15, 0, 0),
                "targets": [
                    {"pid": self.hvy.pk, "lives": 13, "shots": 20, "points": 4}
                ],
            },
        )
        # Nuke detonation: opponents in targets[], losing lives (a Down).
        self.event(
            90,
            "special",
            self.cmd,
            points=500,
            description="PbRed commander nuke detonates",
            metadata={
                **_actor(15, 30, 500, 0),
                "targets": [
                    {"pid": self.hvy.pk, "lives": 10, "shots": 20, "points": 4},
                    {"pid": self.med.pk, "lives": 17, "shots": 15, "points": 0},
                ],
            },
        )
        hvy = self.snapshots(self.hvy)
        self.assertEqual(hvy[1], _snap(80, lives=13, shots=20, score=4, missiles=5))
        self.assertEqual(
            hvy[2],
            _snap(90, lives=10, shots=20, score=4, missiles=5, downed_at=90),
        )
        self.assertEqual(self.snapshots(self.med)[-1]["lives"], 17)
        self.assertEqual(self.snapshots(self.med)[-1]["downed_at"], 90)
        self.assertEqual(self.snapshots(self.cmd)[-1]["score"], 500)


class TestSpecialFlags(_PlaybackCase):
    def test_rapid_fire_on_from_activation_until_a_received_resupply(self) -> None:
        self.event(
            100,
            "special",
            self.scout,
            description="PbRed scout activates rapid fire",
            metadata=_actor(15, 30, 0, 0),
        )
        # A resupply the scout gives (it is the actor) does not end it.
        self.event(
            110,
            "resupply_ammo",
            self.scout,
            self.cmd,
            metadata={**_actor(15, 30, 0, 0), **_target(15, 30, 0)},
        )
        self.event(
            120,
            "resupply_ammo",
            self.cmd,
            self.scout,
            metadata={**_actor(15, 30, 0, 0), **_target(15, 30, 0)},
        )
        self.assertEqual(
            [(s["tick"], s["rapid_fire"]) for s in self.snapshots(self.scout)],
            [(0, False), (100, True), (120, False)],
        )
        self.assertFalse(self.snapshots(self.cmd)[-1]["rapid_fire"])

    def arm(self, tick: int, fires_at: int) -> None:
        self.event(
            tick,
            "special",
            self.cmd,
            description="PbRed commander activates nuke",
            metadata={**_actor(15, 30, 0, 0), "fires_at": fires_at},
        )

    def nuking(self) -> list[tuple[int, bool]]:
        return [(s["tick"], s["nuking"]) for s in self.snapshots(self.cmd)]

    def test_nuking_on_until_detonation(self) -> None:
        self.arm(200, fires_at=210)
        self.event(
            205,
            "special",
            self.cmd,
            points=500,
            description="PbRed commander nuke detonates",
            metadata={**_actor(15, 30, 500, 0), "targets": []},
        )
        self.assertEqual(self.nuking(), [(0, False), (200, True), (205, False)])

    def test_nuking_on_until_nuke_cancelled(self) -> None:
        self.arm(200, fires_at=210)
        self.event(203, "nuke_cancelled", self.cmd, metadata=_actor(15, 30, 0, 0))
        self.assertEqual(self.nuking(), [(0, False), (200, True), (203, False)])

    def test_nuking_off_at_fires_at_without_any_event(self) -> None:
        """The fuse burns out (no detonation or cancel logged) before the
        Commander's next event."""
        self.arm(200, fires_at=212)
        self.event(300, "miss", self.cmd, metadata=_actor(15, 29, 0, 0))
        self.assertEqual(
            self.nuking(), [(0, False), (200, True), (212, False), (300, False)]
        )

    def test_nuking_off_at_fires_at_on_a_tick_with_events(self) -> None:
        """The fuse burns out on a tick that also has an event: one snapshot
        at that tick, with ``nuking`` already off."""
        self.arm(200, fires_at=212)
        self.event(212, "miss", self.cmd, metadata=_actor(15, 29, 0, 0))
        self.assertEqual(self.nuking(), [(0, False), (200, True), (212, False)])

    def test_nuking_off_at_fires_at_with_no_later_events(self) -> None:
        self.arm(200, fires_at=212)
        self.assertEqual(self.nuking(), [(0, False), (200, True), (212, False)])

    def test_another_commanders_detonation_does_not_end_nuking(self) -> None:
        self.arm(200, fires_at=212)
        self.event(
            205,
            "special",
            self.hvy,
            points=500,
            description="PbBlue heavy nuke detonates",
            metadata={**_actor(10, 20, 500, 0), "targets": []},
        )
        self.assertEqual(self.nuking(), [(0, False), (200, True), (212, False)])
