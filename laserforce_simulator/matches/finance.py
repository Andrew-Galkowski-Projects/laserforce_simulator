"""FIN-01 — team finance economy (pure, deterministic money math).

Django-free module owning the per-(Team, Season) revenue / expense / profit /
hype computation plus the owner-mood money-axis input. Faithful to ZenGM's
basketball finance model (``writeTeamStats`` revenue/expense lines, the
``getLuxuryTaxAmount`` / ``getMinPayrollAmount`` penalties, the hype winp loop,
and the ``updateOwnerMood`` money axis), with these locked simplifications:

* **Cost-only this slice.** The facilities budget additionally feeds revenue;
  scouting / coaching buy NO gameplay edge yet (FIN-02/03) — they are pure
  expense lines.
* **Season-level + deterministic.** No per-game stream, NO RNG / gauss noise.
  A single fixed market (no per-team population), so revenue is a function of
  hype + ticket price + the facilities level only.
* **Money formula (LOCKED, ZenGM-faithful).**
  ``salaryCapFactor = salary_cap / BASELINE_SALARY_CAP``;
  ``expected_profit = EXPECTED_PROFIT_BASE * salaryCapFactor``;
  ``money_delta = (profit − expected_profit) / (MONEY_DELTA_DIVISOR * scf)``.
  ``BASELINE_SALARY_CAP == SALARY_CAP`` so ``salaryCapFactor == 1.0`` for now.

All dollar figures are in **thousands of dollars** (the ZenGM convention), so a
typical mid-table season's ``profit`` lands near ``EXPECTED_PROFIT_BASE = 15``
and the money axis produces meaningful per-season deltas (≈ ±0.05…±0.5), not
pinned at 0.

Frozen import allowlist (the ONLY modules this file may import): ``dataclasses``,
``typing``, ``math``, ``collections``. NO ``django.*`` / ORM / ``random`` /
``datetime`` / I/O / logging — defended by ``TestNoDjangoImportsLeaked``
(subprocess fresh-import + ``sys.modules`` walk), the ``owner_mood.py`` /
``development.py`` / ``season_awards.py`` precedent. The view assembles flat
ints / floats / levels and calls these functions; the module never sees a
``Team`` / ``Season`` / ``League`` / ORM type. ``money_total`` cap-chains
through ``owner_mood.MOOD_FACTOR_CAP`` / ``cap_cumulative`` in the writer — NOT
here.

See ``.claude/worktrees/fin-01-seam-contract.md`` for the locked seam.
"""

from dataclasses import dataclass

# --- Constants (LOCKED-but-tunable magic numbers) --------------------------

MAX_LEVEL = 100
DEFAULT_LEVEL = 34  # neutral level — facility cost mid-range, effect 0
BUDGET_LEVEL_SCALE = 1.1
MAX_COACHING_EFFECT = 0.09  # FIN-02 — max develop-curve scale at MAX_LEVEL

BASELINE_SALARY_CAP = 90000.0  # frozen baseline — salaryCapFactor denominator
SALARY_CAP = 90000.0  # the league cap (== baseline this slice; tunable)

EXPECTED_PROFIT_BASE = 15.0  # expected_profit = 15 * salaryCapFactor
MONEY_DELTA_DIVISOR = 100.0  # money_delta = (profit - expected) / (100 * scf)

# Salary derivation (overall → dollars). Linear cap-fraction: a roster of 6
# players at overall 50 sums to ~SALARY_CAP, so a mid payroll sits near the
# cap and the luxury / min-payroll thresholds bracket it tightly.
SALARY_OVERALL_SCALE = 2.0  # salary = (overall/100) * (cap/6) * SCALE

# Luxury tax / min-payroll thresholds (in thousands; tunable). Bracket a
# typical full-roster payroll (~SALARY_CAP/1000 == 90) so over/under-spending
# is a real expense line.
LUXURY_PAYROLL = 105.0  # luxury-tax threshold (thousands)
LUXURY_TAX_RATE = 1.5
MIN_PAYROLL = 60.0  # min-payroll floor (thousands)

# Revenue coefficients + caps (the doc-02 table, cap-scaled, deterministic).
# Attendance is driven by hype (squared) + ticket price (inverse) + facilities;
# every revenue line scales off that single attendance figure. Tuned so a
# neutral mid-table season (hype ~0.4, ticket ~25, facilities 34) nets revenue
# a touch above a full-roster payroll + neutral budget costs, landing profit
# near EXPECTED_PROFIT_BASE.
BASE_ATTENDANCE = 320.0  # floor draw regardless of hype
HYPE_ATTENDANCE_SCALE = 700.0  # hype² draws up to this many extra (thousands)
TICKET_PRICE_FLOOR = 1.0  # avoid div-by-zero / runaway at price 0
FACILITIES_ATTENDANCE_SCALE = 0.0375  # ±3.75% per the doc-02 facilities band

# Per-line revenue coefficients (× attendance, cap-scaled) and caps. Tuned so a
# neutral mid-table season (hype 0.4, ticket 25, facilities 34, payroll ~90)
# nets revenue a touch above its payroll + neutral-budget costs, landing
# profit near EXPECTED_PROFIT_BASE.
TICKET_COEFF = 0.28
NATIONAL_TV_FLAT = 22.0  # flat (shared equally, not attendance-based)
LOCAL_TV_COEFF = 0.075
LOCAL_TV_CAP = 90.0
SPONSOR_COEFF = 0.055
SPONSOR_CAP = 65.0
MERCH_COEFF = 0.028
MERCH_CAP = 28.0

# Budget cost magnitude (level → thousands). A neutral DEFAULT_LEVEL budget
# costs roughly this much; higher levels cost proportionally more.
BUDGET_COST_BASE = 5.0  # cost at level 1 (thousands)
BUDGET_COST_PER_LEVEL = 0.12  # extra thousands per level above 1

# Hype loop bounds (ZenGM doc-02). hype += 0.01*(winp-0.55) + 0.015*(winp-old).
HYPE_BASELINE_WINP = 0.55
HYPE_WINP_WEIGHT = 0.01
HYPE_IMPROVEMENT_WEIGHT = 0.015
HYPE_MIN = 0.0
HYPE_MAX = 1.0


# --- Dataclasses (frozen, pinned field order) ------------------------------


@dataclass(frozen=True)
class RevenueLines:
    ticket: float
    national_tv: float
    local_tv: float
    sponsor: float
    merch: float


@dataclass(frozen=True)
class ExpenseLines:
    payroll: float
    scouting: float
    coaching: float
    facilities: float
    luxury_tax: float
    min_payroll_penalty: float


@dataclass(frozen=True)
class TeamFinanceResult:
    revenue_lines: RevenueLines
    expense_lines: ExpenseLines
    revenue: float  # sum of revenue_lines
    expenses: float  # sum of expense_lines
    profit: float  # revenue - expenses
    hype: float  # next-season hype (winp loop)
    money_delta: float  # the owner-mood money axis input


# --- Helpers ---------------------------------------------------------------


def _salary_cap_factor(salary_cap: float) -> float:
    """``salary_cap / BASELINE_SALARY_CAP`` (1.0 while cap == baseline)."""
    if BASELINE_SALARY_CAP == 0:
        return 1.0
    return salary_cap / BASELINE_SALARY_CAP


def _bound(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# --- Pure functions --------------------------------------------------------


def coaching_effect(level: int) -> float:
    """FIN-02 — a coaching budget level (1..100) → its develop-curve effect.

    Linear in the level relative to the neutral ``DEFAULT_LEVEL``: the neutral
    level yields 0.0, ``MAX_LEVEL`` yields ``MAX_COACHING_EFFECT`` (0.09), and
    the floor of 1 yields a negative effect (≈ -0.045). The
    ``development.develop_player_stats`` curve multiplies the base age-change by
    ``1 + sign(change) * coaching_effect`` (FIN-02). Mapping lives here, not in
    ``development.py``.
    """
    lvl = _bound(float(level), 1.0, float(MAX_LEVEL))
    return MAX_COACHING_EFFECT * (lvl - DEFAULT_LEVEL) / (MAX_LEVEL - DEFAULT_LEVEL)


def level_to_amount(level: int, salary_cap: float = SALARY_CAP) -> float:
    """A budget level (1..100) → its dollar cost (thousands), cap-scaled.

    Linear in the level above the floor of 1, scaled by the salary-cap factor
    so a given level costs proportionally the same regardless of the cap.
    """
    scf = _salary_cap_factor(salary_cap)
    lvl = _bound(float(level), 1.0, float(MAX_LEVEL))
    return (BUDGET_COST_BASE + BUDGET_COST_PER_LEVEL * (lvl - 1.0)) * scf


def salary_for_overall(overall: float, salary_cap: float = SALARY_CAP) -> float:
    """A Player's ``overall_rating`` → its derived salary (dollars), cap-scaled.

    Linear cap-fraction (NOT a contract): a roster of 6 players at overall 50
    sums to roughly the salary cap. Returned in **thousands of dollars** (the
    module-wide unit), so an overall-50 player ≈ 15 and a full 6-player roster
    ≈ 90 — bracketed by the cap-scaled luxury / min-payroll thresholds. Derived
    fresh by the finance writer at each rollover; never edited via a form.
    """
    cap_k = salary_cap / 1000.0
    return (overall / 100.0) * (cap_k / 6.0) * SALARY_OVERALL_SCALE


def compute_hype(prev_hype: float, winp: float, winp_old: float) -> float:
    """Next-season hype from this season's win%, bound to ``[0, 1]``.

    ``hype += 0.01*(winp - 0.55) + 0.015*(winp - winp_old)`` (ZenGM doc-02):
    the 0.55 anchor means you must win more than half just to hold hype; the
    second term rewards improvement over recent history.
    """
    new_hype = (
        prev_hype
        + HYPE_WINP_WEIGHT * (winp - HYPE_BASELINE_WINP)
        + HYPE_IMPROVEMENT_WEIGHT * (winp - winp_old)
    )
    return _bound(new_hype, HYPE_MIN, HYPE_MAX)


def _attendance(hype: float, ticket_price: float, facilities_level: int) -> float:
    """Deterministic season attendance (thousands) — the revenue driver.

    Hype dominates (squared), ticket price suppresses inversely, facilities
    nudge ±3.75%. No RNG / gauss noise (deterministic this slice).
    """
    base = BASE_ATTENDANCE + HYPE_ATTENDANCE_SCALE * (hype * hype)
    price = max(ticket_price, TICKET_PRICE_FLOOR)
    base *= TICKET_PRICE_FLOOR / price + 1.0
    # facilities factor: 1.0 at DEFAULT_LEVEL, ±FACILITIES_ATTENDANCE_SCALE band.
    lvl = _bound(float(facilities_level), 1.0, float(MAX_LEVEL))
    facilities_factor = 1.0 + FACILITIES_ATTENDANCE_SCALE * (
        (lvl - DEFAULT_LEVEL) / (MAX_LEVEL - DEFAULT_LEVEL)
    )
    return max(0.0, base * facilities_factor)


def season_revenue(
    hype: float,
    ticket_price: float,
    facilities_level: int,
    salary_cap: float = SALARY_CAP,
) -> RevenueLines:
    """The five season revenue lines (thousands), cap-scaled + capped.

    All five scale off the single deterministic attendance figure (national TV
    is a flat shared line). Facilities feeds revenue (the cost-only-except-
    facilities rule). No RNG.
    """
    scf = _salary_cap_factor(salary_cap)
    att = _attendance(hype, ticket_price, facilities_level)

    ticket = TICKET_COEFF * ticket_price * att / 100.0 * scf
    national_tv = NATIONAL_TV_FLAT * scf
    local_tv = min(LOCAL_TV_COEFF * att * scf, LOCAL_TV_CAP * scf)
    sponsor = min(SPONSOR_COEFF * att * scf, SPONSOR_CAP * scf)
    merch = min(MERCH_COEFF * att * scf, MERCH_CAP * scf)

    return RevenueLines(
        ticket=ticket,
        national_tv=national_tv,
        local_tv=local_tv,
        sponsor=sponsor,
        merch=merch,
    )


def luxury_tax(payroll: float, salary_cap: float = SALARY_CAP) -> float:
    """Luxury-tax expense: ``rate * (payroll − luxury_threshold)`` when over.

    A flat marginal rate on the amount over the cap-scaled luxury line; 0 at
    or below it. ``payroll`` is in thousands.
    """
    scf = _salary_cap_factor(salary_cap)
    threshold = LUXURY_PAYROLL * scf
    if payroll > threshold:
        return LUXURY_TAX_RATE * (payroll - threshold)
    return 0.0


def min_payroll_penalty(payroll: float, salary_cap: float = SALARY_CAP) -> float:
    """Min-payroll penalty: the shortfall below the cap-scaled floor, else 0.

    Spending too little on players is charged the shortfall — no benefit to
    tanking payroll below the floor. ``payroll`` is in thousands.
    """
    scf = _salary_cap_factor(salary_cap)
    floor = MIN_PAYROLL * scf
    if payroll < floor:
        return floor - payroll
    return 0.0


def season_expenses(
    payroll: float,
    scouting_level: int,
    coaching_level: int,
    facilities_level: int,
    salary_cap: float = SALARY_CAP,
) -> ExpenseLines:
    """The six season expense lines (thousands), cap-scaled.

    Payroll is the summed active-roster salary; the three budgets convert
    level→dollars via ``level_to_amount``; the two penalties are pure functions
    of payroll. Scouting / coaching are pure cost (no gameplay edge this slice).
    """
    return ExpenseLines(
        payroll=payroll,
        scouting=level_to_amount(scouting_level, salary_cap),
        coaching=level_to_amount(coaching_level, salary_cap),
        facilities=level_to_amount(facilities_level, salary_cap),
        luxury_tax=luxury_tax(payroll, salary_cap),
        min_payroll_penalty=min_payroll_penalty(payroll, salary_cap),
    )


def season_profit(revenue: float, expenses: float) -> float:
    """``revenue - expenses``."""
    return revenue - expenses


def money_delta(profit: float, salary_cap: float = SALARY_CAP) -> float:
    """The owner-mood money axis input (LOCKED formula).

    ``salaryCapFactor = salary_cap / BASELINE_SALARY_CAP``;
    ``expected_profit = EXPECTED_PROFIT_BASE * salaryCapFactor``;
    ``money_delta = (profit − expected_profit) / (MONEY_DELTA_DIVISOR * scf)``.
    Beat the ~15-profit expectation ⇒ positive money mood; miss it ⇒ negative.
    """
    scf = _salary_cap_factor(salary_cap)
    expected = EXPECTED_PROFIT_BASE * scf
    denom = MONEY_DELTA_DIVISOR * scf
    if denom == 0:
        return 0.0
    return (profit - expected) / denom


def compute_team_finance(
    *,
    payroll: float,
    scouting_level: int,
    coaching_level: int,
    facilities_level: int,
    ticket_price: float,
    prev_hype: float,
    winp: float,
    winp_old: float,
    salary_cap: float = SALARY_CAP,
) -> TeamFinanceResult:
    """The single entry the finance writer calls — the full season aggregator.

    Flat int / float / level inputs only (never a Django object). Computes the
    revenue + expense lines, the derived totals (revenue / expenses / profit),
    the carried-forward hype, and the owner-mood ``money_delta``.
    """
    revenue_lines = season_revenue(
        prev_hype, ticket_price, facilities_level, salary_cap
    )
    expense_lines = season_expenses(
        payroll, scouting_level, coaching_level, facilities_level, salary_cap
    )

    revenue = (
        revenue_lines.ticket
        + revenue_lines.national_tv
        + revenue_lines.local_tv
        + revenue_lines.sponsor
        + revenue_lines.merch
    )
    expenses = (
        expense_lines.payroll
        + expense_lines.scouting
        + expense_lines.coaching
        + expense_lines.facilities
        + expense_lines.luxury_tax
        + expense_lines.min_payroll_penalty
    )
    profit = season_profit(revenue, expenses)
    hype = compute_hype(prev_hype, winp, winp_old)
    delta = money_delta(profit, salary_cap)

    return TeamFinanceResult(
        revenue_lines=revenue_lines,
        expense_lines=expense_lines,
        revenue=revenue,
        expenses=expenses,
        profit=profit,
        hype=hype,
        money_delta=delta,
    )
