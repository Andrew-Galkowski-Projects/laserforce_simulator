"""ANG-01f-5 -- HTML regressions for the Player-pool / Draw pages (ticket #195).

The pool and draw views now delegate to ``matches.tournament_services.draw``
and bind its strict Forms. Covered here: the add-existing ownership leak, the
draw-edit drawn-Team leak, strict generate / edit input (flashed, never
defaulted), the embedded and preset guards, and the flashed Draw rule.
"""

from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from matches.models import SeasonPhase, Tournament, TournamentPlayerEntry
from matches.tests.league_api_fixtures import make_league, make_teams
from teams.models import Player
from matches.tests.test_api_tournaments_draw import (
    NOT_POOL_MSG,
    _drawn,
    _fill_pool,
    _players,
    _pool_tournament,
)


def _flashes(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class _PoolViewTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_shared_manager()
        self.client.force_login(self.user)

    def post(self, name: str, t: Tournament, data: dict | None = None):
        return self.client.post(reverse(name, args=[t.id]), data or {})


class AddExistingLeakTests(_PoolViewTestCase):
    def test_another_accounts_player_is_not_added(self) -> None:
        other = get_user_model().objects.create_user(
            email="ang01f5-b@example.com", password="Str0ng-Passphrase-9"
        )
        foreign_team = make_teams("ForeignHtml", 1, manager=other)[0]
        foreign = _players(1, "ForeignHtml", team=foreign_team)[0]
        mine = _players(1, "MineHtml")[0]
        t = _pool_tournament("LeakAdd", manager=self.user)

        response = self.post(
            "tournament_pool_add_existing",
            t,
            {"players": [str(mine.id), str(foreign.id)]},
        )

        self.assertRedirects(response, reverse("tournament_detail", args=[t.id]))
        # The whole request is rejected; the foreign Player never enters.
        self.assertFalse(t.player_entries.exists())
        self.assertTrue(any("valid choice" in m for m in _flashes(response)))

    def test_owned_players_are_added(self) -> None:
        mine = _players(2, "OwnedHtml")
        t = _pool_tournament("OwnedAdd", manager=self.user)
        self.post(
            "tournament_pool_add_existing",
            t,
            {"players": [str(p.id) for p in mine]},
        )
        self.assertEqual(t.player_entries.count(), 2)


class StrictGenerateTests(_PoolViewTestCase):
    def test_non_numeric_count_is_flashed_with_no_players(self) -> None:
        t = _pool_tournament("GenBadCount", manager=self.user)
        response = self.post("tournament_pool_generate", t, {"count": "lots"})
        self.assertFalse(t.player_entries.exists())
        self.assertTrue(any(m.startswith("Count:") for m in _flashes(response)))

    def test_bad_mean_is_rejected_not_defaulted(self) -> None:
        # The old view fell back to mean 50 and generated the Players anyway.
        t = _pool_tournament("GenBadMean", manager=self.user)
        response = self.post(
            "tournament_pool_generate", t, {"count": "3", "mean": "x", "std_dev": "15"}
        )
        self.assertFalse(t.player_entries.exists())
        self.assertTrue(any(m.startswith("Mean:") for m in _flashes(response)))

    def test_valid_generate_still_pools_players(self) -> None:
        t = _pool_tournament("GenOk", manager=self.user)
        self.post(
            "tournament_pool_generate", t, {"count": "3", "mean": "50", "std_dev": "15"}
        )
        self.assertEqual(t.player_entries.count(), 3)


class DrawEditTests(_PoolViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _drawn("HtmlEdit", manager=self.user)
        self.entry = self.t.player_entries.order_by("player_id").first()
        self.before = (self.entry.tier, self.entry.drawn_team_id)

    def edit(self, data: dict):
        return self.post(
            "tournament_draw_edit",
            self.t,
            {"player_id": str(self.entry.player_id), **data},
        )

    def assert_unchanged(self) -> None:
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.tier, self.entry.drawn_team_id), self.before)

    def test_another_tournaments_drawn_team_is_rejected(self) -> None:
        # Leak regression: any is_draw_team Team used to be accepted.
        other = _drawn("HtmlEditOther", manager=self.user)
        foreign_team = other.participants.first().team
        response = self.edit({"drawn_team": str(foreign_team.id)})
        self.assert_unchanged()
        self.assertTrue(any(m.startswith("Drawn team:") for m in _flashes(response)))

    def test_tier_seven_is_rejected(self) -> None:
        response = self.edit({"tier": "7"})
        self.assert_unchanged()
        self.assertTrue(any(m.startswith("Tier:") for m in _flashes(response)))

    def test_non_numeric_tier_is_rejected_not_ignored(self) -> None:
        response = self.edit({"tier": "high"})
        self.assert_unchanged()
        self.assertTrue(any(m.startswith("Tier:") for m in _flashes(response)))

    def test_unknown_player_is_flashed(self) -> None:
        outsider = _players(1, "HtmlOutsider")[0]
        response = self.post(
            "tournament_draw_edit",
            self.t,
            {"player_id": str(outsider.id), "tier": "2"},
        )
        self.assertIn("That player is not in the pool.", _flashes(response))


class GuardTests(_PoolViewTestCase):
    """Every pool / draw page write is refused (flash + redirect, no write)
    on an embedded or a preset Tournament."""

    WRITES: tuple[tuple[str, Callable[[Player], dict]], ...] = (
        ("tournament_pool_add_existing", lambda p: {"players": [str(p.id)]}),
        ("tournament_pool_generate", lambda p: {"count": "1"}),
        ("tournament_pool_remove", lambda p: {"player_id": str(p.id)}),
        ("tournament_draw", lambda p: {}),
        ("tournament_draw_edit", lambda p: {"player_id": str(p.id), "tier": "2"}),
        ("tournament_pool_import", lambda p: {}),
    )

    def assert_refused(self, t: Tournament, message: str) -> None:
        pooled = _fill_pool(t, 1, f"Guard{t.id}-")[0]
        for name, data in self.WRITES:
            with self.subTest(view=name):
                response = self.post(name, t, data(pooled))
                self.assertRedirects(
                    response, reverse("tournament_detail", args=[t.id])
                )
                self.assertIn(message, _flashes(response))
        entry = TournamentPlayerEntry.objects.get(tournament=t)
        self.assertEqual((entry.player_id, entry.tier), (pooled.id, None))

    def test_embedded_tournament_is_refused(self) -> None:
        t = _pool_tournament("HtmlEmbedded", manager=self.user)
        league = make_league("Html Emb League", manager=self.user)
        season = league.seasons.create(
            name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
        )
        SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="tournament", tournament=t
        )
        self.assert_refused(
            t,
            "This Tournament belongs to a League Season and is played through "
            "that Season.",
        )

    def test_preset_tournament_is_refused(self) -> None:
        t = Tournament.objects.create(name="HtmlPreset", manager=self.user)
        self.assert_refused(t, NOT_POOL_MSG)


class DrawRuleTests(_PoolViewTestCase):
    def test_bad_pool_size_flashes_the_rule(self) -> None:
        t = _pool_tournament("HtmlDraw23", manager=self.user)
        _fill_pool(t, 23, "Html23-")
        response = self.post("tournament_draw", t)
        self.assertFalse(t.participants.exists())
        self.assertTrue(
            any("It currently has 23." in m for m in _flashes(response)),
        )

    def test_unknown_player_remove_is_flashed(self) -> None:
        t = _pool_tournament("HtmlRemove", manager=self.user)
        outsider = _players(1, "HtmlRemOut")[0]
        response = self.post(
            "tournament_pool_remove", t, {"player_id": str(outsider.id)}
        )
        self.assertIn("That player is not in the pool.", _flashes(response))
