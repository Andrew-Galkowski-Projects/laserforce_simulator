"""``matches.simulation`` — public surface preserved post split.

The pre-split single-file ``matches/simulation.py`` (~2473 lines) was split
into three sibling modules under this package:

* :mod:`matches.simulation.round_loop` — per-tick mechanics (no ORM).
* :mod:`matches.simulation.entrypoints` — :class:`BatchSimulator` + the
  ``simulate_*`` / ``run`` / ``save_games`` / batch-execution glue.
* :mod:`matches.simulation.persistence` — :func:`flush_to_db`
  (ORM serialisation; the only module here that imports ORM models).

Every name any caller previously imported from ``matches.simulation`` is
re-exported here so ``from matches.simulation import …`` lines need no
changes. The most important re-exports:

* ``BatchSimulator`` — the simulator class.
* ``random`` — the stdlib ``random`` module reference; ``patch(
  "matches.simulation.random.randint")`` resolves to the very module the
  entrypoints methods use.
* ``_chunk_size_for`` / ``_precompute_roster`` — module-level helpers.
* ``_check_medic_under_fire`` / ``_broadcast_communication`` /
  ``_update_player_memory`` / ``_apply_nuke_activation_broadcast`` —
  shot-resolver lazy-import targets.
* ``_get_los_targets`` / ``_get_base_interaction`` /
  ``_can_tag_through_windowed_wall`` / ``elevation_hit_modifier`` —
  combat-helper passthroughs exercised by ``test_map.py``.
* ``_batch_worker`` / ``_worker_django_init`` — parallel-worker
  back-compat aliases.
"""

# Re-export the canonical ``random`` module reference. Tests patch
# ``matches.simulation.random.randint``, which resolves to this attribute
# and therefore to the stdlib ``random`` module shared with every other
# importer (modules are singletons).
import random  # noqa: F401

# Public class and module-level helpers.
from .entrypoints import (  # noqa: F401
    BatchSimulator,
    _PlayerData,
    _SIMULATION_STATS,
    _chunk_size_for,
    _precompute_roster,
    logger,
)

# Tick-loop free functions. ``sim_helpers/shot.py`` lazy-imports the four
# below from ``matches.simulation``; the test suite also exercises some
# directly.
from .round_loop import (  # noqa: F401
    _apply_nuke_activation_broadcast,
    _apply_nuke_reaction_flags,
    _apply_score_broadcast,
    _broadcast_communication,
    _check_medic_under_fire,
    _observe_lives,
    _str_tag_id,
    _update_player_memory,
)

# Combat-helper passthroughs. The pre-split file imported these and
# re-exported them implicitly via module-level import; preserve the
# back-compat surface so ``from matches.simulation import _get_los_targets``
# (and friends) keeps working.
from ..sim_helpers.combat import (  # noqa: F401
    _NEUTRAL_BASE_TYPES,
    _can_tag_through_windowed_wall,
    _elevation_hit_modifier,
    _get_base_interaction,
    _get_los_targets,
    elevation_hit_modifier,
)

# Parallel-worker re-exports (the pre-split file exposed
# ``_batch_worker`` / ``_worker_django_init`` aliases at module bottom).
from ..sim_helpers.parallel_worker import (  # noqa: F401
    batch_round_worker as _batch_worker,
    worker_django_init as _worker_django_init,
)

# Role-stats re-export (the pre-split file did
# ``from matches.sim_helpers.role_constants import ROLE_STATS`` at module
# scope). Preserve so direct ``matches.simulation.ROLE_STATS`` attribute
# access keeps working.
from ..sim_helpers.role_constants import ROLE_STATS  # noqa: F401
