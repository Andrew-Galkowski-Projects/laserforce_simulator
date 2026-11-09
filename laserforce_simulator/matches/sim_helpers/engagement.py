"""Engagement switches (CAL-02, spec #563, 2026-11-08).

The sim's players tagged and were tagged far less than the arena's (287 Tags a
round against 403 over the 128 Internationals 2026 Test games). One reason was
one-sided: a tagged player learned nothing from the Hit, so it stood blind while
the attacker alone knew where its enemy was. The switch below gives the defender
the same sighting a real player gets from the flash and the direction of the shot.

Pure Python, no imports. Read at call time (``engagement.DEFENDER_LEARNS_ATTACKER``,
never ``from ... import``), so a probe or a test can flip it with ``monkeypatch``
or ``patch.object``. Off, the engine is exactly as it was.
"""

from __future__ import annotations

#: CAL-02 decision 12 (D4): at every Hit on a map, after the attacker's memory
#: write and call-out (``shot.resolve_shot``, phase 7), the attacker is written
#: into the defender's memory (cell, role, status, as any sighting through
#: ``round_loop._update_player_memory``) and the defender rolls its own call-out
#: (``_broadcast_communication``). The defender's draws come after the attacker's,
#: so a map-less round, which writes no memory on a Hit, draws nothing new.
#: Measured on the 128 Test games (one round, seed 42): Tags a round 288 -> 299,
#: times tagged Commander 28.7 -> 31.3. Off, only the attacker learns the
#: defender's cell.
DEFENDER_LEARNS_ATTACKER: bool = True
