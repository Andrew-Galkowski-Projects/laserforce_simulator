"""ANG-01f-4 -- the Tournament setup API (spec #190, ticket #194).

``PUT /api/tournaments/<id>/seeds/``, ``POST .../lock/`` and ``POST
.../import-participants/`` driven through DRF ``APIClient`` under the ADR-0039
envelope. Every write runs ``get_object()`` (404), then the guards (409), then
the Form (400), then the ``tournament_services.setup`` service.
"""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile

from matches.models import SeasonPhase, Tournament, TournamentParticipant
from matches.tests.league_api_fixtures import LeagueApiTestCase, make_league, make_teams
from teams.models import Team
from teams.roster_importer import REQUIRED_COLUMNS

PLAYER_POOL_IMPORT_MSG = (
    "This Tournament uses a Player-pool draw; add Players to its pool instead."
)


def _url(tournament_id: int, action: str) -> str:
    return f"/api/tournaments/{tournament_id}/{action}/"


def _tournament(name: str, *, manager=None, n_teams: int = 4, **fields) -> Tournament:
    t = Tournament.objects.create(name=name, manager=manager, **fields)
    for seed, team in enumerate(make_teams(f"{name}-T", n_teams), start=1):
        TournamentParticipant.objects.create(tournament=t, team=team, seed=seed)
    return t


def _seeds_of(t: Tournament) -> dict[int, int]:
    return dict(t.participants.values_list("team_id", "seed"))


def _reversed_seeds(t: Tournament) -> dict[str, int]:
    n = t.participants.count()
    return {str(team_id): n + 1 - seed for team_id, seed in _seeds_of(t).items()}


def _embed(t: Tournament) -> None:
    league = make_league(f"{t.name} League")
    season = league.seasons.create(
        name="S1", start_date="2026-01-01", schedule_format="single_round_robin"
    )
    SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="tournament", tournament=t
    )


# --- CSV builders (mirror teams/tests/test_roster_import_view.py) ---------

_HEADER = ",".join(REQUIRED_COLUMNS)
_ROLES = ("commander", "heavy", "scout", "scout", "medic", "ammo")


def _row(team: str, name: str, role: str) -> str:
    return f"{team},{name},{role},28,16,100,Ultrazone Chicago,5'7\""


def _csv(*team_names: str) -> SimpleUploadedFile:
    rows = [
        _row(team, f"{team}-P{i}", role)
        for team in team_names
        for i, role in enumerate(_ROLES)
    ]
    body = "\n".join([_HEADER, *rows]) + "\n"
    return SimpleUploadedFile("roster.csv", body.encode("utf-8"), "text/csv")


class SeedsApiTests(LeagueApiTestCase):
    def put(self, t: Tournament, body: object, client=None):
        return (client or self.api).put(_url(t.id, "seeds"), body, format="json")

    def test_valid_permutation_is_200_with_updated_detail(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        new = _reversed_seeds(t)

        data = self.ok(self.put(t, {"seeds": new}))

        self.assertEqual(data["id"], t.id)
        self.assertEqual(
            {str(p["team"]["id"]): p["seed"] for p in data["participants"]}, new
        )
        self.assertEqual(
            _seeds_of(t), {int(team_id): seed for team_id, seed in new.items()}
        )

    def assert_rejected(self, t: Tournament, seeds: object) -> dict:
        before = _seeds_of(t)
        entry = self.error(self.put(t, {"seeds": seeds}), 400, "invalid", "seeds")
        self.assertEqual(_seeds_of(t), before)
        return entry

    def test_duplicate_seed_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        ids = list(_seeds_of(t))
        entry = self.assert_rejected(t, {str(i): 1 for i in ids})
        self.assertIn("1 to 4", entry["message"])

    def test_gap_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        ids = list(_seeds_of(t))
        self.assert_rejected(t, {str(i): s for i, s in zip(ids, [1, 2, 3, 5])})

    def test_missing_team_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        ids = list(_seeds_of(t))
        entry = self.assert_rejected(t, {str(i): s for i, s in zip(ids[:3], [1, 2, 3])})
        self.assertIn(str(ids[3]), entry["message"])

    def test_extra_team_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        outsider = Team.objects.create(name="Outsider")
        seeds = {str(i): s for i, s in _seeds_of(t).items()}
        seeds[str(outsider.id)] = 5
        entry = self.assert_rejected(t, seeds)
        self.assertIn(str(outsider.id), entry["message"])

    def test_non_integer_seed_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        seeds: dict[str, object] = {str(i): s for i, s in _seeds_of(t).items()}
        seeds[next(iter(seeds))] = "first"
        self.assert_rejected(t, seeds)

    def test_float_seed_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        seeds: dict[str, object] = {str(i): s for i, s in _seeds_of(t).items()}
        seeds[next(iter(seeds))] = 1.5
        self.assert_rejected(t, seeds)

    def test_non_object_seeds_is_400(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        self.assert_rejected(t, [1, 2, 3, 4])

    def test_missing_seeds_key_is_400_required(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        self.error(self.put(t, {}), 400, "required", "seeds")

    def test_locked_is_409_not_setup(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        t.lock_and_build()
        before = _seeds_of(t)
        self.error(
            self.put(t, {"seeds": _reversed_seeds(t)}), 409, "tournament_not_setup"
        )
        self.assertEqual(_seeds_of(t), before)

    def test_locked_with_bad_body_is_still_409(self) -> None:
        # Guards run before the Form.
        t = _tournament("Cup", manager=self.user_a)
        t.lock_and_build()
        self.error(self.put(t, {"seeds": "junk"}), 409, "tournament_not_setup")

    def test_embedded_is_409_embedded(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        _embed(t)
        before = _seeds_of(t)
        self.error(
            self.put(t, {"seeds": _reversed_seeds(t)}), 409, "tournament_embedded"
        )
        self.assertEqual(_seeds_of(t), before)

    def test_cross_account_is_404(self) -> None:
        t = _tournament("Foreign", manager=self.user_b)
        before = _seeds_of(t)
        self.error(self.put(t, {"seeds": _reversed_seeds(t)}), 404, "not_found")
        self.assertEqual(_seeds_of(t), before)

    def test_anonymous_is_403(self) -> None:
        t = _tournament("Cup")
        self.error(
            self.put(t, {"seeds": _reversed_seeds(t)}, client=self.anon),
            403,
            "not_authenticated",
        )


class LockApiTests(LeagueApiTestCase):
    def post(self, t: Tournament, client=None):
        return (client or self.api).post(_url(t.id, "lock"), {}, format="json")

    def test_success_moves_setup_to_active_with_bracket(self) -> None:
        t = _tournament("Cup", manager=self.user_a)

        data = self.ok(self.post(t))

        self.assertEqual(data["state"], "active")
        winners = [b for b in data["brackets"] if b["bracket_type"] == "winners"]
        self.assertEqual(len(winners), 1)
        t.refresh_from_db()
        self.assertEqual(t.state, "active")
        self.assertEqual(t.nodes.count(), 3)

    def assert_lock_invalid(self, t: Tournament, reason: str) -> None:
        entry = self.error(self.post(t), 400, "invalid", None)
        self.assertIn(reason, entry["message"])
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.nodes.count(), 0)

    def test_fewer_than_four_is_400_invalid(self) -> None:
        t = _tournament("Small", manager=self.user_a, n_teams=3)
        self.assert_lock_invalid(t, "at least 4 participants")

    def test_rrde_advancers_above_field_is_400_invalid(self) -> None:
        t = _tournament(
            "RRDE",
            manager=self.user_a,
            format="round_robin_double_elim",
            wb_advancers=8,
            lb_advancers=0,
        )
        self.assert_lock_invalid(t, "exceeds participant count")

    def test_odd_swiss_field_is_400_invalid(self) -> None:
        t = _tournament(
            "Swiss", manager=self.user_a, n_teams=5, format="swiss", swiss_rounds=3
        )
        self.assert_lock_invalid(t, "even number of participants")

    def test_already_active_is_409_not_setup(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        t.lock_and_build()
        self.error(self.post(t), 409, "tournament_not_setup")

    def test_embedded_is_409_embedded(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        _embed(t)
        self.error(self.post(t), 409, "tournament_embedded")
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")

    def test_cross_account_is_404(self) -> None:
        t = _tournament("Foreign", manager=self.user_b)
        self.error(self.post(t), 404, "not_found")
        t.refresh_from_db()
        self.assertEqual(t.state, "setup")

    def test_anonymous_is_403(self) -> None:
        t = _tournament("Cup")
        self.error(self.post(t, client=self.anon), 403, "not_authenticated")


class ImportParticipantsApiTests(LeagueApiTestCase):
    def post(self, t: Tournament, upload=None, client=None):
        body = {} if upload is None else {"csv_file": upload}
        return (client or self.api).post(
            _url(t.id, "import-participants"), body, format="multipart"
        )

    def test_201_with_new_owned_participants_reseeded(self) -> None:
        t = _tournament("Cup", manager=self.user_a, n_teams=1)
        existing_id = t.participants.get().team_id
        # A pre-existing Team the CSV appends to does not join the field.
        Team.objects.create(name="Appendee", manager=self.user_a)
        upload = _csv("ImpA", "ImpB", "ImpC")
        body = upload.read().decode() + _row("Appendee", "Bench", "commander") + "\n"
        upload = SimpleUploadedFile("roster.csv", body.encode("utf-8"), "text/csv")

        data = self.ok(self.post(t, upload), status=201)

        rows = data["participants"]
        imported = {Team.objects.get(name=n).id for n in ("ImpA", "ImpB", "ImpC")}
        self.assertEqual({p["team"]["id"] for p in rows}, imported | {existing_id})
        self.assertEqual([p["seed"] for p in rows], [1, 2, 3, 4])
        for name in ("ImpA", "ImpB", "ImpC"):
            self.assertEqual(Team.objects.get(name=name).manager, self.user_a)

    def test_bad_csv_is_400_roster_invalid_with_row_fields(self) -> None:
        t = _tournament("Cup", manager=self.user_a, n_teams=0)
        body = "\n".join([_HEADER, "Bad,Guy,wizard,28,16,100,Site,5'7\""]) + "\n"
        upload = SimpleUploadedFile("roster.csv", body.encode("utf-8"), "text/csv")

        errors = self.errors(self.post(t, upload), 400)

        self.assertTrue(all(e["code"] == "roster_invalid" for e in errors), errors)
        # The first data row is row 1 (row 0 is the header).
        self.assertIn("row:1:role", {e["field"] for e in errors})
        self.assertFalse(Team.objects.filter(name="Bad").exists())
        self.assertEqual(t.participants.count(), 0)

    def test_missing_file_is_400_required(self) -> None:
        t = _tournament("Cup", manager=self.user_a, n_teams=0)
        self.error(self.post(t), 400, "required", "csv_file")

    def test_locked_is_409_not_setup(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        t.lock_and_build()
        self.error(self.post(t, _csv("Late")), 409, "tournament_not_setup")
        self.assertFalse(Team.objects.filter(name="Late").exists())

    def test_embedded_is_409_embedded(self) -> None:
        t = _tournament("Cup", manager=self.user_a)
        _embed(t)
        self.error(self.post(t, _csv("Late")), 409, "tournament_embedded")
        self.assertFalse(Team.objects.filter(name="Late").exists())

    def test_player_pool_is_400_invalid_and_imports_nothing(self) -> None:
        # ANG-01f review (P1): a Player-pool Tournament adds Players, not Teams.
        t = _tournament(
            "Pool", manager=self.user_a, n_teams=0, team_assembly="player_pool"
        )
        err = self.error(self.post(t, _csv("PoolImp")), 400, "invalid", None)
        self.assertEqual(err["message"], PLAYER_POOL_IMPORT_MSG)
        self.assertFalse(Team.objects.filter(name="PoolImp").exists())
        self.assertEqual(t.participants.count(), 0)

    def test_cross_account_is_404(self) -> None:
        t = _tournament("Foreign", manager=self.user_b)
        self.error(self.post(t, _csv("Late")), 404, "not_found")
        self.assertFalse(Team.objects.filter(name="Late").exists())

    def test_anonymous_is_403(self) -> None:
        t = _tournament("Cup")
        self.error(
            self.post(t, _csv("Late"), client=self.anon), 403, "not_authenticated"
        )
