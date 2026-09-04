"""UX-01 — the `claim_unmanaged` management command.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §10 — six locked
output lines, the per-model counts, `CommandError` on an unknown email, and
idempotency across two runs.

Replaces the `RunPython` backfill ADR-0038 rejected as vacuous: a custom user
model means an empty user table on every existing database, so there is no
superuser for a migration to stamp rows to.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from matches.models import GameRound, League, Match, Season, Tournament
from accounts.permissions import is_owned_by
from teams.models import Team, get_free_agents_team

User = get_user_model()

#: §10 — the five root models, in the command's locked iteration order.
EXPECTED_LINE_ORDER: tuple[str, ...] = (
    "Team",
    "League",
    "Tournament",
    "Match",
    "GameRound",
)


def run(email: str) -> str:
    out = StringIO()
    call_command("claim_unmanaged", "--user", email, stdout=out)
    return out.getvalue()


class ClaimUnmanagedTestBase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.claimer = User.objects.create_user(
            email="claimer@example.com", password="Str0ng-Passphrase-9"
        )
        self.other = User.objects.create_user(
            email="other@example.com", password="Str0ng-Passphrase-9"
        )


class TestClaimUnmanagedOutputShape(ClaimUnmanagedTestBase):
    """§10 — six lines, in order, even when nothing matches."""

    def test_prints_exactly_six_lines(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 6, lines)

    def test_the_five_model_lines_are_in_the_locked_order(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        for expected, line in zip(EXPECTED_LINE_ORDER, lines[:5]):
            with self.subTest(model=expected):
                self.assertTrue(
                    line.startswith(f"{expected}: "),
                    f"expected a {expected!r} line, got {line!r}",
                )

    def test_each_model_line_ends_with_claimed(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        for line in lines[:5]:
            with self.subTest(line=line):
                self.assertTrue(line.endswith(" claimed"), line)

    def test_final_line_is_the_total_naming_the_account(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        self.assertEqual(lines[5], "Total: 0 rows claimed by claimer@example.com")

    def test_empty_database_reports_zero_everywhere(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        for model, line in zip(EXPECTED_LINE_ORDER, lines[:5]):
            with self.subTest(model=model):
                self.assertEqual(line, f"{model}: 0 claimed")


class TestClaimUnmanagedCounts(ClaimUnmanagedTestBase):
    """§10 — per-model counts, and only `manager IS NULL` rows are touched."""

    def setUp(self) -> None:
        super().setUp()
        # Unmanaged rows: 2 Teams, 1 League, 1 Tournament, 1 Match, 3 GameRounds.
        self.team_open_1 = Team.objects.create(name="Open 1")
        self.team_open_2 = Team.objects.create(name="Open 2")
        self.league_open = League.objects.create(name="Open League")
        self.tourney_open = Tournament.objects.create(name="Open Cup")
        self.match_open = Match.objects.create(
            team_red=self.team_open_1, team_blue=self.team_open_2
        )
        self.rounds_open = [GameRound.objects.create(round_number=n) for n in (1, 2, 3)]
        # Already owned by somebody else — must be left alone.
        self.team_other = Team.objects.create(name="Other Team", manager=self.other)
        self.league_other = League.objects.create(
            name="Other League", manager=self.other
        )

    def test_counts_match_the_unmanaged_rows(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        self.assertEqual(lines[0], "Team: 2 claimed")
        self.assertEqual(lines[1], "League: 1 claimed")
        self.assertEqual(lines[2], "Tournament: 1 claimed")
        self.assertEqual(lines[3], "Match: 1 claimed")
        self.assertEqual(lines[4], "GameRound: 3 claimed")

    def test_total_is_the_sum(self) -> None:
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        self.assertEqual(lines[5], "Total: 8 rows claimed by claimer@example.com")

    def test_unmanaged_rows_are_stamped(self) -> None:
        run("claimer@example.com")
        for row in (
            self.team_open_1,
            self.team_open_2,
            self.league_open,
            self.tourney_open,
            self.match_open,
            *self.rounds_open,
        ):
            with self.subTest(row=f"{type(row).__name__}:{row.pk}"):
                row.refresh_from_db()
                self.assertEqual(row.manager_id, self.claimer.pk)

    def test_rows_owned_by_another_account_are_untouched(self) -> None:
        run("claimer@example.com")
        for row in (self.team_other, self.league_other):
            with self.subTest(row=f"{type(row).__name__}:{row.pk}"):
                row.refresh_from_db()
                self.assertEqual(row.manager_id, self.other.pk)

    def test_derived_rows_are_not_stamped_because_they_have_no_manager(self) -> None:
        """Only the five roots carry `manager`; derived rows inherit by traversal."""
        season = Season.objects.create(
            league=self.league_open, name="S", start_date=date(2026, 1, 1)
        )
        run("claimer@example.com")
        self.assertFalse(
            any(f.name == "manager" for f in season._meta.get_fields()),
            "Season must not carry a `manager` FK",
        )


class TestClaimUnmanagedIdempotency(ClaimUnmanagedTestBase):
    """§10 — a second run matches nothing and reports 0 for every model."""

    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Idem Team")
        self.league = League.objects.create(name="Idem League")

    def test_second_run_reports_zero_for_every_model(self) -> None:
        run("claimer@example.com")
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        for model, line in zip(EXPECTED_LINE_ORDER, lines[:5]):
            with self.subTest(model=model):
                self.assertEqual(line, f"{model}: 0 claimed")

    def test_second_run_reports_a_zero_total(self) -> None:
        run("claimer@example.com")
        lines = [ln for ln in run("claimer@example.com").splitlines() if ln.strip()]
        self.assertEqual(lines[5], "Total: 0 rows claimed by claimer@example.com")

    def test_second_run_does_not_reassign_the_rows(self) -> None:
        run("claimer@example.com")
        run("claimer@example.com")
        self.team.refresh_from_db()
        self.league.refresh_from_db()
        self.assertEqual(self.team.manager_id, self.claimer.pk)
        self.assertEqual(self.league.manager_id, self.claimer.pk)

    def test_a_different_account_cannot_steal_already_claimed_rows(self) -> None:
        run("claimer@example.com")
        run("other@example.com")
        self.team.refresh_from_db()
        self.assertEqual(self.team.manager_id, self.claimer.pk)


class TestClaimUnmanagedInvalidInput(ClaimUnmanagedTestBase):
    """§10 — failure modes."""

    def test_unknown_email_raises_command_error(self) -> None:
        Team.objects.create(name="Untouched")
        with self.assertRaises(CommandError) as ctx:
            run("nobody@example.com")
        self.assertIn("nobody@example.com", str(ctx.exception))

    def test_unknown_email_stamps_nothing(self) -> None:
        team = Team.objects.create(name="Untouched")
        with self.assertRaises(CommandError):
            run("nobody@example.com")
        team.refresh_from_db()
        self.assertIsNone(team.manager_id)

    def test_missing_user_argument_is_rejected(self) -> None:
        with self.assertRaises(CommandError):
            call_command("claim_unmanaged", stdout=StringIO())


class TestClaimUnmanagedSkipsFreeAgentsSingleton(ClaimUnmanagedTestBase):
    """Regression (code review): the shared "Free Agents" pool stays Unmanaged.

    `_generate_free_agents` deliberately never stamps the global singleton
    because it is a cross-Account shared pool -- stamping it would let the
    first Account capture it permanently and 404 it for everyone else.
    `claim_unmanaged` must honour the same rule, or it reintroduces exactly
    that failure by the back door.
    """

    def test_global_free_agents_team_is_not_claimed(self) -> None:
        free_agents = get_free_agents_team()
        self.assertIsNone(free_agents.manager_id)

        run(self.claimer.email)

        free_agents.refresh_from_db()
        self.assertIsNone(
            free_agents.manager_id,
            "the shared Free Agents singleton must stay Unmanaged",
        )

    def test_another_account_can_still_reach_the_pool_after_a_claim(self) -> None:
        free_agents = get_free_agents_team()

        run(self.claimer.email)

        free_agents.refresh_from_db()
        self.assertTrue(is_owned_by(free_agents, self.other))
        self.assertTrue(is_owned_by(free_agents, self.claimer))

    def test_the_pool_is_excluded_from_the_reported_team_count(self) -> None:
        get_free_agents_team()
        Team.objects.create(name="Ordinary Unmanaged Team")

        output = run(self.claimer.email)

        team_line = next(
            line for line in output.splitlines() if line.startswith("Team:")
        )
        self.assertEqual(team_line, "Team: 1 claimed")

    def test_a_per_league_free_agent_pool_team_IS_claimed(self) -> None:
        """Only the *global* singleton is exempt -- per-League pools are owned."""
        pool = Team.objects.create(name="Some League Free Agents")
        League.objects.create(name="Some League", free_agent_pool=pool)

        run(self.claimer.email)

        pool.refresh_from_db()
        self.assertEqual(pool.manager_id, self.claimer.pk)
