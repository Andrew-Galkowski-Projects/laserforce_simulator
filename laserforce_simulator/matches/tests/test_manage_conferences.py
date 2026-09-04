"""CONF-05 — tests for the draft-Season Manage Conferences composer.

Covers the pure partition validator (`_validate_conference_partition`), the
`manage_conferences` view (GET draft composer / GET active read-only / POST
create-replace-clear / validation errors / draft-only + method guards /
session write), and the draft-only dashboard entry link. Builds on the CONF-01
`Conference` model; no model/migration. Schema-level assertions only.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.league_views import _validate_conference_partition
from matches.models import Conference, League, Season
from matches.tests.conftest import make_team_with_slots


def _draft_season_with_teams(prefix: str, n: int):
    """A draft Season enrolling ``n`` fully-slotted Teams (no Conferences)."""
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        team, _ = make_team_with_slots(f"{prefix}t{i}")
        season.teams.add(team)
        teams.append(team)
    return season, teams


# ---------------------------------------------------------------------------
# Pure validator
# ---------------------------------------------------------------------------


class TestValidateConferencePartition(TestCase):
    def test_empty_submission_is_zero_conference(self):
        errors, normalized = _validate_conference_partition([], {}, {1, 2, 3, 4})
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [])

    def test_blank_only_names_is_zero_conference(self):
        # No conference names at all (the composer submits none) ⇒ flat Season.
        errors, normalized = _validate_conference_partition(
            [], {1: None, 2: None}, {1, 2}
        )
        self.assertEqual((errors, normalized), ([], []))

    def test_valid_full_partition(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("West", [1, 2]), ("East", [3, 4])])

    def test_single_conference_with_all_teams_is_valid(self):
        errors, normalized = _validate_conference_partition(
            ["Only"], {1: 0, 2: 0, 3: 0}, {1, 2, 3}
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("Only", [1, 2, 3])])

    def test_unassigned_team_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: None}, {1, 2, 3, 4}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)

    def test_conference_under_two_teams_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: 0}, {1, 2, 3, 4}
        )
        self.assertIn("Each conference needs at least 2 teams.", errors)
        self.assertIsNone(normalized)

    def test_empty_name_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "  "], {1: 0, 2: 0, 3: 1, 4: 1}, {1, 2, 3, 4}
        )
        self.assertIn("Conference names cannot be empty.", errors)
        self.assertIsNone(normalized)

    def test_out_of_range_index_counts_as_unassigned(self):
        errors, normalized = _validate_conference_partition(
            ["West"], {1: 0, 2: 0, 3: 5}, {1, 2, 3}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


class TestManageConferencesView(TestCase):
    def _url(self, season):
        return reverse("manage_conferences", args=[season.id])

    def test_get_draft_renders_composer(self):
        season, teams = _draft_season_with_teams("get", 4)
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "seasons/manage_conferences.html")
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-form"', html)
        self.assertIn('id="manage-conferences-add"', html)
        self.assertIn('id="manage-conferences-submit"', html)
        for team in teams:
            self.assertIn(f'id="manage-conferences-team-{team.id}"', html)

    def test_get_writes_last_league_id(self):
        season, _ = _draft_season_with_teams("sess", 2)
        self.client.get(self._url(season))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_missing_season_404(self):
        resp = self.client.get(reverse("manage_conferences", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_disallowed_method_405(self):
        season, _ = _draft_season_with_teams("meth", 2)
        resp = self.client.generic("DELETE", self._url(season))
        self.assertEqual(resp.status_code, 405)

    def test_post_valid_creates_partition(self):
        season, teams = _draft_season_with_teams("post", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "1",
            f"team_{teams[3].id}_conference": "1",
        }
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual([c.name for c in confs], ["West", "East"])
        self.assertEqual([c.ordinal for c in confs], [1, 2])
        self.assertEqual(
            set(confs[0].teams.values_list("id", flat=True)),
            {teams[0].id, teams[1].id},
        )
        self.assertEqual(
            set(confs[1].teams.values_list("id", flat=True)),
            {teams[2].id, teams[3].id},
        )

    def test_post_replaces_existing_conferences(self):
        season, teams = _draft_season_with_teams("replace", 4)
        stale = Conference.objects.create(season=season, name="Stale", ordinal=1)
        stale.teams.set([teams[0].id, teams[1].id])
        data = {
            "conference_name": ["North", "South"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "1",
            f"team_{teams[2].id}_conference": "0",
            f"team_{teams[3].id}_conference": "1",
        }
        self.client.post(self._url(season), data)
        self.assertFalse(season.conferences.filter(name="Stale").exists())
        self.assertEqual(
            list(season.conferences.order_by("ordinal").values_list("name", flat=True)),
            ["North", "South"],
        )

    def test_post_empty_clears_conferences(self):
        season, teams = _draft_season_with_teams("clear", 4)
        conf = Conference.objects.create(season=season, name="Gone", ordinal=1)
        conf.teams.set([t.id for t in teams])
        resp = self.client.post(self._url(season), {})
        self.assertRedirects(resp, self._url(season))
        self.assertEqual(season.conferences.count(), 0)

    def test_post_unassigned_team_re_renders_with_error_no_write(self):
        season, teams = _draft_season_with_teams("err", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "1",
            # teams[3] unassigned
        }
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="manage-conferences-errors"', resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_post_under_two_per_conference_error(self):
        season, teams = _draft_season_with_teams("small", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "0",
            f"team_{teams[3].id}_conference": "1",  # East has only 1
        }
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Each conference needs at least 2 teams.", resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_post_on_active_season_rejected(self):
        season, _ = _draft_season_with_teams("active", 4)
        season.start_season()
        resp = self.client.post(self._url(season), {"conference_name": ["X", "Y"]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(season.conferences.count(), 0)

    def test_get_active_season_is_read_only(self):
        season, teams = _draft_season_with_teams("ro", 4)
        conf = Conference.objects.create(season=season, name="West", ordinal=1)
        conf.teams.set([teams[0].id, teams[1].id])
        conf2 = Conference.objects.create(season=season, name="East", ordinal=2)
        conf2.teams.set([teams[2].id, teams[3].id])
        season.start_season()
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-readonly"', html)
        self.assertNotIn('id="manage-conferences-form"', html)
        self.assertIn("West", html)
        self.assertIn("East", html)


# ---------------------------------------------------------------------------
# Dashboard entry link
# ---------------------------------------------------------------------------


class TestManageConferencesDashboardLink(TestCase):
    def test_draft_dashboard_shows_link(self):
        season, _ = _draft_season_with_teams("dlink", 2)
        resp = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'id="season-dashboard-manage-conferences-link"', resp.content.decode()
        )

    def test_active_dashboard_hides_link(self):
        season, _ = _draft_season_with_teams("alink", 2)
        season.start_season()
        resp = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(
            'id="season-dashboard-manage-conferences-link"', resp.content.decode()
        )


class TestManageConferencesLeagueDashboardLink(TestCase):
    """CONF-05 — the draft-only Manage Conferences link also renders on the
    LEAGUE dashboard (/leagues/<id>/), where the create flow lands."""

    def test_draft_league_dashboard_shows_link(self):
        season, _ = _draft_season_with_teams("ldlink", 2)
        resp = self.client.get(reverse("league_dashboard", args=[season.league_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'id="league-dashboard-manage-conferences-link"', resp.content.decode()
        )

    def test_active_league_dashboard_hides_link(self):
        season, _ = _draft_season_with_teams("lalink", 2)
        season.start_season()
        resp = self.client.get(reverse("league_dashboard", args=[season.league_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(
            'id="league-dashboard-manage-conferences-link"', resp.content.decode()
        )


class TestConf02PartitionBracketFloor(TestCase):
    """CONF-02 review fix - ``min_per_conference`` raises the floor to 4 when
    the Season composes a tournament phase (each Conference must field its own
    Regional playoff bracket)."""

    def test_two_per_conference_rejected_when_floor_is_four(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
            min_per_conference=4,
        )
        self.assertIsNone(normalized)
        self.assertTrue(any("at least 4 teams" in e for e in errors))

    def test_two_per_conference_allowed_at_the_default_floor(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(normalized)

    def test_four_per_conference_allowed_when_floor_is_four(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1},
            set(range(1, 9)),
            min_per_conference=4,
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(normalized)


# ---------------------------------------------------------------------------
# CONF-06 — per-Conference rotation composer on Manage Conferences
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/conf-06-seam-contract.md`` SS6 / SS7:
#   - GET (draft) renders one hidden ``conference_rotation`` input per
#     Conference row (``manage-conferences-rotation-{i}``) plus the
#     ``manage-conferences-confirmed-maps`` JSON script block.
#   - POST reads ``request.POST.getlist("conference_rotation")``, index-aligned
#     with ``conference_name``, parses each row with ``parse_rotation_ids`` and
#     stores it VERBATIM (submitted order) on ``map_rotation_ids_json``.
#   - A malformed id is a page-level error for EVERY map mode; the
#     ``Each conference needs at least 1 rotation map.`` guard is appended ONCE
#     and ONLY under ``map_mode == "rotate_by_conference"``.
#   - Non-draft GET renders ``manage-conferences-readonly-rotation-{i}`` and no
#     editable rotation input; non-draft POST still 400s.
#
# WILL fail until the Code agent lands the view + template edits.

import io as _conf06_io

from django.core.files.uploadedfile import (
    SimpleUploadedFile as _Conf06SimpleUploadedFile,
)

from core.models import (
    ArenaMap as _Conf06ArenaMap,
    MapZoneConfig as _Conf06MapZoneConfig,
)


def _conf06_png_bytes() -> bytes:
    from PIL import Image as _PILImage

    buf = _conf06_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(0, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _conf06_confirmed_map(name: str) -> _Conf06ArenaMap:
    """An ``ArenaMap`` with a confirmed ``MapZoneConfig`` so it surfaces in
    ``_maps_with_confirmed_config()`` (the rotation picker's queryset)."""
    arena_map = _Conf06ArenaMap.objects.create(
        name=name,
        image=_Conf06SimpleUploadedFile(
            f"{name}.png", _conf06_png_bytes(), content_type="image/png"
        ),
        img_width=10,
        img_height=10,
    )
    _Conf06MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        zone_data={"zones": [[1, 1], [1, 1]]},
        confirmed=True,
    )
    return arena_map


def _conf06_wire(ids: list[int]) -> str:
    """The hidden input's wire format — comma-joined ids in author order."""
    return ",".join(str(i) for i in ids)


class TestConf06ManageConferencesRotation(TestCase):
    """The rotation composer's GET surface, POST persistence and error paths."""

    def _url(self, season):
        return reverse("manage_conferences", args=[season.id])

    def _season_with_two_conferences(self, prefix: str):
        """A draft Season, 4 teams, 2 Conferences already partitioned."""
        season, teams = _draft_season_with_teams(prefix, 4)
        west = Conference.objects.create(season=season, name="West", ordinal=1)
        west.teams.set([teams[0].id, teams[1].id])
        east = Conference.objects.create(season=season, name="East", ordinal=2)
        east.teams.set([teams[2].id, teams[3].id])
        return season, teams, [west, east]

    def _partition_post(self, teams, names, rotations):
        data = {
            "conference_name": names,
            "conference_rotation": rotations,
        }
        half = len(teams) // 2
        for i, team in enumerate(teams):
            data[f"team_{team.id}_conference"] = "0" if i < half else "1"
        return data

    # ---- GET (draft) ----

    def test_get_draft_renders_rotation_input_per_conference(self):
        season, _teams, _confs = self._season_with_two_conferences("c6get")
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-rotation-0"', html)
        self.assertIn('id="manage-conferences-rotation-1"', html)
        self.assertIn('name="conference_rotation"', html)

    def test_get_draft_renders_confirmed_maps_json_block(self):
        season, _teams, _confs = self._season_with_two_conferences("c6maps")
        m = _conf06_confirmed_map("C6Pick")
        resp = self.client.get(self._url(season))
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-confirmed-maps"', html)
        self.assertIn(str(m.id), html)
        self.assertIn("C6Pick", html)

    def test_get_draft_prefills_existing_rotation_in_author_order(self):
        season, _teams, confs = self._season_with_two_conferences("c6fill")
        m_a = _conf06_confirmed_map("C6FillA")
        m_b = _conf06_confirmed_map("C6FillB")
        # NON-ascending author order — a stray sort would change the value.
        confs[0].map_rotation_ids_json = [m_b.id, m_a.id]
        confs[0].save(update_fields=["map_rotation_ids_json"])
        resp = self.client.get(self._url(season))
        html = resp.content.decode()
        self.assertIn(f'value="{m_b.id},{m_a.id}"', html)

    # ---- POST persistence ----

    def test_post_saves_rotation_per_conference_in_submitted_order(self):
        season, teams = _draft_season_with_teams("c6post", 4)
        m_a = _conf06_confirmed_map("C6PostA")
        m_b = _conf06_confirmed_map("C6PostB")
        m_c = _conf06_confirmed_map("C6PostC")
        data = self._partition_post(
            teams,
            ["West", "East"],
            [_conf06_wire([m_c.id, m_a.id]), _conf06_wire([m_b.id])],
        )
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual([c.name for c in confs], ["West", "East"])
        # Index i of conference_rotation aligns with index i of conference_name.
        self.assertEqual(confs[0].map_rotation_ids_json, [m_c.id, m_a.id])
        self.assertEqual(confs[1].map_rotation_ids_json, [m_b.id])

    def test_post_saves_empty_list_for_an_unauthored_rotation(self):
        """``[]`` (authored-empty), never ``None``, once the page has saved."""
        season, teams = _draft_season_with_teams("c6empty", 4)
        data = self._partition_post(teams, ["West", "East"], ["", ""])
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        for conf in season.conferences.order_by("ordinal"):
            self.assertEqual(conf.map_rotation_ids_json, [])

    def test_post_keeps_duplicate_ids_in_a_rotation(self):
        season, teams = _draft_season_with_teams("c6dup", 4)
        m = _conf06_confirmed_map("C6Dup")
        data = self._partition_post(
            teams, ["West", "East"], [_conf06_wire([m.id, m.id]), _conf06_wire([m.id])]
        )
        self.client.post(self._url(season), data)
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual(confs[0].map_rotation_ids_json, [m.id, m.id])

    def test_post_under_rotate_by_conference_saves(self):
        season, teams = _draft_season_with_teams("c6mode", 4)
        season.map_mode = "rotate_by_conference"
        season.save(update_fields=["map_mode"])
        m_a = _conf06_confirmed_map("C6ModeA")
        m_b = _conf06_confirmed_map("C6ModeB")
        data = self._partition_post(
            teams, ["West", "East"], [_conf06_wire([m_a.id]), _conf06_wire([m_b.id])]
        )
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual(confs[0].map_rotation_ids_json, [m_a.id])
        self.assertEqual(confs[1].map_rotation_ids_json, [m_b.id])

    # ---- POST errors ----

    def test_post_empty_rotation_under_the_mode_re_renders_with_error(self):
        season, teams = _draft_season_with_teams("c6guard", 4)
        season.map_mode = "rotate_by_conference"
        season.save(update_fields=["map_mode"])
        m = _conf06_confirmed_map("C6Guard")
        data = self._partition_post(teams, ["West", "East"], [_conf06_wire([m.id]), ""])
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Each conference needs at least 1 rotation map.", html)
        self.assertIn('id="manage-conferences-errors"', html)
        self.assertEqual(season.conferences.count(), 0)

    def test_empty_rotation_guard_appended_once_not_per_conference(self):
        season, teams = _draft_season_with_teams("c6once", 4)
        season.map_mode = "rotate_by_conference"
        season.save(update_fields=["map_mode"])
        data = self._partition_post(teams, ["West", "East"], ["", ""])
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(
            html.count("Each conference needs at least 1 rotation map."), 1
        )

    def test_empty_rotation_guard_silent_under_other_modes(self):
        """The guard is mode-scoped — an empty rotation is fine for a
        ``none`` Season and the partition still saves."""
        season, teams = _draft_season_with_teams("c6silent", 4)
        self.assertEqual(season.map_mode, "none")
        data = self._partition_post(teams, ["West", "East"], ["", ""])
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        self.assertEqual(season.conferences.count(), 2)

    def test_post_with_a_non_numeric_token_re_renders_with_parse_error(self):
        season, teams = _draft_season_with_teams("c6bad", 4)
        m = _conf06_confirmed_map("C6Bad")
        data = self._partition_post(teams, ["West", "East"], ["abc", str(m.id)])
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Map rotation contains an invalid id.", resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_post_with_an_unknown_map_id_re_renders_with_parse_error(self):
        season, teams = _draft_season_with_teams("c6unknown", 4)
        m = _conf06_confirmed_map("C6Unknown")
        data = self._partition_post(teams, ["West", "East"], ["999999", str(m.id)])
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Map rotation contains an unknown map id.", resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_parse_errors_surface_under_every_map_mode(self):
        """A malformed id is invalid regardless of ``season.map_mode``."""
        season, teams = _draft_season_with_teams("c6badany", 4)
        self.assertEqual(season.map_mode, "none")
        data = self._partition_post(teams, ["West", "East"], ["abc", ""])
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Map rotation contains an invalid id.", resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_failed_post_re_renders_the_submitted_rotation(self):
        """A failed POST must not make the author re-pick their maps."""
        season, teams = _draft_season_with_teams("c6resub", 4)
        m_a = _conf06_confirmed_map("C6ResubA")
        m_b = _conf06_confirmed_map("C6ResubB")
        data = self._partition_post(
            teams, ["West", "East"], [_conf06_wire([m_b.id, m_a.id]), "abc"]
        )
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # The comma-joined pair is distinctive enough to pin the round-trip.
        self.assertIn(f'value="{m_b.id},{m_a.id}"', html)

    # ---- Non-draft (read-only) ----

    def test_non_draft_get_renders_readonly_rotation_names(self):
        season, _teams, confs = self._season_with_two_conferences("c6ro")
        m_a = _conf06_confirmed_map("C6RoAlpha")
        m_b = _conf06_confirmed_map("C6RoBeta")
        confs[0].map_rotation_ids_json = [m_b.id, m_a.id]
        confs[0].save(update_fields=["map_rotation_ids_json"])
        confs[1].map_rotation_ids_json = [m_a.id]
        confs[1].save(update_fields=["map_rotation_ids_json"])
        season.start_season()
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-readonly-rotation-0"', html)
        self.assertIn('id="manage-conferences-readonly-rotation-1"', html)
        self.assertIn("C6RoAlpha", html)
        self.assertIn("C6RoBeta", html)
        # Author order survives into the read-only list.
        self.assertLess(html.index("C6RoBeta"), html.index("C6RoAlpha"))

    def test_non_draft_get_has_no_editable_rotation_input(self):
        season, _teams, confs = self._season_with_two_conferences("c6noedit")
        m = _conf06_confirmed_map("C6NoEdit")
        for conf in confs:
            conf.map_rotation_ids_json = [m.id]
            conf.save(update_fields=["map_rotation_ids_json"])
        season.start_season()
        html = self.client.get(self._url(season)).content.decode()
        self.assertNotIn('id="manage-conferences-rotation-0"', html)
        self.assertNotIn('name="conference_rotation"', html)

    def test_non_draft_post_still_400s(self):
        season, teams, confs = self._season_with_two_conferences("c6post400")
        m = _conf06_confirmed_map("C6Post400")
        for conf in confs:
            conf.map_rotation_ids_json = [m.id]
            conf.save(update_fields=["map_rotation_ids_json"])
        season.start_season()
        resp = self.client.post(
            self._url(season),
            {
                "conference_name": ["X", "Y"],
                "conference_rotation": [str(m.id), str(m.id)],
            },
        )
        self.assertEqual(resp.status_code, 400)
        for conf in confs:
            conf.refresh_from_db()
            self.assertEqual(conf.map_rotation_ids_json, [m.id])

    def test_readonly_rotation_drops_a_deleted_map_silently(self):
        season, _teams, confs = self._season_with_two_conferences("c6rodel")
        m = _conf06_confirmed_map("C6RoDel")
        for conf in confs:
            conf.map_rotation_ids_json = [m.id, 999999]
            conf.save(update_fields=["map_rotation_ids_json"])
        season.start_season()
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("C6RoDel", resp.content.decode())
