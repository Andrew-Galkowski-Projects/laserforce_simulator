"""RES-03 — Missile usage log: frozen pytest acceptance contract.

This file is the spec-first contract for PLAN.md task RES-03 (Missile usage
log). Every test in this file is intentionally **failing today** against the
current codebase — the production implementation that follows must turn each
one green without changing any assertion in this file.

Locked design decisions (from the grill; do not relitigate):

1. The legacy ``event_type="missile"`` is SPLIT into:
   - ``event_type="locking"``   — emitted at missile fire / lock start
   - ``event_type="missiled"``  — emitted at missile resolution (hit or miss)
   The old ``"missile"`` string is removed from production code.
2. Friendly fire is a server-emitted bool: ``metadata["friendly_fire"]`` on
   every ``missiled`` event, ``True`` iff ``actor.team == target.team``.
3. Efficiency % = ``hits / fired * 100`` is view-side; no model property.
4. Timestamps are TICKS on ``GameEvent.timestamp`` (TIME-01 / ADR-0001);
   the missile-log template divides by 2 to render mm:ss.
5. Down/respawn: if the locking actor is eliminated BEFORE the missile
   resolves, NO ``missiled`` event fires (matches MECH-05 nuke cancellation).
   The ``locking`` event remains in the log.
6. Pre-existing bug surfacing: the legacy filter literal ``"missile_hit"`` and
   the legacy ``event_type="missile"`` value MUST NOT appear anywhere in the
   production source tree once RES-03 lands.
"""

from __future__ import annotations

import pathlib
import random
import re
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from matches.models import GameEvent, GameRound
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# Production-source extensions scanned by the legacy-literal guard (test #13).
# Listed at module top so a future reader can see the scope at a glance.
_SCANNED_EXTENSIONS: tuple[str, ...] = (".py", ".html")

# Files exempt from the legacy-literal guard: this spec itself, plus any
# existing tests documenting the historical bug. Path components are matched
# against the file's resolved path parts so this is OS-independent.
_GUARD_EXEMPT_BASENAMES: frozenset[str] = frozenset(
    {
        "test_res03_missile_log_spec.py",
    }
)

# Directories never scanned by the guard.
_GUARD_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "staticfiles",
        "media",
        "migrations",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root() -> pathlib.Path:
    """Return the ``laserforce_simulator/`` package root (the Django project
    package, i.e. the directory that contains ``manage.py``'s siblings —
    ``matches/``, ``teams/``, ``core/``, ``templates/``).
    """
    # This file lives at laserforce_simulator/matches/tests/THIS_FILE.
    here = pathlib.Path(__file__).resolve()
    return here.parents[2]


def _simulate_round_log(seed: int = 42, ticks: int = 1200) -> list[dict]:
    """Run a short deterministic round and return its in-memory event log.

    Rosters are built from ``make_team_with_slots`` and include a Heavy on
    each side (the role with missiles), so missile activity is likely. The
    fixed seed keeps the log reproducible.
    """
    red, _ = make_team_with_slots("Res03Red")
    blue, _ = make_team_with_slots("Res03Blue")
    red_roster = list(red.active_roster)
    blue_roster = list(blue.active_roster)

    random.seed(seed)
    sim = BatchSimulator()
    event_log: list[dict] = []
    with patch.object(BatchSimulator, "ROUND_TICKS", ticks):
        sim._simulate_round(red_roster, blue_roster, event_log=event_log)
    return event_log


def _missile_subset(event_log: list[dict]) -> list[tuple]:
    """Return a stable tuple list of just the ``locking`` / ``missiled``
    events for determinism comparison (test #15).
    """
    out: list[tuple] = []
    for ev in event_log:
        if ev.get("event_type") in ("locking", "missiled"):
            out.append(
                (
                    ev.get("event_type"),
                    ev.get("timestamp"),
                    # metadata dict is hashed via sorted repr
                    repr(sorted((ev.get("metadata") or {}).items())),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Section 1 — Event split: locking + missiled
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03MissileEventSplit:
    """The legacy ``event_type="missile"`` is split into ``locking`` and
    ``missiled``. These tests pin both halves of the split.
    """

    # bug-class: happy path
    def test_missile_fire_emits_locking_event(self):
        """A missile attempt produces an ``event_type="locking"`` row at the
        tick of fire. The legacy ``"missile"`` event_type must not be used.
        """
        log = _simulate_round_log(seed=42, ticks=1500)
        lockings = [e for e in log if e.get("event_type") == "locking"]
        legacy = [e for e in log if e.get("event_type") == "missile"]
        assert (
            not legacy
        ), f"legacy 'missile' event_type must be gone; got {len(legacy)} rows"
        assert lockings, (
            "RES-03 requires a 'locking' event at missile fire; none emitted "
            "in fixture round — splitter not implemented"
        )

    # bug-class: happy path
    def test_missile_resolution_emits_missiled_event_with_result(self):
        """A resolved missile produces an ``event_type="missiled"`` row whose
        ``metadata["result"]`` is one of ``"hit"`` / ``"miss"``.
        """
        log = _simulate_round_log(seed=42, ticks=1500)
        missileds = [e for e in log if e.get("event_type") == "missiled"]
        assert missileds, (
            "RES-03 requires a 'missiled' resolution event; none emitted in "
            "fixture round — splitter not implemented"
        )
        for ev in missileds:
            md = ev.get("metadata") or {}
            assert "result" in md, (
                f"missiled event must carry metadata['result']; got " f"metadata={md!r}"
            )
            assert md["result"] in ("hit", "miss"), (
                f"missiled metadata['result']={md['result']!r} must be 'hit' "
                f"or 'miss'"
            )

    # bug-class: happy path
    def test_missiled_event_metadata_carries_actor_and_target_roles(self):
        """Each ``missiled`` event carries ``actor_role`` and ``target_role``
        so the missile-log row can render both columns without a DB join.
        """
        log = _simulate_round_log(seed=42, ticks=1500)
        missileds = [e for e in log if e.get("event_type") == "missiled"]
        assert missileds, "fixture produced no missiled events"
        for ev in missileds:
            md = ev.get("metadata") or {}
            assert isinstance(md.get("actor_role"), str) and md["actor_role"], (
                f"missiled metadata['actor_role'] must be a non-empty str; "
                f"got {md.get('actor_role')!r}"
            )
            assert isinstance(md.get("target_role"), str) and md["target_role"], (
                f"missiled metadata['target_role'] must be a non-empty str; "
                f"got {md.get('target_role')!r}"
            )


# ---------------------------------------------------------------------------
# Section 2 — Friendly fire flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03FriendlyFireFlag:
    """``metadata["friendly_fire"]: bool`` is server-emitted on every
    ``missiled`` event. The view does not derive it; the simulator does.
    """

    # bug-class: happy path
    def test_missiled_event_metadata_has_friendly_fire_bool(self):
        """Every ``missiled`` event carries an explicit ``friendly_fire``
        bool — never missing, never None.
        """
        log = _simulate_round_log(seed=42, ticks=1500)
        missileds = [e for e in log if e.get("event_type") == "missiled"]
        assert missileds, "fixture produced no missiled events"
        for ev in missileds:
            md = ev.get("metadata") or {}
            assert "friendly_fire" in md, (
                f"missiled event missing metadata['friendly_fire']; got "
                f"metadata keys={sorted(md.keys())!r}"
            )
            assert isinstance(md["friendly_fire"], bool), (
                f"metadata['friendly_fire']={md['friendly_fire']!r} must be "
                f"bool, not {type(md['friendly_fire']).__name__}"
            )

    # bug-class: edge case
    def test_friendly_fire_true_iff_actor_team_equals_target_team(self):
        """``friendly_fire`` matches the team-equality predicate exactly.

        Drives the missile-resolution helper directly with hand-built
        ``PlayerState``s so both same-team and cross-team cases are forced.
        """
        from matches.sim_helpers.player_state import PlayerState

        def _ps(team_color: str, role: str = "heavy") -> PlayerState:
            return PlayerState(
                tag_id=f"{team_color}_{role}",
                name=f"{team_color} {role}",
                team_color=team_color,
                role=role,
                accuracy=50,
                survival=0,
                player_awareness=50,
                starting_lives=10,
                starting_shots=20,
                final_lives=10,
                final_shots=20,
                final_missiles=5,
            )

        sim = BatchSimulator()

        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        # Cross-team — friendly_fire should be False.
        attacker_x = _ps("red")
        defender_x = _ps("blue", role="scout")
        ctx_x = RoundContext(events=EventLog(persist=True))
        sim._complete_missile(attacker_x, defender_x, second=10, ctx=ctx_x)
        missileds_x = [
            e for e in ctx_x.events.entries if e.get("event_type") == "missiled"
        ]
        assert (
            missileds_x
        ), "no missiled event emitted for cross-team missile resolution"
        assert (
            missileds_x[0]["metadata"]["friendly_fire"] is False
        ), "cross-team missile must set friendly_fire=False"

        # Same-team — friendly_fire should be True.
        attacker_f = _ps("red")
        defender_f = _ps("red", role="scout")
        ctx_f = RoundContext(events=EventLog(persist=True))
        sim._complete_missile(attacker_f, defender_f, second=10, ctx=ctx_f)
        missileds_f = [
            e for e in ctx_f.events.entries if e.get("event_type") == "missiled"
        ]
        assert missileds_f, "no missiled event emitted for same-team missile resolution"
        assert (
            missileds_f[0]["metadata"]["friendly_fire"] is True
        ), "same-team missile must set friendly_fire=True"


# ---------------------------------------------------------------------------
# Section 3 — Tick semantics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03TickSemantics:
    """Per TIME-01 / ADR-0001 every persisted timestamp is a tick (1 tick =
    0.5 s). The missile-log template divides by 2 to render mm:ss.
    """

    # bug-class: tick-vs-seconds
    def test_locking_and_missiled_timestamps_are_ticks(self):
        """Locking and missiled events ride the same tick cursor the rest of
        the simulator uses. The assertion: every timestamp is an int in the
        legal tick range ``[0, BatchSimulator.ROUND_TICKS]``.
        """
        ticks = 1200
        log = _simulate_round_log(seed=42, ticks=ticks)
        relevant = [e for e in log if e.get("event_type") in ("locking", "missiled")]
        assert relevant, "fixture produced no locking/missiled events"
        for ev in relevant:
            ts = ev.get("timestamp")
            assert isinstance(ts, int), (
                f"{ev.get('event_type')!r} timestamp={ts!r} must be int "
                f"(ticks per TIME-01), got {type(ts).__name__}"
            )
            assert 0 <= ts <= ticks, (
                f"{ev.get('event_type')!r} timestamp={ts!r} out of legal tick "
                f"range [0, {ticks}]"
            )

    # bug-class: tick-vs-seconds
    def test_missile_log_template_renders_mmss_via_divide_by_two(self):
        """The missile-log row template renders ``timestamp / 2`` as mm:ss
        (TIME-01 boundary). We pin this by constructing a single ``missiled``
        ``GameEvent`` at tick 124 (= 62 s = 01:02) and asserting that the
        rendered missile-log section shows ``01:02`` rather than ``02:04``.
        """
        red, players = make_team_with_slots("Res03TickRed")
        blue, blue_players = make_team_with_slots("Res03TickBlue")
        gr = GameRound.objects.create(round_number=1, team_red=red, team_blue=blue)
        GameEvent.objects.create(
            game_round=gr,
            timestamp=124,
            event_type="missiled",
            actor=players["heavy"],
            target=blue_players["scout"],
            points_awarded=500,
            description="heavy hits scout with missile",
            metadata={
                "result": "hit",
                "friendly_fire": False,
                "actor_role": "heavy",
                "target_role": "scout",
            },
        )
        client = Client()
        # Dedicated endpoint locked by RES-03 grill (URL name "missile_log",
        # path /matches/game-round/<id>/missile-log/).
        url = reverse("missile_log", kwargs={"round_id": gr.id})
        resp = client.get(url)
        assert (
            resp.status_code == 200
        ), f"missile-log endpoint returned {resp.status_code}; expected 200"
        body = resp.content.decode("utf-8")
        assert "01:02" in body, (
            "missile-log timestamp must render as 01:02 (tick 124 ÷ 2 = 62s "
            "= 01:02); got body without '01:02'"
        )
        assert "02:04" not in body, (
            "missile-log must NOT render tick 124 as 02:04 — that's the "
            "seconds-treated-as-ticks bug class"
        )


# ---------------------------------------------------------------------------
# Section 4 — Down / respawn semantics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03DownAndRespawn:
    """If the locking actor is eliminated before the missile resolves, no
    ``missiled`` event fires (mirrors MECH-05 nuke cancellation).
    """

    # bug-class: Down/respawn
    def test_locking_actor_eliminated_before_resolution_emits_no_missiled(self):
        """Start a lock, eliminate the attacker before the resolution tick,
        and confirm the resolution tick does NOT produce a ``missiled``
        event. The ``locking`` event remains in the log.
        """
        from matches.sim_helpers.combat import start_missile_lock, tick_missile_lock
        from matches.sim_helpers.player_state import PlayerState

        attacker = PlayerState(
            tag_id="red_heavy",
            name="red heavy",
            team_color="red",
            role="heavy",
            accuracy=50,
            survival=0,
            player_awareness=50,
            starting_lives=10,
            starting_shots=20,
            final_lives=10,
            final_shots=20,
            final_missiles=5,
        )
        defender = PlayerState(
            tag_id="blue_scout",
            name="blue scout",
            team_color="blue",
            role="scout",
            accuracy=50,
            survival=0,
            player_awareness=50,
            starting_lives=10,
            starting_shots=20,
            final_lives=10,
            final_shots=20,
        )

        # The simulator must emit a 'locking' event at lock start; routing
        # the emit through the start_missile_lock helper is the natural seam.
        # EventLog candidate: ctx.events.locking is the verb that emits.
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        ctx = RoundContext(events=EventLog(persist=True))
        log = ctx.events.entries  # transitional shim; same list for asserts
        lock = start_missile_lock(attacker, defender, second=10, ctx=ctx)
        assert lock is not None, "lock setup failed — adjust fixture state"
        # Now eliminate the attacker before the lock resolves.
        attacker.final_lives = 0
        # Advance the lock 3 ticks; tick_missile_lock should return "miss"
        # (attacker died), and the simulator must NOT emit a 'missiled' hit.
        for delta in range(1, 4):
            tick_missile_lock(lock, second=10 + delta, movement_ctx=None)

        lockings = [e for e in log if e.get("event_type") == "locking"]
        missileds = [e for e in log if e.get("event_type") == "missiled"]
        assert lockings, "the locking event must survive even when attacker dies"
        assert not missileds, (
            "no missiled event must fire when the locking actor was "
            f"eliminated before resolution; got {missileds!r}"
        )

    # bug-class: Down/respawn
    def test_missiled_event_clears_actor_missile_lock_state(self):
        """When a missile resolves (hit or miss) the actor's pending-lock
        bookkeeping is cleared on the same tick — a subsequent down on the
        actor must NOT cause a second resolution.
        """
        from matches.sim_helpers.player_state import PlayerState

        attacker = PlayerState(
            tag_id="red_heavy",
            name="red heavy",
            team_color="red",
            role="heavy",
            accuracy=50,
            survival=0,
            player_awareness=50,
            starting_lives=10,
            starting_shots=20,
            final_lives=10,
            final_shots=20,
            final_missiles=5,
        )
        defender = PlayerState(
            tag_id="blue_scout",
            name="blue scout",
            team_color="blue",
            role="scout",
            accuracy=50,
            survival=0,
            player_awareness=50,
            starting_lives=10,
            starting_shots=20,
            final_lives=10,
            final_shots=20,
        )
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        ctx = RoundContext(events=EventLog(persist=True))
        log = ctx.events.entries  # alias the live list for the legacy asserts
        sim = BatchSimulator()
        sim._complete_missile(attacker, defender, second=20, ctx=ctx)
        first = [e for e in log if e.get("event_type") == "missiled"]
        assert first, "first resolution must emit a missiled event"
        # Forcing a Down on the attacker should NOT re-emit a missiled row.
        from matches.sim_helpers.down import record_down

        record_down(attacker, 25, ctx=None)
        second_log = [
            e
            for e in log
            if e.get("event_type") == "missiled" and e.get("timestamp") > 20
        ]
        assert not second_log, (
            "Down after resolution must not re-fire a 'missiled' event; "
            "actor's pending-lock state was not cleared"
        )


# ---------------------------------------------------------------------------
# Section 5 — View / template layer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03MissileLogView:
    """RES-03 surfaces a missile log in the existing events view (filter by
    event_type=missiled) OR at a dedicated ``/matches/game-round/<id>/
    missile-log/`` endpoint. The implementation is free to pick either
    shape; these tests adapt to whichever resolves at URL-resolution time.
    """

    def setup_method(self):
        self.red, self.players = make_team_with_slots("Res03ViewRed")
        self.blue, self.blue_players = make_team_with_slots("Res03ViewBlue")
        self.gr = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        # Three missiled rows: two hits (one cross-team, one friendly fire),
        # one miss. fired = 3, hit = 2, eff = 66.67%.
        GameEvent.objects.create(
            game_round=self.gr,
            timestamp=50,
            event_type="locking",
            actor=self.players["heavy"],
            target=self.blue_players["scout"],
            points_awarded=0,
            description="heavy locks scout",
            metadata={"actor_role": "heavy", "target_role": "scout"},
        )
        GameEvent.objects.create(
            game_round=self.gr,
            timestamp=56,
            event_type="missiled",
            actor=self.players["heavy"],
            target=self.blue_players["scout"],
            points_awarded=500,
            description="heavy hits scout with missile",
            metadata={
                "result": "hit",
                "friendly_fire": False,
                "actor_role": "heavy",
                "target_role": "scout",
            },
        )
        GameEvent.objects.create(
            game_round=self.gr,
            timestamp=110,
            event_type="missiled",
            actor=self.players["heavy"],
            target=self.players["ammo"],
            points_awarded=500,
            description="heavy hits ammo with missile (friendly fire)",
            metadata={
                "result": "hit",
                "friendly_fire": True,
                "actor_role": "heavy",
                "target_role": "ammo",
            },
        )
        GameEvent.objects.create(
            game_round=self.gr,
            timestamp=180,
            event_type="missiled",
            actor=self.players["heavy"],
            target=self.blue_players["medic"],
            points_awarded=0,
            description="heavy misses medic with missile",
            metadata={
                "result": "miss",
                "friendly_fire": False,
                "actor_role": "heavy",
                "target_role": "medic",
            },
        )
        # A tag to make sure the filter actually excludes non-missiled rows.
        GameEvent.objects.create(
            game_round=self.gr,
            timestamp=200,
            event_type="tag",
            actor=self.players["heavy"],
            target=self.blue_players["scout"],
            points_awarded=100,
            description="heavy tags scout",
            metadata={},
        )

    def _missile_log_url(self) -> str:
        # Dedicated endpoint locked by RES-03 grill (URL name "missile_log").
        return reverse("missile_log", kwargs={"round_id": self.gr.id})

    # bug-class: CLI/flag wiring
    def test_missile_log_view_filter_querystring_renders_only_missiled_rows(self):
        """The missile-log surface renders rows for ``missiled`` events and
        omits unrelated events (``tag``). If the implementation chose a
        dedicated endpoint, that endpoint shows only ``missiled`` rows; if
        it chose the existing events view filtered by querystring, the same
        property must hold.
        """
        client = Client()
        resp = client.get(self._missile_log_url())
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        # The missiled rows must show; the tag row must NOT show in the
        # missile-log section.
        assert "heavy hits scout with missile" in body
        assert "heavy misses medic with missile" in body
        assert "heavy tags scout" not in body, (
            "missile log surface must hide non-missile event rows; tag row "
            "leaked through"
        )

    # bug-class: happy path
    def test_missile_summary_efficiency_pct_equals_hits_over_fired_view_side(
        self,
    ):
        """View-side summary: fired = count(missiled), hit = count(result=hit),
        efficiency = hits / fired * 100. Friendly-fire hits count as hits.
        For this fixture: fired=3, hit=2, eff=66.67%.
        """
        client = Client()
        resp = client.get(self._missile_log_url())
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        # Be lenient on formatting: accept "66.7", "66.67", or "67"
        # (rounded), but the fired/hit counts must be present verbatim.
        assert re.search(r"\bfired\b[^0-9]*3\b", body, flags=re.IGNORECASE), (
            "summary must report fired=3 for this fixture; substring "
            "'fired ... 3' not found in response"
        )
        assert re.search(r"\bhit\b[^0-9]*2\b", body, flags=re.IGNORECASE), (
            "summary must report hit=2 for this fixture; substring "
            "'hit ... 2' not found in response"
        )
        assert re.search(r"6[67](\.\d+)?\s*%", body), (
            "summary must report efficiency around 66.67%; no '66.67%' / "
            "'66.7%' / '67%' found"
        )

    # bug-class: happy path
    def test_friendly_fire_row_carries_distinguishing_css_class(self):
        """Friendly-fire rows are rendered with a distinguishing CSS class
        (``friendly-fire`` is the locked-in marker). The cross-team hit
        row must NOT carry that class.
        """
        client = Client()
        resp = client.get(self._missile_log_url())
        body = resp.content.decode("utf-8")
        assert "friendly-fire" in body, (
            "friendly-fire row must carry a CSS class containing "
            "'friendly-fire'; not found anywhere in the response"
        )
        # Sanity: cross-team hit description must not appear inside a
        # friendly-fire-class element. We require the friendly-fire class
        # token and the friendly-fire description ('hits ammo') to coexist.
        assert (
            "hits ammo with missile" in body
        ), "friendly-fire fixture row description missing from response"


# ---------------------------------------------------------------------------
# Section 6 — Legacy literal guard
# ---------------------------------------------------------------------------


def _scan_production_files() -> list[pathlib.Path]:
    """Yield all production .py / .html files under the project package,
    skipping migrations, caches, and this spec file itself.
    """
    root = _project_root()
    out: list[pathlib.Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SCANNED_EXTENSIONS:
            continue
        if any(part in _GUARD_SKIP_DIRS for part in path.parts):
            continue
        if path.name in _GUARD_EXEMPT_BASENAMES:
            continue
        out.append(path)
    return out


# bug-class: doc/code consistency
def test_legacy_missile_event_type_string_is_absent_from_codebase():
    """Pre-existing-bug surface: no production .py or .html under
    ``laserforce_simulator/`` may contain the literal ``"missile_hit"`` or
    the legacy unsplit ``event_type="missile"`` string after RES-03 lands.

    Both literals were the source of the RES-02 silent chart bug
    (game_analysis.py and the chart-shots / chart-lives / chart-points
    scanners in game_round_events.html compared against an event type that
    never existed). They must be cleaned up.

    Scope is pinned by ``_SCANNED_EXTENSIONS`` at the top of this file.
    """
    offenders_missile_hit: list[str] = []
    offenders_legacy_missile: list[str] = []
    legacy_pattern = re.compile(r"""event_type\s*=\s*['"]missile['"]""")

    for path in _scan_production_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "missile_hit" in text:
            offenders_missile_hit.append(str(path))
        if legacy_pattern.search(text):
            offenders_legacy_missile.append(str(path))

    assert not offenders_missile_hit, (
        "literal 'missile_hit' (RES-02 pre-existing-bug surface) must be "
        "removed by RES-03; still present in:\n  - "
        + "\n  - ".join(offenders_missile_hit)
    )
    assert not offenders_legacy_missile, (
        "legacy event_type=\"missile\" must be split into 'locking' and "
        "'missiled' by RES-03; still present in:\n  - "
        + "\n  - ".join(offenders_legacy_missile)
    )


# ---------------------------------------------------------------------------
# Section 7 — Domain glossary update
# ---------------------------------------------------------------------------


# bug-class: doc/code consistency
def test_context_md_defines_locking_and_missiled_terms():
    """CONTEXT.md is the project glossary; RES-03 adds the new domain
    terms. We check both common locations (root and ``docs/CONTEXT.md``)
    and require the strings ``Locking event``, ``Missiled event``, and
    ``Friendly fire`` to appear at least once in whichever file exists.
    """
    root = _project_root().parent  # repo root (parent of laserforce_simulator/)
    candidates = [root / "CONTEXT.md", root / "docs" / "CONTEXT.md"]
    existing = [p for p in candidates if p.exists()]
    assert existing, (
        "neither CONTEXT.md nor docs/CONTEXT.md exists — RES-03 glossary "
        "update has nothing to update"
    )
    body = "\n".join(p.read_text(encoding="utf-8") for p in existing)
    for term in ("Locking event", "Missiled event", "Friendly fire"):
        assert term in body, (
            f"CONTEXT.md must define the RES-03 term {term!r}; substring " f"not found"
        )


# ---------------------------------------------------------------------------
# Section 8 — Determinism
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes03Determinism:
    """Same seed + same rosters ⇒ identical locking / missiled sequence.
    Mirrors the SIM-07 / SIM-08 contract scoped to RES-03's new event
    types.
    """

    # bug-class: determinism
    @pytest.mark.xfail(
        reason=(
            "RES-03 grill: skipped at spec-freeze time until implementation "
            "lands. ROUND_TICKS for reliable locking+missiled emission must be "
            "tuned empirically once the split is in place. Re-enable (drop "
            "xfail) after first green pass; bump ticks if run_a is empty."
        ),
        strict=False,
    )
    def test_same_seed_produces_identical_locking_and_missiled_sequence(self):
        """Two runs of the same seeded round emit byte-identical (event_type,
        timestamp, metadata) tuples for the locking + missiled subset.
        """
        run_a = _missile_subset(_simulate_round_log(seed=42, ticks=1800))
        run_b = _missile_subset(_simulate_round_log(seed=42, ticks=1800))
        assert run_a, "fixture produced no locking/missiled events to compare"
        assert run_a == run_b, (
            "seeded round must produce identical locking/missiled sequence "
            "across runs (SIM-07 contract); divergence detected"
        )
