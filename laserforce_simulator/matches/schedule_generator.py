"""LG-01 schedule generator — pure deterministic fixture-list builder.

Public surface:

* ``SCHEDULE_FORMATS`` — tuple of accepted schedule-format names.
* ``ScheduleFixture`` — frozen dataclass describing one fixture
  (``matchday``, ``round_number``, ``team_a_id``, ``team_b_id``).
* ``generate_schedule(team_ids, schedule_format="single_round_robin")``
  — returns a deterministic ``list[ScheduleFixture]`` sorted by
  ``(matchday, team_a_id)``. The output is a function of the *set* of
  ``team_ids`` (the function sorts ascending internally before running
  the algorithm).

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``. No Django, no ``random``, no ``datetime``,
no I/O, no logging. The contract is enforced by the
``TestNoDjangoImportsLeaked`` subprocess check.
"""

from dataclasses import dataclass

SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin",)


# Internal bye sentinel used by the circle method when N is odd. Pairs
# containing this value are dropped from the output. Documented here so
# tests can assert it never leaks into a fixture.
_BYE_SENTINEL: int = -1


@dataclass(frozen=True)
class ScheduleFixture:
    """One scheduled fixture in a Season."""

    matchday: int  # 1-based
    round_number: int  # 1 or 2
    team_a_id: int  # min of the pair
    team_b_id: int  # max of the pair


def generate_schedule(
    team_ids: list[int],
    schedule_format: str = "single_round_robin",
) -> list[ScheduleFixture]:
    """Return the deterministic fixture list for these enrolled teams.

    Args:
        team_ids: list of team ids. Sorted ascending internally before
            the algorithm runs, so the output is a function of the *set*,
            not of input order.
        schedule_format: one of ``SCHEDULE_FORMATS``.

    Returns:
        list of ``ScheduleFixture`` sorted by ``(matchday, team_a_id)``.

    Raises:
        ValueError: if ``schedule_format`` not in ``SCHEDULE_FORMATS``.
        ValueError: if ``len(team_ids) < 2``.
    """
    if schedule_format not in SCHEDULE_FORMATS:
        raise ValueError(
            f"Unknown schedule_format {schedule_format!r}; "
            f"expected one of {SCHEDULE_FORMATS}"
        )
    if len(team_ids) < 2:
        raise ValueError(
            f"generate_schedule requires at least 2 team_ids; got {len(team_ids)}"
        )

    # Sort ascending so the output depends only on the *set* of inputs.
    sorted_ids = sorted(team_ids)

    # Append the bye sentinel for odd N so the circle method has an
    # even number of slots; pairs involving the sentinel are dropped
    # from the output.
    if len(sorted_ids) % 2 == 1:
        slots = sorted_ids + [_BYE_SENTINEL]
    else:
        slots = list(sorted_ids)

    n = len(slots)  # even after the bye-padding above
    # Round-1 matchdays span 1..n-1 (so n-1 matchdays).
    round1_fixtures: list[ScheduleFixture] = []

    # Circle method: fix slots[0]; rotate slots[1:] across n-1 steps.
    fixed = slots[0]
    rotating = list(slots[1:])
    rotating_len = len(rotating)  # = n - 1

    for k in range(rotating_len):
        matchday = k + 1  # 1-based
        # Pair the fixed slot with the head of the rotating slice.
        pair_fixed = (fixed, rotating[0])
        pairs: list[tuple[int, int]] = [pair_fixed]
        # Pair the remaining rotating slots symmetrically.
        for i in range(1, rotating_len // 2 + 1):
            left = rotating[i]
            right = rotating[rotating_len - i]
            assert left != right, "circle-method centre wrap should never happen"
            pairs.append((left, right))

        for a, b in pairs:
            if a == _BYE_SENTINEL or b == _BYE_SENTINEL:
                continue
            team_a_id = min(a, b)
            team_b_id = max(a, b)
            round1_fixtures.append(
                ScheduleFixture(
                    matchday=matchday,
                    round_number=1,
                    team_a_id=team_a_id,
                    team_b_id=team_b_id,
                )
            )

        # Rotate the rotating slice one step (head moves to tail).
        rotating = [rotating[-1]] + rotating[:-1]

    # Round-2 matchdays span n..2*(n-1); each round-1 matchday's
    # fixtures replay with round_number=2 on matchday k + (n-1).
    round2_fixtures: list[ScheduleFixture] = []
    for fixture in round1_fixtures:
        round2_fixtures.append(
            ScheduleFixture(
                matchday=fixture.matchday + (n - 1),
                round_number=2,
                team_a_id=fixture.team_a_id,
                team_b_id=fixture.team_b_id,
            )
        )

    all_fixtures = round1_fixtures + round2_fixtures
    all_fixtures.sort(key=lambda f: (f.matchday, f.team_a_id))
    return all_fixtures
