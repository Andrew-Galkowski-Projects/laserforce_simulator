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
