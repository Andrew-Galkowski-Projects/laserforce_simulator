"""The public Job status vocabulary, mapped from Celery's native task states.

Shared by the batch / save polling views (``matches.views``), the tournament
play-all poll (``matches.tournament_views.play``) and the Season play-run status
(``matches.league_play_services``). Kept free of view imports so the service
layer never has to import a views module.
"""


def celery_state_to_job_status(state: str) -> str:
    """Map a Celery native state to the public SIM-10 vocabulary."""
    if state == "SUCCESS":
        return "complete"
    if state in ("FAILURE", "REVOKED"):
        return "error"
    # PENDING / STARTED / PROGRESS / RETRY / anything else → keep polling.
    return "running"
