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


# ---------------------------------------------------------------------------
# TestLg01fNextSeasonSessionWrite (LG-01f — appended per seam contract §9g)
# ---------------------------------------------------------------------------


class TestLg01fNextSeasonSessionWrite(TestCase):
    """LG-01f — ``next_season`` writes
    ``request.session["last_league_id"] = league.id`` BEFORE the 302
    redirect so the session middleware commits the cookie alongside the
    redirect response.
    """

    def test_lg01f_session_writes_last_league_id_before_redirect(self) -> None:
        league = _make_league("LfNxtSess")
        teams = _make_teams("LfNS", 2)
        _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        # 302 redirect AND session has been written.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["last_league_id"], league.id)


# ---------------------------------------------------------------------------
# TestNextSeasonMapConfigCarryForward (LG-01j — appended per seam contract
# Section 8 ``next_season`` extension)
# ---------------------------------------------------------------------------


import io as _lg01j_io  # noqa: E402

from django.core.files.uploadedfile import (  # noqa: E402
    SimpleUploadedFile as _Lg01jSimpleUploadedFile,
)

from core.models import ArenaMap as _Lg01jArenaMap  # noqa: E402


def _lg01j_png() -> bytes:
    from PIL import Image as _PILImage

    buf = _lg01j_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _lg01j_make_arena_map(name: str) -> _Lg01jArenaMap:
    return _Lg01jArenaMap.objects.create(
        name=name,
        image=_Lg01jSimpleUploadedFile(
            f"{name}.png", _lg01j_png(), content_type="image/png"
        ),
        img_width=10,
        img_height=10,
    )


class TestNextSeasonMapConfigCarryForward(TestCase):
    """LG-01j — ``next_season`` carries ``map_mode`` from
    ``latest_completed`` verbatim, and rehydrates ``map_pool`` from
    ``latest_completed.starting_map_pool_ids_json`` (the SNAPSHOT, NOT
    the live M2M).
    """

    def _setup_completed_with_map_config(
        self, league_name: str, *, map_mode: str, snapshot_ids: list[int]
    ) -> tuple[League, Season, list[Team]]:
        league = _make_league(league_name)
        teams = _make_teams(f"{league_name}T", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        prev.map_mode = map_mode
        prev.starting_map_pool_ids_json = sorted(snapshot_ids)
        prev.save()
        return league, prev, teams

    def test_carry_mode_none_yields_empty_pool(self) -> None:
        league, _prev, _teams = self._setup_completed_with_map_config(
            "CarryNone", map_mode="none", snapshot_ids=[]
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.map_mode, "none")
        self.assertEqual(new_season.map_pool.count(), 0)

    def test_carry_mode_single_with_one_map(self) -> None:
        m = _lg01j_make_arena_map("CarrySingleMap")
        league, _prev, _teams = self._setup_completed_with_map_config(
            "CarrySingle", map_mode="single", snapshot_ids=[m.id]
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.map_mode, "single")
        ids = list(new_season.map_pool.values_list("id", flat=True))
        self.assertEqual(ids, [m.id])

    def test_carry_mode_random_per_round_with_three_maps(self) -> None:
        ms = [_lg01j_make_arena_map(f"CarryR{i}") for i in range(3)]
        league, _prev, _teams = self._setup_completed_with_map_config(
            "CarryRand",
            map_mode="random_per_round",
            snapshot_ids=[m.id for m in ms],
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.map_mode, "random_per_round")
        ids = sorted(new_season.map_pool.values_list("id", flat=True))
        self.assertEqual(ids, sorted([m.id for m in ms]))

    def test_carry_drops_deleted_map_silently(self) -> None:
        """Defensive: snapshot includes a deleted-after-activation id.
        The carry-forward uses ``filter(id__in=)`` so the missing id is
        silently dropped from the new Season's pool — no 400, no crash.
        """
        m1 = _lg01j_make_arena_map("CarryAlive1")
        m2 = _lg01j_make_arena_map("CarryAlive2")
        league, _prev, _teams = self._setup_completed_with_map_config(
            "CarryDrop",
            map_mode="random_per_round",
            snapshot_ids=[m1.id, m2.id, 999_999],
        )
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        new_season = league.seasons.order_by("-id").first()
        ids = sorted(new_season.map_pool.values_list("id", flat=True))
        self.assertEqual(ids, sorted([m1.id, m2.id]))
        self.assertNotIn(999_999, ids)

    def test_carry_reads_snapshot_not_live_m2m(self) -> None:
        """LG-01j locked rule: carry-forward reads from
        ``starting_map_pool_ids_json`` (snapshot), NOT from the live
        ``map_pool`` M2M.

        Pin this by mutating the previous Season's live M2M after
        activation (adding a 3rd map) while leaving the snapshot at 2
        ids — and asserting the new Season's pool equals the snapshot,
        NOT the live M2M.
        """
        m1 = _lg01j_make_arena_map("CarrySnap1")
        m2 = _lg01j_make_arena_map("CarrySnap2")
        league, prev, _teams = self._setup_completed_with_map_config(
            "CarrySnapNotLive",
            map_mode="random_per_round",
            snapshot_ids=[m1.id, m2.id],
        )
        # Live M2M had 2 maps at snapshot time; mutate it to include a
        # 3rd one (post-activation admin drift).
        m3 = _lg01j_make_arena_map("CarrySnap3")
        prev.map_pool.add(m1, m2, m3)
        prev.save()
        # Cross-check: live M2M has 3, snapshot still has 2.
        self.assertEqual(prev.map_pool.count(), 3)
        self.assertEqual(len(prev.starting_map_pool_ids_json), 2)

        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        ids = sorted(new_season.map_pool.values_list("id", flat=True))
        # New Season inherits ONLY the snapshot (2 ids), NOT m3.
        self.assertEqual(ids, sorted([m1.id, m2.id]))
        self.assertNotIn(m3.id, ids)


# ---------------------------------------------------------------------------
# LG-02-Part2a — next_season seeds one explicit round_robin SeasonPhase
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2a-seam-contract.md`` §1.5 / §4:
# ``next_season`` creates the new draft Season AND its one explicit
# ``SeasonPhase(ordinal=1, phase_type="round_robin")`` inside the same
# ``@transaction.atomic`` block. Appended as a NEW class; no existing class is
# modified.


from matches.models import SeasonPhase as _Lg02SeasonPhase  # noqa: E402


class TestLg02Part2aNextSeasonSeasonPhase(TestCase):
    """LG-02-Part2a/b — ``next_season`` carries the source's single RR phase
    forward onto the new draft.

    LG-02-Part2b superseded Part2a's "always create exactly one RR phase"
    behaviour with verbatim carry-forward of the source Season's composition
    (seam contract §5b). A real create-path Season always has >= 1 phase, so
    ``_setup`` now seeds the source completed Season with the single RR phase
    ``league_create`` would have written; ``next_season`` copies it forward.
    """

    def _setup(self) -> League:
        league = _make_league("Lg02NextL")
        teams = _make_teams("Lg02Next", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        # The source completed Season carries the single RR phase a real
        # create-path Season would have (the raw helper does not write one).
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        return league

    def test_next_season_creates_exactly_one_round_robin_phase(self) -> None:
        league = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        # The new draft Season was created.
        self.assertEqual(new_season.state, "draft")
        phases = list(new_season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].ordinal, 1)
        self.assertEqual(phases[0].phase_type, "round_robin")

    def test_next_season_phase_linked_to_new_season_only(self) -> None:
        league = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        # The new Season carries exactly one phase, pointing at itself — the
        # carry-forward copies onto the NEW Season only (the source keeps its
        # own row, so two rows exist in total).
        self.assertEqual(new_season.phases.count(), 1)
        self.assertEqual(_Lg02SeasonPhase.objects.count(), 2)
        phase = new_season.phases.get()
        self.assertEqual(phase.season_id, new_season.id)


# ---------------------------------------------------------------------------
# LG-02-Part2b — next_season copies the full phase composition forward
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2b-seam-contract.md`` §5b:
# ``next_season`` has no composer — it carries the previous Season's
# composition forward, copying ``ordinal`` / ``phase_type`` / ``schedule_format``
# verbatim from each source phase while RESETTING ``tournament=None`` on every
# new phase (always NULL in Part2b).
#
# Appended as a NEW class; no existing class is modified.


class TestLg02Part2bNextSeasonCopiesComposition(TestCase):
    """LG-02-Part2b — next_season copies a multi-row composition forward."""

    def _setup_completed_with_phases(self) -> tuple[League, Season]:
        league = _make_league("Lg02bCarryL")
        teams = _make_teams("Lg02bCarry", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        # Build a 3-row composition on the SOURCE completed Season:
        # round_robin -> tournament -> round_robin.
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=3,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        return league, prev

    def test_next_season_copies_full_composition_forward(self) -> None:
        league, _prev = self._setup_completed_with_phases()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())  # Meta.ordering=["ordinal"]
        self.assertEqual(len(new_phases), 3)
        self.assertEqual([p.ordinal for p in new_phases], [1, 2, 3])
        self.assertEqual(
            [p.phase_type for p in new_phases],
            ["round_robin", "tournament", "round_robin"],
        )

    def test_next_season_copies_schedule_format_verbatim(self) -> None:
        league, _prev = self._setup_completed_with_phases()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())
        # RR rows keep single_round_robin; the tournament row keeps None.
        self.assertEqual(new_phases[0].schedule_format, "single_round_robin")
        self.assertIsNone(new_phases[1].schedule_format)
        self.assertEqual(new_phases[2].schedule_format, "single_round_robin")

    def test_next_season_resets_tournament_fk_to_none(self) -> None:
        league, _prev = self._setup_completed_with_phases()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        for phase in new_season.phases.all():
            self.assertIsNone(
                phase.tournament_id,
                f"new phase ordinal={phase.ordinal} carried a non-null tournament FK",
            )


# ---------------------------------------------------------------------------
# LG-02-Part2c-3b — next_season carries tournament_mode forward verbatim
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3b-seam-contract.md``: the
# carry-forward loop copies ``tournament_mode`` verbatim. The load-bearing
# forward-compat guard for Part2c-3c: a non-default mode set directly on a
# source phase (the composer can't write one yet) must reproduce across seasons.
#
# Appended as a NEW class; no existing class is modified.


class TestLg02Part2c3bNextSeasonCarriesTournamentMode(TestCase):
    """LG-02-Part2c-3b — next_season copies tournament_mode forward verbatim."""

    def _setup_completed_with_mode(self, mode: str) -> League:
        league = _make_league("Lg02c3bModeL")
        teams = _make_teams("Lg02c3bMode", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        # A tournament phase carrying a NON-default mode set directly via the
        # ORM (the composer cannot write one yet — this simulates a future
        # Part2c-3c composition).
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode=mode,
        )
        return league

    def test_non_default_mode_is_carried_forward(self) -> None:
        league = self._setup_completed_with_mode("strength")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())
        self.assertEqual(new_phases[0].tournament_mode, "standings")  # RR row default
        self.assertEqual(new_phases[1].tournament_mode, "strength")  # carried verbatim

    def test_standings_mode_is_carried_forward(self) -> None:
        league = self._setup_completed_with_mode("standings")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.phases.all()[1].tournament_mode, "standings")


# ---------------------------------------------------------------------------
# LG-02-Part2c-3d — next_season carries tournament_cut + tournament_format fwd
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3d-seam-contract.md`` §5 / §9:
# the carry-forward copy loop adds BOTH ``tournament_cut=src.tournament_cut`` AND
# ``tournament_format=src.tournament_format`` (next_season copies from the
# persisted source SeasonPhase row, which has both real columns). Hand-set a
# source completed Season's tournament phase to ``tournament_cut=8`` +
# ``tournament_format`` via the ORM, run next_season, assert the new draft
# Season's copied phase reproduces BOTH verbatim.
#
# Appended as a NEW class; no existing class is modified. These WILL fail until
# the Code agent lands the carry-forward kwargs + the two new SeasonPhase
# columns — the TDD red state.


class TestLg02Part2c3dNextSeasonCarriesCutAndFormat(TestCase):
    """LG-02-Part2c-3d — next_season copies tournament_cut + tournament_format
    forward verbatim."""

    def _setup_completed_with_cut_and_format(self, *, cut: int, fmt: str) -> League:
        league = _make_league("Lg02c3dL")
        teams = _make_teams("Lg02c3d", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        # A tournament phase carrying a non-default cut + format set directly via
        # the ORM (the composer cannot write a format yet — it is dormant).
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=cut,
            tournament_format=fmt,
        )
        return league

    def test_cut_and_format_carried_forward_verbatim(self) -> None:
        league = self._setup_completed_with_cut_and_format(cut=8, fmt="swiss")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())  # Meta.ordering=["ordinal"]

        # The RR row keeps the cut/format defaults (inert there).
        self.assertEqual(new_phases[0].tournament_cut, 0)
        self.assertEqual(new_phases[0].tournament_format, "single_elimination")

        # The tournament row reproduces BOTH columns verbatim.
        self.assertEqual(new_phases[1].phase_type, "tournament")
        self.assertEqual(new_phases[1].tournament_cut, 8)
        self.assertEqual(new_phases[1].tournament_format, "swiss")

    def test_default_cut_and_format_carried_forward(self) -> None:
        # A source phase with the column defaults reproduces them too.
        league = self._setup_completed_with_cut_and_format(
            cut=0, fmt="single_elimination"
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())
        self.assertEqual(new_phases[1].tournament_cut, 0)
        self.assertEqual(new_phases[1].tournament_format, "single_elimination")


# ---------------------------------------------------------------------------
# LG-02-Part2c-3e — next_season carries all 8 new sub-config fields forward
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3e-seam-contract.md`` §7:
# ``next_season`` carries ALL 8 new fields forward verbatim —
#   tournament_format, final_series_length, semifinal_series_length,
#   quarterfinal_series_length, earlier_series_length, wb_advancers, lb_advancers,
#   swiss_rounds. Hand-set a source completed Season's tournament phase via the
#   ORM to a non-default full sub-config, run next_season, assert the new draft
#   Season's copied phase reproduces ALL 8 verbatim.
#
# Appended as a NEW class; no existing class is modified. These WILL fail until
# the Code agent lands the carry-forward kwargs + the 7 new SeasonPhase columns —
# the TDD red state.


class TestLg02Part2c3eNextSeasonCarriesSubConfig(TestCase):
    """LG-02-Part2c-3e — next_season copies all 8 new sub-config fields forward
    verbatim."""

    def _setup_completed_with_sub_config(self, **sub) -> League:
        league = _make_league("Lg02c3eL")
        teams = _make_teams("Lg02c3e", 2)
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=8,
            **sub,
        )
        return league

    def test_swiss_sub_config_carried_forward_verbatim(self) -> None:
        league = self._setup_completed_with_sub_config(
            tournament_format="swiss",
            final_series_length=1,
            semifinal_series_length=1,
            quarterfinal_series_length=1,
            earlier_series_length=1,
            wb_advancers=0,
            lb_advancers=0,
            swiss_rounds=6,
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        new_phases = list(new_season.phases.all())  # Meta.ordering=["ordinal"]

        t = new_phases[1]
        self.assertEqual(t.phase_type, "tournament")
        self.assertEqual(t.tournament_format, "swiss")
        self.assertEqual(t.swiss_rounds, 6)
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)

    def test_rr_de_sub_config_carried_forward_verbatim(self) -> None:
        league = self._setup_completed_with_sub_config(
            tournament_format="round_robin_double_elim",
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=3,
            earlier_series_length=1,
            wb_advancers=8,
            lb_advancers=4,
            swiss_rounds=0,
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        t = list(new_season.phases.all())[1]

        self.assertEqual(t.tournament_format, "round_robin_double_elim")
        self.assertEqual(t.final_series_length, 5)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 3)
        self.assertEqual(t.earlier_series_length, 1)
        self.assertEqual(t.wb_advancers, 8)
        self.assertEqual(t.lb_advancers, 4)
        self.assertEqual(t.swiss_rounds, 0)

    def test_rr_row_keeps_sub_config_defaults(self) -> None:
        league = self._setup_completed_with_sub_config(
            tournament_format="double_elimination",
            final_series_length=3,
            semifinal_series_length=3,
            quarterfinal_series_length=1,
            earlier_series_length=1,
            wb_advancers=0,
            lb_advancers=0,
            swiss_rounds=0,
        )
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        rr = list(new_season.phases.all())[0]
        # The RR row carries the column defaults (inert there).
        self.assertEqual(rr.tournament_format, "single_elimination")
        self.assertEqual(rr.final_series_length, 1)
        self.assertEqual(rr.semifinal_series_length, 1)
        self.assertEqual(rr.quarterfinal_series_length, 1)
        self.assertEqual(rr.earlier_series_length, 1)
        self.assertEqual(rr.wb_advancers, 0)
        self.assertEqual(rr.lb_advancers, 0)
        self.assertEqual(rr.swiss_rounds, 0)


# ---------------------------------------------------------------------------
# LG-04 — next_season develops the rolling League's developing set
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-04-player-development-seam-contract.md``
# §5 / §7.4: inside ``next_season``'s ``@transaction.atomic``, AFTER the
# carry-forward, the view ages + develops every Player in the developing set
# (the rolling League's snapshot Teams' players — active slots + bench — plus
# its ``free_agent_pool`` players), ticks ``total_games``, and writes one
# ``PlayerSeasonRating`` row tagged to the NEW Season. Production builds a FRESH
# ``random.Random()`` per rollover, so these integration tests assert SCHEMA-LEVEL
# outcomes (age +1, range/clamp invariants, tick bounds, one row per developed
# Player, League isolation, playoff exclusion) — NEVER exact developed stat
# values.
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the model + the develop loop + ``next_season``
# wiring — the TDD red state.


from matches.development import STAT_FIELDS as _Lg04StatFields  # noqa: E402
from matches.models import (  # noqa: E402
    GameRound as _Lg04GameRound,
    Match as _Lg04Match,
    PlayerRoundState as _Lg04PlayerRoundState,
    PlayerSeasonRating as _Lg04PlayerSeasonRating,
)
from teams.models import Player as _Lg04Player  # noqa: E402


def _lg04_developing_setup(
    league_name: str = "DevL",
    *,
    n_teams: int = 2,
    n_free_agents: int = 3,
) -> tuple[League, Season, list[Team], list]:
    """Build a League with a completed Season + snapshot Teams + a free-agent
    pool. Returns ``(league, prev_completed_season, snapshot_teams, fa_players)``.

    The snapshot Teams are pinned on ``starting_team_ids_json`` so ``next_season``
    carries them forward and the LG-04 developing-set gatherer resolves them.
    """
    league = _make_league(league_name)
    teams = _make_teams(f"{league_name}T", n_teams)
    team_ids = [t.id for t in teams]
    prev = _make_completed_season(
        league,
        name="Season 1",
        start_date=date(2025, 1, 1),
        team_ids=team_ids,
    )

    # Attach a dedicated free-agent pool Team with some players.
    pool = Team.objects.create(name=f"{league_name} Free Agents")
    league.free_agent_pool = pool
    league.save(update_fields=["free_agent_pool"])
    fa_players = [
        _Lg04Player.objects.create(team=pool, name=f"{league_name}-FA{i}", age=25)
        for i in range(n_free_agents)
    ]
    return league, prev, teams, fa_players


def _lg04_add_regular_round(
    season: Season, team_red: Team, team_blue: Team, players_with_color
) -> _Lg04GameRound:
    """Persist a regular-season Match (``match.season=season``) + one GameRound
    with a PlayerRoundState per (player, color) — one real appearance each."""
    match = _Lg04Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    game_round = _Lg04GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        is_completed=True,
    )
    for player, color in players_with_color:
        _Lg04PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role="scout",
        )
    return game_round


def _lg04_add_playoff_round(
    team_red: Team, team_blue: Team, players_with_color
) -> _Lg04GameRound:
    """Persist a PLAYOFF Match (``match.season=NULL``) + one GameRound + PRS rows.

    Playoff rounds carry ``match.season = None`` (Part2c-1 #3) so they are
    naturally EXCLUDED from the regular-season appearance count."""
    match = _Lg04Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=None, is_completed=True
    )
    game_round = _Lg04GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        is_completed=True,
    )
    for player, color in players_with_color:
        _Lg04PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role="scout",
        )
    return game_round


class TestLg04NextSeasonAgeTick(TestCase):
    """Every developing-set Player's ``age`` is incremented by exactly 1."""

    def test_snapshot_team_players_age_plus_one(self) -> None:
        league, prev, teams, _fa = _lg04_developing_setup("AgeTeamL")
        # Pin known ages on the snapshot Teams' players.
        before = {}
        for team in teams:
            for player in team.players.all():
                player.age = 22
                player.save(update_fields=["age"])
                before[player.id] = 22
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        for pid, old_age in before.items():
            player = _Lg04Player.objects.get(id=pid)
            self.assertEqual(player.age, old_age + 1, f"player {pid} age tick")

    def test_free_agent_players_age_plus_one(self) -> None:
        league, prev, _teams, fa = _lg04_developing_setup("AgeFAL")
        for p in fa:
            p.age = 30
            p.save(update_fields=["age"])
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        for p in fa:
            p.refresh_from_db()
            self.assertEqual(p.age, 31)


class TestLg04NextSeasonStatRangeInvariants(TestCase):
    """Every developing-set Player's 19 live stat fields stay within [0,100]
    after development (range / clamp invariant — NOT exact values)."""

    def test_all_developed_stats_remain_in_range(self) -> None:
        league, prev, teams, fa = _lg04_developing_setup("RangeL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        developing_ids = set()
        for team in teams:
            developing_ids |= set(team.players.values_list("id", flat=True))
        developing_ids |= {p.id for p in fa}
        for pid in developing_ids:
            player = _Lg04Player.objects.get(id=pid)
            for name in _Lg04StatFields:
                value = getattr(player, name)
                self.assertGreaterEqual(value, 0, f"{name} on {pid}")
                self.assertLessEqual(value, 100, f"{name} on {pid}")

    def test_stats_clamp_from_extreme_floor(self) -> None:
        # All-zero stats can only stay in [0,100] after a develop pass.
        league, prev, teams, fa = _lg04_developing_setup("FloorL")
        for team in teams:
            for player in team.players.all():
                for name in _Lg04StatFields:
                    setattr(player, name, 0)
                player.save()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        for team in teams:
            for player in team.players.all():
                player.refresh_from_db()
                for name in _Lg04StatFields:
                    self.assertGreaterEqual(getattr(player, name), 0, name)
                    self.assertLessEqual(getattr(player, name), 100, name)


class TestLg04NextSeasonTotalGamesTick(TestCase):
    """``total_games`` ticks: an active-Team player by their EXACT regular-season
    appearance count; a free-agent-pool player by a value in
    ``[0, median_active // 2]``."""

    def test_active_player_total_games_rises_by_appearance_count(self) -> None:
        league, prev, teams, _fa = _lg04_developing_setup("GamesActiveL")
        team_a, team_b = teams
        pa = team_a.players.first()
        # Pin a known starting total_games and 3 regular-season appearances.
        pa.total_games = 10
        pa.save(update_fields=["total_games"])
        for _ in range(3):
            _lg04_add_regular_round(prev, team_a, team_b, [(pa, "red")])
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        pa.refresh_from_db()
        self.assertEqual(pa.total_games, 10 + 3)

    def test_active_player_with_no_appearances_total_games_unchanged(self) -> None:
        league, prev, teams, _fa = _lg04_developing_setup("GamesZeroL")
        team_a, _team_b = teams
        pa = team_a.players.first()
        pa.total_games = 7
        pa.save(update_fields=["total_games"])
        # No PlayerRoundState rows ⇒ zero appearances ⇒ no tick.
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        pa.refresh_from_db()
        self.assertEqual(pa.total_games, 7)

    def test_playoff_appearances_do_not_count(self) -> None:
        # A player with ONLY playoff (season=NULL) PRS rows must NOT have those
        # counted toward the total_games tick — only regular-season rounds count.
        league, prev, teams, _fa = _lg04_developing_setup("PlayoffL")
        team_a, team_b = teams
        pa = team_a.players.first()
        pa.total_games = 5
        pa.save(update_fields=["total_games"])
        # 1 regular-season appearance + 2 playoff appearances.
        _lg04_add_regular_round(prev, team_a, team_b, [(pa, "red")])
        _lg04_add_playoff_round(team_a, team_b, [(pa, "red")])
        _lg04_add_playoff_round(team_a, team_b, [(pa, "red")])
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        pa.refresh_from_db()
        # Only the single regular-season round counts.
        self.assertEqual(pa.total_games, 5 + 1)

    def test_free_agent_total_games_rises_within_bound(self) -> None:
        league, prev, teams, fa = _lg04_developing_setup("GamesFAL", n_free_agents=3)
        team_a, team_b = teams
        # Give the active players a known appearance distribution so the median
        # is well-defined: one active player appears twice.
        pa = team_a.players.first()
        for _ in range(2):
            _lg04_add_regular_round(prev, team_a, team_b, [(pa, "red")])
        for p in fa:
            p.total_games = 0
            p.save(update_fields=["total_games"])
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        # median_active over the active set; the free-agent tick lands in
        # [0, median_active // 2]. We can't pin the exact median view-side, but
        # the tick is never negative and never exceeds the active appearance
        # max // 2 (an upper bound on median // 2).
        active_appearances = _Lg04PlayerRoundState.objects.filter(
            game_round__match__season=prev
        ).count()
        upper = max(0, active_appearances) // 2
        for p in fa:
            p.refresh_from_db()
            self.assertGreaterEqual(p.total_games, 0)
            self.assertLessEqual(p.total_games, upper)


class TestLg04NextSeasonRatingRows(TestCase):
    """Exactly one ``PlayerSeasonRating`` per developed Player tagged to the NEW
    Season, with ``age == post-tick age``, ``overall_rating == mean of developed
    stats``, ``potential is None``."""

    def test_one_developed_row_per_player_tagged_to_new_season(self) -> None:
        league, prev, teams, fa = _lg04_developing_setup("RowL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        developing_ids = set()
        for team in teams:
            developing_ids |= set(team.players.values_list("id", flat=True))
        developing_ids |= {p.id for p in fa}
        rows = _Lg04PlayerSeasonRating.objects.filter(season=new_season)
        self.assertEqual(rows.count(), len(developing_ids))
        self.assertEqual(set(rows.values_list("player_id", flat=True)), developing_ids)

    def test_developed_row_age_equals_post_tick_age(self) -> None:
        league, prev, teams, _fa = _lg04_developing_setup("RowAgeL")
        team_a = teams[0]
        pa = team_a.players.first()
        pa.age = 24
        pa.save(update_fields=["age"])
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        pa.refresh_from_db()
        row = _Lg04PlayerSeasonRating.objects.get(season=new_season, player=pa)
        # The row's age equals the post-tick (incremented) live age.
        self.assertEqual(row.age, 25)
        self.assertEqual(row.age, pa.age)

    def test_developed_row_overall_is_mean_of_developed_stats(self) -> None:
        league, prev, teams, _fa = _lg04_developing_setup("RowOvrL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        row = (
            _Lg04PlayerSeasonRating.objects.filter(season=new_season)
            .select_related("player")
            .first()
        )
        self.assertIsNotNone(row)
        # overall_rating == unweighted mean of the row's developed 19 stats.
        mean_of_row = sum(getattr(row, n) for n in _Lg04StatFields) / len(
            _Lg04StatFields
        )
        self.assertAlmostEqual(row.overall_rating, mean_of_row, places=4)
        # And the row stats equal the developed live Player stats.
        for name in _Lg04StatFields:
            self.assertEqual(getattr(row, name), getattr(row.player, name), name)

    def test_developed_row_potential_is_filled(self) -> None:
        # LG-05 supersedes the LG-04 "potential is None" contract: the developed
        # row now carries a computed potential, floored at the row's overall.
        league, prev, teams, _fa = _lg04_developing_setup("RowPotL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        rows = list(_Lg04PlayerSeasonRating.objects.filter(season=new_season))
        self.assertTrue(rows)
        for row in rows:
            self.assertIsNotNone(row.potential)
            self.assertGreaterEqual(row.potential, row.overall_rating)
            self.assertLessEqual(row.potential, 100.0)


class TestLg04NextSeasonLeagueIsolation(TestCase):
    """A Player in a DIFFERENT League is NOT developed and gets NO new-Season
    row — the developing set is league-scoped by construction (two-League
    fixture)."""

    def test_other_league_player_untouched(self) -> None:
        rolling, prev, r_teams, _r_fa = _lg04_developing_setup("RollL")
        other, _o_prev, o_teams, _o_fa = _lg04_developing_setup("OtherL")
        # Pin a known age + a known stat on an other-League Player.
        other_player = o_teams[0].players.first()
        other_player.age = 22
        other_player.accuracy = 50
        other_player.save(update_fields=["age", "accuracy"])
        before_age = other_player.age
        before_acc = other_player.accuracy

        # Roll only the rolling League forward.
        self.client.post(reverse("next_season", kwargs={"league_id": rolling.id}))

        other_player.refresh_from_db()
        # The other-League Player is untouched: no age tick, no stat change.
        self.assertEqual(other_player.age, before_age)
        self.assertEqual(other_player.accuracy, before_acc)
        # And no PlayerSeasonRating row was written for the rolling League's
        # NEW Season tagged to the other-League Player.
        new_season = rolling.seasons.order_by("-id").first()
        self.assertFalse(
            _Lg04PlayerSeasonRating.objects.filter(
                season=new_season, player=other_player
            ).exists()
        )

    def test_other_league_gets_no_new_season_row(self) -> None:
        rolling, _r_prev, _r_teams, _r_fa = _lg04_developing_setup("IsoRollL")
        other, _o_prev, _o_teams, _o_fa = _lg04_developing_setup("IsoOtherL")
        before_other_rows = _Lg04PlayerSeasonRating.objects.filter(
            season__league=other
        ).count()
        self.client.post(reverse("next_season", kwargs={"league_id": rolling.id}))
        after_other_rows = _Lg04PlayerSeasonRating.objects.filter(
            season__league=other
        ).count()
        # The other League's rating-row count is unchanged by the rolling
        # League's rollover.
        self.assertEqual(after_other_rows, before_other_rows)


# ---------------------------------------------------------------------------
# LG-05 — next_season recomputes Player.potential + fills the developed
# PlayerSeasonRating.potential row
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-05-player-potential-seam-contract.md``
# §4 / §6: at a per-League ``next_season`` rollover, every developing-set Player
# has ``Player.potential`` recomputed on its POST-development stats + the
# already-incremented age (non-None, within ``[overall, 100]``), and the
# developed ``PlayerSeasonRating`` row's ``potential`` column is filled non-None.
# The potential noise consumes a FRESH ``random.Random()`` SEPARATE from the
# develop RNG, so LG-04's seeded develop output is unperturbed.
#
# Tests assert SCHEMA-LEVEL invariants (non-None, range, independence) — NEVER
# exact unseeded potential floats. Reuses the LG-04 ``_lg04_developing_setup``
# fixture verbatim. Appended as NEW classes; no existing class is modified.
# These WILL fail until the Code agent lands ``Player.potential`` +
# ``compute_potential`` wiring in ``_develop_league_for_new_season`` — the TDD
# red state, not a defect in this file.


class TestLg05NextSeasonPotentialRecomputed(TestCase):
    """Every developed Player's ``Player.potential`` is recomputed (non-None,
    within ``[overall, 100]``) after a rollover."""

    def _developing_ids(self, teams, fa) -> set[int]:
        ids: set[int] = set()
        for team in teams:
            ids |= set(team.players.values_list("id", flat=True))
        ids |= {p.id for p in fa}
        return ids

    def test_developed_players_potential_is_non_none(self) -> None:
        league, _prev, teams, fa = _lg04_developing_setup("PotNonNoneL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        for pid in self._developing_ids(teams, fa):
            player = _Lg04Player.objects.get(id=pid)
            self.assertIsNotNone(
                player.potential, f"player {pid} potential is None after rollover"
            )

    def test_developed_players_potential_within_overall_and_100(self) -> None:
        league, _prev, teams, fa = _lg04_developing_setup("PotRangeL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        for pid in self._developing_ids(teams, fa):
            player = _Lg04Player.objects.get(id=pid)
            self.assertIsNotNone(player.potential)
            # Floor = the player's current (post-development) overall.
            self.assertGreaterEqual(
                player.potential,
                player.overall_rating - 1e-6,
                f"player {pid} potential below current overall",
            )
            self.assertLessEqual(
                player.potential, 100.0, f"player {pid} potential above 100"
            )


class TestLg05NextSeasonRatingRowPotentialFilled(TestCase):
    """The developed ``PlayerSeasonRating`` row's ``potential`` column is filled
    non-None and equals the live ``Player.potential`` (recomputed on the same
    post-development stats)."""

    def test_developed_rating_rows_potential_non_none(self) -> None:
        league, _prev, teams, fa = _lg04_developing_setup("RowPotFilledL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        rows = _Lg04PlayerSeasonRating.objects.filter(season=new_season)
        self.assertTrue(rows.exists())
        for row in rows:
            self.assertIsNotNone(
                row.potential, "developed rating-row potential must be filled"
            )

    def test_rating_row_potential_within_overall_and_100(self) -> None:
        league, _prev, teams, fa = _lg04_developing_setup("RowPotRangeL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        for row in _Lg04PlayerSeasonRating.objects.filter(season=new_season):
            self.assertIsNotNone(row.potential)
            self.assertGreaterEqual(row.potential, row.overall_rating - 1e-6)
            self.assertLessEqual(row.potential, 100.0)

    def test_rating_row_potential_matches_live_player_potential(self) -> None:
        league, _prev, teams, fa = _lg04_developing_setup("RowPotMatchL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        new_season = league.seasons.order_by("-id").first()
        for row in _Lg04PlayerSeasonRating.objects.filter(
            season=new_season
        ).select_related("player"):
            self.assertAlmostEqual(
                row.potential,
                row.player.potential,
                places=4,
                msg=f"rating-row potential drifted from live for {row.player.name!r}",
            )


class TestLg05PlayerOutsideLeagueFlowHasNonePotential(TestCase):
    """A Player never run through a league flow keeps ``potential is None``
    (the field default)."""

    def test_freshly_created_player_potential_is_none(self) -> None:
        team = Team.objects.create(name="LonePotTeam")
        player = _Lg04Player.objects.create(team=team, name="Loner", age=25)
        self.assertIsNone(player.potential)

    def test_other_league_player_keeps_none_potential(self) -> None:
        # Rolling one League forward must NOT touch a player in a DIFFERENT
        # League — their potential stays None.
        rolling, _r_prev, _r_teams, _r_fa = _lg04_developing_setup("RollPotL")
        other, _o_prev, o_teams, _o_fa = _lg04_developing_setup("OtherPotL")
        other_player = o_teams[0].players.first()
        self.assertIsNone(other_player.potential)
        self.client.post(reverse("next_season", kwargs={"league_id": rolling.id}))
        other_player.refresh_from_db()
        self.assertIsNone(
            other_player.potential, "other-League player potential must stay None"
        )


class TestLg05DevelopRngIndependentOfPotentialRng(TestCase):
    """LG-04 regression guard — the develop RNG and the potential RNG are two
    DISTINCT ``random.Random()`` instances, so adding LG-05's potential draw does
    NOT perturb LG-04's seeded 1-gauss + 19-uniform develop sequence.

    The develop loop builds a fresh (unseeded) ``random.Random()`` per rollover,
    so the develop OUTPUT is not reproducible across two rollovers; instead we
    pin that ``_develop_league_for_new_season`` constructs TWO separate
    ``random.Random`` instances (one for develop, one for potential).
    """

    def test_two_distinct_random_instances_constructed(self) -> None:
        import random as _random

        instances: list = []
        real_random_cls = _random.Random

        def _tracking_random(*args, **kwargs):
            inst = real_random_cls(*args, **kwargs)
            instances.append(inst)
            return inst

        with patch("matches.league_views.random.Random", side_effect=_tracking_random):
            league, _prev, _teams, _fa = _lg04_developing_setup("TwoRngL")
            self.client.post(reverse("next_season", kwargs={"league_id": league.id}))

        # At least two Random() instances were constructed during the develop
        # path, and they are DISTINCT objects (develop rng != potential rng).
        self.assertGreaterEqual(
            len(instances),
            2,
            "expected a separate develop rng and potential rng to be constructed",
        )
        # All constructed instances are distinct objects (no shared rng).
        self.assertEqual(
            len({id(i) for i in instances}),
            len(instances),
            "the develop rng and potential rng must be distinct objects",
        )

    def test_develop_output_stats_remain_in_range_with_potential_landed(self) -> None:
        # Behavioural sanity: with LG-05 landed, the developed 19 stats still
        # clamp to [0,100] (the LG-04 invariant is unperturbed by the separate
        # potential draw).
        league, _prev, teams, fa = _lg04_developing_setup("DevStillRangeL")
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        developing_ids: set[int] = set()
        for team in teams:
            developing_ids |= set(team.players.values_list("id", flat=True))
        developing_ids |= {p.id for p in fa}
        for pid in developing_ids:
            player = _Lg04Player.objects.get(id=pid)
            for name in _Lg04StatFields:
                value = getattr(player, name)
                self.assertGreaterEqual(value, 0, f"{name} below 0 for {pid}")
                self.assertLessEqual(value, 100, f"{name} above 100 for {pid}")


# ---------------------------------------------------------------------------
# CAR-02 — next_season is the verdict gate + rollover caller
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/car-02-performance-based-firing-seam-contract.md``
# §3.2 / §6.6: the rewritten ``next_season`` (kept ``@transaction.atomic``)
# becomes the VERDICT GATE — it ensures the OwnerEvaluation rows for the
# just-completed Season, reads the verdict, and:
#
#   * non-fired (retained / hot_seat) ⇒ runs ``_run_season_rollover`` exactly as
#     before — the new-Season output is BYTE-EQUIVALENT to the pre-CAR-02
#     rollover (same name / start / teams / schedule_format / phases) — AND the
#     OwnerEvaluation row for the just-completed Season is written.
#   * fired-AND-unreassigned (``league.current_team`` still == the row's
#     ``team_managed``) ⇒ redirected to ``new_team_picker``, NO new Season rolled.
#
# The existing rollback/atomicity guarantee still holds (the verdict gate +
# ensure-writer are inside the atomic boundary).
#
# Standings come from hand-built completed Matches (the LG-01c fixture-pattern);
# assertions are schema-level (Season name/start/teams/phase shape, redirect
# target, row presence) — NEVER simulated point totals. These WILL fail until the
# Code agent lands the OwnerEvaluation model + the verdict gate — the TDD red
# state. Appended as NEW classes; no existing class above is modified.


from matches.models import OwnerEvaluation as _Car02OwnerEvaluation  # noqa: E402


def _car02_add_win(season: Season, winner: Team, loser: Team) -> None:
    """A completed 2-0 Match the ``winner`` wins — gives the manager team a
    winning record so its (in-grace) verdict is ``retained``."""
    match = _Lg04Match.objects.create(
        team_red=winner,
        team_blue=loser,
        season=season,
        red_round1_points=100,
        blue_round1_points=1,
        red_round2_points=100,
        blue_round2_points=1,
        is_completed=True,
    )
    _Lg04GameRound.objects.create(
        match=match,
        team_red=winner,
        team_blue=loser,
        round_number=1,
        red_points=100,
        blue_points=1,
        is_completed=True,
    )
    _Lg04GameRound.objects.create(
        match=match,
        team_red=loser,
        team_blue=winner,
        round_number=2,
        red_points=1,
        blue_points=100,
        is_completed=True,
    )


class TestCar02NonFiredRolloverByteEquivalent(TestCase):
    """A non-fired (retained) Manager's ``next_season`` rollover output is
    byte-equivalent to the pre-CAR-02 rollover, AND the OwnerEvaluation row for
    the just-completed Season is written."""

    def _setup(self) -> tuple[League, Season, list[Team]]:
        teams = _make_teams("Car02NF", 2)
        manager_team = teams[0]
        league = _make_league("Car02NFL")
        league.current_team = manager_team
        league.save(update_fields=["current_team"])
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 3, 15),
            team_ids=[t.id for t in teams],
        )
        # The single RR phase a real create-path Season carries.
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        # Winning record ⇒ inside-grace verdict is retained (NOT fired).
        _car02_add_win(prev, manager_team, teams[1])
        return league, prev, teams

    def test_non_fired_creates_byte_equivalent_new_season(self) -> None:
        league, _prev, teams = self._setup()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        new_season = league.seasons.order_by("-id").first()
        # Same Season name / start / teams / schedule_format as today's rollover.
        self.assertEqual(new_season.state, "draft")
        self.assertEqual(new_season.name, "Season 2")
        self.assertEqual(new_season.start_date, date(2026, 1, 1))
        self.assertEqual(new_season.schedule_format, "single_round_robin")
        self.assertEqual(
            set(new_season.teams.values_list("id", flat=True)),
            {t.id for t in teams},
        )
        # The phase composition carried forward (one RR phase).
        phases = list(new_season.phases.all())
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].phase_type, "round_robin")

    def test_non_fired_redirects_to_new_season_dashboard(self) -> None:
        league, _prev, _teams = self._setup()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(
            response["Location"], reverse("season_dashboard", args=[new_season.id])
        )

    def test_owner_evaluation_row_written_for_just_completed_season(self) -> None:
        league, prev, _teams = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        # The verdict gate ensured the just-completed Season's eval row.
        self.assertTrue(
            _Car02OwnerEvaluation.objects.filter(league=league, season=prev).exists()
        )
        row = _Car02OwnerEvaluation.objects.get(league=league, season=prev)
        # In-grace retained (the rollover proceeded).
        self.assertEqual(row.verdict, "retained")


class TestCar02FiredUnreassignedBlocked(TestCase):
    """A fired-and-unreassigned Manager hitting ``next_season`` is redirected to
    ``new_team_picker`` and NO new Season is created."""

    def _setup(self) -> tuple[League, Season, Team]:
        teams = _make_teams("Car02Fired", 2)
        manager_team = teams[0]
        league = _make_league("Car02FiredL")
        league.current_team = manager_team
        league.save(update_fields=["current_team"])
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        # Hand-write a "fired" eval row for the just-completed Season (the
        # writer's idempotent get_or_create leaves it untouched), and leave
        # current_team == team_managed (the manager has NOT reassigned).
        _Car02OwnerEvaluation.objects.create(
            league=league,
            season=prev,
            team_managed=manager_team,
            wins_delta=-0.25,
            playoffs_delta=-0.2,
            wins_total=-1.2,
            playoffs_total=-0.2,
            verdict="fired",
            hot_seat_level=0,
        )
        return league, prev, manager_team

    def test_fired_unreassigned_redirects_to_new_team_picker(self) -> None:
        league, _prev, _team = self._setup()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("new_team_picker", kwargs={"league_id": league.id}),
        )

    def test_fired_unreassigned_creates_no_new_season(self) -> None:
        league, _prev, _team = self._setup()
        pre_count = league.seasons.count()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        self.assertEqual(league.seasons.count(), pre_count)


class TestCar02VerdictGateAtomicity(TestCase):
    """The existing rollback guarantee still holds with the verdict gate +
    ensure-writer inside the atomic boundary: a mid-flow Season.objects.create
    failure rolls back the whole view body (no new Season, no orphan rows)."""

    def test_mid_flow_create_failure_rolls_back(self) -> None:
        teams = _make_teams("Car02Atom", 2)
        manager_team = teams[0]
        league = _make_league("Car02AtomL")
        league.current_team = manager_team
        league.save(update_fields=["current_team"])
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        _car02_add_win(prev, manager_team, teams[1])

        pre_season_count = Season.objects.count()
        with patch(
            "matches.models.Season.objects.create",
            side_effect=Exception("contrived create failure"),
        ):
            with self.assertRaises(Exception):
                self.client.post(
                    reverse("next_season", kwargs={"league_id": league.id})
                )
        # No new Season row leaked past the rollback.
        self.assertEqual(Season.objects.count(), pre_season_count)


# ---------------------------------------------------------------------------
# TestCar03MultiplayerNextSeason (CAR-03 — career isolation)
# ---------------------------------------------------------------------------


class TestCar03MultiplayerNextSeason(TestCase):
    """CAR-03 — on a ``multiplayer`` League the owner-mood verdict gate is inert:
    a completed Season simply rolls into a fresh draft Season (302) with NO
    ``OwnerEvaluation`` row written and ``current_team`` unchanged."""

    def _setup(self) -> tuple[League, Season, Team]:
        teams = _make_teams("Car03MpNS", 2)
        manager_team = teams[0]
        league = _make_league("Car03MpNSL")
        # Flip to multiplayer — the representative non-career fixture.
        league.mode = "multiplayer"
        league.current_team = manager_team
        league.save(update_fields=["mode", "current_team"])
        prev = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
        )
        _Lg02SeasonPhase.objects.create(
            season=prev,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        _car02_add_win(prev, manager_team, teams[1])
        return league, prev, manager_team

    def test_post_redirects_to_new_draft_season(self) -> None:
        league, _prev, _mgr = self._setup()
        pre_count = league.seasons.count()
        response = self.client.post(
            reverse("next_season", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 302)
        # A new draft Season was created and is the redirect target.
        self.assertEqual(league.seasons.count(), pre_count + 1)
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.state, "draft")
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[new_season.id]),
        )

    def test_post_writes_no_owner_evaluation_row(self) -> None:
        league, _prev, _mgr = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        self.assertEqual(_Car02OwnerEvaluation.objects.count(), 0)

    def test_current_team_unchanged(self) -> None:
        league, _prev, manager_team = self._setup()
        self.client.post(reverse("next_season", kwargs={"league_id": league.id}))
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, manager_team.id)
