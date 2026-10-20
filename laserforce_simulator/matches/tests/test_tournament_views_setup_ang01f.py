"""ANG-01f-4 -- the HTML setup views on the shared setup service (ticket #194).

``tournament_reseed`` binds ``SeedsForm`` (a bad map flashes, never a 500),
and reseed / lock / import gain the embedded and not-setup guards: each
flashes, redirects to the detail page and writes nothing. The pre-existing
behaviour is pinned by ``test_tournament_views.py``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from matches.models import SeasonPhase, Tournament, TournamentParticipant
from matches.tests.league_api_fixtures import make_league, make_teams
from teams.models import Team
from teams.roster_importer import REQUIRED_COLUMNS

User = get_user_model()

_HEADER = ",".join(REQUIRED_COLUMNS)
_ROLES = ("commander", "heavy", "scout", "scout", "medic", "ammo")


def _csv(*team_names: str) -> SimpleUploadedFile:
    rows = [
        f"{team},{team}-P{i},{role},28,16,100,Ultrazone Chicago,5'7\""
        for team in team_names
        for i, role in enumerate(_ROLES)
    ]
    body = "\n".join([_HEADER, *rows]) + "\n"
    return SimpleUploadedFile("roster.csv", body.encode("utf-8"), "text/csv")


def _tournament(name: str = "Cup", *, n_teams: int = 4, manager=None) -> Tournament:
    t = Tournament.objects.create(name=name, manager=manager)
    for seed, team in enumerate(make_teams(f"{name}-T", n_teams), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _embed(t: Tournament) -> None:
    league = make_league(f"{t.name} League")
    season = league.seasons.create(
        name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
    )
    SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="tournament", tournament=t
    )


def _seeds_of(t: Tournament) -> dict[int, int]:
    return dict(t.participants.values_list("team_id", "seed"))


class _SetupViewTestCase(TestCase):
    def flashes(self, response) -> list[str]:
        return [str(m) for m in get_messages(response.wsgi_request)]

    def assert_flash_redirect(self, response, t: Tournament, text: str) -> None:
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("tournament_detail", args=[t.id])
        )
        flashes = self.flashes(response)
        self.assertTrue(any(text in m for m in flashes), flashes)


class ReseedViewTests(_SetupViewTestCase):
    def post(self, t: Tournament, data: dict):
        return self.client.post(reverse("tournament_reseed", args=[t.id]), data)

    def test_duplicate_seed_flashes_not_500_and_writes_nothing(self) -> None:
        t = _tournament()
        before = _seeds_of(t)
        response = self.post(t, {f"seed_{team_id}": "1" for team_id in before})
        self.assert_flash_redirect(response, t, "1 to 4")
        self.assertEqual(_seeds_of(t), before)

    def test_missing_seed_flashes_and_writes_nothing(self) -> None:
        t = _tournament()
        before = _seeds_of(t)
        first, *rest = before
        data = {f"seed_{team_id}": str(before[team_id]) for team_id in rest}
        response = self.post(t, data)
        self.assert_flash_redirect(response, t, str(first))
        self.assertEqual(_seeds_of(t), before)

    def test_non_integer_seed_flashes_and_writes_nothing(self) -> None:
        t = _tournament()
        before = _seeds_of(t)
        data = {f"seed_{team_id}": str(5 - seed) for team_id, seed in before.items()}
        data[f"seed_{next(iter(before))}"] = "top"
        response = self.post(t, data)
        self.assert_flash_redirect(response, t, "whole number")
        self.assertEqual(_seeds_of(t), before)

    def test_embedded_flashes_and_writes_nothing(self) -> None:
        t = _tournament()
        _embed(t)
        before = _seeds_of(t)
        data = {f"seed_{team_id}": str(5 - seed) for team_id, seed in before.items()}
        response = self.post(t, data)
        self.assert_flash_redirect(response, t, "League Season")
        self.assertEqual(_seeds_of(t), before)

    def test_cross_account_is_404(self) -> None:
        other = User.objects.create_user(
            email="ang01f4-b@example.com", password="Str0ng-Passphrase-9"
        )
        t = _tournament(manager=other)
        before = _seeds_of(t)
        data = {f"seed_{team_id}": str(5 - seed) for team_id, seed in before.items()}
        self.assertEqual(self.post(t, data).status_code, 404)
        self.assertEqual(_seeds_of(t), before)


class LockViewTests(_SetupViewTestCase):
    def post(self, t: Tournament):
        return self.client.post(reverse("tournament_lock", args=[t.id]))

    def test_embedded_flashes_and_stays_setup(self) -> None:
        t = _tournament()
        _embed(t)
        response = self.post(t)
        self.assert_flash_redirect(response, t, "League Season")
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)

    def test_already_active_flashes_not_setup(self) -> None:
        t = _tournament()
        t.lock_and_build()
        nodes = t.nodes.count()
        response = self.post(t)
        self.assert_flash_redirect(response, t, "no longer in setup")
        self.assertEqual(t.nodes.count(), nodes)

    def test_format_rule_reason_is_flashed(self) -> None:
        t = _tournament(n_teams=3)
        response = self.post(t)
        self.assert_flash_redirect(response, t, "at least 4 participants")


class ImportViewTests(_SetupViewTestCase):
    def post(self, t: Tournament, upload):
        return self.client.post(
            reverse("tournament_import_participants", args=[t.id]),
            {"csv_file": upload},
        )

    def test_embedded_flashes_and_imports_nothing(self) -> None:
        t = _tournament()
        _embed(t)
        response = self.post(t, _csv("Late"))
        self.assert_flash_redirect(response, t, "League Season")
        self.assertFalse(Team.objects.filter(name="Late").exists())
        self.assertEqual(t.participants.count(), 4)

    def test_player_pool_flashes_and_imports_nothing(self) -> None:
        # ANG-01f review (P1): a Player-pool Tournament adds Players, not Teams.
        t = _tournament(n_teams=0)
        t.team_assembly = "player_pool"
        t.save(update_fields=["team_assembly"])
        response = self.post(t, _csv("PoolImp"))
        self.assert_flash_redirect(
            response,
            t,
            "This Tournament uses a Player-pool draw; add Players to its pool "
            "instead.",
        )
        self.assertFalse(Team.objects.filter(name="PoolImp").exists())
        self.assertEqual(t.participants.count(), 0)

    def test_imported_teams_owned_by_requester(self) -> None:
        t = _tournament(n_teams=0)
        response = self.post(t, _csv("Mine"))
        self.assertEqual(response.status_code, 302)
        team = Team.objects.get(name="Mine")
        self.assertEqual(team.manager_id, response.wsgi_request.user.id)
        self.assertEqual(
            list(t.participants.values_list("team_id", flat=True)), [team.id]
        )
