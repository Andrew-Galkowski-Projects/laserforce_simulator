# SIM-01 seam contract — Document and test action weights

**Task:** Add docstrings to every weight function in `weights.py`, extract the
action-weight baseline into a documented constant, cover weight sums with tests,
and add a `plan_action` non-negative-weight regression test.

**Nature:** pure documentation + test-hardening + one constant extraction.
**No formula change. No baseline value change. No behavioural change. No
migration. No Score Calibration re-baseline. No CONTEXT.md term. No ADR.**

---

## Names / shapes (all agents hold to these exactly)

| Name | Owner module | Shape / signature | Notes |
|------|--------------|-------------------|-------|
| `BASELINE_ACTION_WEIGHTS` | `matches/sim_helpers/weights.py` | module-level `list[int]`, exactly `[70, 30, 0, 0, 0, 0, 0, 0, 0]` (9 elems) | NEW. Public (no underscore). Documented with the index→action map. |
| `plan_action` | `matches/sim_helpers/combat.py` | signature **unchanged** | Replaces the literal `weights = [70, 30, 0, 0, 0, 0, 0, 0, 0]` (combat.py:293) with `weights = list(BASELINE_ACTION_WEIGHTS)` (copy — it mutates). Adds `BASELINE_ACTION_WEIGHTS` to its existing `from .weights import ...` line. |
| `_get_medic_weights` | `weights.py` | signature **unchanged** | Add docstring. |
| `_get_ammo_weights` | `weights.py` | signature **unchanged** | Add docstring. |
| `_get_scout_weights` | `weights.py` | signature **unchanged** | Add docstring. |
| `_get_heavy_weights` | `weights.py` | signature **unchanged** | Add docstring. |
| `_get_commander_weights` | `weights.py` | signature **unchanged** | Add docstring. |
| `_MEDIC`/`_AMMO`/`_SCOUT`/`_HEAVY`/`_COMMANDER` | `weights.py` | const dicts **unchanged** | Add per-key inline comments. |

## Action-weight array layout (index → action), documented on `BASELINE_ACTION_WEIGHTS`

```
0 tag_player   1 only_move   2 hide        3 capture_base  4 use_special
5 resupply_ally 6 missile_player 7 request_resupply 8 hold
```

## Role function docstring content (what each must state)

- The role's **baseline totals** after role-adjustment (Medic 10/0/90; Ammo
  35/0/95 + hold 20; Scout 50/50 + hold 10; Heavy 70/25 + hold 20; Commander
  70/30 + hold 10 — see `sim_helpers/CLAUDE.md` "Role baselines" table).
- Which **situational blocks** it applies and in what order (baseline → hold →
  capture/missile → critical-resource seek → special → not-active → endgame →
  stat scaling → MECH-04 nuke → MECH-06 score-broadcast).
- The **non-negative invariant** (`random.choices` rejects negative weights).

## Test boundary (`matches/tests/test_weights.py` ONLY)

- **Migrate** to a single **9-slot** fixture sourced from `BASELINE_ACTION_WEIGHTS`
  (import it; build `_fresh()` as `list(BASELINE_ACTION_WEIGHTS)` with a 9-key
  `_ACTION_IDX` matching the layout above). **Delete** the legacy 7-slot `_BASE`
  / 7-key `_ACTION_IDX` and the separate `_BASE9`/`_ACTION_IDX9` (collapse to one).
- Existing assertions (`sum(w)==100`, `==95`, exact vectors) **stay valid** — the
  `hold` redistribution is zero-sum within the array and `request_resupply`
  stays 0 at baseline. Where an old 7-elem expected-vector literal exists, widen
  it to 9 elems by appending the correct `request_resupply` (idx 7) and `hold`
  (idx 8) values for that state (hold per role: Medic 0 / Ammo 20 / Scout 10 /
  Heavy 20 / Commander 10 at baseline; recompute if a block touched the source slot).
- **Keep** `test_medic_can_capture_base_prioritises_capture` (asserts +5) and the
  Scout shots-critical `xfail` **as-is** — only sharpen their docstrings to
  explain why the value is what it is and where the ideal/clamp lives.
- **NEW regression test** `test_plan_action_never_emits_negative_weight` (or
  similar) in `test_weights.py`:
  - Build **in-memory `PlayerState`** objects (from
    `matches/sim_helpers/player_state.py`) — **no `@pytest.mark.django_db`**, no ORM.
  - Call **`combat.plan_action`** for all 5 roles × ~10 targeted edge states:
    baseline, low-lives (≤3 / ≤30%), not-active (`last_downed_time` recent),
    shots-critical, nuke-reacting (`reacting_to_nuke=True`),
    score-broadcast losing, score-broadcast winning+low-lives,
    stamina-penalty (`stamina_penalty_count>0`), on-cooldown (recent
    `last_shot_time`). Heavy/Commander set `final_missiles`/`missiles_used` so
    both missile branches are reachable.
  - Assert **every element of the returned/used weight vector is ≥ 0** (the real
    production invariant — `plan_action` is what feeds `random.choices`).
  - Deterministic: seed RNG if `plan_action`'s `random.choices` matters, but the
    assertion is on the weight vector pre-choice where possible (the test pins
    non-negativity, not the chosen action).

## Docs

- `matches/sim_helpers/CLAUDE.md` (weights.py section): note `BASELINE_ACTION_WEIGHTS`
  moved out of `combat.plan_action`; note the new `plan_action` negative-weight
  regression test; note test fixture is now single-9-slot sourced from the constant.
- `PLAN.md`: mark `SIM-01 - completed` + dense house-style note.
- Do **not** touch CONTEXT.md or add an ADR.

## File ownership (Step-7 parallel agents)

- **Code agent:** `weights.py` (constant + docstrings + per-key comments),
  `combat.py` (use the constant). No tests, no docs.
- **Tests agent:** `test_weights.py` only.
- **Docs agent:** `sim_helpers/CLAUDE.md`, `PLAN.md`. No `.py`.
