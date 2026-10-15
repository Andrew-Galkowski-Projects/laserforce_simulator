"""UX-02 — the "You" badge and link / unlink buttons on League / Season / Round pages.

Seam contract: `.claude/worktrees/ux-02-seam-contract.md` §5 (tag), §6
(buttons on the League player page), §8 (badge coverage — rows 6, 10, 13, 16,
18, 19, 20 spot-checked here) and §9 (test boundary).

Assertions stay on the §9 public surface: the substring `you-badge` on a page,
the locked button DOM ids, `PLAYER_ROW_KEYS` still having 28 entries, and the
`team_finances` row dicts now carrying `player_id` (the one §8 view change on a
League screen). No simulation is run — every League / Season / Match /
GameRound / PlayerRoundState row is hand-built with the real ORM.

Fixture warning (§9): Teams from `make_team_with_slots` are **Unmanaged**. The
"home" Team is stamped `manager = get_shared_manager()` (the Account the
autouse fixture logs `self.client` in as) so its Players can be a Self-insert;
the opponent Team is deliberately left Unmanaged.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.self_insert import set_self_insert
from conftest import get_shared_manager
from matches.models import (
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    SeasonPhase,
)
from matches.round_summary import PLAYER_ROW_KEYS
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

BADGE = "you-badge"
LINK_FORM = 'id="self-insert-link-form"'
LINK_BUTTON = 'id="self-insert-link-button"'
UNLINK_FORM = 'id="self-insert-unlink-form"'
UNLINK_BUTTON = 'id="self-insert-unlink-button"'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_league(name: str, *, finance_enabled: bool = False) -> League:
    league = League.objects.create(
        name=name, mode="league", state="active", finance_enabled=finance_enabled
    )
    pool = Team.objects.create(name=f"{name} Free Agents")
    league.free_agent_pool = pool
    league.save(update_fields=["free_agent_pool"])
    return league


def _make_round(
    team_red: Team,
    team_blue: Team,
    states: list[tuple[Player, str, str, int, int, int]],
    *,
    season: Season | None = None,
) -> GameRound:
    """A completed Match + GameRound plus PRS rows.

    ``states`` is ``(player, team_color, role, tags, tagged, score)``.
    """
    match = Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )
    for player, color, role, tags, tagged, score in states:
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role=role,
            tags_made=tags,
            times_tagged=tagged,
            points_scored=score,
        )
    return game_round


class _LeagueFixture(TestCase):
    """An active League with one played regular-season Round.

    * `self.home` — stamped to the shared Account (eligible Players).
    * `self.away` — left Unmanaged.
    * `self.star` — a `home` scout who leads every board; the usual link target.
    """

    finance_enabled = False

    def setUp(self) -> None:
        self.me = get_shared_manager()
        self.league = _make_league("SiLeague", finance_enabled=self.finance_enabled)
        self.season = Season.objects.create(
            league=self.league, name="S1", start_date=date(2026, 6, 1)
        )
        self.home, self.home_players = make_team_with_slots("SiHome")
        self.away, self.away_players = make_team_with_slots("SiAway")
        self.season.teams.add(self.home, self.away)
        self.season.start_season()
        self.season.refresh_from_db()
        Team.objects.filter(pk=self.home.pk).update(manager=self.me)
        self.home.refresh_from_db()
        self.league.current_team = self.home
        self.league.save(update_fields=["current_team"])

        self.star = self.home_players["scout"]
        self.weak = self.away_players["heavy"]
        self.game_round = _make_round(
            self.home,
            self.away,
            [
                (self.star, "red", "scout", 30, 0, 5000),
                (self.weak, "blue", "heavy", 2, 12, 600),
            ],
            season=self.season,
        )

    def _get(self, url: str) -> str:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def _link_star(self) -> None:
        set_self_insert(self.me, self.star)


# ===========================================================================
# League player page — badge + both button states
# ===========================================================================


class TestLeaguePlayerDetail(_LeagueFixture):
    def _url(self, player: Player) -> str:
        return reverse("league_player_detail", args=[self.league.id, player.id])

    def test_badge_present_when_linked(self) -> None:
        self._link_star()
        self.assertIn(BADGE, self._get(self._url(self.star)))

    def test_no_badge_for_other_player(self) -> None:
        self._link_star()
        self.assertNotIn(BADGE, self._get(self._url(self.home_players["medic"])))

    def test_link_button_when_owned_and_not_linked(self) -> None:
        html = self._get(self._url(self.star))
        self.assertIn(LINK_FORM, html)
        self.assertIn(LINK_BUTTON, html)
        self.assertNotIn(UNLINK_FORM, html)
        self.assertIn(reverse("self_insert_link", args=[self.star.id]), html)

    def test_unlink_button_when_linked(self) -> None:
        self._link_star()
        html = self._get(self._url(self.star))
        self.assertIn(UNLINK_FORM, html)
        self.assertIn(UNLINK_BUTTON, html)
        self.assertNotIn(LINK_FORM, html)
        self.assertNotIn(LINK_BUTTON, html)

    def test_neither_button_on_unmanaged_team(self) -> None:
        html = self._get(self._url(self.weak))
        self.assertNotIn(LINK_FORM, html)
        self.assertNotIn(LINK_BUTTON, html)
        self.assertNotIn(UNLINK_FORM, html)
        self.assertNotIn(UNLINK_BUTTON, html)

    def test_link_round_trip_returns_to_league_page(self) -> None:
        url = self._url(self.star)
        response = self.client.post(
            reverse("self_insert_link", args=[self.star.id]), {"next": url}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url)
        html = self._get(url)
        self.assertIn(BADGE, html)
        self.assertIn(UNLINK_FORM, html)


# ===========================================================================
# League team roster / leaders
# ===========================================================================


class TestLeagueTeamRoster(_LeagueFixture):
    def test_badge_exactly_once_on_roster(self) -> None:
        self._link_star()
        url = reverse("team_roster", args=[self.league.id])
        html = self._get(f"{url}?team_id={self.home.id}")
        self.assertEqual(html.count(BADGE), 1)

    def test_no_badge_on_other_team_roster(self) -> None:
        self._link_star()
        url = reverse("team_roster", args=[self.league.id])
        self.assertNotIn(BADGE, self._get(f"{url}?team_id={self.away.id}"))


class TestLeagueLeaders(_LeagueFixture):
    def test_badge_present_when_linked(self) -> None:
        self._link_star()
        html = self._get(reverse("stats_league_leaders", args=[self.league.id]))
        self.assertIn(BADGE, html)


# ===========================================================================
# Round box score
# ===========================================================================


class TestGameRoundDetail(_LeagueFixture):
    def test_badge_present_when_linked(self) -> None:
        self._link_star()
        html = self._get(reverse("game_round_detail", args=[self.game_round.id]))
        self.assertIn(BADGE, html)

    def test_badge_present_for_blue_side(self) -> None:
        # A Self-insert on the blue side (an owned Team playing blue).
        Team.objects.filter(pk=self.away.pk).update(manager=self.me)
        set_self_insert(self.me, self.weak)
        html = self._get(reverse("game_round_detail", args=[self.game_round.id]))
        self.assertIn(BADGE, html)

    def test_player_row_keys_still_28(self) -> None:
        self.assertEqual(len(PLAYER_ROW_KEYS), 28)
        self.assertNotIn("player_id", PLAYER_ROW_KEYS)


# ===========================================================================
# Round comparison
# ===========================================================================


class TestCompareRounds(_LeagueFixture):
    def test_badge_present_when_linked(self) -> None:
        second = _make_round(
            self.home,
            self.away,
            [
                (self.star, "red", "scout", 20, 3, 4000),
                (self.weak, "blue", "heavy", 4, 10, 900),
            ],
            season=self.season,
        )
        self._link_star()
        url = reverse("compare_rounds")
        html = self._get(f"{url}?round_a={self.game_round.id}&round_b={second.id}")
        self.assertIn(BADGE, html)


# ===========================================================================
# Season awards
# ===========================================================================


class TestSeasonAwards(TestCase):
    def setUp(self) -> None:
        self.me = get_shared_manager()
        league = League.objects.create(name="SiAwards", mode="league", state="active")
        self.home, home_players = make_team_with_slots("SiAwHome")
        self.away, away_players = make_team_with_slots("SiAwAway")
        Team.objects.filter(pk=self.home.pk).update(manager=self.me)
        self.season = Season.objects.create(
            league=league,
            name="S1",
            start_date=date(2026, 1, 1),
            state="completed",
            starting_team_ids_json=sorted([self.home.id, self.away.id]),
        )
        self.season.teams.add(self.home, self.away)
        SeasonPhase.objects.create(
            season=self.season, ordinal=1, phase_type="round_robin"
        )
        self.star = home_players["scout"]
        _make_round(
            self.home,
            self.away,
            [
                (self.star, "red", "scout", 30, 0, 5000),
                (away_players["scout"], "blue", "scout", 2, 12, 100),
            ],
            season=self.season,
        )

    def _html(self) -> str:
        response = self.client.get(reverse("season_awards", args=[self.season.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_badge_present_when_linked(self) -> None:
        set_self_insert(self.me, self.star)
        self.assertIn(BADGE, self._html())

    def test_no_badge_when_unlinked(self) -> None:
        self.assertNotIn(BADGE, self._html())


# ===========================================================================
# Team finances (the view now supplies player_id)
# ===========================================================================


class TestTeamFinances(_LeagueFixture):
    finance_enabled = True

    def _response(self):
        url = reverse("team_finances", kwargs={"league_id": self.league.id})
        response = self.client.get(f"{url}?team_id={self.home.id}")
        self.assertEqual(response.status_code, 200)
        return response

    def test_roster_salary_rows_carry_player_id(self) -> None:
        rows = self._response().context["roster_salaries"]
        self.assertTrue(rows)
        self.assertIn(self.star.id, {r["player_id"] for r in rows})

    def test_availability_rows_carry_player_id(self) -> None:
        Player.objects.filter(pk=self.star.pk).update(games_unavailable=2)
        rows = self._response().context["availability"]
        self.assertEqual([r["player_id"] for r in rows], [self.star.id])

    def test_badge_on_salary_row_when_linked(self) -> None:
        self._link_star()
        html = self._response().content.decode()
        self.assertEqual(html.count(BADGE), 1)

    def test_badge_on_salary_and_availability_rows(self) -> None:
        Player.objects.filter(pk=self.star.pk).update(games_unavailable=2)
        self._link_star()
        html = self._response().content.decode()
        self.assertEqual(html.count(BADGE), 2)


# ===========================================================================
# Regression guard — an unlinked / drifted Account sees no badge anywhere
# ===========================================================================


class TestUnlinkedAccountSeesNoBadge(_LeagueFixture):
    finance_enabled = True

    def _urls(self) -> list[str]:
        roster = reverse("team_roster", args=[self.league.id])
        finances = reverse("team_finances", kwargs={"league_id": self.league.id})
        return [
            reverse("league_player_detail", args=[self.league.id, self.star.id]),
            f"{roster}?team_id={self.home.id}",
            reverse("stats_league_leaders", args=[self.league.id]),
            reverse("game_round_detail", args=[self.game_round.id]),
            f"{finances}?team_id={self.home.id}",
        ]

    def test_unlinked_account_sees_no_badge(self) -> None:
        for url in self._urls():
            with self.subTest(url=url):
                self.assertNotIn(BADGE, self._get(url))

    def test_drifted_account_sees_no_badge(self) -> None:
        self._link_star()
        Team.objects.filter(pk=self.home.pk).update(manager=None)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertNotIn(BADGE, self._get(url))

    def test_linked_account_sees_badge_on_every_page(self) -> None:
        # Positive twin of the guard above, so it cannot pass vacuously.
        self._link_star()
        for url in self._urls():
            with self.subTest(url=url):
                self.assertIn(BADGE, self._get(url))
