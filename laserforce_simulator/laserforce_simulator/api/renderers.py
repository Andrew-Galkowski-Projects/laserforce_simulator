"""ANG-01a — the JSON renderer that applies the REST envelope (ADR-0039).

Success → ``{"data": <payload>, "meta": <response.envelope_meta or {}>}``;
4xx → ``{"errors": [...], "meta": {}}`` (already shaped by
``envelope_exception_handler``); 204 → empty body. ``BrowsableAPIRenderer``
picks this renderer as its default, so the browsable page shows the envelope.
"""

from __future__ import annotations

from typing import Any

from rest_framework.renderers import JSONRenderer

from laserforce_simulator.api.exceptions import errors_from_detail

_ERROR_KEYS: frozenset[str] = frozenset({"errors", "meta"})


class EnvelopeJSONRenderer(JSONRenderer):
    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: dict | None = None,
    ) -> bytes:
        response = (renderer_context or {}).get("response")
        status_code = getattr(response, "status_code", 200)

        if status_code == 204:
            return b""

        if status_code >= 400:
            if not (isinstance(data, dict) and set(data) == _ERROR_KEYS):
                # Defensive: a hand-built error Response that bypassed the
                # exception handler. New code must raise instead.
                data = {"errors": errors_from_detail(data), "meta": {}}
            return super().render(data, accepted_media_type, renderer_context)

        envelope = {"data": data, "meta": getattr(response, "envelope_meta", {})}
        return super().render(envelope, accepted_media_type, renderer_context)
