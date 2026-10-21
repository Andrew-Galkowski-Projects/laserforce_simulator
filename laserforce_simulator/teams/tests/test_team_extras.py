"""UXP-19 -- the ``money_k`` template filter (``teams/templatetags/team_extras.py``).

Finance amounts are stored in thousands of dollars; the filter renders them as
``$7.2k`` with the sign *before* the currency symbol (``-$7.2k``, never ``$-7.2k``).
"""

from __future__ import annotations

from django.template import Context, Template
from django.test import SimpleTestCase

from teams.templatetags.team_extras import money_k


class TestMoneyK(SimpleTestCase):
    def test_positive(self) -> None:
        self.assertEqual(money_k(7.2), "$7.2k")

    def test_zero(self) -> None:
        self.assertEqual(money_k(0), "$0.0k")

    def test_negative_puts_sign_before_currency(self) -> None:
        self.assertEqual(money_k(-7.2), "-$7.2k")

    def test_rounds_half_up_to_one_decimal(self) -> None:
        self.assertEqual(money_k(1.25), "$1.3k")
        self.assertEqual(money_k(-1.25), "-$1.3k")

    def test_negative_that_rounds_to_zero_has_no_sign(self) -> None:
        self.assertEqual(money_k(-0.04), "$0.0k")

    def test_large_amounts_keep_thousands_unit(self) -> None:
        self.assertEqual(money_k(12345.67), "$12345.7k")
        self.assertEqual(money_k(-2500000), "-$2500000.0k")

    def test_non_numeric_passes_through_as_empty(self) -> None:
        self.assertEqual(money_k(None), "")
        self.assertEqual(money_k("abc"), "")

    def test_usable_from_template(self) -> None:
        rendered = Template("{% load team_extras %}{{ cash|money_k }}").render(
            Context({"cash": -7.2})
        )
        self.assertEqual(rendered, "-$7.2k")
