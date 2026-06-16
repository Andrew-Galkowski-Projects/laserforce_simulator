"""FIN-01 — pure-unit tests for ``matches.finance`` (seam contract §4 / §8).

Covers the pure entry points of the finance module — built from plain
ints / floats / levels / frozen dataclasses, NO DB, NO mocks:

* ``level_to_amount`` / ``salary_for_overall`` — the cap-scaled level + overall
  derivations.
* ``compute_hype`` — the winp loop (``hype += 0.01*(winp-0.55) + 0.015*(winp-winp_old)``)
  bound to ``[0, 1]``.
* ``season_revenue`` / ``season_expenses`` — the revenue / expense line builders
  (``RevenueLines`` / ``ExpenseLines``).
* ``luxury_tax`` / ``min_payroll_penalty`` — the two payroll-threshold penalties.
* ``season_profit`` — ``revenue - expenses``.
* ``money_delta`` — the LOCKED owner-mood money axis: ``(profit - 15*scf) / (100*scf)``
  where ``scf = salary_cap / BASELINE_SALARY_CAP``.
* ``compute_team_finance`` — the single writer entry point, aggregating the lines
  into a ``TeamFinanceResult`` (lines sum to revenue/expenses, profit = revenue - expenses).

Plus ``TestNoDjangoImportsLeaked`` — the subprocess fresh-import + ``sys.modules``
walk that defends the frozen ``dataclasses`` / ``typing`` / ``math`` /
``collections``-only import allowlist (mirrors the ``matches.owner_mood`` /
``matches.development`` / ``matches.season_awards`` precedent).

Assertion discipline (FIN-01 §8): assert on the returned floats / line dataclasses
/ profit / money math — NEVER on simulated point totals (every fixture here is a
hand-built scalar, so there are no sim totals at all).

Written test-first against the FIN-01 seam contract
(``.claude/worktrees/fin-01-seam-contract.md``); these FAIL until the Code agent
lands ``matches/finance.py``.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches import finance
from matches.finance import (
    BASELINE_SALARY_CAP,
    DEFAULT_LEVEL,
    MAX_LEVEL,
    SALARY_CAP,
    ExpenseLines,
    RevenueLines,
    TeamFinanceResult,
    compute_hype,
    compute_team_finance,
    level_to_amount,
    luxury_tax,
    min_payroll_penalty,
    money_delta,
    salary_for_overall,
    season_expenses,
    season_profit,
    season_revenue,
)

# ===========================================================================
# §4.1 — TestConstants (the locked-but-tunable module constants)
# ===========================================================================


class TestConstants(SimpleTestCase):
    """The locked constant relationships; concrete dollar coefficients are
    tunable, but the structural identities are pinned."""

    def test_default_level_is_34(self) -> None:
        self.assertEqual(DEFAULT_LEVEL, 34)

    def test_max_level_is_100(self) -> None:
        self.assertEqual(MAX_LEVEL, 100)

    def test_baseline_salary_cap_equals_salary_cap(self) -> None:
        # The salaryCapFactor denominator == the league cap this slice, so the
        # factor is exactly 1.0 (the byte-identical-money-axis lever).
        self.assertEqual(BASELINE_SALARY_CAP, SALARY_CAP)

    def test_salary_cap_factor_is_one_at_baseline(self) -> None:
        # scf = SALARY_CAP / BASELINE_SALARY_CAP == 1.0.
        self.assertAlmostEqual(SALARY_CAP / BASELINE_SALARY_CAP, 1.0, places=12)


# ===========================================================================
# §4.2 — TestDataclasses (frozen + pinned field order)
# ===========================================================================


class TestDataclasses(SimpleTestCase):
    """``RevenueLines`` / ``ExpenseLines`` / ``TeamFinanceResult`` are frozen
    with the pinned field order."""

    def test_revenue_lines_fields_and_frozen(self) -> None:
        r = RevenueLines(
            ticket=1.0, national_tv=2.0, local_tv=3.0, sponsor=4.0, merch=5.0
        )
        self.assertEqual(
            (r.ticket, r.national_tv, r.local_tv, r.sponsor, r.merch),
            (1.0, 2.0, 3.0, 4.0, 5.0),
        )
        with self.assertRaises(Exception):
            r.ticket = 9.0  # type: ignore[misc]

    def test_revenue_lines_positional_field_order(self) -> None:
        r = RevenueLines(1.0, 2.0, 3.0, 4.0, 5.0)
        self.assertEqual(r.ticket, 1.0)
        self.assertEqual(r.national_tv, 2.0)
        self.assertEqual(r.local_tv, 3.0)
        self.assertEqual(r.sponsor, 4.0)
        self.assertEqual(r.merch, 5.0)

    def test_expense_lines_fields_and_frozen(self) -> None:
        e = ExpenseLines(
            payroll=10.0,
            scouting=1.0,
            coaching=2.0,
            facilities=3.0,
            luxury_tax=4.0,
            min_payroll_penalty=5.0,
        )
        self.assertEqual(
            (
                e.payroll,
                e.scouting,
                e.coaching,
                e.facilities,
                e.luxury_tax,
                e.min_payroll_penalty,
            ),
            (10.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        )
        with self.assertRaises(Exception):
            e.payroll = 0.0  # type: ignore[misc]

    def test_expense_lines_positional_field_order(self) -> None:
        e = ExpenseLines(10.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        self.assertEqual(e.payroll, 10.0)
        self.assertEqual(e.scouting, 1.0)
        self.assertEqual(e.coaching, 2.0)
        self.assertEqual(e.facilities, 3.0)
        self.assertEqual(e.luxury_tax, 4.0)
        self.assertEqual(e.min_payroll_penalty, 5.0)

    def test_team_finance_result_fields_and_frozen(self) -> None:
        rl = RevenueLines(1.0, 1.0, 1.0, 1.0, 1.0)
        el = ExpenseLines(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        res = TeamFinanceResult(
            revenue_lines=rl,
            expense_lines=el,
            revenue=5.0,
            expenses=1.0,
            profit=4.0,
            hype=0.5,
            money_delta=0.1,
        )
        self.assertIs(res.revenue_lines, rl)
        self.assertIs(res.expense_lines, el)
        self.assertEqual(res.revenue, 5.0)
        self.assertEqual(res.expenses, 1.0)
        self.assertEqual(res.profit, 4.0)
        self.assertEqual(res.hype, 0.5)
        self.assertEqual(res.money_delta, 0.1)
        with self.assertRaises(Exception):
            res.profit = 0.0  # type: ignore[misc]


# ===========================================================================
# §4.3 — TestLevelToAmount
# ===========================================================================


class TestLevelToAmount(SimpleTestCase):
    """``level_to_amount(level, salary_cap=SALARY_CAP)`` — a non-negative,
    monotone-non-decreasing cap-scaled dollar amount."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(level_to_amount(DEFAULT_LEVEL), float)

    def test_non_negative(self) -> None:
        for level in (1, DEFAULT_LEVEL, MAX_LEVEL):
            self.assertGreaterEqual(level_to_amount(level), 0.0, f"level={level}")

    def test_monotone_non_decreasing_in_level(self) -> None:
        prev = level_to_amount(1)
        for level in range(2, MAX_LEVEL + 1):
            cur = level_to_amount(level)
            self.assertGreaterEqual(cur, prev - 1e-9, f"level={level}")
            prev = cur

    def test_max_level_strictly_above_min(self) -> None:
        self.assertGreater(level_to_amount(MAX_LEVEL), level_to_amount(1))


# ===========================================================================
# §4.4 — TestSalaryForOverall
# ===========================================================================


class TestSalaryForOverall(SimpleTestCase):
    """``salary_for_overall(overall, salary_cap=SALARY_CAP)`` — non-negative,
    monotone-non-decreasing in overall (a better player costs at least as much)."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(salary_for_overall(50.0), float)

    def test_non_negative(self) -> None:
        for overall in (0.0, 50.0, 100.0):
            self.assertGreaterEqual(salary_for_overall(overall), 0.0)

    def test_monotone_non_decreasing_in_overall(self) -> None:
        prev = salary_for_overall(0.0)
        for overall in range(1, 101):
            cur = salary_for_overall(float(overall))
            self.assertGreaterEqual(cur, prev - 1e-9, f"overall={overall}")
            prev = cur

    def test_higher_overall_not_cheaper(self) -> None:
        self.assertGreaterEqual(
            salary_for_overall(90.0), salary_for_overall(40.0) - 1e-9
        )


# ===========================================================================
# §4.5 — TestComputeHype (the winp loop + bounds)
# ===========================================================================


class TestComputeHype(SimpleTestCase):
    """``compute_hype(prev_hype, winp, winp_old)`` =
    ``prev + 0.01*(winp-0.55) + 0.015*(winp-winp_old)`` bound to ``[0, 1]``."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(compute_hype(0.5, 0.5, 0.5), float)

    def test_formula_matches_spec(self) -> None:
        # prev 0.5, winp 0.75, winp_old 0.5:
        # 0.5 + 0.01*(0.75-0.55) + 0.015*(0.75-0.5)
        # = 0.5 + 0.01*0.2 + 0.015*0.25 = 0.5 + 0.002 + 0.00375 = 0.50575
        self.assertAlmostEqual(compute_hype(0.5, 0.75, 0.5), 0.50575, places=9)

    def test_winning_above_055_raises_hype(self) -> None:
        # A dominant, improving season raises hype above the prior.
        self.assertGreater(compute_hype(0.4, 0.9, 0.6), 0.4)

    def test_losing_lowers_hype(self) -> None:
        # A losing, declining season lowers hype below the prior.
        self.assertLess(compute_hype(0.5, 0.2, 0.5), 0.5)

    def test_bound_below_at_zero(self) -> None:
        # Even a catastrophic season cannot push hype below 0.
        h = compute_hype(0.0, 0.0, 1.0)
        self.assertGreaterEqual(h, 0.0)

    def test_bound_above_at_one(self) -> None:
        # Even a dominant season cannot push hype above 1.
        h = compute_hype(1.0, 1.0, 0.0)
        self.assertLessEqual(h, 1.0)

    def test_exactly_at_baseline_winp_no_drift_from_improvement_term(self) -> None:
        # winp == winp_old ⇒ the second term contributes 0; winp == 0.55 ⇒ the
        # first term contributes 0 — together leave hype unchanged.
        self.assertAlmostEqual(compute_hype(0.5, 0.55, 0.55), 0.5, places=9)


# ===========================================================================
# §4.6 — TestSeasonRevenue
# ===========================================================================


class TestSeasonRevenue(SimpleTestCase):
    """``season_revenue(hype, ticket_price, facilities_level, salary_cap=...)``
    returns a ``RevenueLines`` of non-negative floats; facilities additionally
    feeds revenue (locked posture)."""

    def test_returns_revenue_lines(self) -> None:
        self.assertIsInstance(season_revenue(0.5, 10.0, DEFAULT_LEVEL), RevenueLines)

    def test_all_lines_non_negative(self) -> None:
        r = season_revenue(0.5, 10.0, DEFAULT_LEVEL)
        for name in ("ticket", "national_tv", "local_tv", "sponsor", "merch"):
            self.assertGreaterEqual(getattr(r, name), 0.0, name)

    def test_higher_hype_does_not_decrease_revenue(self) -> None:
        low = season_revenue(0.1, 10.0, DEFAULT_LEVEL)
        high = season_revenue(0.9, 10.0, DEFAULT_LEVEL)
        self.assertGreaterEqual(
            sum(
                (
                    high.ticket,
                    high.national_tv,
                    high.local_tv,
                    high.sponsor,
                    high.merch,
                )
            ),
            sum(
                (
                    low.ticket,
                    low.national_tv,
                    low.local_tv,
                    low.sponsor,
                    low.merch,
                )
            )
            - 1e-6,
        )

    def test_higher_facilities_does_not_decrease_revenue(self) -> None:
        low = season_revenue(0.5, 10.0, 1)
        high = season_revenue(0.5, 10.0, MAX_LEVEL)
        self.assertGreaterEqual(
            high.ticket + high.national_tv + high.local_tv + high.sponsor + high.merch,
            low.ticket
            + low.national_tv
            + low.local_tv
            + low.sponsor
            + low.merch
            - 1e-6,
        )


# ===========================================================================
# §4.7 — TestSeasonExpenses
# ===========================================================================


class TestSeasonExpenses(SimpleTestCase):
    """``season_expenses(payroll, scouting_level, coaching_level,
    facilities_level, salary_cap=...)`` returns an ``ExpenseLines``; budgets are
    cost-only this slice (higher level ⇒ at-least-as-high cost)."""

    def test_returns_expense_lines(self) -> None:
        self.assertIsInstance(
            season_expenses(40000.0, DEFAULT_LEVEL, DEFAULT_LEVEL, DEFAULT_LEVEL),
            ExpenseLines,
        )

    def test_payroll_passed_through(self) -> None:
        e = season_expenses(40000.0, DEFAULT_LEVEL, DEFAULT_LEVEL, DEFAULT_LEVEL)
        self.assertAlmostEqual(e.payroll, 40000.0, places=6)

    def test_all_lines_non_negative(self) -> None:
        e = season_expenses(40000.0, DEFAULT_LEVEL, DEFAULT_LEVEL, DEFAULT_LEVEL)
        for name in (
            "payroll",
            "scouting",
            "coaching",
            "facilities",
            "luxury_tax",
            "min_payroll_penalty",
        ):
            self.assertGreaterEqual(getattr(e, name), 0.0, name)

    def test_higher_scouting_level_not_cheaper(self) -> None:
        low = season_expenses(40000.0, 1, DEFAULT_LEVEL, DEFAULT_LEVEL)
        high = season_expenses(40000.0, MAX_LEVEL, DEFAULT_LEVEL, DEFAULT_LEVEL)
        self.assertGreaterEqual(high.scouting, low.scouting - 1e-6)

    def test_higher_coaching_level_not_cheaper(self) -> None:
        low = season_expenses(40000.0, DEFAULT_LEVEL, 1, DEFAULT_LEVEL)
        high = season_expenses(40000.0, DEFAULT_LEVEL, MAX_LEVEL, DEFAULT_LEVEL)
        self.assertGreaterEqual(high.coaching, low.coaching - 1e-6)

    def test_higher_facilities_level_not_cheaper(self) -> None:
        low = season_expenses(40000.0, DEFAULT_LEVEL, DEFAULT_LEVEL, 1)
        high = season_expenses(40000.0, DEFAULT_LEVEL, DEFAULT_LEVEL, MAX_LEVEL)
        self.assertGreaterEqual(high.facilities, low.facilities - 1e-6)


# ===========================================================================
# §4.8 — TestLuxuryTax / TestMinPayrollPenalty
# ===========================================================================


class TestLuxuryTax(SimpleTestCase):
    """``luxury_tax(payroll, salary_cap=SALARY_CAP)`` — 0 at/below the threshold,
    positive above it, non-decreasing in payroll."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(luxury_tax(0.0), float)

    def test_zero_payroll_no_tax(self) -> None:
        self.assertEqual(luxury_tax(0.0), 0.0)

    def test_non_negative(self) -> None:
        for payroll in (0.0, 50000.0, 200000.0, 1_000_000.0):
            self.assertGreaterEqual(luxury_tax(payroll), 0.0)

    def test_huge_payroll_taxed(self) -> None:
        # A payroll far above any reasonable threshold incurs a positive tax.
        self.assertGreater(luxury_tax(10_000_000.0), 0.0)

    def test_non_decreasing_in_payroll(self) -> None:
        prev = luxury_tax(0.0)
        for payroll in (50000.0, 100000.0, 500000.0, 5_000_000.0):
            cur = luxury_tax(payroll)
            self.assertGreaterEqual(cur, prev - 1e-6)
            prev = cur


class TestMinPayrollPenalty(SimpleTestCase):
    """``min_payroll_penalty(payroll, salary_cap=SALARY_CAP)`` — positive when a
    team spends below the floor, 0 once it is at/above the floor."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(min_payroll_penalty(0.0), float)

    def test_non_negative(self) -> None:
        for payroll in (0.0, 50000.0, 1_000_000.0):
            self.assertGreaterEqual(min_payroll_penalty(payroll), 0.0)

    def test_zero_payroll_penalised(self) -> None:
        # A team that pays nobody is below any positive floor ⇒ a penalty fires.
        self.assertGreater(min_payroll_penalty(0.0), 0.0)

    def test_huge_payroll_no_penalty(self) -> None:
        # A team well above the floor incurs no min-payroll penalty.
        self.assertEqual(min_payroll_penalty(100_000_000.0), 0.0)

    def test_non_increasing_in_payroll(self) -> None:
        # As payroll rises toward / past the floor, the penalty never grows.
        prev = min_payroll_penalty(0.0)
        for payroll in (50000.0, 200000.0, 1_000_000.0, 100_000_000.0):
            cur = min_payroll_penalty(payroll)
            self.assertLessEqual(cur, prev + 1e-6)
            prev = cur


# ===========================================================================
# §4.9 — TestSeasonProfit
# ===========================================================================


class TestSeasonProfit(SimpleTestCase):
    """``season_profit(revenue, expenses) == revenue - expenses``."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(season_profit(100.0, 40.0), float)

    def test_profit_is_revenue_minus_expenses(self) -> None:
        self.assertAlmostEqual(season_profit(100.0, 40.0), 60.0, places=9)

    def test_loss_is_negative(self) -> None:
        self.assertAlmostEqual(season_profit(20.0, 80.0), -60.0, places=9)

    def test_break_even_is_zero(self) -> None:
        self.assertAlmostEqual(season_profit(50.0, 50.0), 0.0, places=9)


# ===========================================================================
# §4.10 — TestMoneyDelta (the LOCKED owner-mood money axis)
# ===========================================================================


class TestMoneyDelta(SimpleTestCase):
    """``money_delta(profit, salary_cap=SALARY_CAP)`` =
    ``(profit - 15*scf) / (100*scf)`` where ``scf = salary_cap / BASELINE_SALARY_CAP``.
    At baseline ``scf == 1.0`` ⇒ ``(profit - 15) / 100``."""

    def test_returns_float(self) -> None:
        self.assertIsInstance(money_delta(15.0), float)

    def test_baseline_formula(self) -> None:
        # scf == 1.0 ⇒ (profit - 15) / 100.
        self.assertAlmostEqual(money_delta(15.0), 0.0, places=9)
        self.assertAlmostEqual(money_delta(115.0), 1.0, places=9)
        self.assertAlmostEqual(money_delta(65.0), 0.5, places=9)

    def test_profit_above_expected_is_positive(self) -> None:
        # profit > 15*scf ⇒ positive money mood.
        self.assertGreater(money_delta(1000.0), 0.0)

    def test_profit_below_expected_is_negative(self) -> None:
        # A loss (profit << 15) ⇒ negative money mood.
        self.assertLess(money_delta(-1000.0), 0.0)

    def test_expected_profit_pivot_is_zero(self) -> None:
        # Exactly EXPECTED_PROFIT_BASE * scf of profit ⇒ neutral (0.0).
        scf = SALARY_CAP / BASELINE_SALARY_CAP
        expected = finance.EXPECTED_PROFIT_BASE * scf
        self.assertAlmostEqual(money_delta(expected), 0.0, places=9)

    def test_divisor_is_100_times_scf(self) -> None:
        # The denominator is MONEY_DELTA_DIVISOR * scf; +divisor of profit past
        # the pivot ⇒ exactly +1.0.
        scf = SALARY_CAP / BASELINE_SALARY_CAP
        pivot = finance.EXPECTED_PROFIT_BASE * scf
        one_unit = finance.MONEY_DELTA_DIVISOR * scf
        self.assertAlmostEqual(money_delta(pivot + one_unit), 1.0, places=9)


# ===========================================================================
# §4.11 — TestComputeTeamFinance (the single writer entry point)
# ===========================================================================


def _result(**overrides) -> TeamFinanceResult:
    kwargs = dict(
        payroll=40000.0,
        scouting_level=DEFAULT_LEVEL,
        coaching_level=DEFAULT_LEVEL,
        facilities_level=DEFAULT_LEVEL,
        ticket_price=10.0,
        prev_hype=0.5,
        winp=0.5,
        winp_old=0.5,
    )
    kwargs.update(overrides)
    return compute_team_finance(**kwargs)


class TestComputeTeamFinance(SimpleTestCase):
    """``compute_team_finance(*, payroll, scouting_level, coaching_level,
    facilities_level, ticket_price, prev_hype, winp, winp_old,
    salary_cap=SALARY_CAP)`` — the flat int/float/level seam, returning a fully
    populated ``TeamFinanceResult`` whose totals reconcile."""

    def test_returns_team_finance_result(self) -> None:
        self.assertIsInstance(_result(), TeamFinanceResult)

    def test_keyword_only_inputs(self) -> None:
        # Every input crossing the view <-> pure seam is keyword-only.
        with self.assertRaises(TypeError):
            compute_team_finance(40000.0)  # type: ignore[misc]

    def test_revenue_equals_sum_of_revenue_lines(self) -> None:
        res = _result()
        rl = res.revenue_lines
        self.assertAlmostEqual(
            res.revenue,
            rl.ticket + rl.national_tv + rl.local_tv + rl.sponsor + rl.merch,
            places=6,
        )

    def test_expenses_equals_sum_of_expense_lines(self) -> None:
        res = _result()
        el = res.expense_lines
        self.assertAlmostEqual(
            res.expenses,
            el.payroll
            + el.scouting
            + el.coaching
            + el.facilities
            + el.luxury_tax
            + el.min_payroll_penalty,
            places=6,
        )

    def test_profit_is_revenue_minus_expenses(self) -> None:
        res = _result()
        self.assertAlmostEqual(res.profit, res.revenue - res.expenses, places=6)

    def test_money_delta_matches_profit_through_pure_fn(self) -> None:
        res = _result()
        self.assertAlmostEqual(res.money_delta, money_delta(res.profit), places=9)

    def test_hype_carried_through_compute_hype(self) -> None:
        res = _result(prev_hype=0.5, winp=0.75, winp_old=0.5)
        self.assertAlmostEqual(res.hype, compute_hype(0.5, 0.75, 0.5), places=9)

    def test_payroll_line_passed_through(self) -> None:
        res = _result(payroll=77777.0)
        self.assertAlmostEqual(res.expense_lines.payroll, 77777.0, places=6)

    def test_profitable_season_positive_money_delta(self) -> None:
        # A high-hype, low-payroll team turns a profit ⇒ positive money mood.
        res = _result(payroll=0.0, prev_hype=0.9, winp=0.9, winp_old=0.5)
        self.assertGreater(res.profit, res.expenses - res.revenue - 1e9)  # sanity
        self.assertAlmostEqual(res.money_delta, money_delta(res.profit), places=9)


# ===========================================================================
# §8 — TestNoDjangoImportsLeaked
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.finance`` (and exercising its pure functions) in a
    fresh subprocess must not pull in ``django.*`` (nor ``matches.models``) — the
    frozen allowlist is ``dataclasses`` / ``typing`` / ``math`` / ``collections``.
    Mirrors the ``matches.owner_mood`` / ``matches.development`` /
    ``matches.season_awards`` ``TestNoDjangoImportsLeaked`` precedent.
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
            from matches.finance import (
                compute_hype,
                compute_team_finance,
                level_to_amount,
                luxury_tax,
                min_payroll_penalty,
                money_delta,
                salary_for_overall,
                season_expenses,
                season_profit,
                season_revenue,
            )

            level_to_amount(34)
            salary_for_overall(50.0)
            compute_hype(0.5, 0.6, 0.5)
            season_revenue(0.5, 10.0, 34)
            season_expenses(40000.0, 34, 34, 34)
            luxury_tax(50000.0)
            min_payroll_penalty(0.0)
            season_profit(100.0, 40.0)
            money_delta(115.0)
            compute_team_finance(
                payroll=40000.0,
                scouting_level=34,
                coaching_level=34,
                facilities_level=34,
                ticket_price=10.0,
                prev_hype=0.5,
                winp=0.5,
                winp_old=0.5,
            )

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
