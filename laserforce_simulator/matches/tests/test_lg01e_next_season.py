"""LG-01e — Django ``TestCase`` tests for the Start Next Season endpoint
at ``POST /leagues/<int:league_id>/next-season/`` (URL name ``next_season``,
view ``matches.views.next_season``).

The seam contract is locked at
``.claude/worktrees/lg-01e-seam-contract.md`` (§1, §2, §7a, §8). The
view is POST-only, decorated ``@transaction.atomic``, and on success
creates one new ``Season`` in the same ``League`` with:

  - ``state="draft"``
  - ``name = f"Season {league.seasons.count() + 1}"`` (count taken
    BEFORE the create)
  - ``start_date = date(latest_completed.start_date.year + 1, 1, 1)``
  - ``schedule_format = latest_completed.schedule_format``
  - Teams M2M populated from the previous Season's
    ``starting_team_ids_json`` snapshot (NOT the live ``teams.all()``)

Then redirects (HTTP 302) to ``reverse("season_dashboard",
season_id=new_season.id)``.

Locked per the seam contract: NO ``mock.patch`` on
``League.objects.get`` / ``League.seasons.filter`` /
``Season.objects.create`` / ``Team.objects.filter`` /
``season.teams.add`` EXCEPT in ``TestNextSeasonAtomicity`` — every
other test exercises the real ORM end-to-end so that signature drift
between LG-01e's call sites and the ORM surfaces as a test failure
rather than a silent mock pass.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_completed_season(
    league: League,
    *,
    name: str = "Season 1",
    start_date: date = date(2025, 1, 1),
    schedule_format: str = "single_round_robin",
    team_ids: list[int] | None = None,
) -> Season:
    """Create a completed Season with a pinned snapshot.

    ``Season.start_season()`` is the production writer of
    ``starting_team_ids_json`` at activation. For test fixture setup
    we write it directly so the test can drive ``team_ids`` without
    running the activation transition.
    """
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format=schedule_format,
        state="completed",
        starting_team_ids_json=sorted(team_ids) if team_ids is not None else None,
    )
    if team_ids:
        teams = Team.objects.filter(id__in=team_ids)
        season.teams.add(*teams)
    return season


def _make_teams(prefix: str, n: int) -> list[Team]:
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    return teams


# ---------------------------------------------------------------------------
# TestNextSeasonRouting
# ---------------------------------------------------------------------------


class TestNextSeasonRouting(TestCase):
    """URL reverse, 405 on GET/PUT/DELETE, 404 on missing League."""

    def test_reverse_resolves_to_expected_path(self) -> None:
        league = _make_league("RouteRev")
        self.assertEqual(
            reverse("next_season", kwargs={"league_id": league.id}),
            f"/leagues/{league.id}/next-season/",
        )

    def test_get_returns_405(self) -> None:
        league = _make_league("RouteGet")
        response = self.client.get(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_put_returns_405(self) -> None:
        league = _make_league("RoutePut")
        response = self.client.put(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_delete_returns_405(self) -> None:
        league = _make_league("RouteDel")
        response = self.client.delete(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_post_returns_404_for_missing_league(self) -> None:
        response = self.client.post(reverse("next_season", kwargs={"league_id": 99999}))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# TestNextSeasonHappyPath
# ---------------------------------------------------------------------------


class TestNextSeasonHappyPath(TestCase):
    """POST creates a fresh ``draft`` Season + 302 redirect."""

    def _setup(self) -> tuple[League, Season, list[Team]]:
        league = _make_league("HappyL")
        teams = _make_teams("Happy", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        return league, prev, teams

    def test_post_creates_new_draft_season_with_locked_fields(self) -> None:
        league, _prev, _teams = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        # The new Season is the highest-id Season in this League.
        new_season = league.seasons.order_by("-id").first()
        self.assertIsNotNone(new_season)
        self.assertEqual(new_season.state, "draft")
        self.assertEqual(new_season.league_id, league.id)
        self.assertIsNone(new_season.champion_team)
        # starting_team_ids_json is NOT set on the new Season (snapshotted
        # at activation via start_season(), not at create — LG-01 precedent).
        self.assertIsNone(new_season.starting_team_ids_json)

    def test_post_redirects_to_new_season_dashboard(self) -> None:
        league, _prev, _teams = self._setup()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[new_season.id]),
        )
        # Path-level equality cross-check.
        self.assertEqual(response["Location"], f"/seasons/{new_season.id}/")

    def test_post_increments_season_count_by_exactly_one(self) -> None:
        league, _prev, _teams = self._setup()
        pre = league.seasons.count()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        post = league.seasons.count()
        self.assertEqual(post, pre + 1)


# ---------------------------------------------------------------------------
# TestNextSeasonNameFormat
# ---------------------------------------------------------------------------


class TestNextSeasonNameFormat(TestCase):
    """``f"Season {league.seasons.count() + 1}"`` across n=1, 2, 5."""

    def test_name_when_one_completed_exists(self) -> None:
        league = _make_league("Name1")
        teams = _make_teams("N1", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.name, "Season 2")

    def test_name_when_two_seasons_exist(self) -> None:
        league = _make_league("Name2")
        teams = _make_teams("N2", 2)
        # Two completed Seasons already.
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _make_completed_season(
            league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.name, "Season 3")

    def test_name_when_five_seasons_exist(self) -> None:
        league = _make_league("Name5")
        teams = _make_teams("N5", 2)
        for i in range(5):
            _make_completed_season(
                league,
                name=f"Season {i + 1}",
                start_date=date(2020 + i, 1, 1),
                team_ids=[t.id for t in teams],
            )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.name, "Season 6")


# ---------------------------------------------------------------------------
# TestNextSeasonStartDate
# ---------------------------------------------------------------------------


class TestNextSeasonStartDate(TestCase):
    """``date(prev.start_date.year + 1, 1, 1)`` across multiple years."""

    def test_start_date_calendar_year_jump(self) -> None:
        league = _make_league("DateMid")
        teams = _make_teams("DM", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 3, 15),
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.start_date, date(2026, 1, 1))

    def test_start_date_jan_1_when_prev_was_jan_1(self) -> None:
        league = _make_league("DateJan")
        teams = _make_teams("DJ", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.start_date, date(2026, 1, 1))

    def test_start_date_jan_1_when_prev_was_dec_31(self) -> None:
        league = _make_league("DateDec")
        teams = _make_teams("DD", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 12, 31),
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        # Locked: date(prev.year + 1, 1, 1) — NOT prev + 365 days.
        self.assertEqual(new_season.start_date, date(2026, 1, 1))

    def test_start_date_across_multiple_year_boundary(self) -> None:
        """Three sequential creates from a 2024-started Season produce
        2025, 2026, 2027 starts (each iteration seeds a fresh completed
        Season of the previous year before its create)."""
        league = _make_league("DateChain")
        teams = _make_teams("DC", 2)
        team_ids = [t.id for t in teams]

        # Seed: completed Season 1 starting 2024-01-01.
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=team_ids,
        )
        expected_years = [2025, 2026, 2027]
        for expected_year in expected_years:
            self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
            new_season = league.seasons.order_by("-id").first()
            self.assertEqual(
                new_season.start_date,
                date(expected_year, 1, 1),
                f"expected start_date year={expected_year}, "
                f"got {new_season.start_date}",
            )
            # Mark the new Season completed so the next iteration's
            # active-Season guard is not tripped — and so the
            # ``latest_completed`` lookup advances forward by one year.
            new_season.state = "completed"
            new_season.save()


# ---------------------------------------------------------------------------
# TestNextSeasonScheduleFormatCarry
# ---------------------------------------------------------------------------


class TestNextSeasonScheduleFormatCarry(TestCase):
    """``schedule_format`` carried over from latest_completed."""

    def test_schedule_format_inherited_from_previous(self) -> None:
        league = _make_league("FmtL")
        teams = _make_teams("Fmt", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            schedule_format="single_round_robin",
            team_ids=[t.id for t in teams],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.schedule_format, "single_round_robin")


# ---------------------------------------------------------------------------
# TestNextSeasonTeamsCopiedFromSnapshot
# ---------------------------------------------------------------------------


class TestNextSeasonTeamsCopiedFromSnapshot(TestCase):
    """M2M populated from ``starting_team_ids_json``, NOT live M2M."""

    def test_teams_m2m_populated_from_snapshot_json(self) -> None:
        league = _make_league("SnapL")
        teams = _make_teams("Snap", 4)
        team_ids = [t.id for t in teams]
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=team_ids,
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_team_ids = set(new_season.teams.values_list("id", flat=True))
        self.assertEqual(new_team_ids, set(team_ids))
        self.assertEqual(new_season.teams.count(), 4)

    def test_teams_copy_uses_snapshot_not_live_m2m(self) -> None:
        """Pin the snapshot-as-source-of-truth rule against future
        refactors. Snapshot has 4 team ids; live M2M is artificially
        mutated to include a 5th. The new Season's teams must equal the
        snapshot, NOT the live M2M.
        """
        league = _make_league("LiveL")
        teams = _make_teams("Live", 4)
        snapshot_ids = [t.id for t in teams]
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=snapshot_ids,
        )
        # Add a 5th team to the live M2M only — snapshot remains 4 ids.
        extra_team, _ = make_team_with_slots("LiveExtra")
        prev.teams.add(extra_team)
        # Sanity: live M2M now has 5; snapshot still has 4.
        self.assertEqual(prev.teams.count(), 5)
        self.assertEqual(len(prev.starting_team_ids_json), 4)

        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_team_ids = set(new_season.teams.values_list("id", flat=True))
        self.assertEqual(new_team_ids, set(snapshot_ids))
        self.assertNotIn(extra_team.id, new_team_ids)

    def test_missing_team_in_snapshot_skipped_silently(self) -> None:
        """Snapshot ``[t1, t2, 999]`` where Team 999 does not exist ⇒
        new Season's teams.all() is exactly {t1, t2}. No error, no log,
        no 400 — silently dropped by the ``filter(id__in=…)`` IN clause.
        """
        league = _make_league("MissL")
        teams = _make_teams("Miss", 2)
        existing_ids = [t.id for t in teams]
        # Build snapshot with a guaranteed-missing id.
        snapshot_ids = existing_ids + [999_999]
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=existing_ids,  # live M2M has just the 2 real teams
        )
        # Manually override the snapshot to include the missing id.
        prev = league.seasons.get()
        prev.starting_team_ids_json = snapshot_ids
        prev.save()

        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        # No error path — must redirect (302), not 400/500.
        self.assertEqual(response.status_code, 302)
        new_season = league.seasons.order_by("-id").first()
        new_team_ids = set(new_season.teams.values_list("id", flat=True))
        self.assertEqual(new_team_ids, set(existing_ids))
        self.assertNotIn(999_999, new_team_ids)


# ---------------------------------------------------------------------------
# TestNextSeasonActiveSeasonGuard
# ---------------------------------------------------------------------------


class TestNextSeasonActiveSeasonGuard(TestCase):
    """Draft/active Season ⇒ 302 to that Season's dashboard; no new Season."""

    def test_post_redirects_when_active_season_exists(self) -> None:
        league = _make_league("ActGuard")
        teams = _make_teams("AG", 2)
        # Completed prev Season + currently-active Season.
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[t.id for t in teams],
        )
        active = Season.objects.create(
            league=league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            state="active",
        )
        pre_count = league.seasons.count()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[active.id]),
        )
        # No new Season was created.
        self.assertEqual(league.seasons.count(), pre_count)

    def test_post_redirects_when_draft_season_exists(self) -> None:
        league = _make_league("DraftGuard")
        teams = _make_teams("DG", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[t.id for t in teams],
        )
        draft = Season.objects.create(
            league=league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            state="draft",
        )
        pre_count = league.seasons.count()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[draft.id]),
        )
        self.assertEqual(league.seasons.count(), pre_count)

    def test_active_season_guard_does_not_create_new_season(self) -> None:
        """Cross-check: Season.objects.count() is unchanged across the
        blocked POST (defends against a future refactor accidentally
        creating a Season after the guard fires).
        """
        league = _make_league("GuardCount")
        teams = _make_teams("GC", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2024, 1, 1),
            team_ids=[t.id for t in teams],
        )
        Season.objects.create(
            league=league,
            name="Season 2",
            start_date=date(2025, 1, 1),
            state="active",
        )
        pre_total = Season.objects.count()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        self.assertEqual(Season.objects.count(), pre_total)


# ---------------------------------------------------------------------------
# TestNextSeasonNoCompletedGuard
# ---------------------------------------------------------------------------


class TestNextSeasonNoCompletedGuard(TestCase):
    """Zero completed Seasons ⇒ 400 + body substring; count unchanged."""

    def test_post_returns_400_when_no_completed_season_exists(self) -> None:
        # League with zero Seasons total — the active-Season guard does
        # NOT fire (no non-completed Season exists either), so the
        # no-completed-Season guard is the one that trips.
        league = _make_league("NoCompL")
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"No completed Season in this League.", response.content)

    def test_post_returns_400_does_not_create_season(self) -> None:
        league = _make_league("NoCompCount")
        pre_total = Season.objects.count()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        # No Season was created in the 400 branch.
        self.assertEqual(Season.objects.count(), pre_total)
        self.assertEqual(league.seasons.count(), 0)


# ---------------------------------------------------------------------------
# TestNextSeasonAtomicity
# ---------------------------------------------------------------------------


class TestNextSeasonAtomicity(TestCase):
    """Mid-flow ``Season.objects.create`` failure rolls back the entire
    view body (no orphan Season row, no orphan M2M rows).

    This is the ONE allowed ``mock.patch`` per the seam contract — every
    other test exercises the real ORM end-to-end so signature drift
    surfaces as a test failure rather than a silent mock pass.
    """

    def test_mid_flow_create_failure_rolls_back_m2m(self) -> None:
        league = _make_league("AtomicL")
        teams = _make_teams("Atom", 4)
        team_ids = [t.id for t in teams]
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=team_ids,
        )
        pre_season_count = Season.objects.count()
        pre_team_count = Team.objects.count()
        # Snapshot the M2M-through table row counts on the previous
        # Season so we can assert no extra rows were added to it.
        prev = league.seasons.get(name="Season 1")
        pre_prev_m2m_count = prev.teams.count()

        # Patch the model-side path (LG-01b precedent). The @transaction.atomic
        # boundary on the view must roll the whole view body back.
        with patch(
            "matches.models.Season.objects.create",
            side_effect=Exception("contrived create failure"),
        ):
            with self.assertRaises(Exception):
                self.client.post(
                    reverse("next_season", kwargs={"league_id": league.id})
                )

        # No new Season row.
        self.assertEqual(Season.objects.count(), pre_season_count)
        # No Team rows added / removed.
        self.assertEqual(Team.objects.count(), pre_team_count)
        # Previous Season's M2M is untouched (no orphan rows leaked into
        # the through table from a failed mid-flow add — defensive).
        prev.refresh_from_db()
        self.assertEqual(prev.teams.count(), pre_prev_m2m_count)
