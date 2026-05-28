"""EventLog — single source of truth for the ``GameEvent``-dict shape.

Replaces the 23+ inline ``event_log.append({...})`` sites that were
scattered across ``matches/simulation.py``, ``sim_helpers/shot.py``,
``sim_helpers/down.py``, ``sim_helpers/combat.py``, and
``sim_helpers/resupply_queue.py`` (each duplicating the 7-key dict
shape and the 5-key actor / 4-key target metadata blocks).

Null-object pattern: ``persist=True`` records every emit into
``self._entries``; ``persist=False`` drops them silently. Both modes
are indistinguishable to callers — every emit site is one unguarded
line. The legacy ``if event_log is not None:`` guards delete from
all 23 sites.

Pure Python, no Django imports. Pinned by the seam contract at
``.claude/worktrees/event-log-seam-contract.md``.

**Wire-format normalization.** Resupply events go through a single
description path here, not the divergent paths the pre-refactor code
took. The simulation.py ``_attempt_resupply`` route used the
combat.py wording (``"X heals Y"`` / ``"X resupplies Y"``); the
resupply_queue.py route went through the ``_resupply_event_dict``
adapter and produced ``"resupply request: resupply_lives"`` etc.
EventLog standardizes on the combat.py wording for all paths — a
clear improvement over the inconsistency. No test asserts on the
old adapter wording, so this is safe.

**Movement events stay off the EventLog.** Per the seam contract,
``event_type="movement"`` rows are written directly to ``GameEvent``
at ``_flush_to_db`` time from ``PlayerState.movement_trail``. No
``movement`` verb here.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Private metadata builders — the universal RES-02b actor/target snapshot
# blocks. Owned by EventLog; the duplicate copies that lived on
# simulation.py / shot.py / down.py all delete in this migration.
# ---------------------------------------------------------------------------


def _actor_meta(actor) -> dict:
    """5-key actor-snapshot block: role / shots / lives / points / sp."""
    return {
        "actor_role": actor.role,
        "actor_shots": actor.final_shots,
        "actor_lives": actor.final_lives,
        "actor_points": actor.points_scored,
        "sp": actor.final_special,
    }


def _target_meta(target) -> dict:
    """4-key target-snapshot block: role / shots / lives / points."""
    return {
        "target_role": target.role,
        "target_shots": target.final_shots,
        "target_lives": target.final_lives,
        "target_points": target.points_scored,
    }


def _build_meta(actor, target=None, **extras) -> dict:
    """Compose the metadata dict: actor block + optional target block + extras."""
    md = _actor_meta(actor)
    if target is not None:
        md.update(_target_meta(target))
    md.update(extras)
    return md


def _kind_extras(kind: str, chain_depth: int) -> dict:
    """Kind-to-metadata-flag translation for tag/miss verbs.

    Mirrors the (deleted) ``shot._kind_extras`` helper byte-for-byte:

    - ``initial``:   no extra flag
    - ``follow_up``: ``is_follow_up=True``, ``chain=<chain_depth>``
    - ``reaction``:  ``is_reaction=True``
    - ``overwatch``: ``overwatch=True``
    """
    if kind == "follow_up":
        return {"is_follow_up": True, "chain": chain_depth}
    if kind == "reaction":
        return {"is_reaction": True}
    if kind == "overwatch":
        return {"overwatch": True}
    return {}


# ---------------------------------------------------------------------------
# EventLog — 13 per-event-type verbs
# ---------------------------------------------------------------------------


class EventLog:
    """Null-object-pattern event recorder.

    Single source of truth for the ``GameEvent``-dict shape. Every
    emit site in the simulation path goes through one of the 13
    verbs below; the dict shape and metadata schemas are constructed
    internally so no caller sees the 7-key literal.

    ``persist=True`` records every emit into ``self._entries``;
    ``persist=False`` drops them (each verb early-returns). Both
    modes are otherwise indistinguishable to callers.

    Read API:
      - ``events.entries`` — ``list[dict]``, the internal storage.
        Consumed by ``BatchSimulator._flush_to_db`` (constructs
        ``GameEvent`` rows) and by tests inspecting emitted events.
        Returns the live list, not a copy.
      - ``iter(events)`` / ``len(events)`` — convenience.
    """

    __slots__ = ("_persist", "_entries")

    def __init__(
        self,
        *,
        persist: bool = True,
        buffer: Optional[list] = None,
    ) -> None:
        """``persist`` toggles null-object behaviour. ``buffer`` is a
        transitional shim: when provided, EventLog stores entries into
        the caller's pre-existing list instead of allocating its own.
        Used during the candidate-#2 migration so ``simulation.py``'s
        local ``event_log: list`` and ``ctx.events.entries`` reference
        the same list — the 18 inline ``event_log.append({...})`` sites
        keep working until step 8 retires them. Once those sites
        migrate, ``buffer`` becomes dead and can be removed.
        """
        self._persist: bool = persist
        self._entries: list[dict] = buffer if buffer is not None else []

    # ------------------------------------------------------------------ #
    # Read API
    # ------------------------------------------------------------------ #

    @property
    def entries(self) -> list[dict]:
        """The underlying list of event dicts (NOT a copy).

        Callers must not mutate this list. Consumed by
        ``BatchSimulator._flush_to_db`` to construct ``GameEvent``
        rows; the dict shape is the same 7-key shape the simulator
        produced pre-refactor (``event_type``, ``actor_id``,
        ``target_id``, ``timestamp``, ``points_awarded``,
        ``description``, ``metadata``).
        """
        return self._entries

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        kind = "persist" if self._persist else "null"
        return f"EventLog({kind}, n={len(self._entries)})"

    # ------------------------------------------------------------------ #
    # Shot resolution verbs (3): tag / miss / elimination
    # ------------------------------------------------------------------ #

    def tag(
        self,
        attacker,
        defender,
        tick: int,
        *,
        kind: str = "initial",
        chain_depth: int = 0,
    ) -> None:
        """Emit ``event_type='tag'``. points_awarded=100.

        ``kind`` ∈ {``initial`` (default), ``follow_up``, ``reaction``,
        ``overwatch``}. Metadata = actor block + target block + kind
        extras. Description varies by kind (see seam contract §1.3).
        """
        if not self._persist:
            return
        if kind == "follow_up":
            desc = f"{attacker.name} follow-up tags {defender.name}"
        elif kind == "reaction":
            desc = f"{attacker.name} reacts to {defender.name}"
        else:  # initial OR overwatch (overwatch reuses the initial wording)
            desc = f"{attacker.name} tags {defender.name}"
        self._entries.append(
            {
                "event_type": "tag",
                "actor_id": attacker.player_id,
                "target_id": defender.player_id,
                "timestamp": tick,
                "points_awarded": 100,
                "description": desc,
                "metadata": _build_meta(
                    attacker, defender, **_kind_extras(kind, chain_depth)
                ),
            }
        )

    def miss(
        self,
        attacker,
        defender,
        tick: int,
        *,
        kind: str = "initial",
        chain_depth: int = 0,
        reason: Optional[str] = None,
    ) -> None:
        """Emit ``event_type='miss'``. points_awarded=0.

        ``reason='hiding'`` is the only non-None value in production
        (defender's is_hiding triggered the 50% hide-miss roll); when
        present, metadata gets ``reason="hiding"`` and description
        becomes ``"X misses Y (hiding)"``.
        """
        if not self._persist:
            return
        extras = _kind_extras(kind, chain_depth)
        if reason is not None:
            extras["reason"] = reason
            desc = f"{attacker.name} misses {defender.name} ({reason})"
        elif kind == "follow_up":
            desc = f"{attacker.name} follow-up miss on {defender.name}"
        elif kind == "reaction":
            desc = f"{attacker.name} reaction miss on {defender.name}"
        else:
            desc = f"{attacker.name} misses {defender.name}"
        self._entries.append(
            {
                "event_type": "miss",
                "actor_id": attacker.player_id,
                "target_id": defender.player_id,
                "timestamp": tick,
                "points_awarded": 0,
                "description": desc,
                "metadata": _build_meta(attacker, defender, **extras),
            }
        )

    def elimination(
        self,
        attacker,
        defender,
        tick: int,
        *,
        action: str = "tag",
    ) -> None:
        """Emit ``event_type='elimination'``. points_awarded=0.

        ``action`` ∈ {``tag`` (default), ``follow_up_tag``,
        ``reaction``, ``missile``, ``nuke``}. Description varies by
        action; metadata carries ``elimination_action=action``.
        """
        if not self._persist:
            return
        if action == "follow_up_tag":
            desc = f"{attacker.name} eliminates {defender.name} (follow-up)"
        elif action == "reaction":
            desc = f"{attacker.name} eliminates {defender.name} (reaction)"
        elif action == "missile":
            desc = f"{defender.name} eliminated by missile from {attacker.name}"
        elif action == "nuke":
            desc = f"{defender.name} eliminated by nuke"
        else:  # action == "tag" (default; also covers overwatch which maps to "tag")
            desc = f"{defender.name} eliminated by {attacker.name}"
        self._entries.append(
            {
                "event_type": "elimination",
                "actor_id": attacker.player_id,
                "target_id": defender.player_id,
                "timestamp": tick,
                "points_awarded": 0,
                "description": desc,
                "metadata": _build_meta(attacker, defender, elimination_action=action),
            }
        )

    # ------------------------------------------------------------------ #
    # Down chokepoint verbs (2): nuke_cancelled / medic_reset (RV-02)
    # ------------------------------------------------------------------ #

    def nuke_cancelled(self, commander, tick: int) -> None:
        """Emit ``event_type='nuke_cancelled'``. points_awarded=0.

        Commander downed during own nuke fuse → emitted once per
        live pending nuke at the down/disarm tick (single source from
        ``sim_helpers.down.record_down``; the cancelled nuke is
        LEFT in ``pending_nukes`` with ``cancel_logged=True`` so the
        MECH-05 drain path is unchanged). No target. RV-02.
        """
        if not self._persist:
            return
        self._entries.append(
            {
                "event_type": "nuke_cancelled",
                "actor_id": commander.player_id,
                "target_id": None,
                "timestamp": tick,
                "points_awarded": 0,
                "description": f"{commander.name} nuke cancelled",
                "metadata": _actor_meta(commander),
            }
        )

    def medic_reset(self, medic, tick: int) -> None:
        """Emit ``event_type='medic_reset'``. points_awarded=0.

        Medic re-Downed within the respawn cooldown (chain_count
        reached 2) → emitted once per chain from
        ``sim_helpers.down.record_down``. No target. RV-02.
        """
        if not self._persist:
            return
        self._entries.append(
            {
                "event_type": "medic_reset",
                "actor_id": medic.player_id,
                "target_id": None,
                "timestamp": tick,
                "points_awarded": 0,
                "description": f"{medic.name} medic reset (down-chain)",
                "metadata": _actor_meta(medic),
            }
        )

    # ------------------------------------------------------------------ #
    # Special-ability verb (1): special
    # ------------------------------------------------------------------ #

    def special(
        self,
        actor,
        tick: int,
        *,
        description: str,
        points: int = 0,
        metadata_extras: Optional[dict] = None,
    ) -> None:
        """Emit ``event_type='special'``.

        Covers the five distinct ``special`` emit sites in
        ``_use_special`` and ``_complete_nuke``: nuke activation
        (points=0, metadata_extras={"fires_at": ...}), nuke
        detonation (points=500, metadata_extras={"targets": [...]}),
        scout rapid-fire activation (points=0), commander shield
        activation (points=0), etc. Caller passes the description
        and any kind-specific metadata extras; verb appends the
        7-key dict with the actor block.
        """
        if not self._persist:
            return
        extras = metadata_extras or {}
        self._entries.append(
            {
                "event_type": "special",
                "actor_id": actor.player_id,
                "target_id": None,
                "timestamp": tick,
                "points_awarded": points,
                "description": description,
                "metadata": _build_meta(actor, **extras),
            }
        )

    # ------------------------------------------------------------------ #
    # Missile verbs (3): locking / missiled / missile_dodge (RES-03)
    # ------------------------------------------------------------------ #

    def locking(self, attacker, defender, tick: int) -> None:
        """Emit ``event_type='locking'`` at the missile fire tick.
        points_awarded=0. RES-03 — pairs with a later ``missiled``
        resolution event (unless the locking actor is Down'd first,
        in which case the missiled never fires per the MECH-05
        precedent).
        """
        if not self._persist:
            return
        self._entries.append(
            {
                "event_type": "locking",
                "actor_id": attacker.player_id,
                "target_id": defender.player_id,
                "timestamp": int(tick),
                "points_awarded": 0,
                "description": f"{attacker.name} locks on {defender.name}",
                "metadata": _build_meta(attacker, defender),
            }
        )

    def missiled(
        self,
        attacker,
        defender,
        tick: int,
        *,
        result: str,
        friendly_fire: bool,
    ) -> None:
        """Emit ``event_type='missiled'`` at the missile resolution tick.

        ``result`` ∈ {``hit``, ``miss``}. ``hit`` ⇒ points_awarded=500;
        ``miss`` ⇒ 0. Metadata carries the four RES-03-required keys:
        ``result``, ``friendly_fire``, ``actor_role``, ``target_role``
        (the last two come from the actor/target blocks).
        """
        if not self._persist:
            return
        if result == "hit":
            desc = f"{attacker.name} hits {defender.name} with missile"
            points = 500
        else:  # "miss"
            desc = f"{attacker.name} misses {defender.name} with missile"
            points = 0
        self._entries.append(
            {
                "event_type": "missiled",
                "actor_id": attacker.player_id,
                "target_id": defender.player_id,
                "timestamp": int(tick),
                "points_awarded": points,
                "description": desc,
                "metadata": _build_meta(
                    attacker, defender, result=result, friendly_fire=friendly_fire
                ),
            }
        )

    def missile_dodge(self, defender, attacker, tick: int) -> None:
        """Emit ``event_type='missile_dodge'``. points_awarded=0.

        Note ``actor=defender`` and ``target=attacker`` — the dodging
        defender is the protagonist of the event (mirrors the
        pre-refactor wire format).
        """
        if not self._persist:
            return
        self._entries.append(
            {
                "event_type": "missile_dodge",
                "actor_id": defender.player_id,
                "target_id": attacker.player_id,
                "timestamp": int(tick),
                "points_awarded": 0,
                "description": f"{defender.name} dodges missile from {attacker.name}",
                "metadata": _build_meta(defender, attacker),
            }
        )

    # ------------------------------------------------------------------ #
    # Resupply verbs (3): resupply_lives / resupply_ammo / combo_resupply
    # ------------------------------------------------------------------ #

    def resupply_lives(
        self,
        supporter,
        requestor,
        tick: int,
        *,
        amount: Optional[int] = None,
    ) -> None:
        """Emit ``event_type='resupply_lives'``. points_awarded=0.

        Medic restores lives on a teammate. ``amount`` (when
        provided by combat.py) goes into metadata for analytics.

        Wire-description normalization: the pre-refactor combat.py
        path produced ``"X heals Y"``; the resupply_queue.py path
        produced ``"resupply request: resupply_lives"``. EventLog
        standardizes on the combat.py wording for all paths.
        """
        if not self._persist:
            return
        extras = {"amount": amount} if amount is not None else {}
        self._entries.append(
            {
                "event_type": "resupply_lives",
                "actor_id": supporter.player_id,
                "target_id": requestor.player_id,
                "timestamp": int(tick),
                "points_awarded": 0,
                "description": f"{supporter.name} heals {requestor.name}",
                "metadata": _build_meta(supporter, requestor, **extras),
            }
        )

    def resupply_ammo(
        self,
        supporter,
        requestor,
        tick: int,
        *,
        amount: Optional[int] = None,
    ) -> None:
        """Emit ``event_type='resupply_ammo'``. points_awarded=0.

        Ammo restores shots on a teammate. Wire-description
        normalization mirrors ``resupply_lives``.
        """
        if not self._persist:
            return
        extras = {"amount": amount} if amount is not None else {}
        self._entries.append(
            {
                "event_type": "resupply_ammo",
                "actor_id": supporter.player_id,
                "target_id": requestor.player_id,
                "timestamp": int(tick),
                "points_awarded": 0,
                "description": f"{supporter.name} resupplies {requestor.name}",
                "metadata": _build_meta(supporter, requestor, **extras),
            }
        )

    def combo_resupply(self, requestor, medic, ammo, tick: int) -> None:
        """Emit ``event_type='combo_resupply'``. points_awarded=0.

        Both lives + shots arrive in the same tick. By RES-02b
        convention the medic is the actor and the requestor is the
        target; the ammo supporter is named via the ``ammo_tag``
        metadata extra. ``medic_tag`` and ``ammo_tag`` carry the
        ``tag_id_key`` of each supporter (works on both
        ``PlayerState`` and ``PlayerRoundState`` via duck-typing).
        """
        if not self._persist:
            return
        self._entries.append(
            {
                "event_type": "combo_resupply",
                "actor_id": medic.player_id,
                "target_id": requestor.player_id,
                "timestamp": int(tick),
                "points_awarded": 0,
                "description": f"{medic.name} combo-resupplies {requestor.name}",
                "metadata": _build_meta(
                    medic,
                    requestor,
                    medic_tag=medic.tag_id_key,
                    ammo_tag=ammo.tag_id_key,
                ),
            }
        )

    # ------------------------------------------------------------------ #
    # Base capture verb (1): base_capture
    # ------------------------------------------------------------------ #

    def base_capture(
        self,
        actor,
        tick: int,
        *,
        base_id: int,
        points: int = 1001,
        description: Optional[str] = None,
        metadata_extras: Optional[dict] = None,
    ) -> None:
        """Emit ``event_type='base_capture'``. points_awarded=points
        (default 1001; combat.py's standard capture reward).

        Three pre-refactor descriptions distinguish active captures
        from end-of-round awards:
          - active capture: ``"X captures base neutral"`` /
            ``"X captures base opposing"`` (default when ``description``
            is ``None``)
          - awarded neutral (round-end): caller passes
            ``description="X awarded neutral base"``
          - awarded opposing (round-end): caller passes
            ``description="X awarded opposing base"``

        ``metadata_extras`` lets the active-capture site attach its
        extras (``shots_remaining``, ``points_scored``,
        ``target_base_type``, ``role``); the awarded sites pass nothing.
        ``base_id`` is always in metadata.
        """
        if not self._persist:
            return
        if description is None:
            description = (
                f"{actor.name} captures base "
                f"{'neutral' if base_id == 15 else 'opposing'}"
            )
        extras = {"base_id": base_id}
        if metadata_extras:
            extras.update(metadata_extras)
        self._entries.append(
            {
                "event_type": "base_capture",
                "actor_id": actor.player_id,
                "target_id": None,
                "timestamp": int(tick),
                "points_awarded": points,
                "description": description,
                "metadata": _build_meta(actor, **extras),
            }
        )
