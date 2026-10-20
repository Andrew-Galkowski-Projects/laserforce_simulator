"""ANG-01f-5 -- the Player pool and Draw API (spec #190, ticket #195).

``POST .../pool/players/``, ``DELETE .../pool/players/<player_id>/``,
``POST .../pool/generate/``, ``POST .../pool/import/`` (multipart),
``POST .../draw/`` and ``PATCH .../draw/entries/<player_id>/``, driven through
DRF ``APIClient`` under the ADR-0039 envelope. Assertions are on structure,
codes and counts; the simulator never runs.
"""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile

from matches.models import SeasonPhase, Tournament, TournamentPlayerEntry
from matches.tests.league_api_fixtures import LeagueApiTestCase, make_league, make_teams
from teams.models import Player, Team, get_free_agents_team
from teams.roster_importer import REQUIRED_COLUMNS

_STAT_FIELDS = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)

NOT_POOL_MSG = "This Tournament does not use a Player-pool draw."


def _url(t: Tournament, suffix: str) -> str:
    return f"/api/tournaments/{t.id}/{suffix}"


def _pool_tournament(name: str, *, manager=None, **fields) -> Tournament:
    """A setup-state Player-pool draw RR->DE Tournament with an empty pool."""
    return Tournament.objects.create(
        name=name,
        manager=manager,
        format="round_robin_double_elim",
        team_assembly="player_pool",
        wb_advancers=4,
        lb_advancers=0,
        **fields,
    )


def _players(n: int, prefix: str, *, team: Team | None = None) -> list[Player]:
    """``n`` Players with distinct, descending overall ratings."""
    team = team or get_free_agents_team()
    return [
        Player.objects.create(
            team=team, name=f"{prefix}{i}", **{f: 90 - i for f in _STAT_FIELDS}
        )
        for i in range(n)
    ]


def _fill_pool(t: Tournament, n: int, prefix: str) -> list[Player]:
    players = _players(n, prefix)
    for p in players:
        TournamentPlayerEntry.objects.create(tournament=t, player=p)
    return players


def _csv_row(name: str, role: str = "commander") -> str:
    return f"IgnoredTeam,{name},{role},28,16,100,Ultrazone Chicago,5'7\""


def _csv(*rows: str) -> SimpleUploadedFile:
    body = "\n".join([",".join(REQUIRED_COLUMNS), *rows]) + "\n"
    return SimpleUploadedFile("roster.csv", body.encode(), content_type="text/csv")


class PoolAddPlayersTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _pool_tournament("Pool", manager=self.user_a)

    def post(self, player_ids):
        return self.api.post(
            _url(self.t, "pool/players/"), {"player_ids": player_ids}, format="json"
        )

    def test_owned_players_are_added(self) -> None:
        players = _players(3, "Add")
        data = self.ok(self.post([p.id for p in players]), 201)
        self.assertEqual(
            sorted(e["player"]["id"] for e in data), sorted(p.id for p in players)
        )
        self.assertEqual(set(data[0]), {"player", "tier", "drawn_team"})
        self.assertIsNone(data[0]["tier"])
        self.assertEqual(self.t.player_entries.count(), 3)

    def test_another_accounts_player_is_invalid_choice(self) -> None:
        # Leak regression: the lookup used to be every Player in the database.
        foreign_team = make_teams("Foreign", 1, manager=self.user_b)[0]
        foreign = _players(1, "Foreign", team=foreign_team)[0]
        self.error(self.post([foreign.id]), 400, "invalid_choice", "player_ids")
        self.assertEqual(self.t.player_entries.count(), 0)

    def test_player_already_in_pool_is_400(self) -> None:
        pooled = _fill_pool(self.t, 1, "Dup")[0]
        fresh = _players(1, "Fresh")[0]
        self.error(
            self.post([fresh.id, pooled.id]), 400, "already_in_pool", "player_ids"
        )
        self.assertEqual(self.t.player_entries.count(), 1)

    def test_empty_list_is_required(self) -> None:
        self.error(self.post([]), 400, "required", "player_ids")

    def test_preset_tournament_is_400_invalid(self) -> None:
        preset = Tournament.objects.create(name="Preset", manager=self.user_a)
        player = _players(1, "Preset")[0]
        err = self.error(
            self.api.post(
                _url(preset, "pool/players/"),
                {"player_ids": [player.id]},
                format="json",
            ),
            400,
            "invalid",
            None,
        )
        self.assertEqual(err["message"], NOT_POOL_MSG)


class PoolGenerateTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _pool_tournament("Gen", manager=self.user_a)

    def post(self, body: dict):
        return self.api.post(_url(self.t, "pool/generate/"), body, format="json")

    def test_generates_count_entries_on_free_agents(self) -> None:
        data = self.ok(self.post({"count": 6, "mean": 60, "std_dev": 10}), 201)
        self.assertEqual(len(data), 6)
        entries = self.t.player_entries.select_related("player")
        self.assertEqual(entries.count(), 6)
        fa = get_free_agents_team()
        self.assertTrue(all(e.player.team_id == fa.id for e in entries))

    def test_mean_and_std_dev_are_optional(self) -> None:
        self.ok(self.post({"count": 2}), 201)
        self.assertEqual(self.t.player_entries.count(), 2)

    def test_count_zero_is_400(self) -> None:
        self.error(self.post({"count": 0}), 400, "min_value", "count")
        self.assertEqual(self.t.player_entries.count(), 0)

    def test_count_not_a_number_is_400(self) -> None:
        self.error(self.post({"count": "lots"}), 400, "invalid", "count")

    def test_missing_count_is_400(self) -> None:
        self.error(self.post({}), 400, "required", "count")

    def test_bad_mean_and_std_dev_are_400(self) -> None:
        self.error(self.post({"count": 1, "mean": "x"}), 400, "invalid", "mean")
        self.error(self.post({"count": 1, "mean": 101}), 400, "max_value", "mean")
        self.error(self.post({"count": 1, "std_dev": 0}), 400, "min_value", "std_dev")


class PoolImportTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _pool_tournament("Imp", manager=self.user_a)

    def post(self, upload):
        body = {} if upload is None else {"csv_file": upload}
        return self.api.post(_url(self.t, "pool/import/"), body, format="multipart")

    def test_import_is_201_with_one_entry_per_row(self) -> None:
        rows = [_csv_row(f"Imp{i}", role) for i, role in enumerate(("heavy", "scout"))]
        data = self.ok(self.post(_csv(*rows)), 201)
        self.assertEqual(sorted(e["player"]["name"] for e in data), ["Imp0", "Imp1"])
        self.assertEqual(self.t.player_entries.count(), 2)
        fa = get_free_agents_team()
        self.assertTrue(
            all(e.player.team_id == fa.id for e in self.t.player_entries.all())
        )

    def test_bad_csv_is_400_roster_invalid_with_no_writes(self) -> None:
        players_before = Player.objects.count()
        response = self.post(_csv(_csv_row("Bad", role="captain")))
        errors = self.errors(response, 400)
        self.assertEqual({e["code"] for e in errors}, {"roster_invalid"})
        self.assertTrue(all(e["field"].startswith("row:") for e in errors))
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(self.t.player_entries.count(), 0)

    def test_missing_file_is_400_required(self) -> None:
        self.error(self.post(None), 400, "required", "csv_file")


class PoolRemoveTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _pool_tournament("Rem", manager=self.user_a)
        self.players = _fill_pool(self.t, 3, "Rem")

    def delete(self, player_id: int):
        return self.api.delete(_url(self.t, f"pool/players/{player_id}/"))

    def test_remove_is_204_and_drops_the_entry(self) -> None:
        victim = self.players[0]
        response = self.delete(victim.id)
        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(self.t.player_entries.filter(player=victim).exists())
        self.assertEqual(self.t.player_entries.count(), 2)

    def test_player_not_in_pool_is_404(self) -> None:
        outsider = _players(1, "Outsider")[0]
        self.error(self.delete(outsider.id), 404, "not_found")
        self.assertEqual(self.t.player_entries.count(), 3)


class DrawTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _pool_tournament("Draw", manager=self.user_a)

    def draw(self):
        return self.api.post(_url(self.t, "draw/"))

    def test_pool_of_24_gives_4_teams_with_one_player_per_tier(self) -> None:
        _fill_pool(self.t, 24, "D")
        data = self.ok(self.draw())
        self.assertEqual(data["id"], self.t.id)
        self.assertTrue(data["is_drawn"])
        self.assertEqual(data["pool_size"], 24)
        self.assertEqual([p["seed"] for p in data["participants"]], [1, 2, 3, 4])
        team_ids = {p["team"]["id"] for p in data["participants"]}
        for team in Team.objects.filter(id__in=team_ids):
            self.assertTrue(team.is_draw_team)
            self.assertEqual(team.manager, self.user_a)
            tiers = sorted(
                self.t.player_entries.filter(drawn_team=team).values_list(
                    "tier", flat=True
                )
            )
            self.assertEqual(tiers, [1, 2, 3, 4, 5, 6])

    def test_reroll_replaces_the_drawn_teams(self) -> None:
        _fill_pool(self.t, 24, "R")
        first = {p["team"]["id"] for p in self.ok(self.draw())["participants"]}
        second = {p["team"]["id"] for p in self.ok(self.draw())["participants"]}
        self.assertEqual(len(second), 4)
        self.assertFalse(first & second)
        self.assertFalse(Team.objects.filter(id__in=first).exists())
        self.assertEqual(self.t.participants.count(), 4)

    def test_participant_import_is_refused_so_a_reroll_keeps_only_drawn_teams(
        self,
    ) -> None:
        # ANG-01f review (P1): an imported Team could once survive a re-roll in
        # the drawn field; the import is now refused, so nothing is added.
        _fill_pool(self.t, 24, "Imp")
        self.ok(self.draw())
        roster = ",".join(REQUIRED_COLUMNS)
        rows = [
            f"Sneak,Sneak-P{i},{role},28,16,100,Ultrazone Chicago,5'7\""
            for i, role in enumerate(
                ("commander", "heavy", "scout", "scout", "medic", "ammo")
            )
        ]
        upload = SimpleUploadedFile(
            "roster.csv", ("\n".join([roster, *rows]) + "\n").encode(), "text/csv"
        )
        self.error(
            self.api.post(
                _url(self.t, "import-participants/"),
                {"csv_file": upload},
                format="multipart",
            ),
            400,
            "invalid",
            None,
        )
        self.assertFalse(Team.objects.filter(name="Sneak").exists())
        self.assertEqual(self.t.participants.count(), 4)

        data = self.ok(self.draw())
        team_ids = [p["team"]["id"] for p in data["participants"]]
        self.assertEqual(len(team_ids), 4)
        self.assertTrue(
            all(t.is_draw_team for t in Team.objects.filter(id__in=team_ids))
        )

    def test_bad_pool_size_is_400_with_the_rule(self) -> None:
        for n in (23, 18):
            with self.subTest(n=n):
                self.t.player_entries.all().delete()
                _fill_pool(self.t, n, f"Bad{n}-")
                err = self.error(self.draw(), 400, "invalid", None)
                self.assertIn("divisible by 6", err["message"])
                self.assertIn(f"It currently has {n}.", err["message"])
                self.assertEqual(self.t.participants.count(), 0)


def _drawn(name: str, *, manager=None) -> Tournament:
    """A Player-pool Tournament with a 24-Player pool, drawn (4 Teams)."""
    from matches.tournament_services.draw import run_draw

    t = _pool_tournament(name, manager=manager)
    _fill_pool(t, 24, f"{name}-")
    run_draw(t, user=manager)
    return t


class DrawEntryEditTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.t = _drawn("Edit", manager=self.user_a)
        self.entry = self.t.player_entries.order_by("player_id").first()
        self.other_team = (
            self.t.participants.exclude(team_id=self.entry.drawn_team_id).first().team
        )

    def patch(self, body: dict, player_id: int | None = None):
        player_id = self.entry.player_id if player_id is None else player_id
        return self.api.patch(
            _url(self.t, f"draw/entries/{player_id}/"), body, format="json"
        )

    def test_valid_tier_and_team_are_saved(self) -> None:
        new_tier = 6 if self.entry.tier != 6 else 1
        data = self.ok(self.patch({"tier": new_tier, "drawn_team": self.other_team.id}))
        self.assertEqual(data["player"]["id"], self.entry.player_id)
        self.assertEqual(data["tier"], new_tier)
        self.assertEqual(data["drawn_team"]["id"], self.other_team.id)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.tier, new_tier)
        self.assertEqual(self.entry.drawn_team_id, self.other_team.id)

    def test_tier_alone_is_a_partial_update(self) -> None:
        team_before = self.entry.drawn_team_id
        self.ok(self.patch({"tier": 3}))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.tier, 3)
        self.assertEqual(self.entry.drawn_team_id, team_before)

    def test_tier_out_of_range_is_400(self) -> None:
        self.error(self.patch({"tier": 7}), 400, "max_value", "tier")
        self.error(self.patch({"tier": 0}), 400, "min_value", "tier")
        self.error(self.patch({"tier": "high"}), 400, "invalid", "tier")

    def test_another_tournaments_draw_team_is_400(self) -> None:
        # Leak regression: any is_draw_team Team used to be accepted.
        other = _drawn("Other", manager=self.user_a)
        foreign_team = other.participants.first().team
        before = (self.entry.tier, self.entry.drawn_team_id)
        self.error(
            self.patch({"drawn_team": foreign_team.id}),
            400,
            "invalid_choice",
            "drawn_team",
        )
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.tier, self.entry.drawn_team_id), before)

    def test_empty_body_is_400(self) -> None:
        self.error(self.patch({}), 400, "required", None)

    def test_player_not_in_pool_is_404(self) -> None:
        outsider = _players(1, "Outsider")[0]
        self.error(self.patch({"tier": 2}, outsider.id), 404, "not_found")


class DrawWriteGuardTests(LeagueApiTestCase):
    """Every pool / draw write: 409 after lock, 409 embedded, 400 preset,
    404 cross-Account, 401 anonymous -- each with no write."""

    def setUp(self) -> None:
        super().setUp()
        self.player = _players(1, "Guard")[0]

    def writes(self, t: Tournament, client=None):
        """(label, response) for each write endpoint against ``t``."""
        client = client or self.api
        pid = self.player.id
        TournamentPlayerEntry.objects.get_or_create(tournament=t, player=self.player)
        fresh = _players(1, f"Fresh{t.id}-")[0]
        return [
            (
                "add",
                client.post(
                    _url(t, "pool/players/"), {"player_ids": [fresh.id]}, format="json"
                ),
            ),
            (
                "generate",
                client.post(_url(t, "pool/generate/"), {"count": 1}, format="json"),
            ),
            (
                "import",
                client.post(
                    _url(t, "pool/import/"),
                    {"csv_file": _csv(_csv_row(f"Csv{t.id}"))},
                    format="multipart",
                ),
            ),
            ("remove", client.delete(_url(t, f"pool/players/{pid}/"))),
            ("draw", client.post(_url(t, "draw/"))),
            (
                "edit",
                client.patch(
                    _url(t, f"draw/entries/{pid}/"), {"tier": 2}, format="json"
                ),
            ),
        ]

    def assert_all(self, t: Tournament, status: int, code: str, client=None) -> None:
        for label, response in self.writes(t, client):
            with self.subTest(write=label):
                if code == "not_authenticated":
                    # ADR-0041: anonymous is 401 (was 403).
                    self.assertEqual(response.status_code, status, response.content)
                    self.assertEqual(
                        response.json()["errors"][0]["code"], "not_authenticated"
                    )
                else:
                    self.error(response, status, code)
        self.assertEqual(t.player_entries.count(), 1)
        self.assertEqual(t.player_entries.get().tier, None)

    def test_locked_tournament_is_409_not_setup(self) -> None:
        t = _pool_tournament("Locked", manager=self.user_a, state="active")
        self.assert_all(t, 409, "tournament_not_setup")

    def test_embedded_tournament_is_409_embedded(self) -> None:
        t = _pool_tournament("Embedded", manager=self.user_a, state="active")
        league = make_league("Emb League", manager=self.user_a)
        season = league.seasons.create(
            name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
        )
        SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="tournament", tournament=t
        )
        # Embedded is reported before the (locked) state.
        self.assert_all(t, 409, "tournament_embedded")

    def test_preset_tournament_is_400_invalid(self) -> None:
        t = Tournament.objects.create(name="Preset", manager=self.user_a)
        self.assert_all(t, 400, "invalid")

    def test_cross_account_is_404(self) -> None:
        t = _pool_tournament("Foreign", manager=self.user_b)
        self.assert_all(t, 404, "not_found")

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        t = _pool_tournament("Anon", manager=self.user_a)
        self.assert_all(t, 401, "not_authenticated", client=self.anon)
