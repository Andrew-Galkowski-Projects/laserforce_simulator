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
