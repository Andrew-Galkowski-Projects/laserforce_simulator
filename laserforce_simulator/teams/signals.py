"""HX-02 — Cache-invalidation signals on PlayerRoundState.

Both `post_save` and `post_delete` bump the global role-benchmark version
via `invalidate_role_benchmarks()`. The simulator's bulk_create path
skips `post_save`, so the equivalent invalidation also fires from
`BatchSimulator._flush_to_db` (see `matches/simulation.py`).
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from matches.models import PlayerRoundState

from .role_benchmarks_cache import invalidate_role_benchmarks


@receiver(post_save, sender=PlayerRoundState)
@receiver(post_delete, sender=PlayerRoundState)
def _bump_role_benchmark_version(sender, instance, **kwargs) -> None:  # noqa: ARG001
    """Invalidate the role-benchmark cache on any PlayerRoundState write."""
    invalidate_role_benchmarks()
