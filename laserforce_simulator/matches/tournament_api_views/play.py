"""ANG-01f-6 -- the play actions of ``TournamentViewSet``: play-next, play-all
and the play-job status (ticket ANG-01f-6, ADR-0040 Tournaments).

Order inside every write: ``get_object()`` (404 cross-Account), then the
``tournament_services.play`` service inside ``translate_tournament_errors()``
(409 ``tournament_embedded`` / ``tournament_not_active`` /
``play_in_progress`` / ``no_playable_node``, 503 ``broker_unavailable``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import reverse
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from matches.api_refs import team_ref
from matches.tournament_services.detail import _node_payload, _node_view
from matches.tournament_services.play import (
    PlayedNode,
    build_play_status,
    enqueue_play_all,
    play_next,
)

if TYPE_CHECKING:
    from matches.models import Tournament


def _played_node_payload(played: PlayedNode) -> dict:
    """``{node, match_ids, state, champion}`` for a play-next result."""
    node = played.node
    return {
        "node": {
            "id": node.id,
            "bracket_type": node.bracket_type,
            "bracket_round": node.bracket_round,
            **_node_payload(_node_view(node)),
        },
        "match_ids": played.match_ids,
        "state": played.state,
        "champion": team_ref(played.champion) if played.champion else None,
    }


class PlayActionsMixin:
    """Play-next / play-all / play-job status actions."""

    if TYPE_CHECKING:
        # Provided by ``TournamentViewSet`` (``GenericViewSet``).
        def get_object(self) -> Tournament: ...

    @action(detail=True, methods=["post"], url_path="play-next", url_name="play-next")
    def play_next(self, request: Request, pk: str | None = None) -> Response:
        """Play the next ready Match synchronously: 200 ``{node, match_ids,
        state, champion}``."""
        from matches.tournament_api_views.base import translate_tournament_errors

        tournament = self.get_object()
        with translate_tournament_errors():
            played = play_next(tournament)
        return Response(_played_node_payload(played))

    @action(detail=True, methods=["post"], url_path="play-all", url_name="play-all")
    def play_all(self, request: Request, pk: str | None = None) -> Response:
        """Start the play-all Job: 200 ``{job_id, tournament_id, status_url}``."""
        from matches.tournament_api_views.base import translate_tournament_errors

        tournament = self.get_object()
        with translate_tournament_errors():
            job_id = enqueue_play_all(tournament)
        return Response(
            {
                "job_id": job_id,
                "tournament_id": tournament.pk,
                "status_url": reverse(
                    "tournament-play-job", args=[tournament.pk, job_id]
                ),
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"play-jobs/(?P<job_id>[^/.]+)",
        url_name="play-job",
    )
    def play_job(
        self, request: Request, pk: str | None = None, job_id: str | None = None
    ) -> Response:
        """The Job status (the HTML polling keys). The job id is a capability
        token (API-03)."""
        tournament = self.get_object()
        return Response(build_play_status(tournament, job_id or ""))
