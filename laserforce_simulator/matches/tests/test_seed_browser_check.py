"""``seed_browser_check``: throwaway data for a browser check, and its cleanup.

Seeding costs seconds (the map's sight-line compute, the League's Seasons), so
the read-only tests share one seed per class (``setUpTestData``, with a
class-scoped ``MEDIA_ROOT``; PERF-02). Tests that run ``--clean`` (its on-commit
hook deletes map files, which a rollback does not restore) or need an unseeded
database live in the ``*CleanTests`` classes and seed per test.
"""

from io import StringIO
from typing import Any, Callable
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db.models import Q
from django.test import TestCase
from rest_framework.test import APIClient

from core.map_test_fixtures import ClassTempMediaMixin, TempMediaMixin
from core.models import ArenaMap
from matches.management.commands.seed_browser_check import (
    MISSILE_ROUND_ATTEMPTS,
    REVIEW_ROUND_SEED,
    review_ticks,
)
from matches.models import (
    Conference,
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    Tournament,
)
from matches.sim_helpers.map_loader import load_map_context
from matches.simulation import BatchSimulator
from teams.models import Player, Team

_A = "browser-a@example.com"
_B = "browser-b@example.com"
_MAP_NAME = "Browser Check Arena"
_SYRACUSE_NAME = "Browser Check Syracuse"
_LEAGUE_NAME = "Browser Check League"
_CONFERENCE_LEAGUE_NAME = "Browser Check Conferences"


def _seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_browser_check", *args, stdout=out)
    return out.getvalue()


class SeedBrowserCheckTests(ClassTempMediaMixin, TestCase):
    """The default seed (no ``--league``), made once for the class."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed()

    def test_seeds_two_accounts_and_account_a_data(self) -> None:
        user_model = get_user_model()
        a = user_model.objects.get(email=_A)
        b = user_model.objects.get(email=_B)
        self.assertTrue(a.check_password("browser-check-pass"))
        self.assertTrue(b.check_password("browser-check-pass"))
        self.assertEqual(Team.objects.filter(manager=a).count(), 4)
        self.assertTrue(all(t.is_valid_roster for t in Team.objects.filter(manager=a)))
        self.assertFalse(Team.objects.filter(manager=b).exists())

        matches = Match.objects.filter(manager=a)
        self.assertEqual(matches.filter(is_completed=True).count(), 1)
        unfinished = matches.get(is_completed=False)
        self.assertIsNone(unfinished.winner)
        self.assertGreater(unfinished.red_round1_points, 0)
        self.assertEqual(
            GameRound.objects.filter(
                manager=a, match__isnull=True, arena_map__isnull=True
            ).count(),
            1,
        )
        self.assertIn(_A, self.output)
        self.assertIn("browser-check-pass", self.output)

    def test_refuses_to_seed_twice(self) -> None:
        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed()

    def test_without_the_flag_no_league_is_seeded(self) -> None:
        self.assertFalse(League.objects.exists())
        self.assertFalse(Season.objects.exists())
        self.assertNotIn("League", self.output)


class SeedBrowserCheckCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_only_the_seeded_accounts_rows(self) -> None:
        other = get_user_model().objects.create_user(
            email="owner@example.com", password="x"
        )
        kept = Team.objects.create(name="Owner Team", manager=other)
        _seed()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(get_user_model().objects.filter(email__in=[_A, _B]).exists())
        self.assertFalse(Match.objects.exclude(manager=other).exists())
        self.assertFalse(GameRound.objects.exclude(manager=other).exists())
        self.assertFalse(Player.objects.exclude(team=kept).exists())
        self.assertEqual(list(Team.objects.all()), [kept])
        self.assertIn("removed", output)

    def test_clean_counts_each_labelled_model_not_its_cascade(self) -> None:
        # A GameRound delete also cascades its GameEvents and PlayerRoundStates;
        # the summary counts only the rows its label names.
        _seed()
        users = get_user_model().objects.filter(email__in=[_A, _B])
        rounds = GameRound.objects.filter(manager__in=users).count()
        matches = Match.objects.filter(manager__in=users).count()
        players = Player.objects.filter(team__manager__in=users).count()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertIn(
            f"removed Single Rounds: {rounds}, Matches: {matches}, "
            f"Players: {players}, Teams: 4, Accounts: 2, Maps: 1",
            output,
        )


class SeedBrowserCheckMapTests(ClassTempMediaMixin, TestCase):
    """The configured map and the full-fidelity Single Round played on it."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed()

    def _mapped_round(self) -> GameRound:
        a = get_user_model().objects.get(email=_A)
        return GameRound.objects.get(
            manager=a, match__isnull=True, arena_map__name=_MAP_NAME
        )

    def test_seeded_map_image_is_under_the_class_media_root(self) -> None:
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)

        self.assertTrue(self.media_root.joinpath(arena_map.image.name).exists())

    def test_seeded_map_resolves_via_load_map_context(self) -> None:
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        movement_ctx, zone_size = load_map_context(arena_map)
        self.assertIsNotNone(movement_ctx)
        self.assertEqual(zone_size, 20)
        self.assertIn(f"Map {arena_map.pk}", self.output)

    def test_mapped_round_is_full_fidelity_with_movement_events(self) -> None:
        game_round = self._mapped_round()
        self.assertIsNotNone(game_round.arena_map_id)
        self.assertEqual(game_round.fidelity, "full")
        self.assertTrue(game_round.events.filter(event_type="movement").exists())
        self.assertIn(f"mapped Single Round {game_round.pk}", self.output)

    def test_compare_rounds_has_points_for_both_single_rounds(self) -> None:
        # The two Single Rounds share their Teams, so they are the Compare
        # Rounds check pair; both need events for the points-over-time chart.
        mapped = self._mapped_round()
        mapless = GameRound.objects.get(
            manager=mapped.manager, match__isnull=True, arena_map__isnull=True
        )
        api = APIClient()
        api.force_login(mapped.manager)

        response = api.get(
            "/api/rounds/compare/", {"round_a": mapless.pk, "round_b": mapped.pk}
        )

        self.assertEqual(response.status_code, 200, response.content)
        series = response.json()["data"]["points_series"]
        self.assertTrue(series)
        for team in series:
            self.assertTrue(team["a"], team["team_name"])
            self.assertTrue(team["b"], team["team_name"])

    def test_missile_log_has_one_friendly_fire_row_beside_ordinary_ones(
        self,
    ) -> None:
        # A simulated Round rarely fires a missile at a teammate, so the seed
        # marks one: the Missile log check needs its tinted row (ANG-06-10 retro).
        game_round = self._mapped_round()
        api = APIClient()
        api.force_login(game_round.manager)

        response = api.get(f"/api/rounds/{game_round.pk}/missile-log/")

        self.assertEqual(response.status_code, 200, response.content)
        flags = [row["friendly_fire"] for row in response.json()["data"]["rows"]]
        self.assertEqual(flags.count(True), 1, flags)
        self.assertIn(False, flags)

    def test_playback_api_returns_a_map_for_the_mapped_round(self) -> None:
        game_round = self._mapped_round()
        api = APIClient()
        api.force_login(game_round.manager)

        response = api.get(f"/api/rounds/{game_round.pk}/playback/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["data"]["map"])


#: The rows each lock ending the mapped Round needs (MECH-21, #583 [15]).
_LOCK_ENDING_ROWS = {
    "missile": Q(event_type="missiled"),
    "dodge": Q(event_type="lock_cancelled", metadata__reason="dodge"),
    "cancel": Q(event_type="lock_cancelled") & ~Q(metadata__reason="dodge"),
}


#: A row standing in for each lock ending a simulated Round happens to lack.
_LOCK_ENDING_STAND_INS = {
    "missile": ("missiled", {"result": "hit", "friendly_fire": False}),
    "dodge": ("lock_cancelled", {"reason": "dodge"}),
    "cancel": ("lock_cancelled", {"reason": "sight"}),
}


def _mapped_rounds_lose_their_missiles(
    lose_them: Callable[[int], bool], ending: str = "missile"
) -> Any:
    """Patch ``ensure_fidelity`` so the nth mapped Round has no ``ending`` rows
    when ``lose_them(n)``: a real Round does this too rarely to wait for.

    Every other lock ending is made present (a stand-in row where the Round has
    none): a mapped Round draws its seed from OS entropy, so a real one lacks a
    dodge often enough to make the attempt counts flaky.
    """
    real = BatchSimulator.ensure_fidelity
    mapped_rounds = 0

    def ensure_fidelity(sim: BatchSimulator, game_round: GameRound, level: str) -> Any:
        nonlocal mapped_rounds
        upgraded = real(sim, game_round, level)
        if game_round.arena_map_id is not None:
            mapped_rounds += 1
            _add_missing_lock_endings(upgraded)
            if lose_them(mapped_rounds):
                upgraded.events.filter(_LOCK_ENDING_ROWS[ending]).delete()
        return upgraded

    return patch.object(BatchSimulator, "ensure_fidelity", ensure_fidelity)


def _add_missing_lock_endings(game_round: GameRound) -> None:
    states = list(game_round.player_states.order_by("id")[:2])
    for name, rows in _LOCK_ENDING_ROWS.items():
        if not game_round.events.filter(rows).exists():
            event_type, metadata = _LOCK_ENDING_STAND_INS[name]
            game_round.events.create(
                timestamp=100,
                event_type=event_type,
                actor_id=states[0].player_id,
                target_id=states[1].player_id,
                metadata=metadata,
            )


class SeedBrowserCheckMissileRetryTests(TempMediaMixin, TestCase):
    """The Missile log and Playback checks need a missile, a dodge and another
    cancelled lock, and a real Round can have none of one."""

    def test_a_round_missing_a_dodge_or_a_cancel_is_simulated_again(self) -> None:
        for ending in ("dodge", "cancel"):
            with self.subTest(ending=ending):
                attempts = []

                def lose_them(n: int) -> bool:
                    attempts.append(n)
                    return n == 1

                with _mapped_rounds_lose_their_missiles(lose_them, ending):
                    with self.captureOnCommitCallbacks(execute=True):
                        _seed()
                game_round = self._mapped_rounds().get()
                self.assertEqual(attempts, [1, 2])
                rows = game_round.events.filter(_LOCK_ENDING_ROWS[ending])
                self.assertTrue(rows.exists())
                with self.captureOnCommitCallbacks(execute=True):
                    _seed("--clean")

    def test_the_warning_names_only_what_is_missing(self) -> None:
        with _mapped_rounds_lose_their_missiles(lambda n: True, "dodge"):
            output = _seed()

        game_round = self._mapped_rounds().get()
        self.assertIn(
            f"Warning: mapped Single Round {game_round.pk} has no dodge in "
            f"{MISSILE_ROUND_ATTEMPTS} attempts",
            output,
        )
        warning = next(l for l in output.splitlines() if l.startswith("Warning"))
        self.assertNotIn("missile", warning)
        self.assertNotIn("cancel", warning)

    def _mapped_rounds(self) -> Any:
        return GameRound.objects.filter(arena_map__name=_MAP_NAME)

    def test_a_round_with_no_missile_is_simulated_again(self) -> None:
        attempts = []

        def lose_them(n: int) -> bool:
            attempts.append(n)
            return n == 1

        with _mapped_rounds_lose_their_missiles(lose_them):
            _seed()

        self.assertEqual(attempts, [1, 2])
        game_round = self._mapped_rounds().get()
        missiles = game_round.events.filter(event_type="missiled")
        self.assertTrue(missiles.exists())
        self.assertEqual(missiles.filter(metadata__friendly_fire=True).count(), 1)

    def test_it_keeps_the_last_attempt_and_warns(self) -> None:
        # A short Round fires no missile (the Tournament tests' 40 ticks), and the
        # rest of the seed must still run.
        attempts = []

        def lose_them(n: int) -> bool:
            attempts.append(n)
            return True

        with _mapped_rounds_lose_their_missiles(lose_them):
            output = _seed()

        self.assertEqual(attempts, list(range(1, MISSILE_ROUND_ATTEMPTS + 1)))
        game_round = self._mapped_rounds().get()
        self.assertIn(
            f"Warning: mapped Single Round {game_round.pk} has no missile in", output
        )
        self.assertTrue(Match.objects.filter(is_completed=False).exists())

    def test_no_warning_when_a_missile_was_fired(self) -> None:
        with _mapped_rounds_lose_their_missiles(lambda n: n == 1):
            output = _seed()

        self.assertNotIn("Warning", output)

    def test_the_last_attempt_can_still_succeed(self) -> None:
        with _mapped_rounds_lose_their_missiles(lambda n: n < MISSILE_ROUND_ATTEMPTS):
            _seed()

        game_round = self._mapped_rounds().get()
        self.assertTrue(game_round.events.filter(event_type="missiled").exists())


class SeedBrowserCheckMapCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_the_map_and_its_files(self) -> None:
        _seed()
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        self.assertTrue(self.media_root.joinpath(arena_map.image.name).exists())

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")

        self.assertFalse(ArenaMap.objects.filter(name=_MAP_NAME).exists())
        self.assertFalse(self.media_root.joinpath(arena_map.image.name).exists())
        self.assertFalse(self.processed_cache_path(arena_map).exists())

    def test_clean_twice_is_harmless(self) -> None:
        _seed()
        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")
        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")
        self.assertIn("removed", output)


class SeedBrowserCheckSyracuseTests(ClassTempMediaMixin, TestCase):
    """``--syracuse``: a reviewable full-fidelity Round on the committed fixture."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed("--syracuse")

    def _round(self) -> GameRound:
        return GameRound.objects.get(arena_map__name=_SYRACUSE_NAME)

    def test_the_map_resolves_and_keeps_the_fixture_scale(self) -> None:
        arena_map = ArenaMap.objects.get(name=_SYRACUSE_NAME)
        self.assertEqual(arena_map.feet_per_pixel, 0.05)
        self.assertIsNotNone(load_map_context(arena_map)[0])

    def test_the_image_is_under_the_media_root(self) -> None:
        arena_map = ArenaMap.objects.get(name=_SYRACUSE_NAME)
        self.assertTrue(self.media_root.joinpath(arena_map.image.name).exists())
        # upload_to already adds maps/, and a second one would nest the file.
        self.assertEqual(
            arena_map.image.name,
            "maps/browser_check_Syracuse_Laser_Tag_Map_cropped.png",
        )

    def test_the_round_is_full_fidelity_and_account_a_owns_it(self) -> None:
        game_round = self._round()
        self.assertEqual(game_round.fidelity, "full")
        self.assertEqual(game_round.manager.email, _A)
        self.assertTrue(game_round.events.filter(event_type="movement").exists())

    def test_playback_returns_the_map(self) -> None:
        game_round = self._round()
        api = APIClient()
        api.force_login(game_round.manager)
        response = api.get(f"/api/rounds/{game_round.pk}/playback/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["data"]["map"]["arena_map"]["name"], _SYRACUSE_NAME
        )

    def test_the_summary_names_the_round_screens(self) -> None:
        pk = self._round().pk
        for screen in ("playback", "events", "heatmap"):
            self.assertIn(f"/rounds/{pk}/{screen}", self.output)

    def test_seeding_twice_is_refused(self) -> None:
        with self.assertRaises(CommandError):
            _seed("--syracuse")

    def test_the_round_plays_the_pinned_seed(self) -> None:
        self.assertEqual(self._round().rng_seed, REVIEW_ROUND_SEED)

    def test_the_summary_names_the_review_ticks_and_each_is_real(self) -> None:
        """RV-07 retro: the summary says where to look, so a spec can name a tick."""
        game_round = self._round()
        events = list(
            game_round.events.values(
                "id",
                "event_type",
                "timestamp",
                "actor_id",
                "target_id",
                "description",
                "metadata",
            )
        )
        ticks = review_ticks(events)
        line = next(
            l for l in self.output.splitlines() if l.startswith("Review ticks:")
        )
        for name, tick in ticks.items():
            self.assertIn(f"{name} {tick if tick is not None else '-'}", line)
        if ticks["capture"] is not None:
            self.assertTrue(
                game_round.events.filter(
                    event_type="base_capture", timestamp=ticks["capture"]
                ).exists()
            )


def _event(
    tick: int, kind: str, actor: int, target: int | None = None, **meta: Any
) -> dict:
    return {
        "event_type": kind,
        "timestamp": tick,
        "actor_id": actor,
        "target_id": target,
        "description": meta.pop("description", ""),
        "metadata": meta,
    }


class ReviewTicksTests(TestCase):
    """``review_ticks``: the first tick of each sight worth a browser check."""

    def test_finds_the_first_of_each(self) -> None:
        events = [
            _event(16, "base_capture", 3, shots_remaining=27, base_id=15),
            _event(18, "tag", 1, 2),
            _event(19, "miss", 2, 1),
            _event(30, "special", 4, fires_at=44, description="X activates nuke"),
            _event(40, "special", 5, description="Y activates rapid fire"),
            _event(50, "tag", 1, 2),
            _event(51, "tag", 2, 1),
        ]
        self.assertEqual(
            review_ticks(events),
            {
                "mutual tag": 19,
                "capture": 16,
                "nuke": 30,
                "rapid fire": 40,
                # No locking events here (RV-08 retro's lock keys have their own tests).
                "lock hit": None,
                "lock dodge": None,
                "lock lost": None,
            },
        )

    def test_an_awarded_capture_and_a_one_way_tag_do_not_count(self) -> None:
        events = [
            _event(10, "tag", 1, 2),
            _event(14, "tag", 3, 1),
            _event(
                1799,
                "base_capture",
                3,
                base_id=15,
                description="X awarded neutral base",
            ),
        ]
        self.assertEqual(
            review_ticks(events),
            {
                "mutual tag": None,
                "capture": None,
                "nuke": None,
                "rapid fire": None,
                "lock hit": None,
                "lock dodge": None,
                "lock lost": None,
            },
        )

    def test_a_reply_two_ticks_later_is_not_mutual(self) -> None:
        events = [_event(10, "tag", 1, 2), _event(12, "tag", 2, 1)]
        self.assertIsNone(review_ticks(events)["mutual tag"])

    def test_finds_the_start_of_the_first_lock_ending_each_way(self) -> None:
        """MECH-21 (#583 [15]): a lock ends at its shooter's first later
        ``missiled`` on its target (hit) or ``lock_cancelled`` (dodge for
        ``reason=dodge``, lost for any other reason)."""
        events = [
            _event(10, "locking", 1, 2, lock_ticks=2),
            _event(11, "lock_cancelled", 1, 2, reason="shot"),
            _event(20, "locking", 1, 2, lock_ticks=2),
            _event(22, "missiled", 1, 2, result="hit"),
            _event(30, "locking", 3, 4, lock_ticks=2),
            _event(32, "lock_cancelled", 3, 4, reason="dodge"),
        ]
        ticks = review_ticks(events)
        self.assertEqual(
            (ticks["lock hit"], ticks["lock dodge"], ticks["lock lost"]),
            (20, 30, 10),
        )

    def test_each_other_reason_is_lost(self) -> None:
        for reason in ("sight", "shot", "retarget", "down", "out"):
            with self.subTest(reason=reason):
                events = [
                    _event(10, "locking", 1, 2, lock_ticks=2),
                    _event(11, "lock_cancelled", 1, 2, reason=reason),
                ]
                ticks = review_ticks(events)
                self.assertEqual(
                    (ticks["lock hit"], ticks["lock dodge"], ticks["lock lost"]),
                    (None, None, 10),
                )

    def test_a_lock_open_at_round_end_or_paired_wrongly_is_none_of_them(
        self,
    ) -> None:
        events = [
            # Another shooter's missile and a dodge row the wrong way round
            # (the target as actor) end neither lock.
            _event(10, "locking", 1, 2, lock_ticks=2),
            _event(12, "missiled", 3, 2, result="hit"),
            _event(12, "lock_cancelled", 2, 1, reason="dodge"),
            # A missile on another target is not this lock's hit.
            _event(20, "locking", 5, 6, lock_ticks=2),
            _event(22, "missiled", 5, 7, result="hit"),
            # Still open when the round ends: no row at all.
            _event(1799, "locking", 8, 9, lock_ticks=2),
        ]
        ticks = review_ticks(events)
        self.assertEqual(
            (ticks["lock hit"], ticks["lock dodge"], ticks["lock lost"]),
            (None, None, None),
        )

    def test_a_retarget_ends_the_old_lock_not_the_new_one(self) -> None:
        """A retarget writes the old lock's ``lock_cancelled`` and the new
        ``locking`` on one tick: the cancel, logged first, is not the new lock's."""
        events = [
            _event(10, "locking", 1, 2, lock_ticks=2),
            _event(11, "lock_cancelled", 1, 2, reason="retarget"),
            _event(11, "locking", 1, 3, lock_ticks=2),
            _event(13, "missiled", 1, 3, result="hit"),
        ]
        ticks = review_ticks(events)
        self.assertEqual((ticks["lock hit"], ticks["lock lost"]), (11, 10))

    def test_a_cancel_logged_after_the_lock_on_its_tick_ends_it(self) -> None:
        # A reaction shot on the lock's own tick cancels it there.
        events = [
            _event(10, "locking", 1, 2, lock_ticks=2),
            _event(10, "lock_cancelled", 1, 2, reason="shot"),
        ]
        self.assertEqual(review_ticks(events)["lock lost"], 10)


class SeedBrowserCheckReproducibleTests(TempMediaMixin, TestCase):
    """RV-07 retro: two seeds make the same Players, so the same Rounds."""

    def _players(self) -> list[tuple[str, int, int]]:
        return list(
            Player.objects.filter(team__manager__email=_A)
            .order_by("team__name", "name")
            .values_list("name", "accuracy", "survival")
        )

    def test_a_second_seed_makes_the_same_players(self) -> None:
        _seed()
        first = self._players()
        _seed("--clean")
        _seed()
        self.assertEqual(self._players(), first)


class SeedBrowserCheckWithoutSyracuseTests(ClassTempMediaMixin, TestCase):
    def test_without_the_flag_no_syracuse_map_is_seeded(self) -> None:
        _seed()
        self.assertFalse(ArenaMap.objects.filter(name=_SYRACUSE_NAME).exists())

    def test_a_stray_syracuse_map_refuses_the_seed(self) -> None:
        ArenaMap.objects.create(name=_SYRACUSE_NAME)
        with self.assertRaises(CommandError):
            _seed()


class SeedBrowserCheckSyracuseCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_the_map_and_only_its_own_image(self) -> None:
        _seed("--syracuse")
        arena_map = ArenaMap.objects.get(name=_SYRACUSE_NAME)
        own_image = self.media_root.joinpath(arena_map.image.name)
        default_image = self.media_root / "maps" / "Syracuse_Laser_Tag_Map_cropped.png"
        default_image.parent.mkdir(parents=True, exist_ok=True)
        default_image.write_bytes(b"the default map's image")
        self.assertNotEqual(own_image, default_image)

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertIn("Maps: 2", output)
        self.assertFalse(ArenaMap.objects.filter(name=_SYRACUSE_NAME).exists())
        self.assertFalse(own_image.exists())
        self.assertTrue(default_image.exists())


def _api_for_a() -> APIClient:
    api = APIClient()
    api.force_login(get_user_model().objects.get(email=_A))
    return api


class SeedBrowserCheckLeagueTests(ClassTempMediaMixin, TestCase):
    """``--league``: one League with two completed Seasons and an active one."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed("--league")

    def test_league_flag_seeds_a_league_with_completed_and_active_seasons(
        self,
    ) -> None:
        a = get_user_model().objects.get(email=_A)
        league = League.objects.get(name=_LEAGUE_NAME)
        self.assertEqual(league.manager, a)
        seasons = list(league.seasons.order_by("id"))
        self.assertEqual(
            [s.state for s in seasons], ["completed", "completed", "active"]
        )
        for season in seasons:
            self.assertTrue(season.matches.filter(is_completed=True).exists())
            self.assertTrue(
                PlayerRoundState.objects.filter(
                    game_round__match__season=season
                ).exists()
            )
        api = _api_for_a()
        for season in seasons[:2]:
            standings = api.get(f"/api/seasons/{season.pk}/standings/").json()
            self.assertTrue(standings["data"]["groups"][0]["rows"])
            awards = api.get(f"/api/seasons/{season.pk}/awards/").json()["data"]
            self.assertTrue(awards["has_rounds"])
            self.assertIsNotNone(awards["most_points"])
        season_ids = ", ".join(str(s.pk) for s in seasons)
        self.assertIn(f"League {league.pk} ({_LEAGUE_NAME})", self.output)
        self.assertIn(f"Seasons {season_ids}", self.output)

    def test_active_season_has_a_built_playoff_bracket(self) -> None:
        # ANG-06-15 retro: the Playoffs screen needs a bracket to draw.
        league = League.objects.get(name=_LEAGUE_NAME)
        response = _api_for_a().get(f"/api/leagues/{league.pk}/playoffs/")
        self.assertEqual(response.status_code, 200, response.content)
        brackets = response.json()["data"]["brackets"]
        self.assertEqual(len(brackets), 1)
        self.assertFalse(brackets[0]["pending"])
        first_round = brackets[0]["rounds"][0]["nodes"]
        self.assertTrue(first_round)
        self.assertTrue(all(n["team_a"] and n["team_b"] for n in first_round))

    def test_league_history_and_player_apis_return_data_for_account_a(
        self,
    ) -> None:
        league = League.objects.get(name=_LEAGUE_NAME)
        player = league.current_team.slot_commander
        api = _api_for_a()

        history = api.get(f"/api/leagues/{league.pk}/history/")
        player_page = api.get(f"/api/leagues/{league.pk}/players/{player.pk}/")
        owner_eval = api.get(
            f"/api/seasons/{league.seasons.order_by('id')[0].pk}/owner-evaluation/"
        )

        for response in (history, player_page, owner_eval):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["data"])
        self.assertEqual(len(history.json()["data"]), 3)
        self.assertEqual(len(player_page.json()["data"]["seasons"]), 3)
        self.assertIsNotNone(player_page.json()["data"]["career"])

    def test_free_agents_api_lists_the_seeded_pool(self) -> None:
        # ANG-06-16 retro: the Free Agents table needs a pool to draw.
        league = League.objects.get(name=_LEAGUE_NAME)

        response = _api_for_a().get(f"/api/leagues/{league.pk}/free-agents/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["count"], 3)

    def test_player_page_has_a_ratings_history_line(self) -> None:
        # ANG-06-16 retro: the ratings-history chart needs one point per Season
        # and a line that moves.
        league = League.objects.get(name=_LEAGUE_NAME)
        player = league.current_team.slot_commander

        response = _api_for_a().get(f"/api/leagues/{league.pk}/players/{player.pk}/")

        history = response.json()["data"]["ratings_history"]
        self.assertEqual(len(history), 3)
        self.assertEqual(len({row["overall_rating"] for row in history}), 3)

    def test_team_finances_have_settled_history_cash_and_an_injury(self) -> None:
        # ANG-06-17 retro: the finance screens need settled figures, not only
        # their empty states, before any screen runs the lazy finance ensure.
        league = League.objects.get(name=_LEAGUE_NAME)
        team = league.current_team

        response = _api_for_a().get(
            f"/api/leagues/{league.pk}/teams/{team.pk}/finances/"
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(
            [row["season"]["name"] for row in data["history"]],
            ["Season 2", "Season 1"],
        )
        self.assertNotEqual(data["cash"], 0)
        self.assertEqual(len(data["availability"]), 1)

    def test_conference_league_has_a_draft_season_in_two_conferences(self) -> None:
        # ANG-06-18: the editable Manage Conferences screen and the draft
        # Standings preview need a non-career League with a draft Season.
        a = get_user_model().objects.get(email=_A)
        league = League.objects.get(name=_CONFERENCE_LEAGUE_NAME)
        self.assertEqual(league.manager, a)
        self.assertNotEqual(league.mode, "league")
        season = league.seasons.get()
        self.assertEqual(season.state, "draft")
        self.assertEqual(season.map_mode, "rotate_by_conference")
        enrolled = {t.id for t in season.teams.all()}
        self.assertGreaterEqual(len(enrolled), 4)

        conferences = list(season.conferences.order_by("ordinal"))
        self.assertEqual(len(conferences), 2)
        members = [{t.id for t in c.teams.all()} for c in conferences]
        self.assertEqual(members[0] | members[1], enrolled)
        self.assertFalse(members[0] & members[1])
        arena_map = ArenaMap.objects.get(name=_MAP_NAME)
        for conference, member_ids in zip(conferences, members):
            self.assertGreaterEqual(len(member_ids), 2)
            self.assertIn(len(conference.map_rotation_ids_json), (1, 2))
            self.assertEqual(set(conference.map_rotation_ids_json), {arena_map.pk})

        api = _api_for_a()
        state = api.get(f"/api/seasons/{season.pk}/conferences/").json()["data"]
        self.assertTrue(state["editable"])
        self.assertEqual(state["unassigned_team_ids"], [])
        standings = api.get(f"/api/seasons/{season.pk}/standings/").json()["data"]
        self.assertTrue(standings["is_draft_preview"])
        self.assertEqual(len(standings["groups"]), 2)
        self.assertIn(
            f"League {league.pk} ({_CONFERENCE_LEAGUE_NAME}), Season {season.pk} "
            "(draft, 2 Conferences)",
            self.output,
        )


class SeedBrowserCheckLeagueCleanTests(TempMediaMixin, TestCase):
    """``--league`` reruns and ``--clean``, each from its own seed or none."""

    def test_clean_removes_the_league_and_everything_under_it(self) -> None:
        _seed("--league")

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(League.objects.exists())
        self.assertFalse(Season.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(GameRound.objects.exists())
        self.assertFalse(PlayerRoundState.objects.exists())
        self.assertFalse(Team.objects.exists())
        self.assertFalse(Conference.objects.exists())
        # The career League and the draft Conference League (ANG-06-18).
        self.assertIn("Leagues: 2", output)
        # The League goes without taking A's Teams with it, so the rest of the
        # line still counts them (teardown_league's orphan sweep would not):
        # A's four Teams plus the free-agent pool Team (ANG-06-16 retro).
        self.assertIn("Teams: 5", output)

    def test_rerun_refuses_and_leaves_one_league(self) -> None:
        _seed("--league")
        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")
        _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

    def test_refuses_when_a_stray_league_of_that_name_exists(self) -> None:
        League.objects.create(name=_LEAGUE_NAME)

        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed("--league")
        self.assertEqual(League.objects.filter(name=_LEAGUE_NAME).count(), 1)

    def test_refuses_when_a_stray_conference_league_exists(self) -> None:
        League.objects.create(name=_CONFERENCE_LEAGUE_NAME)

        with self.assertRaisesMessage(CommandError, "--clean"):
            _seed("--league")
        self.assertEqual(League.objects.filter(name=_CONFERENCE_LEAGUE_NAME).count(), 1)

    def test_clean_removes_a_stray_unmanaged_league_of_that_name(self) -> None:
        League.objects.create(name=_LEAGUE_NAME)
        League.objects.create(name=_CONFERENCE_LEAGUE_NAME)

        with self.captureOnCommitCallbacks(execute=True):
            _seed("--clean")

        self.assertFalse(League.objects.exists())


# Short Rounds: the Tournament seed plays real Matches (ANG-06-13b retro).
_FAST_TICKS = 40


def _seed_tournaments() -> str:
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        return _seed("--tournaments")


class SeedBrowserCheckTournamentsTests(ClassTempMediaMixin, TestCase):
    """``--tournaments``: one sandbox Tournament per format and stage, for A."""

    output: str

    @classmethod
    def setUpTestData(cls) -> None:
        cls.output = _seed_tournaments()

    def _detail(self, name: str) -> dict:
        tournament = Tournament.objects.get(name=name)
        response = _api_for_a().get(f"/api/tournaments/{tournament.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_every_tournament_belongs_to_account_a(self) -> None:
        a = get_user_model().objects.get(email=_A)
        names = set(Tournament.objects.values_list("name", flat=True))
        self.assertEqual(names, set(_TOURNAMENT_NAMES))
        self.assertFalse(Tournament.objects.exclude(manager=a).exists())

    def test_each_tournament_sits_at_its_stage(self) -> None:
        single = self._detail("BC Single Elim")
        self.assertEqual(single["state"], "active")
        self.assertTrue(single["can_play"])
        self.assertTrue(
            any(n["games"] for r in single["brackets"][0]["rounds"] for n in r["nodes"])
        )

        double = self._detail("BC Double Elim")
        self.assertEqual(double["state"], "completed")
        self.assertIsNotNone(double["champion"])

        round_robin = self._detail("BC Round Robin")
        self.assertEqual(round_robin["state"], "active")
        self.assertTrue(round_robin["rr_crosstable"])

        rrde = self._detail("BC RR to DE")
        self.assertEqual(rrde["stage"], "seeding")
        self.assertTrue(rrde["cut_labels"])

        swiss = self._detail("BC Swiss")
        self.assertEqual(swiss["stage"], "swiss")
        self.assertEqual(len(swiss["brackets"][0]["rounds"]), 2)

        setup = self._detail("BC Setup")
        self.assertEqual(setup["state"], "setup")
        self.assertEqual(len(setup["participants"]), 4)

        pool = self._detail("BC Player Pool")
        self.assertEqual(pool["state"], "setup")
        self.assertTrue(pool["is_drawn"])
        self.assertEqual(pool["pool_size"], 24)

    def test_summary_names_each_tournament_id(self) -> None:
        for name in _TOURNAMENT_NAMES:
            pk = Tournament.objects.get(name=name).pk
            self.assertIn(f"{pk} ({name})", self.output)


_TOURNAMENT_NAMES = (
    "BC Single Elim",
    "BC Double Elim",
    "BC Round Robin",
    "BC RR to DE",
    "BC Swiss",
    "BC Setup",
    "BC Player Pool",
)


class SeedBrowserCheckTournamentsCleanTests(TempMediaMixin, TestCase):
    def test_clean_removes_the_tournaments_their_matches_and_pool_players(
        self,
    ) -> None:
        _seed_tournaments()

        with self.captureOnCommitCallbacks(execute=True):
            output = _seed("--clean")

        self.assertFalse(Tournament.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(GameRound.objects.exists())
        # The pool's generated Players sit on the global Free Agents Team.
        self.assertFalse(Player.objects.exists())
        self.assertFalse(Team.objects.exclude(name="Free Agents").exists())
        self.assertIn(f"Tournaments: {len(_TOURNAMENT_NAMES)}", output)
