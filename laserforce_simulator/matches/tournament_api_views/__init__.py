"""ANG-01f — the Tournaments REST API (``/api/tournaments/``).

``base`` holds ``TournamentViewSet``; ``errors`` (a leaf module) holds the
409 / 503 ``APIException``s and the service-error translator; ``create``,
``setup``, ``draw`` and ``play`` each hold one action mixin the viewset is
composed from. No re-exports: import from the submodule.
"""
