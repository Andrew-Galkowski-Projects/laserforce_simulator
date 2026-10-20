"""ANG-01f — the Tournaments REST API (``/api/tournaments/``).

``base`` holds ``TournamentViewSet``, the 409 / 503 ``APIException``s and the
service-error translator; ``setup``, ``draw`` and ``play`` each hold one action
mixin the viewset is composed from. No re-exports: import from the submodule.
"""
