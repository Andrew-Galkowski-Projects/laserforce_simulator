"""View/template rendering regression tests for the teams app."""

import re

from django.test import TestCase
from django.urls import reverse

from teams.models import Player, Team
from teams.player_generator import _STAT_FIELDS


class TeamDetailRosterStatusTest(TestCase):
    """Regression: T-3 — Roster Status Scout row must read '2 slots', not
    a leftover active-roster count concatenated before it ('6 2 slots')."""

    def setUp(self):
        self.team = Team.objects.create(name="Roster Status Team")
        # Three players assigned to non-Scout slots so active_roster has a
        # distinctive, non-zero length that would leak into the Scout badge
        # if the dead placeholder code were still present.
        cmd = Player.objects.create(team=self.team, name="Cmd")
        hvy = Player.objects.create(team=self.team, name="Hvy")
        med = Player.objects.create(team=self.team, name="Med")
        self.team.slot_commander = cmd
        self.team.slot_heavy = hvy
        self.team.slot_medic = med
        self.team.save()

    def _scout_badge_text(self):
        url = reverse("team_detail", kwargs={"team_id": self.team.id})
        html = self.client.get(url).content.decode()
        # The Roster Status card pairs a role label span with a badge span.
        m = re.search(
            r"<span>\s*Scout\s*</span>\s*" r'<span class="badge[^"]*">(.*?)</span>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "Scout row not found in Roster Status card")
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_scout_row_reads_exactly_two_slots(self):
        self.assertEqual(self._scout_badge_text(), "2 slots")

    def test_no_placeholder_artifact_in_response(self):
        url = reverse("team_detail", kwargs={"team_id": self.team.id})
        self.assertNotContains(self.client.get(url), "<!-- placeholder -->")


class NavbarMobileTogglerTest(TestCase):
    """Regression: T-4 — base.html uses navbar-expand-lg but had no
    hamburger toggler / collapse wrapper, leaving the nav unusable < 992px."""

    def test_navbar_has_toggler_and_collapse_wrapper(self):
        html = self.client.get(reverse("team_list")).content.decode()
        self.assertIn('class="navbar-toggler"', html)
        self.assertIn("navbar-toggler-icon", html)
        # The toggler must control a collapsible nav container by id.
        m = re.search(r'data-bs-target="#([\w-]+)"', html)
        self.assertIsNotNone(m, "navbar-toggler has no data-bs-target")
        target_id = m.group(1)
        self.assertRegex(
            html,
            r'class="collapse navbar-collapse"[^>]*id="%s"' % re.escape(target_id),
        )


class TeamListPlayerCountLabelTest(TestCase):
    """Regression: T-2 — teams list showed 'player_count/6', so a valid
    6-active + 1-bench roster read as a misleading over-capacity '7/6'.
    It must show active/6 plus an explicit bench count instead."""

    def test_valid_roster_with_bench_is_not_over_capacity(self):
        team = Team.objects.create(name="Bench Team")
        slots = ("commander", "heavy", "scout_1", "scout_2", "medic", "ammo")
        for i, slot in enumerate(slots):
            p = Player.objects.create(team=team, name=f"P{i}")
            setattr(team, f"slot_{slot}", p)
        team.save()
        Player.objects.create(team=team, name="Benched")  # 7th, on bench

        html = self.client.get(reverse("team_list")).content.decode()
        self.assertIn("6/6 active", html)
        self.assertIn("(+1 bench)", html)
        # The misleading mixed-total label must be gone.
        self.assertNotIn("7/6", html)

    def test_incomplete_roster_shows_active_over_six(self):
        team = Team.objects.create(name="Incomplete Team")
        p = Player.objects.create(team=team, name="Solo")
        team.slot_commander = p
        team.save()

        html = self.client.get(reverse("team_list")).content.decode()
        self.assertIn("1/6 active", html)
        self.assertNotIn("(+", html)  # no bench players → no bench suffix


class PlayerDetailStatGroupingTest(TestCase):
    """Regression: PD-1 — player detail stat groups must match the five
    documented categories in teams/CLAUDE.md (Awareness / Decision-making
    / Physical / Team / Role), with every one of the 19 stats appearing
    exactly once."""

    DOCUMENTED = {
        "Awareness": {
            "Player Awareness",
            "Game Awareness",
            "Resource Awareness",
        },
        "Decision-making": {"Decision Making"},
        "Physical": {
            "Positioning",
            "Stamina",
            "Speed",
            "Flexibility",
            "Adaptability",
        },
        "Team": {"Communication", "Teamwork"},
        "Role": {
            "Offensive Synergy",
            "Defensive Synergy",
            "Midfield Synergy",
            "Resupply Synergy",
            "Resupply Efficiency",
            "Accuracy",
            "Survival",
            "Special Usage",
        },
    }

    def _groups(self):
        team = Team.objects.create(name="PD1 Team")
        p = Player.objects.create(team=team, name="Stat Player")
        url = reverse("player_detail", kwargs={"team_id": team.id, "player_id": p.id})
        return self.client.get(url).context["stat_groups"]

    def test_groups_match_documented_categories(self):
        got = {name: {label for label, _ in rows} for name, rows in self._groups()}
        self.assertEqual(got, self.DOCUMENTED)

    def test_every_stat_appears_exactly_once(self):
        labels = [label for _, rows in self._groups() for label, _ in rows]
        self.assertEqual(len(labels), 19)
        self.assertEqual(len(set(labels)), 19, "a stat is duplicated/missing")


class PlayerEditA11yLabelTest(TestCase):
    """Regression: PD-2 — the preferred-roles group rendered a bare
    <label class="form-label"> with no 'for' and no wrapped control,
    tripping DevTools 'No label associated with a form field'. The group
    must use fieldset/legend semantics instead."""

    def _edit_html(self):
        team = Team.objects.create(name="A11y Team")
        p = Player.objects.create(team=team, name="A11y Player")
        url = reverse("player_edit", kwargs={"team_id": team.id, "player_id": p.id})
        return self.client.get(url).content.decode()

    def test_preferred_roles_group_uses_legend_not_orphan_label(self):
        html = self._edit_html()
        self.assertIn("<legend", html)
        # The exact orphan label markup must be gone.
        self.assertNotIn('<label class="form-label">Preferred Roles</label>', html)


class PlayerDetailHeadingTest(TestCase):
    """Regression: UXP-19 -- the sandbox player page had no <h1>."""

    def test_player_name_is_the_single_h1(self) -> None:
        team = Team.objects.create(name="H1 Team")
        p = Player.objects.create(team=team, name="Heading Player")
        url = reverse("player_detail", kwargs={"team_id": team.id, "player_id": p.id})
        html = self.client.get(url).content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertRegex(html, r"<h1[^>]*>Heading Player<")


class PlayerAddFillsPreferredSlotTest(TestCase):
    """Regression: UXP-19 -- adding a player with a preferred role left the
    matching empty slot unfilled ("missing Commander") until Assign Slots."""

    def _add(self, team: Team, name: str, roles: list[str]) -> Player:
        data = {
            "name": name,
            "preferred_roles": roles,
            "age": 25,
            "started_playing_age": 16,
            "total_games": 10,
            "home_site": "",
            "height": "",
            **{f: 50 for f in _STAT_FIELDS},
        }
        url = reverse("player_add", kwargs={"team_id": team.id})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content)
        return Player.objects.get(team=team, name=name)

    def test_fills_empty_matching_slot(self) -> None:
        team = Team.objects.create(name="Fill Team")
        player = self._add(team, "Cdr", ["commander"])
        team.refresh_from_db()
        self.assertEqual(team.slot_commander_id, player.id)
        self.assertNotIn("missing Commander", team.roster_errors)

    def test_occupied_slot_is_not_displaced(self) -> None:
        team = Team.objects.create(name="Occupied Team")
        incumbent = Player.objects.create(team=team, name="Incumbent")
        team.slot_commander = incumbent
        team.save()
        self._add(team, "Challenger", ["commander"])
        team.refresh_from_db()
        self.assertEqual(team.slot_commander_id, incumbent.id)

    def test_scouts_fill_scout_1_then_scout_2(self) -> None:
        team = Team.objects.create(name="Scout Team")
        first = self._add(team, "ScoutA", ["scout"])
        second = self._add(team, "ScoutB", ["scout"])
        third = self._add(team, "ScoutC", ["scout"])
        team.refresh_from_db()
        self.assertEqual(team.slot_scout_1_id, first.id)
        self.assertEqual(team.slot_scout_2_id, second.id)
        self.assertNotIn(third.id, (team.slot_scout_1_id, team.slot_scout_2_id))

    def test_no_preferred_role_fills_nothing(self) -> None:
        team = Team.objects.create(name="Bare Team")
        self._add(team, "Nobody", [])
        team.refresh_from_db()
        self.assertEqual(team.active_players, [])


class FaviconLinkTest(TestCase):
    """Regression: T-1 — no favicon was declared, so every page triggered
    a /favicon.ico request that 404'd. base.html must declare an icon so
    the browser never falls back to /favicon.ico."""

    def test_pages_declare_a_favicon(self):
        html = self.client.get(reverse("team_list")).content.decode()
        m = re.search(r'<link[^>]+rel="(?:shortcut )?icon"[^>]*>', html)
        self.assertIsNotNone(m, "base.html declares no <link rel=icon>")
        self.assertRegex(m.group(0), r'href="[^"]+"')
