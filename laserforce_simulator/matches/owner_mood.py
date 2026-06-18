"""CAR-02 — ZenGM-style owner-mood manager firing (pure mood math).

Django-free module owning the per-Season owner-mood delta computation, the
per-factor cumulative cap, and the firing / hot-seat verdict. Faithful to
ZenGM's basketball ``updateOwnerMood`` (wins / playoffs / money deltas, each
capped on the upside at ``+1``) and ``genMessage`` (fired at cumulative ``<= -1``
once past the grace period, with two hot-seat warning projections), with the
``money`` factor DORMANT (always ``0.0`` this slice) and the grace period flat at
2 Seasons (ZenGM's ``+3``-if-joined-at-playoffs nuance dropped).

Frozen import allowlist (the ONLY modules this file may import): ``dataclasses``,
``typing``, ``collections``. NO ``django.*``, NO ORM, NO ``random`` / ``datetime``
/ ``math`` / I/O / logging. The view assembles flat inputs (ints / strings) and
calls these functions; the module never sees a Django object, the ORM, or RNG.
Defended by ``TestNoDjangoImportsLeaked`` (subprocess fresh-import + ``sys.modules``
walk), mirroring ``matches/season_awards.py`` / ``matches/development.py`` /
``matches/standings.py``.

See ``.claude/worktrees/car-02-performance-based-firing-seam-contract.md`` for the
locked seam.
"""

from dataclasses import dataclass

# --- Constants (LOCKED values) ---------------------------------------------

WINS_FACTOR: float = 1.0
WINS_BASELINE_SCALE: float = 0.25
PLAYOFF_TITLE: float = 0.2
PLAYOFF_MISS: float = -0.2
PLAYOFF_ADVANCE_SCALE: float = 0.16
MOOD_FACTOR_CAP: float = 1.0  # per-factor cumulative ceiling (+1; NO negative floor)
FIRE_THRESHOLD: float = -1.0
GRACE_PERIOD_SEASONS: int = 2  # flat — drop ZenGM's +3-if-joined-at-playoffs nuance


# --- Dataclasses (frozen, pinned field order) ------------------------------


@dataclass(frozen=True)
class MoodDeltas:
    """Per-Season factor deltas (pre-cap, this Season only)."""

    wins: float
    playoffs: float
    money: float  # DORMANT — always 0.0 this slice


@dataclass(frozen=True)
class MoodTotals:
    """Per-factor cumulative totals, capped at ``MOOD_FACTOR_CAP`` on the upside."""

    wins: float  # cumulative, per-factor capped at MOOD_FACTOR_CAP
    playoffs: float
    money: float  # DORMANT — always 0.0 this slice


@dataclass(frozen=True)
class Verdict:
    """The owner's annual verdict + the hot-seat warning level."""

    outcome: str  # "retained" | "hot_seat" | "fired"
    hot_seat_level: int  # 0 = none; 1 = "another season..."; 2 = "a couple more..."


# --- Pure functions --------------------------------------------------------


def compute_wins_delta(won: int, games: int) -> float:
    """The per-Season wins delta (pre-cap), scaled around a .500 record.

    ``WINS_FACTOR * WINS_BASELINE_SCALE * (won - games/2) / (games/2)``. A
    Season with no completed Matches (``games == 0``) is neutral and returns
    ``0.0`` (no division by zero).
    """
    if games == 0:
        return 0.0
    half = games / 2
    return WINS_FACTOR * WINS_BASELINE_SCALE * (won - half) / half


def compute_playoffs_delta(
    playoff_result: str, rounds_won: int, num_rounds: int
) -> float:
    """The per-Season playoffs delta (pre-cap), from a view-classified result.

    | ``playoff_result`` | returns                                    |
    |--------------------|--------------------------------------------|
    | ``"champion"``     | ``PLAYOFF_TITLE`` (+0.2)                   |
    | ``"seeded"``       | ``(PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won`` |
    | ``"missed"``       | ``PLAYOFF_MISS`` (-0.2)                    |
    | ``"none"``         | ``0.0`` (no tournament phase — neutral)    |

    ``num_rounds == 0`` ⇒ the ``"seeded"`` branch returns ``0.0`` (defensive —
    never divides by zero). Any unknown string ⇒ ``0.0`` (forgiving fallback,
    the LG-06d / HX-02 precedent).
    """
    if playoff_result == "champion":
        return PLAYOFF_TITLE
    if playoff_result == "missed":
        return PLAYOFF_MISS
    if playoff_result == "seeded":
        if num_rounds == 0:
            return 0.0
        return (PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won
    # "none" or any unknown string.
    return 0.0


def cap_cumulative(prev_cumulative: float, delta: float) -> float:
    """Add ``delta`` to ``prev_cumulative``, capping on the UPSIDE only.

    ``min(prev_cumulative + delta, MOOD_FACTOR_CAP)`` — no negative floor (you
    cannot bank goodwill past +1, but you can sink arbitrarily low). Applied
    per-factor, oldest→newest, by the writer when chaining cumulative totals
    across a tenure.
    """
    return min(prev_cumulative + delta, MOOD_FACTOR_CAP)


def decide_verdict(
    totals: MoodTotals,
    deltas: MoodDeltas,
    *,
    seasons_in_tenure: int,
    luxury_tax_paid: bool = False,
    challenge_fired_luxury_tax: bool = False,
) -> Verdict:
    """The firing / hot-seat verdict (ZenGM-faithful, LOCKED).

    ``seasons_in_tenure`` is keyword-only — how many Seasons (inclusive) the
    Manager has held this team within the current tenure (1 = first Season of the
    tenure). Inside the grace period (``<= GRACE_PERIOD_SEASONS``) the verdict is
    always ``"retained"`` regardless of mood. Past grace: cumulative ``<= -1`` ⇒
    fired; else the hot-seat projection — ``total + delta < -1`` ⇒ level 1 ("another
    season..."), ``total + 2*delta < -1`` ⇒ level 2 ("a couple more..."), with
    level 1 winning when both hold (checked first).

    FIN-05 — the luxury-tax challenge fire is checked FIRST inside the same
    past-grace gate: when the per-League ``challenge_fired_luxury_tax`` rule is on
    and the managed team paid the luxury tax this Season (``luxury_tax_paid``), the
    Manager is fired outright — independent of cumulative owner mood. Both params
    default ``False`` so every existing caller gets exactly today's behaviour.
    """
    total = totals.wins + totals.playoffs + totals.money
    delta = deltas.wins + deltas.playoffs + deltas.money
    past_grace = seasons_in_tenure > GRACE_PERIOD_SEASONS

    # FIN-05 — luxury-tax challenge fire, checked FIRST inside the same past-grace gate.
    if past_grace and challenge_fired_luxury_tax and luxury_tax_paid:
        return Verdict("fired", 0)

    if past_grace and total <= FIRE_THRESHOLD:
        return Verdict("fired", 0)
    # Warning projection (only past grace, not fired).
    if past_grace and total + delta < FIRE_THRESHOLD:
        return Verdict("hot_seat", 1)  # "another season like that..."
    if past_grace and total + 2 * delta < FIRE_THRESHOLD:
        return Verdict("hot_seat", 2)  # "a couple more seasons..."
    return Verdict("retained", 0)
