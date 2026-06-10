"""LG-02-Part2b — pure-unit tests for ``matches/phase_composer.py``.

No DB, no Django imports in the assertion path. The Part2b seam contract is
locked at ``.claude/worktrees/lg-02-part2b-seam-contract.md`` (§3): the pure
module ``matches/phase_composer.py`` parses the composer's comma-separated wire
format (``"round_robin,tournament"``) into an ordered ``list[PhaseSpec]`` via
``parse_phase_composition(raw, *, season_schedule_format)``.

Frozen import allowlist for the module under test: ``dataclasses`` + ``typing``
ONLY — NO django, NO ORM, NO ``random``/``datetime``/``json``/I/O/logging. The
``TestNoDjangoImportsLeaked`` class below defends that allowlist with a
subprocess fresh-import + ``sys.modules`` walk, mirroring
``test_standings.py::TestNoDjangoImportsLeaked``.

NOTE: collection / pass of this file requires the Code agent's
``matches/phase_composer.py`` (``PhaseSpec`` + ``parse_phase_composition``) to
land. Until then these tests are EXPECTED to fail (import error) — that is the
TDD red state, not a defect in this file.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.phase_composer import PhaseSpec, parse_phase_composition

# The format value the view threads in as ``season_schedule_format`` — the
# disabled Season-level field's locked single option.
_SSF = "single_round_robin"


# ---------------------------------------------------------------------------
# §3 — EMPTY / blank ``raw`` short-circuits to the Part2a single-RR default
# ---------------------------------------------------------------------------


class TestParsePhaseCompositionEmpty(SimpleTestCase):
    """Empty / whitespace-only ``raw`` ⇒ exactly one default RR phase."""

    def test_empty_string_returns_single_round_robin_default(self) -> None:
        specs = parse_phase_composition("", season_schedule_format=_SSF)
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.ordinal, 1)
        self.assertEqual(spec.phase_type, "round_robin")
        # The default RR phase copies the season format argument verbatim.
        self.assertEqual(spec.schedule_format, _SSF)

    def test_whitespace_only_returns_single_round_robin_default(self) -> None:
        specs = parse_phase_composition("   ", season_schedule_format=_SSF)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].ordinal, 1)
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].schedule_format, _SSF)

    def test_default_schedule_format_is_the_passed_arg(self) -> None:
        # The default RR's schedule_format is exactly whatever arg was passed,
        # not a hard-coded literal.
        specs = parse_phase_composition("", season_schedule_format="some_other_fmt")
        self.assertEqual(specs[0].schedule_format, "some_other_fmt")

    def test_returns_a_list(self) -> None:
        self.assertIsInstance(
            parse_phase_composition("", season_schedule_format=_SSF), list
        )


# ---------------------------------------------------------------------------
# §3 — single ``round_robin`` token
# ---------------------------------------------------------------------------


class TestParsePhaseCompositionSingleRoundRobin(SimpleTestCase):
    """A single ``"round_robin"`` token copies the season format."""

    def test_single_round_robin_copies_season_schedule_format(self) -> None:
        specs = parse_phase_composition("round_robin", season_schedule_format=_SSF)
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.ordinal, 1)
        self.assertEqual(spec.phase_type, "round_robin")
        self.assertEqual(spec.schedule_format, _SSF)

    def test_single_round_robin_uses_passed_arg_value(self) -> None:
        specs = parse_phase_composition(
            "round_robin", season_schedule_format="double_round_robin"
        )
        self.assertEqual(specs[0].schedule_format, "double_round_robin")


# ---------------------------------------------------------------------------
# §3 — a ``tournament`` phase has ``schedule_format`` None
# ---------------------------------------------------------------------------


class TestTournamentSpecScheduleFormatNone(SimpleTestCase):
    """A tournament spec carries ``schedule_format=None``."""

    def test_round_robin_then_tournament_tournament_format_is_none(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 2)
        # RR phase copies the season format.
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].schedule_format, _SSF)
        # Tournament phase gets None.
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertIsNone(specs[1].schedule_format)


# ---------------------------------------------------------------------------
# §3 — multi-phase: contiguous ordinals 1..N, order preserved
# ---------------------------------------------------------------------------


class TestMultiPhaseComposition(SimpleTestCase):
    """``round_robin,tournament,round_robin`` ⇒ 3 specs, ordinals 1,2,3."""

    def test_three_phase_count_and_order(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 3)
        self.assertEqual(
            [s.phase_type for s in specs],
            ["round_robin", "tournament", "round_robin"],
        )

    def test_three_phase_contiguous_ordinals(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.ordinal for s in specs], [1, 2, 3])

    def test_three_phase_schedule_formats(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament,round_robin", season_schedule_format=_SSF
        )
        # Both RR specs copy the season format; the tournament spec is None.
        self.assertEqual(specs[0].schedule_format, _SSF)
        self.assertIsNone(specs[1].schedule_format)
        self.assertEqual(specs[2].schedule_format, _SSF)

    def test_tokens_are_stripped_of_surrounding_whitespace(self) -> None:
        # Per the contract, each token is ``str.strip()``-ed — so a composition
        # with padded tokens parses identically.
        specs = parse_phase_composition(
            " round_robin , tournament ", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["round_robin", "tournament"])
        self.assertEqual([s.ordinal for s in specs], [1, 2])


# ---------------------------------------------------------------------------
# §3 — zero-round-robin composition raises ValueError (exact message)
# ---------------------------------------------------------------------------


class TestZeroRoundRobinRejected(SimpleTestCase):
    """A non-empty composition with no ``round_robin`` phase is rejected."""

    def test_tournament_only_raises_exact_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("tournament", season_schedule_format=_SSF)
        self.assertEqual(
            str(ctx.exception),
            "composition must contain at least one round-robin phase",
        )


# ---------------------------------------------------------------------------
# LG-02-Part2c-1 — a ``tournament`` phase requires a PRECEDING ``round_robin``
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-1-seam-contract.md`` §5: a new
# compose-time guard fires AFTER the existing zero-RR check — once all tokens
# are known-valid AND at least one RR exists, walking the specs in order and
# raising the LOCKED ValueError string if any ``tournament`` spec is seen before
# the first ``round_robin`` spec. A valid ``round_robin,tournament`` composition
# parses to 2 ordered specs with no raise. The new guard NEVER fires before the
# zero-RR check (a no-RR composition still raises the existing zero-RR error).


class TestTournamentRequiresPrecedingRoundRobin(SimpleTestCase):
    """LG-02-Part2c-1 — ``tournament`` before the first ``round_robin``."""

    _MSG = "a tournament phase requires a preceding round-robin phase"

    def test_tournament_before_round_robin_raises_exact_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "tournament,round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), self._MSG)

    def test_round_robin_then_tournament_parses_to_two_ordered_specs(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual([s.phase_type for s in specs], ["round_robin", "tournament"])
        self.assertEqual([s.ordinal for s in specs], [1, 2])

    def test_tournament_between_two_round_robins_is_valid(self) -> None:
        # The tournament sits after a leading round_robin, so the guard never
        # fires even though another tournament-eligible RR follows.
        specs = parse_phase_composition(
            "round_robin,tournament,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual(
            [s.phase_type for s in specs],
            ["round_robin", "tournament", "round_robin"],
        )

    def test_guard_fires_only_after_zero_rr_check(self) -> None:
        # A composition with NO round_robin at all (and a leading tournament)
        # must raise the EXISTING zero-RR error, NOT the new preceding-RR guard
        # — the zero-RR check runs first.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "tournament,tournament", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception),
            "composition must contain at least one round-robin phase",
        )

    def test_single_tournament_token_raises_zero_rr_not_preceding_guard(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("tournament", season_schedule_format=_SSF)
        # Zero-RR error (one RR is required), NOT the preceding-RR guard.
        self.assertEqual(
            str(ctx.exception),
            "composition must contain at least one round-robin phase",
        )


# ---------------------------------------------------------------------------
# §3 — unknown phase type raises ValueError ``f"unknown phase type: {token!r}"``
# ---------------------------------------------------------------------------


class TestUnknownPhaseTypeRejected(SimpleTestCase):
    """An unrecognised token raises the unknown-type ValueError."""

    def test_unknown_type_raises_with_repr_of_token(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("round_robin,bogus", season_schedule_format=_SSF)
        # Exact message includes ``repr`` of the offending token.
        self.assertEqual(str(ctx.exception), "unknown phase type: 'bogus'")

    def test_member_night_token_rejected_as_unknown_type(self) -> None:
        # ``member_night`` is declared in the model enum but is NOT selectable
        # in Part2b — the composer must reject it as an unknown phase type.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,member_night", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "unknown phase type: 'member_night'")


# ---------------------------------------------------------------------------
# §3 — malformed input (empty token between commas) raises ValueError
# ---------------------------------------------------------------------------


class TestMalformedCompositionRejected(SimpleTestCase):
    """An empty token (after strip) raises the malformed ValueError."""

    def test_empty_token_between_commas_raises_exact_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,,tournament", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_trailing_comma_raises_malformed(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("round_robin,", season_schedule_format=_SSF)
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_whitespace_only_token_between_commas_raises_malformed(self) -> None:
        # A token that is empty AFTER strip is malformed (distinct from a
        # blank/whole-``raw`` short-circuit, which is the Part2a default).
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,   ,tournament", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")


# ---------------------------------------------------------------------------
# PhaseSpec dataclass shape
# ---------------------------------------------------------------------------


class TestPhaseSpecShape(SimpleTestCase):
    """``PhaseSpec`` is constructible with the three locked fields."""

    def test_phase_spec_fields(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.ordinal, 1)
        self.assertEqual(spec.phase_type, "round_robin")
        self.assertEqual(spec.schedule_format, _SSF)

    def test_phase_spec_allows_none_schedule_format(self) -> None:
        spec = PhaseSpec(ordinal=2, phase_type="tournament", schedule_format=None)
        self.assertIsNone(spec.schedule_format)


# ---------------------------------------------------------------------------
# Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LG-02-Part2c-3a — per-token ``type[:format]`` wire format
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3a-seam-contract.md`` §2.12 / §4:
# the composer wire format extends from comma-separated phase-TYPE tokens to
# comma-separated ``type[:format]`` tokens. A bare ``round_robin`` defaults to
# ``single_round_robin`` (Part2b backward-compat); ``round_robin:double_round_robin``
# ⇒ a spec with ``schedule_format == "double_round_robin"``; a ``tournament``
# token carries no format (``schedule_format is None``); an unknown/unsupported
# per-phase schedule_format raises the NEW pure ``ValueError("unknown
# schedule_format: …")``. The 4 existing ValueError cases (zero-RR, unknown-type,
# malformed, tournament-before-RR) still fire, and the purity check stays green.
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the per-token format parse + the new ValueError —
# the TDD red state, not a defect in this file.


class TestParsePhaseCompositionPerTokenFormat(SimpleTestCase):
    """``round_robin:<format>`` sets the spec's ``schedule_format`` per token."""

    def test_double_round_robin_token_sets_schedule_format(self) -> None:
        specs = parse_phase_composition(
            "round_robin:double_round_robin", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].schedule_format, "double_round_robin")

    def test_explicit_single_round_robin_token_sets_schedule_format(self) -> None:
        specs = parse_phase_composition(
            "round_robin:single_round_robin", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].schedule_format, "single_round_robin")

    def test_bare_round_robin_defaults_to_single_round_robin(self) -> None:
        # Backward-compat: a bare ``round_robin`` token (no colon) resolves to
        # ``single_round_robin`` (the season_schedule_format fallback is the
        # locked ``"single_round_robin"``).
        specs = parse_phase_composition("round_robin", season_schedule_format=_SSF)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].schedule_format, "single_round_robin")

    def test_mixed_double_rr_then_tournament_two_specs(self) -> None:
        specs = parse_phase_composition(
            "round_robin:double_round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].schedule_format, "double_round_robin")
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertIsNone(specs[1].schedule_format)

    def test_single_rr_token_then_tournament_two_specs(self) -> None:
        specs = parse_phase_composition(
            "round_robin:single_round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].schedule_format, "single_round_robin")
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertIsNone(specs[1].schedule_format)

    def test_multiple_rr_tokens_each_carry_their_own_format(self) -> None:
        specs = parse_phase_composition(
            "round_robin:single_round_robin,round_robin:double_round_robin",
            season_schedule_format=_SSF,
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual([s.ordinal for s in specs], [1, 2])
        self.assertEqual(specs[0].schedule_format, "single_round_robin")
        self.assertEqual(specs[1].schedule_format, "double_round_robin")

    def test_padded_per_token_format_is_stripped(self) -> None:
        # Surrounding whitespace on the token still parses cleanly.
        specs = parse_phase_composition(
            " round_robin:double_round_robin ", season_schedule_format=_SSF
        )
        self.assertEqual(specs[0].schedule_format, "double_round_robin")


class TestParsePhaseCompositionUnknownFormat(SimpleTestCase):
    """An unknown per-phase ``schedule_format`` raises the new ValueError."""

    def test_unknown_format_raises_with_repr_of_format(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin:triple_round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "unknown schedule_format: 'triple_round_robin'"
        )

    def test_unknown_format_after_valid_rr_still_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin:single_round_robin,round_robin:bogus_format",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "unknown schedule_format: 'bogus_format'")


class TestParsePhaseCompositionExistingErrorsStillFire(SimpleTestCase):
    """The 4 existing ValueError cases still fire under the per-token format."""

    def test_zero_round_robin_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("tournament", season_schedule_format=_SSF)
        self.assertEqual(
            str(ctx.exception),
            "composition must contain at least one round-robin phase",
        )

    def test_unknown_phase_type_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("round_robin,bogus", season_schedule_format=_SSF)
        self.assertEqual(str(ctx.exception), "unknown phase type: 'bogus'")

    def test_malformed_empty_token_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,,tournament", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_tournament_token_with_an_unknown_mode_rejected(self) -> None:
        # LG-02-Part2c-3c SUPERSEDES the old Part2c-3a "tournament takes no
        # format -> malformed" rule: the ``:`` part of a tournament token is now
        # the MODE, so a non-mode value like ``double_round_robin`` raises the
        # NEW ``unknown tournament_mode`` ValueError (not "malformed").
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:double_round_robin",
                season_schedule_format=_SSF,
            )
        self.assertEqual(
            str(ctx.exception), "unknown tournament_mode: 'double_round_robin'"
        )

    def test_tournament_before_round_robin_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "tournament,round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception),
            "a tournament phase requires a preceding round-robin phase",
        )


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.phase_composer`` in a fresh subprocess must not
    pull in ``django.*`` — the allowlist is ``dataclasses`` + ``typing``.

    Mirrors ``test_standings.py::TestNoDjangoImportsLeaked`` exactly.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.phase_composer  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


# ---------------------------------------------------------------------------
# LG-02-Part2c-3b — PhaseSpec.tournament_mode (dormant, defaulted)
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3b-seam-contract.md``:
# ``PhaseSpec`` gains a trailing ``tournament_mode: str = "standings"`` field
# that is NOT parsed from the wire format this slice (the ``:`` syntax stays
# reserved for the Part2c-3c picker), so every parsed spec carries the
# ``"standings"`` default and a ``tournament:<mode>`` token is still malformed.
# Appended as NEW classes; no existing class above is modified.


class TestPhaseSpecTournamentModeDefault(SimpleTestCase):
    """``PhaseSpec.tournament_mode`` defaults to ``"standings"`` and is the
    LAST positional field (existing 3-field constructions stay valid)."""

    def test_default_is_standings(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.tournament_mode, "standings")

    def test_explicit_value_is_carried(self) -> None:
        spec = PhaseSpec(
            ordinal=1,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="strength",
        )
        self.assertEqual(spec.tournament_mode, "strength")


class TestParseStampsStandingsMode(SimpleTestCase):
    """``parse_phase_composition`` leaves every spec at the ``"standings"``
    default — the wire format does not carry a mode this slice."""

    def test_round_robin_only_default(self) -> None:
        specs = parse_phase_composition("round_robin", season_schedule_format=_SSF)
        self.assertEqual([s.tournament_mode for s in specs], ["standings"])

    def test_empty_default_spec(self) -> None:
        specs = parse_phase_composition("", season_schedule_format=_SSF)
        self.assertEqual(specs[0].tournament_mode, "standings")

    def test_round_robin_then_tournament_both_standings(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual([s.tournament_mode for s in specs], ["standings", "standings"])


class TestTournamentModeTokenNowParses(SimpleTestCase):
    """LG-02-Part2c-3c SUPERSEDES the Part2c-3b "tournament:<mode> is still
    malformed" rule: the ``:`` mode syntax now LANDS, so a valid-mode token
    parses + stamps ``PhaseSpec.tournament_mode`` (see ``TestParseTournamentMode``
    for the full surface). This class pins the supersession at the old call
    site so the Part2c-3b expectation does not linger."""

    def test_tournament_strength_token_now_parses(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:strength", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "strength")


# ===========================================================================
# LG-02-Part2c-3c — ``tournament[:mode]`` wire token + mode-stamping + the
# relaxed preceding-RR guard (standings-only)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3c-seam-contract.md`` §2 / §3 /
# §9: the ``tournament`` wire token becomes ``tournament[:mode]``. The token is
# split on the FIRST ``:``; for a ``tournament`` token the ``format_part`` is the
# MODE. Bare ``tournament`` ⇒ ``tournament_mode == "standings"``. Valid modes
# THIS slice are ``standings`` / ``strength`` / ``unseeded`` (``random_draw`` is
# DEFERRED and rejected); any unknown mode raises the NEW locked
# ``ValueError(f"unknown tournament_mode: {mode!r}")``. The preceding-RR guard is
# RELAXED to fire ONLY for a ``standings`` tournament; ``strength`` / ``unseeded``
# may sit anywhere (including first). Every preserved ValueError still fires and
# the parser stays Django-free (``TestNoDjangoImportsLeaked`` above still passes).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the Part2c-3c ``tournament[:mode]`` parse + the
# guard relaxation — the TDD red state, not a defect in this file.


class TestParseTournamentMode(SimpleTestCase):
    """``tournament[:mode]`` parses + stamps ``PhaseSpec.tournament_mode``."""

    def test_bare_tournament_defaults_to_standings(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "standings")

    def test_tournament_standings_token_stamps_standings(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "standings")
        # Tournament phase still carries no schedule_format.
        self.assertIsNone(specs[1].schedule_format)

    def test_tournament_strength_token_stamps_strength(self) -> None:
        # ``strength`` may sit first (no preceding-RR requirement) but here it
        # follows an RR for a vanilla mode-stamp assertion.
        specs = parse_phase_composition(
            "round_robin,tournament:strength", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "strength")
        self.assertIsNone(specs[1].schedule_format)

    def test_tournament_unseeded_token_stamps_unseeded(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:unseeded", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "unseeded")
        self.assertIsNone(specs[1].schedule_format)

    def test_strength_tournament_may_be_first(self) -> None:
        # A ``strength`` tournament needs NO preceding round_robin; the spec
        # parses with the strength mode stamped and the composition still
        # satisfies the >=1-RR rule via the trailing round_robin.
        specs = parse_phase_composition(
            "tournament:strength,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["tournament", "round_robin"])
        self.assertEqual(specs[0].tournament_mode, "strength")

    def test_unseeded_tournament_may_be_first(self) -> None:
        specs = parse_phase_composition(
            "tournament:unseeded,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["tournament", "round_robin"])
        self.assertEqual(specs[0].tournament_mode, "unseeded")

    def test_round_robin_specs_keep_standings_default(self) -> None:
        # Mode-stamping is a tournament-token concern; RR specs keep the default.
        specs = parse_phase_composition(
            "round_robin,tournament:strength", season_schedule_format=_SSF
        )
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].tournament_mode, "standings")


class TestParseTournamentModeRejected(SimpleTestCase):
    """``tournament:random_draw`` (deferred) and any unknown mode raise the NEW
    locked ``ValueError(f"unknown tournament_mode: {mode!r}")``."""

    def test_random_draw_mode_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:random_draw", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "unknown tournament_mode: 'random_draw'")

    def test_bogus_mode_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:bogus", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "unknown tournament_mode: 'bogus'")

    def test_member_night_still_unknown_phase_type(self) -> None:
        # member_night is rejected at the TYPE level (not the mode level) — the
        # exact preserved unknown-type string with the repr of the WHOLE token.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,member_night", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "unknown phase type: 'member_night'")


class TestParseTournamentModePreservedErrors(SimpleTestCase):
    """Every PRE-Part2c-3c ValueError still fires under the new mode parse."""

    def test_malformed_empty_token_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,,tournament", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_unknown_schedule_format_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin:triple_round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "unknown schedule_format: 'triple_round_robin'"
        )

    def test_zero_round_robin_still_rejected(self) -> None:
        # A composition with only a (now-valid) strength tournament and no RR
        # still fails the >=1-RR rule.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition("tournament:strength", season_schedule_format=_SSF)
        self.assertEqual(
            str(ctx.exception),
            "composition must contain at least one round-robin phase",
        )


class TestComposeGuardRelaxedToStandingsOnly(SimpleTestCase):
    """The preceding-RR guard fires ONLY for a ``standings`` tournament.

    The LOCKED string is unchanged (``"a tournament phase requires a preceding
    round-robin phase"``); only the CONDITION narrows — it raises iff the spec
    is ``phase_type == "tournament" AND tournament_mode == "standings" AND no
    preceding round_robin``.
    """

    _MSG = "a tournament phase requires a preceding round-robin phase"

    def test_standings_tournament_before_rr_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "tournament:standings,round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), self._MSG)

    def test_bare_tournament_before_rr_raises(self) -> None:
        # Bare ``tournament`` defaults to standings ⇒ the guard still fires.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "tournament,round_robin", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), self._MSG)

    def test_strength_tournament_before_rr_does_not_raise(self) -> None:
        # No raise — strength may be first.
        specs = parse_phase_composition(
            "tournament:strength,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["tournament", "round_robin"])

    def test_unseeded_tournament_before_rr_does_not_raise(self) -> None:
        specs = parse_phase_composition(
            "tournament:unseeded,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["tournament", "round_robin"])

    def test_standings_tournament_after_rr_does_not_raise(self) -> None:
        # The season-ending standings playoff — RR then standings tournament.
        specs = parse_phase_composition(
            "round_robin,tournament:standings", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["round_robin", "tournament"])
        self.assertEqual(specs[1].tournament_mode, "standings")

    def test_mid_season_standings_does_not_raise(self) -> None:
        # A mid-season standings tournament: RR -> standings tournament -> RR.
        # It has a PRECEDING round_robin, so the guard never fires.
        specs = parse_phase_composition(
            "round_robin,tournament:standings,round_robin",
            season_schedule_format=_SSF,
        )
        self.assertEqual(
            [s.phase_type for s in specs],
            ["round_robin", "tournament", "round_robin"],
        )
        self.assertEqual(specs[1].tournament_mode, "standings")


# ===========================================================================
# LG-02-Part2c-3d — ``tournament[:mode[:cut]]`` wire grammar + ``tournament_cut``
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3d-seam-contract.md`` §4 / §9:
# the tournament wire token grows a THIRD optional field, the participant cut —
# ``tournament[:mode[:cut]]`` parsed via ``split(":")`` on the tournament branch
# only (the round_robin branch keeps its 2-way ``partition(":")`` grammar). The
# locked rules on a tournament token:
#   - bare ``tournament``           ⇒ mode "standings", cut 0
#   - ``tournament:strength``       ⇒ mode "strength",  cut 0  (c-3c back-compat)
#   - ``tournament:standings:8``    ⇒ mode "standings", cut 8
#   - ``tournament:strength:4``     ⇒ mode "strength",  cut 4
#   - cut floor: a parsed ``cut != 0 and cut < 4`` ⇒ the NEW LOCKED
#     ``ValueError("tournament cut must be 0 or at least 4: <n>")`` (no ``!r``)
#   - ``len(parts) > 3`` / non-int cut / empty cut field ⇒ EXISTING
#     ``ValueError("malformed phase composition")``
#   - a 3rd field on a ``round_robin`` token stays ``"malformed phase composition"``
#   - ``PhaseSpec.tournament_cut`` defaults 0; existing keyword constructions
#     stay equality-identical; the purity check stays green (no new import).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the Part2c-3d cut grammar + ``PhaseSpec`` field +
# the new floor ValueError — the TDD red state, not a defect in this file.


class TestPhaseSpecTournamentCutDefault(SimpleTestCase):
    """``PhaseSpec.tournament_cut`` defaults to ``0`` and is the LAST positional
    field (existing 4-field constructions stay equality-identical)."""

    def test_default_is_zero(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.tournament_cut, 0)

    def test_explicit_value_is_carried(self) -> None:
        spec = PhaseSpec(
            ordinal=1,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=8,
        )
        self.assertEqual(spec.tournament_cut, 8)

    def test_existing_keyword_construction_stays_equality_identical(self) -> None:
        # A pre-c-3d 4-field keyword construction (no tournament_cut) must equal
        # an explicit ``tournament_cut=0`` construction — the trailing-default
        # precedent that keeps every prior PhaseSpec build valid.
        a = PhaseSpec(
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="strength",
        )
        b = PhaseSpec(
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="strength",
            tournament_cut=0,
        )
        self.assertEqual(a, b)


class TestParseTournamentCut(SimpleTestCase):
    """``tournament[:mode[:cut]]`` parses + stamps ``PhaseSpec.tournament_cut``."""

    def test_bare_tournament_cut_is_zero(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "standings")
        self.assertEqual(specs[1].tournament_cut, 0)

    def test_tournament_strength_cut_is_zero_back_compat(self) -> None:
        # c-3c back-compat: a two-field ``tournament:strength`` token (no cut)
        # ⇒ strength mode, cut 0.
        specs = parse_phase_composition(
            "round_robin,tournament:strength", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].tournament_mode, "strength")
        self.assertEqual(specs[1].tournament_cut, 0)

    def test_tournament_standings_8_sets_cut_8(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:8", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].phase_type, "tournament")
        self.assertEqual(specs[1].tournament_mode, "standings")
        self.assertEqual(specs[1].tournament_cut, 8)
        # A tournament phase still carries no schedule_format.
        self.assertIsNone(specs[1].schedule_format)

    def test_tournament_strength_4_sets_cut_4(self) -> None:
        # ``strength`` may sit first; the trailing RR satisfies the >=1-RR rule.
        specs = parse_phase_composition(
            "tournament:strength:4,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual([s.phase_type for s in specs], ["tournament", "round_robin"])
        self.assertEqual(specs[0].tournament_mode, "strength")
        self.assertEqual(specs[0].tournament_cut, 4)

    def test_explicit_cut_zero_accepted(self) -> None:
        # ``:0`` is the explicit no-cut value — accepted, parses to cut 0.
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].tournament_cut, 0)

    def test_round_robin_specs_keep_cut_zero_default(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:8", season_schedule_format=_SSF
        )
        self.assertEqual(specs[0].phase_type, "round_robin")
        self.assertEqual(specs[0].tournament_cut, 0)


class TestParseTournamentCutFloor(SimpleTestCase):
    """A parsed ``cut != 0 and cut < 4`` raises the NEW locked floor ValueError
    ``f"tournament cut must be 0 or at least 4: {cut}"`` (cut is the parsed int,
    no ``!r``)."""

    def test_cut_1_raises_floor(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:1", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "tournament cut must be 0 or at least 4: 1"
        )

    def test_cut_2_raises_floor(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:2", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "tournament cut must be 0 or at least 4: 2"
        )

    def test_cut_3_raises_floor(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:3", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "tournament cut must be 0 or at least 4: 3"
        )

    def test_negative_cut_raises_floor(self) -> None:
        # A parseable negative int is != 0 and < 4 ⇒ the floor string (negative
        # is an int, so it does NOT take the non-int malformed path).
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:-1", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "tournament cut must be 0 or at least 4: -1"
        )

    def test_cut_4_accepted_at_floor(self) -> None:
        # 4 is the minimum non-zero cut — accepted, no raise.
        specs = parse_phase_composition(
            "round_robin,tournament:standings:4", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].tournament_cut, 4)


class TestParseTournamentCutMalformed(SimpleTestCase):
    """A non-int cut, an empty cut field, or ``len(parts) > 3`` raises the
    EXISTING ``"malformed phase composition"`` (NOT the new floor string)."""

    def test_non_int_cut_raises_malformed(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:abc", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_empty_cut_field_raises_malformed(self) -> None:
        # A trailing colon with no cut (e.g. ``tournament:standings:``) ⇒ the
        # empty cut field is malformed (NOT cut 0).
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_overlong_tournament_token_raises_malformed(self) -> None:
        # LG-02-Part2c-3e moved the malformed boundary: the tournament token is
        # now positional 11-field (``tournament:mode:cut:format:fsl:ssl:qsl:esl
        # :wb:lb:swiss``), so ``len(parts) > 11`` ⇒ malformed. The 4th field is
        # now the format, no longer the malformed trigger (see
        # TestParseFullSubConfig / TestParseFullSubConfigMalformed).
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:8:single_elimination"
                ":1:1:1:1:0:0:0:extra",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_float_cut_raises_malformed(self) -> None:
        # ``"4.0"`` does not parse as ``int`` ⇒ malformed (not the floor).
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:4.0", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")


class TestParseRoundRobinThirdFieldRejected(SimpleTestCase):
    """The ``round_robin`` branch grammar is UNCHANGED — it keeps its 2-way
    ``partition(":")`` split, so a 3rd field is folded into ``format_part`` and
    rejected as an unknown schedule_format (the cut grammar is tournament-only).
    A round_robin token never silently accepts a cut field."""

    def test_round_robin_three_fields_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin:single_round_robin:8", season_schedule_format=_SSF
            )
        # The RR branch's 2-way partition leaves ``single_round_robin:8`` as the
        # format_part ⇒ the existing unknown-schedule-format ValueError. The
        # load-bearing point: a cut field on a round_robin token is rejected, NOT
        # silently parsed (only the tournament branch grew the cut dimension).
        self.assertEqual(
            str(ctx.exception), "unknown schedule_format: 'single_round_robin:8'"
        )


class TestParseTournamentCutBackCompat(SimpleTestCase):
    """Every c-3c serialized value parses unchanged under the cut grammar — bare
    ``tournament`` ⇒ cut 0, ``tournament:strength`` ⇒ cut 0."""

    def test_bare_tournament_parses_to_standings_cut_zero(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        self.assertEqual(specs[1].tournament_mode, "standings")
        self.assertEqual(specs[1].tournament_cut, 0)

    def test_tournament_strength_parses_to_strength_cut_zero(self) -> None:
        specs = parse_phase_composition(
            "tournament:strength,round_robin", season_schedule_format=_SSF
        )
        self.assertEqual(specs[0].tournament_mode, "strength")
        self.assertEqual(specs[0].tournament_cut, 0)

    def test_unknown_mode_still_raises_unknown_tournament_mode(self) -> None:
        # The mode-membership check still fires (verbatim) even with a cut field.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:bogus:8", season_schedule_format=_SSF
            )
        self.assertEqual(str(ctx.exception), "unknown tournament_mode: 'bogus'")


# ===========================================================================
# LG-02-Part2c-3e — full per-format tournament sub-config wire grammar
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3e-seam-contract.md`` §2 / §3:
# the tournament wire token grows from ``tournament:mode:cut`` to the full
# positional, trailing-optional grammar
#
#     tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss
#
# (parsed via ``split(":")`` on the tournament branch — the round_robin branch
# keeps its 2-way ``partition(":")`` grammar). ``PhaseSpec`` gains 8 new trailing
# defaulted fields: ``tournament_format="single_elimination"``,
# ``final_series_length=1``, ``semifinal_series_length=1``,
# ``quarterfinal_series_length=1``, ``earlier_series_length=1``,
# ``wb_advancers=0``, ``lb_advancers=0``, ``swiss_rounds=0``. Validation order on a
# tournament token (locked):
#   len(parts) > 11 ⇒ "malformed phase composition"
#   → mode (unknown tournament_mode)
#   → cut (tournament cut must be 0 or at least 4: {cut})
#   → format ⇒ f"unknown tournament_format: {fmt!r}"
#   → series tiers ∈ {1,3,5} ⇒ f"series length must be 1, 3, or 5: {n}"
#   → wb/lb combo (RR→DE ONLY) vs {(4,0),(4,2),(8,0),(8,4),(16,0),(16,8)}
#       ⇒ f"invalid wb/lb combo for round_robin_double_elim: {wb}/{lb}"
#   → swiss.
# Non-int / empty field ⇒ "malformed phase composition". Every c-3d/c-3c
# serialized token still parses identically (format→single_elimination, tiers→1,
# wb/lb/swiss→0).
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the 11-field tournament grammar + the 8 new
# PhaseSpec fields + the new ValueErrors — the TDD red state, not a defect here.

# The 5 valid tournament_format values (TOURNAMENT_FORMAT_CHOICES).
_LG3E_FORMATS = (
    "single_elimination",
    "double_elimination",
    "round_robin",
    "round_robin_double_elim",
    "swiss",
)


class TestPhaseSpecSubConfigDefaults(SimpleTestCase):
    """``PhaseSpec`` gains 8 trailing defaulted fields; existing keyword
    constructions stay equality-identical (the trailing-default precedent)."""

    def test_tournament_format_defaults_single_elimination(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.tournament_format, "single_elimination")

    def test_series_length_fields_default_one(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.final_series_length, 1)
        self.assertEqual(spec.semifinal_series_length, 1)
        self.assertEqual(spec.quarterfinal_series_length, 1)
        self.assertEqual(spec.earlier_series_length, 1)

    def test_advancer_and_swiss_fields_default_zero(self) -> None:
        spec = PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=_SSF)
        self.assertEqual(spec.wb_advancers, 0)
        self.assertEqual(spec.lb_advancers, 0)
        self.assertEqual(spec.swiss_rounds, 0)

    def test_explicit_sub_config_is_carried(self) -> None:
        spec = PhaseSpec(
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=0,
            tournament_format="double_elimination",
            final_series_length=5,
            semifinal_series_length=3,
            quarterfinal_series_length=3,
            earlier_series_length=1,
            wb_advancers=8,
            lb_advancers=4,
            swiss_rounds=6,
        )
        self.assertEqual(spec.tournament_format, "double_elimination")
        self.assertEqual(spec.final_series_length, 5)
        self.assertEqual(spec.semifinal_series_length, 3)
        self.assertEqual(spec.quarterfinal_series_length, 3)
        self.assertEqual(spec.earlier_series_length, 1)
        self.assertEqual(spec.wb_advancers, 8)
        self.assertEqual(spec.lb_advancers, 4)
        self.assertEqual(spec.swiss_rounds, 6)

    def test_pre_c3e_keyword_construction_stays_equality_identical(self) -> None:
        # A pre-c-3e 5-field keyword construction (no sub-config) must equal one
        # with the 8 new fields set to their defaults — the trailing-default
        # precedent that keeps every prior PhaseSpec build valid.
        a = PhaseSpec(
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=0,
        )
        b = PhaseSpec(
            ordinal=2,
            phase_type="tournament",
            schedule_format=None,
            tournament_mode="standings",
            tournament_cut=0,
            tournament_format="single_elimination",
            final_series_length=1,
            semifinal_series_length=1,
            quarterfinal_series_length=1,
            earlier_series_length=1,
            wb_advancers=0,
            lb_advancers=0,
            swiss_rounds=0,
        )
        self.assertEqual(a, b)


class TestParseFullSubConfig(SimpleTestCase):
    """The full 11-field tournament token parses each sub-config field."""

    def test_double_elimination_full_token(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:double_elimination:3:3:1:1:0:0:0",
            season_schedule_format=_SSF,
        )
        self.assertEqual(len(specs), 2)
        t = specs[1]
        self.assertEqual(t.phase_type, "tournament")
        self.assertEqual(t.tournament_mode, "standings")
        self.assertEqual(t.tournament_cut, 0)
        self.assertEqual(t.tournament_format, "double_elimination")
        self.assertEqual(t.final_series_length, 3)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_round_robin_double_elim_valid_combo(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:round_robin_double_elim:1:1:1:1:8:4:0",
            season_schedule_format=_SSF,
        )
        t = specs[1]
        self.assertEqual(t.tournament_format, "round_robin_double_elim")
        self.assertEqual(t.wb_advancers, 8)
        self.assertEqual(t.lb_advancers, 4)

    def test_swiss_rounds_field(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:swiss:1:1:1:1:0:0:6",
            season_schedule_format=_SSF,
        )
        t = specs[1]
        self.assertEqual(t.tournament_format, "swiss")
        self.assertEqual(t.swiss_rounds, 6)

    def test_single_elimination_explicit_full_token(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:single_elimination:1:1:1:1:0:0:0",
            season_schedule_format=_SSF,
        )
        t = specs[1]
        self.assertEqual(t.tournament_format, "single_elimination")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_round_robin_format_token(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:round_robin:1:1:1:1:0:0:0",
            season_schedule_format=_SSF,
        )
        self.assertEqual(specs[1].tournament_format, "round_robin")

    def test_mixed_series_tiers(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:single_elimination:5:3:1:1:0:0:0",
            season_schedule_format=_SSF,
        )
        t = specs[1]
        self.assertEqual(t.final_series_length, 5)
        self.assertEqual(t.semifinal_series_length, 3)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)


class TestParseUnknownTournamentFormat(SimpleTestCase):
    """An unknown ``tournament_format`` raises the NEW locked ValueError."""

    def test_bogus_format_raises_with_repr(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:bogus:1:1:1:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "unknown tournament_format: 'bogus'")

    def test_member_night_format_token_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:member_night:1:1:1:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(
            str(ctx.exception), "unknown tournament_format: 'member_night'"
        )

    def test_all_five_formats_accepted(self) -> None:
        for fmt in _LG3E_FORMATS:
            # round_robin_double_elim needs a valid combo; supply 4/2.
            wb, lb = (4, 2) if fmt == "round_robin_double_elim" else (0, 0)
            token = f"round_robin,tournament:standings:0:{fmt}:1:1:1:1:{wb}:{lb}:0"
            specs = parse_phase_composition(token, season_schedule_format=_SSF)
            self.assertEqual(specs[1].tournament_format, fmt)


class TestParseSeriesTierFloor(SimpleTestCase):
    """A series tier not in ``{1, 3, 5}`` raises the locked series ValueError
    ``f"series length must be 1, 3, or 5: {n}"`` (the parsed int, no ``!r``)."""

    def test_final_series_2_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:2:1:1:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "series length must be 1, 3, or 5: 2")

    def test_semifinal_series_4_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:1:4:1:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "series length must be 1, 3, or 5: 4")

    def test_quarterfinal_series_0_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:1:1:0:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "series length must be 1, 3, or 5: 0")

    def test_earlier_series_6_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:1:1:1:6:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "series length must be 1, 3, or 5: 6")

    def test_tiers_1_3_5_all_accepted(self) -> None:
        for n in (1, 3, 5):
            token = (
                f"round_robin,tournament:standings:0:single_elimination:"
                f"{n}:{n}:{n}:{n}:0:0:0"
            )
            specs = parse_phase_composition(token, season_schedule_format=_SSF)
            self.assertEqual(specs[1].final_series_length, n)
            self.assertEqual(specs[1].earlier_series_length, n)


class TestParseWbLbCombo(SimpleTestCase):
    """The wb/lb combo is validated ONLY for ``round_robin_double_elim`` against
    ``{(4,0),(4,2),(8,0),(8,4),(16,0),(16,8)}``."""

    def test_invalid_combo_for_rr_de_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:round_robin_double_elim:1:1:1:1:8:2:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(
            str(ctx.exception),
            "invalid wb/lb combo for round_robin_double_elim: 8/2",
        )

    def test_each_valid_combo_accepted(self) -> None:
        for wb, lb in ((4, 0), (4, 2), (8, 0), (8, 4), (16, 0), (16, 8)):
            token = (
                f"round_robin,tournament:standings:0:round_robin_double_elim:"
                f"1:1:1:1:{wb}:{lb}:0"
            )
            specs = parse_phase_composition(token, season_schedule_format=_SSF)
            self.assertEqual(specs[1].wb_advancers, wb)
            self.assertEqual(specs[1].lb_advancers, lb)

    def test_combo_not_checked_for_non_rr_de_format(self) -> None:
        # A NON-RR→DE format with wb=8/lb=2 does NOT raise the combo error — the
        # combo is only validated for round_robin_double_elim. (The wb/lb fields
        # are still parsed and carried, just not combo-validated.)
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:double_elimination:1:1:1:1:8:2:0",
            season_schedule_format=_SSF,
        )
        self.assertEqual(specs[1].tournament_format, "double_elimination")
        self.assertEqual(specs[1].wb_advancers, 8)
        self.assertEqual(specs[1].lb_advancers, 2)

    def test_single_elim_with_nonzero_advancers_not_combo_checked(self) -> None:
        # single_elimination with a stray wb/lb pair is not combo-validated.
        specs = parse_phase_composition(
            "round_robin,tournament:standings:0:single_elimination:1:1:1:1:8:2:0",
            season_schedule_format=_SSF,
        )
        self.assertEqual(specs[1].wb_advancers, 8)
        self.assertEqual(specs[1].lb_advancers, 2)


class TestParseFullSubConfigMalformed(SimpleTestCase):
    """``len(parts) > 11`` and a non-int / empty sub-config field raise the
    EXISTING ``"malformed phase composition"`` (NOT a sub-config ValueError)."""

    def test_twelve_field_token_raises_malformed(self) -> None:
        # len(parts) > 11 ⇒ malformed.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:1:1:1:1:0:0:0:x",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_non_int_series_field_raises_malformed(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:single_elimination:abc:1:1:1:0:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_empty_swiss_field_raises_malformed(self) -> None:
        # A trailing colon with no swiss value ⇒ empty field is malformed.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:swiss:1:1:1:1:0:0:",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")

    def test_non_int_wb_field_raises_malformed(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:0:double_elimination:1:1:1:1:x:0:0",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "malformed phase composition")


class TestParseFullSubConfigBackCompat(SimpleTestCase):
    """Every c-3d/c-3c serialized token still parses identically — sub-config
    fields take their defaults (format→single_elimination, tiers→1,
    wb/lb/swiss→0)."""

    def test_bare_tournament_defaults(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament", season_schedule_format=_SSF
        )
        t = specs[1]
        self.assertEqual(t.tournament_mode, "standings")
        self.assertEqual(t.tournament_cut, 0)
        self.assertEqual(t.tournament_format, "single_elimination")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.semifinal_series_length, 1)
        self.assertEqual(t.quarterfinal_series_length, 1)
        self.assertEqual(t.earlier_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_c3c_strength_token_defaults(self) -> None:
        specs = parse_phase_composition(
            "tournament:strength,round_robin", season_schedule_format=_SSF
        )
        t = specs[0]
        self.assertEqual(t.tournament_mode, "strength")
        self.assertEqual(t.tournament_cut, 0)
        self.assertEqual(t.tournament_format, "single_elimination")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.swiss_rounds, 0)

    def test_c3d_standings_cut_token_defaults(self) -> None:
        specs = parse_phase_composition(
            "round_robin,tournament:standings:8", season_schedule_format=_SSF
        )
        t = specs[1]
        self.assertEqual(t.tournament_mode, "standings")
        self.assertEqual(t.tournament_cut, 8)
        self.assertEqual(t.tournament_format, "single_elimination")
        self.assertEqual(t.final_series_length, 1)
        self.assertEqual(t.wb_advancers, 0)
        self.assertEqual(t.lb_advancers, 0)
        self.assertEqual(t.swiss_rounds, 0)

    def test_c3d_cut_floor_still_fires_under_full_grammar(self) -> None:
        # The c-3d cut floor still applies before the new format/tier checks.
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:standings:2", season_schedule_format=_SSF
            )
        self.assertEqual(
            str(ctx.exception), "tournament cut must be 0 or at least 4: 2"
        )

    def test_unknown_mode_still_fires_before_format(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_phase_composition(
                "round_robin,tournament:bogus:0:swiss:1:1:1:1:0:0:6",
                season_schedule_format=_SSF,
            )
        self.assertEqual(str(ctx.exception), "unknown tournament_mode: 'bogus'")
