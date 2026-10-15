"""ANG-01a — page-number pagination that feeds the envelope ``meta`` (ADR-0039).

``get_paginated_response`` returns the bare page list as the response data and
hangs the paging facts on ``response.envelope_meta``; ``EnvelopeJSONRenderer``
wraps them as ``{"data": [...], "meta": {page, page_size, count, total_pages}}``.
Out-of-range / non-integer ``?page=`` stays DRF's 404 ``"Invalid page."``;
``?page_size`` clamping / fallback stays DRF's default behaviour.
"""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class EnvelopePagination(PageNumberPagination):
    page_size = 20  # mirrors settings.REST_FRAMEWORK["PAGE_SIZE"]
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data: list) -> Response:
        response = Response(data)
        paginator = self.page.paginator
        response.envelope_meta = {
            "page": self.page.number,
            "page_size": paginator.per_page,
            "count": paginator.count,
            "total_pages": paginator.num_pages,
        }
        return response

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            "type": "object",
            "required": ["data", "meta"],
            "properties": {
                "data": schema,
                "meta": {
                    "type": "object",
                    "required": ["page", "page_size", "count", "total_pages"],
                    "properties": {
                        "page": {"type": "integer", "example": 1},
                        "page_size": {"type": "integer", "example": self.page_size},
                        "count": {"type": "integer", "example": 123},
                        "total_pages": {"type": "integer", "example": 7},
                    },
                },
            },
        }
