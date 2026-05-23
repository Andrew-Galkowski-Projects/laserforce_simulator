"""HX-01 — URL config for player-scoped pages mounted under /players/.

Kept in a sibling URL file (not folded into `teams/urls.py`) because the
root urls.py mounts `teams.urls` at the empty path "" (homepage), so any
prefix added there would change the homepage URL. The new include lives
ABOVE the homepage catch-all in `laserforce_simulator/urls.py` so the
homepage shadow does not eat `/players/...`.

No module-level ``app_name`` is set: the contract reverses via the bare
``{% url 'player_career_stats' player.id %}`` (no ``app_name:`` prefix).
Django only honours ``app_name`` when it is a non-None string, so an
explicit ``app_name = None`` would be a no-op — omitted entirely.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Static `benchmarks/` listed first; `<int:...>` already rejects the
    # literal "benchmarks" but the explicit ordering matches the seam
    # contract and removes any possibility of a future regex change
    # silently shadowing it.
    path(
        "benchmarks/",
        views.role_benchmarks,
        name="role_benchmarks",
    ),
    path(
        "<int:player_id>/stats/",
        views.player_career_stats,
        name="player_career_stats",
    ),
]
