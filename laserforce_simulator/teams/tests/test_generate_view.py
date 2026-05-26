"""LG-00 — View / form / DB tests for the player generation flow.

Covers ``GET /teams/generate/`` (form render) and ``POST /teams/generate/``
(generate Teams and Players inside an atomic transaction, with name-collision
handling and a free-agents pool mode).

Seam contract: ``.claude/worktrees/lg-00-seam-contract.md`` §3 + §11.2.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase
from django.urls import reverse

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Player, Team, get_free_agents_team

# ---------------------------------------------------------------------------
# §11.2 — TestGenerateGet
# ---------------------------------------------------------------------------


class TestGenerateGet(TestCase):
    """``GET /teams/generate/`` renders the form."""

    def test_get_200(self) -> None:
        """GET is 200."""
        response = self.client.get(reverse("generate_players"))
        self.assertEqual(response.status_code, 200)

    def test_form_fields_present(self) -> None:
        """Response body contains every locked DOM id from §8a."""
        response = self.client.get(reverse("generate_players"))
        body = response.content.decode()
        for dom_id in (
            "generate-players-form",
            "generate-players-num-teams",
            "generate-players-per-team",
            "generate-players-mean",
            "generate-players-std-dev",
            "generate-players-submit",
        ):
            self.assertIn(dom_id, body, f"missing DOM id {dom_id!r}")


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostHappyPathTeams
# ---------------------------------------------------------------------------


class TestGeneratePostHappyPathTeams(TestCase):
    """POST 3 teams × 6 players — happy path."""

    def _post(self) -> "object":
        return self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "3",
                "players_per_team": "6",
                "mean": "50",
                "std_dev": "15",
            },
        )

    def test_post_3_teams_6_players_creates_18_players_3_teams(self) -> None:
        """3 regular teams + 18 players in the DB after the POST."""
        before_regular = Team.objects.regular().count()
        before_players = Player.objects.count()
        self._post()
        self.assertEqual(Team.objects.regular().count() - before_regular, 3)
        self.assertEqual(Player.objects.count() - before_players, 18)

    def test_post_3_teams_all_rosters_valid(self) -> None:
        """Each newly created team has ``is_valid_roster == True``."""
        self._post()
        for team in Team.objects.regular():
            self.assertTrue(
                team.is_valid_roster,
                f"team {team.name!r} roster invalid: {team.roster_errors}",
            )

    def test_post_response_is_confirmation_page_with_team_links(self) -> None:
        """200 + ``generate-confirm-teams-list`` id + 3 team_detail anchors.

        The project's root urlconf includes ``teams.urls`` at both ``/teams/``
        and ``""`` (homepage), so ``reverse('team_detail', ...)`` resolves to
        whichever prefix Django registered last (currently ``""``). The test
        uses ``reverse()`` rather than a hard-coded ``/teams/<id>/`` regex so
        it remains correct regardless of which prefix wins.
        """
        response = self._post()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("generate-confirm-teams-list", body)
        created = list(Team.objects.regular().order_by("-id")[:3])
        self.assertEqual(len(created), 3)
        for team in created:
            expected_href = f'href="{reverse("team_detail", args=[team.id])}"'
            self.assertIn(
                expected_href,
                body,
                f"expected anchor {expected_href!r} for team {team.name!r}",
            )


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostHappyPathBenchPlayers
# ---------------------------------------------------------------------------


class TestGeneratePostHappyPathBenchPlayers(TestCase):
    """POST 2 teams × 8 players — 6 active + 2 bench per team."""

    def test_post_2_teams_8_players_each_creates_16_players_with_bench(self) -> None:
        before = Player.objects.count()
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "2",
                "players_per_team": "8",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertEqual(Player.objects.count() - before, 16)
        teams = list(Team.objects.regular())
        # Only the two newly-created teams should be regular here.
        self.assertEqual(len(teams), 2)
        for team in teams:
            self.assertEqual(
                len(team.active_players),
                6,
                f"team {team.name!r} has {len(team.active_players)} active",
            )
            self.assertEqual(
                len(team.bench_players),
                2,
                f"team {team.name!r} has {len(team.bench_players)} bench",
            )


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostHappyPathFreeAgents
# ---------------------------------------------------------------------------


class TestGeneratePostHappyPathFreeAgents(TestCase):
    """POST 0 teams × 20 — free-agent pool mode."""

    def _post(self) -> "object":
        return self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "20",
                "mean": "50",
                "std_dev": "15",
            },
        )

    def test_post_0_teams_20_pool_creates_20_free_agents(self) -> None:
        """No new regular teams; the Free Agents Team gets 20 players."""
        regular_before = Team.objects.regular().count()
        self._post()
        self.assertEqual(Team.objects.regular().count(), regular_before)
        free_agents = get_free_agents_team()
        self.assertEqual(free_agents.players.count(), 20)

    def test_post_0_teams_response_contains_free_agents_notice(self) -> None:
        """The confirmation page contains the locked DOM ids + the count 20."""
        response = self._post()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("generate-confirm-free-agents-notice", body)
        self.assertIn("generate-confirm-free-agent-count", body)
        # The count itself appears inside the span; cheap substring check.
        self.assertIn("20", body)


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostRandomResolutions
# ---------------------------------------------------------------------------


class TestGeneratePostRandomResolutions(TestCase):
    """Random markers resolve into the locked integer ranges."""

    def test_random_2_10_resolves_in_range(self) -> None:
        """10 POSTs of ``random_2_10`` + ``random_team`` — each run within range."""
        for _ in range(10):
            # Clear regular teams between iterations (TestCase rollback only
            # fires per-test, not per-loop). Explicitly delete every regular
            # team plus their cascade-attached players.
            Team.objects.regular().delete()
            self.client.post(
                reverse("generate_players"),
                {
                    "num_teams": "random_2_10",
                    "players_per_team": "random_team",
                    "mean": "50",
                    "std_dev": "15",
                },
            )
            regular_teams = list(Team.objects.regular())
            self.assertGreaterEqual(
                len(regular_teams), 2, f"got {len(regular_teams)} teams"
            )
            self.assertLessEqual(
                len(regular_teams), 10, f"got {len(regular_teams)} teams"
            )
            for team in regular_teams:
                ppt = Player.objects.filter(team=team).count()
                self.assertGreaterEqual(ppt, 6, f"team {team.name!r}: {ppt} players")
                self.assertLessEqual(ppt, 8, f"team {team.name!r}: {ppt} players")

    def test_random_pool_resolves_in_range(self) -> None:
        """10 POSTs of ``num_teams=0`` + ``random_pool`` — count within 12..100."""
        for _ in range(10):
            # Reset the Free Agents Team's players between iterations.
            free_agents = get_free_agents_team()
            Player.objects.filter(team=free_agents).delete()
            self.client.post(
                reverse("generate_players"),
                {
                    "num_teams": "0",
                    "players_per_team": "random_pool",
                    "mean": "50",
                    "std_dev": "15",
                },
            )
            free_agents = get_free_agents_team()
            count = free_agents.players.count()
            self.assertGreaterEqual(count, 12, f"free-agent count {count}")
            self.assertLessEqual(count, 100, f"free-agent count {count}")


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostCrossFieldValidation
# ---------------------------------------------------------------------------


class TestGeneratePostCrossFieldValidation(TestCase):
    """``GenerateLeagueForm.clean()`` enforces the cross-field rules from §4."""

    def test_num_teams_0_with_players_per_team_8_is_invalid(self) -> None:
        """Pool mode with players_per_team=8 → form error, no DB writes."""
        regular_before = Team.objects.regular().count()
        players_before = Player.objects.count()
        response = self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "8",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Players per team must be 12", body)
        # No DB writes.
        self.assertEqual(Team.objects.regular().count(), regular_before)
        self.assertEqual(Player.objects.count(), players_before)

    def test_num_teams_5_with_players_per_team_50_is_invalid(self) -> None:
        """Team mode with players_per_team=50 → form error, no DB writes."""
        regular_before = Team.objects.regular().count()
        players_before = Player.objects.count()
        response = self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "5",
                "players_per_team": "50",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Players per team must be 6", body)
        self.assertEqual(Team.objects.regular().count(), regular_before)
        self.assertEqual(Player.objects.count(), players_before)


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostNameCollisions
# ---------------------------------------------------------------------------


class TestGeneratePostNameCollisions(TestCase):
    """Team / Player name collision handling produces ``" #N"`` suffixes."""

    def test_pre_existing_team_name_gets_hash_suffix(self) -> None:
        """Pre-fill ALL but ONE ``TEAM_NAMES`` entries so any generation
        for ≥2 new teams must collide on a pre-existing name and produce
        at least one ``" #2"``-suffixed team name.
        """
        # Pre-create a Team for every TEAM_NAMES entry — this guarantees
        # that any new generation hits a collision regardless of which
        # name the shuffled pool pops first.
        for name in TEAM_NAMES:
            Team.objects.create(name=name)

        # POST a 2-team run.
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "2",
                "players_per_team": "6",
                "mean": "50",
                "std_dev": "15",
            },
        )
        # At least one newly created team must have a name ending in `" #2"`
        # (the suffix the view appends on collision).
        new_team_names = list(
            Team.objects.regular()
            .exclude(name__in=TEAM_NAMES)
            .values_list("name", flat=True)
        )
        self.assertTrue(
            any(name.endswith(" #2") for name in new_team_names),
            f'no `" #2"`-suffixed team names found among {new_team_names!r}',
        )

    def test_pre_existing_free_agent_player_name_gets_hash_suffix(self) -> None:
        """Pre-fill the Free Agents Team with one of every PLAYER_NAMES entry
        so a free-agent generation must collide and produce ``" #2"`` suffixes.
        """
        free_agents = get_free_agents_team()
        for name in PLAYER_NAMES:
            Player.objects.create(team=free_agents, name=name)

        # POST a free-agent pool of size 12 — enough to force collisions.
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "12",
                "mean": "50",
                "std_dev": "15",
            },
        )
        # At least one of the 12 new players must have a ``" #2"``-suffixed
        # name (the suffix the view appends on collision for the pool branch).
        suffixed = Player.objects.filter(team=free_agents, name__endswith=" #2").count()
        self.assertGreaterEqual(
            suffixed,
            1,
            'no `" #2"`-suffixed player names in the Free Agents Team after collision POST',
        )


# ---------------------------------------------------------------------------
# §11.2 — TestGeneratePostTransactionAtomic
# ---------------------------------------------------------------------------


class TestGeneratePostTransactionAtomic(TestCase):
    """``@transaction.atomic`` rolls back partial generations on any raise."""

    def test_pure_module_raises_mid_generation_rolls_back(self) -> None:
        """Monkey-patch ``teams.views.draw_stats`` to raise on the 8th call
        (well into the second team — after the first team's 6 players have
        been written). POST a 2-team / 6-player run. The exception propagates,
        the response is 5xx, and the DB is empty afterwards.
        """
        regular_before = Team.objects.regular().count()
        players_before = Player.objects.count()

        call_count = {"n": 0}
        real_draw_stats = None  # populated inside the mock setup

        import teams.views as views_module

        # Capture the real function once so the wrapper can delegate.
        real_draw_stats = views_module.draw_stats

        def fail_after_n(rng, mean, std_dev):  # noqa: ANN001
            call_count["n"] += 1
            if call_count["n"] >= 8:
                raise RuntimeError("boom")
            return real_draw_stats(rng, mean, std_dev)

        with mock.patch.object(views_module, "draw_stats", side_effect=fail_after_n):
            # Suppress Django's exception page noise and let the raise
            # propagate. The test client uses ``raise_request_exception=True``
            # by default — set it False so the exception is captured into
            # a 500 response we can assert against.
            self.client.raise_request_exception = False
            response = self.client.post(
                reverse("generate_players"),
                {
                    "num_teams": "2",
                    "players_per_team": "6",
                    "mean": "50",
                    "std_dev": "15",
                },
            )

        # 5xx response.
        self.assertGreaterEqual(response.status_code, 500)
        self.assertLess(response.status_code, 600)

        # Rolled back: no new regular teams, no new players.
        self.assertEqual(Team.objects.regular().count(), regular_before)
        self.assertEqual(Player.objects.count(), players_before)


# ---------------------------------------------------------------------------
# §11.2 — TestFreeAgentsTeamAutoCreated
# ---------------------------------------------------------------------------


class TestFreeAgentsTeamAutoCreated(TestCase):
    """The Free Agents Team is auto-created on first pool POST + reused."""

    def test_free_agents_team_created_on_first_pool_post(self) -> None:
        """No Free Agents Team beforehand; exactly one afterwards."""
        self.assertEqual(Team.objects.filter(name="Free Agents").count(), 0)
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "12",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertEqual(Team.objects.filter(name="Free Agents").count(), 1)

    def test_free_agents_team_reused_on_second_pool_post(self) -> None:
        """Two pool POSTs in sequence — still exactly one Free Agents row;
        the row's pk is unchanged."""
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "12",
                "mean": "50",
                "std_dev": "15",
            },
        )
        first = Team.objects.get(name="Free Agents")
        first_pk = first.pk

        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "12",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertEqual(Team.objects.filter(name="Free Agents").count(), 1)
        second = Team.objects.get(name="Free Agents")
        self.assertEqual(second.pk, first_pk)
