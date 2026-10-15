"""ANG-01a — shared viewset behaviour for the REST API."""

from __future__ import annotations

from django.db.models import QuerySet


class ListOnlyFilterMixin:
    """Run the filter backends on the ``list`` action only.

    Every other action (detail GET, PATCH, PUT, DELETE, extra actions) resolves
    ``get_object()`` against the owned queryset alone, so query params can never
    404 or 400 a detail lookup — and the Free Agents Team, hidden from the team
    list by default, stays reachable by id.
    """

    def filter_queryset(self, queryset: QuerySet) -> QuerySet:
        if getattr(self, "action", None) != "list":
            return queryset
        return super().filter_queryset(queryset)  # type: ignore[misc]
