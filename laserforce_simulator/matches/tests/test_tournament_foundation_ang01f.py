"""ANG-01f-2 -- the Tournament foundation: migration, labels and guards.

Spec #190 / ticket #192. Covers the ``random_draw`` -> ``player_pool``
team-assembly rename (model choices + the migration's row rewrite), the
``format_label`` / ``is_embedded`` labels in ``tournament_services.detail``,
and the ``require_sandbox`` / ``require_setup`` / ``require_active`` guards in
``tournament_services.errors``.
"""

from __future__ import annotations

import importlib

from django.apps import apps as global_apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse

from rest_framework.exceptions import APIException

from laserforce_simulator.api.exceptions import envelope_exception_handler
from matches.models import Season, SeasonPhase, Tournament
from matches.tournament_api_views.base import translate_tournament_errors
from matches.tests.league_api_fixtures import make_league
from matches.tournament_services.detail import format_label, is_embedded
from matches.tournament_services.errors import (
    BrokerUnavailableError,
    DrawNotReadyError,
    NoPlayableNodeError,
    PlayInProgressError,
    TournamentEmbeddedError,
    TournamentNotActiveError,
    TournamentNotSetupError,
    require_active,
    require_sandbox,
    require_setup,
)

_MIGRATION = "matches.migrations.0064_tournament_player_pool_and_active_play_job"


def _season(name: str = "S1") -> Season:
    league = make_league(f"{name} League")
    return Season.objects.create(
        league=league,
        name=name,
        start_date="2026-01-01",
        schedule_format="single_round_robin",
    )


class PlayerPoolRenameTests(TestCase):
    def test_team_assembly_choices_are_preset_and_player_pool(self) -> None:
        self.assertEqual(
            Tournament.TEAM_ASSEMBLY_CHOICES,
            (("preset", "Preset teams"), ("player_pool", "Player pool draw")),
        )

    def test_active_play_job_id_defaults_to_null(self) -> None:
        t = Tournament.objects.create(name="Cup")
        t.refresh_from_db()
        self.assertIsNone(t.active_play_job_id)

    def test_migration_rewrites_random_draw_rows_to_player_pool(self) -> None:
        migration = importlib.import_module(_MIGRATION)
        old = Tournament.objects.create(name="Old")
        Tournament.objects.filter(pk=old.pk).update(team_assembly="random_draw")
        preset = Tournament.objects.create(name="Preset")

        migration.random_draw_to_player_pool(global_apps, None)

        old.refresh_from_db()
        preset.refresh_from_db()
        self.assertEqual(old.team_assembly, "player_pool")
        self.assertEqual(preset.team_assembly, "preset")

    def test_migration_reverse_restores_random_draw(self) -> None:
        migration = importlib.import_module(_MIGRATION)
        t = Tournament.objects.create(name="New", team_assembly="player_pool")

        migration.player_pool_to_random_draw(global_apps, None)

        t.refresh_from_db()
        self.assertEqual(t.team_assembly, "random_draw")

    def test_season_phase_tournament_mode_random_draw_is_untouched(self) -> None:
        values = [value for value, _ in SeasonPhase.TOURNAMENT_MODE_CHOICES]
        self.assertIn("random_draw", values)


class FormatLabelTests(TestCase):
    def test_player_pool_rrde_is_random_draw(self) -> None:
        t = Tournament(format="round_robin_double_elim", team_assembly="player_pool")
        self.assertEqual(format_label(t), "Random Draw")

    def test_preset_rrde_is_the_plain_format_label(self) -> None:
        t = Tournament(format="round_robin_double_elim", team_assembly="preset")
        self.assertEqual(format_label(t), "Round robin → Double elimination")

    def test_player_pool_single_elim_is_the_plain_format_label(self) -> None:
        t = Tournament(format="single_elimination", team_assembly="player_pool")
        self.assertEqual(format_label(t), "Single elimination")


class IsEmbeddedTests(TestCase):
    def test_sandbox_tournament_is_not_embedded(self) -> None:
        t = Tournament.objects.create(name="Sandbox")
        self.assertFalse(is_embedded(t))

    def test_tournament_referenced_by_a_season_phase_is_embedded(self) -> None:
        t = Tournament.objects.create(name="Season bracket")
        SeasonPhase.objects.create(
            season=_season(), ordinal=1, phase_type="tournament", tournament=t
        )
        self.assertTrue(is_embedded(t))

    def test_regional_tournament_with_season_phase_is_embedded(self) -> None:
        phase = SeasonPhase.objects.create(
            season=_season("S2"), ordinal=1, phase_type="tournament"
        )
        t = Tournament.objects.create(name="Regional", season_phase=phase)
        self.assertTrue(is_embedded(t))


class GuardTests(TestCase):
    def _embedded(self, state: str) -> Tournament:
        t = Tournament.objects.create(name=f"Embedded {state}", state=state)
        SeasonPhase.objects.create(
            season=_season(f"G{state}"),
            ordinal=1,
            phase_type="tournament",
            tournament=t,
        )
        return t

    def test_require_sandbox_passes_a_sandbox_tournament(self) -> None:
        require_sandbox(Tournament.objects.create(name="Sandbox"))

    def test_require_sandbox_rejects_an_embedded_tournament(self) -> None:
        with self.assertRaises(TournamentEmbeddedError):
            require_sandbox(self._embedded("setup"))

    def test_require_setup_passes_a_sandbox_setup_tournament(self) -> None:
        require_setup(Tournament.objects.create(name="Setup", state="setup"))

    def test_require_setup_rejects_an_active_tournament(self) -> None:
        with self.assertRaises(TournamentNotSetupError):
            require_setup(Tournament.objects.create(name="Locked", state="active"))

    def test_require_setup_reports_embedded_before_state(self) -> None:
        with self.assertRaises(TournamentEmbeddedError):
            require_setup(self._embedded("active"))

    def test_require_active_passes_an_active_tournament(self) -> None:
        require_active(Tournament.objects.create(name="Active", state="active"))

    def test_require_active_rejects_setup_and_completed(self) -> None:
        for state in ("setup", "completed"):
            with self.subTest(state=state), self.assertRaises(TournamentNotActiveError):
                require_active(Tournament.objects.create(name=state, state=state))

    def test_require_active_reports_embedded_before_state(self) -> None:
        with self.assertRaises(TournamentEmbeddedError):
            require_active(self._embedded("setup"))

    def test_play_in_progress_error_carries_the_job_id(self) -> None:
        exc = PlayInProgressError("job-123")
        self.assertEqual(exc.job_id, "job-123")

    def test_draw_not_ready_error_carries_its_message(self) -> None:
        self.assertEqual(
            str(DrawNotReadyError("Pool must be 24+.")), "Pool must be 24+."
        )

    def test_remaining_errors_are_exceptions(self) -> None:
        for cls in (NoPlayableNodeError, BrokerUnavailableError):
            self.assertTrue(issubclass(cls, Exception))


class HtmlLabelTests(TestCase):
    def test_list_page_shows_random_draw_label(self) -> None:
        Tournament.objects.create(
            name="Pool RRDE",
            format="round_robin_double_elim",
            team_assembly="player_pool",
        )
        response = self.client.get(reverse("tournament_list"))
        self.assertContains(response, "Random Draw")

    def test_detail_page_shows_format_label(self) -> None:
        t = Tournament.objects.create(
            name="Pool RRDE",
            format="round_robin_double_elim",
            team_assembly="player_pool",
        )
        response = self.client.get(reverse("tournament_detail", args=[t.id]))
        self.assertContains(response, 'id="tournament-format-label"')
        self.assertContains(response, "Random Draw")

    def test_create_form_offers_player_pool_draw(self) -> None:
        response = self.client.get(reverse("tournament_create"))
        self.assertContains(response, '<option value="player_pool">Player pool draw')


class TranslatorTests(TestCase):
    """``translate_tournament_errors`` maps each domain error to its envelope."""

    def translate(self, exc: Exception) -> tuple[int, list[dict]]:
        with self.assertRaises(APIException) as ctx:
            with translate_tournament_errors():
                raise exc
        response = envelope_exception_handler(ctx.exception, {})
        return response.status_code, response.data["errors"]

    def assert_maps(
        self, exc: Exception, status: int, code: str, field: str | None = None
    ) -> list[dict]:
        got_status, errors = self.translate(exc)
        self.assertEqual(got_status, status)
        self.assertEqual([(e["code"], e["field"]) for e in errors], [(code, field)])
        return errors

    def test_conflicts_are_409s(self) -> None:
        cases = (
            (TournamentEmbeddedError(), "tournament_embedded"),
            (TournamentNotSetupError(), "tournament_not_setup"),
            (TournamentNotActiveError(), "tournament_not_active"),
            (NoPlayableNodeError(), "no_playable_node"),
        )
        for exc, code in cases:
            with self.subTest(code=code):
                errors = self.assert_maps(exc, 409, code)
                self.assertEqual(errors[0]["message"], str(exc))

    def test_play_in_progress_names_the_job(self) -> None:
        self.assert_maps(
            PlayInProgressError("abc-1"), 409, "play_in_progress", "job:abc-1"
        )

    def test_broker_unavailable_is_503(self) -> None:
        self.assert_maps(BrokerUnavailableError(), 503, "broker_unavailable")

    def test_draw_not_ready_is_400_invalid(self) -> None:
        errors = self.assert_maps(DrawNotReadyError("Pool too small."), 400, "invalid")
        self.assertEqual(errors[0]["message"], "Pool too small.")

    def test_lock_validation_error_is_400_invalid(self) -> None:
        errors = self.assert_maps(
            DjangoValidationError("A tournament requires at least 4 participants."),
            400,
            "invalid",
        )
        self.assertEqual(
            errors[0]["message"], "A tournament requires at least 4 participants."
        )

    def test_other_exceptions_pass_through(self) -> None:
        with self.assertRaises(KeyError):
            with translate_tournament_errors():
                raise KeyError("x")
