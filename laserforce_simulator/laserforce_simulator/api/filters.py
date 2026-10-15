"""ANG-01a — ``?ordering=`` with public-name aliases and a stable tiebreak.

Terms are validated against ``view.ordering_fields`` (public names); unknown
terms are dropped silently (DRF default). Each surviving term is mapped through
``view.ordering_aliases`` (public → ORM, leading ``-`` kept), falling back to
``view.ordering`` (ORM names) when nothing valid remains, and every name in
``view.ordering_tiebreak`` not already present is appended so pagination is
stable.
"""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework.filters import OrderingFilter
from rest_framework.request import Request
from rest_framework.views import APIView


def _bare(term: str) -> str:
    return term[1:] if term.startswith("-") else term


class AliasOrderingFilter(OrderingFilter):
    def get_ordering(
        self, request: Request, queryset: QuerySet, view: APIView
    ) -> list[str] | tuple[str, ...] | None:
        ordering: list[str] = []
        params = request.query_params.get(self.ordering_param)
        if params:
            fields = [param.strip() for param in params.split(",")]
            valid = self.remove_invalid_fields(queryset, fields, view, request)
            aliases: dict[str, str] = getattr(view, "ordering_aliases", {})
            for term in valid:
                prefix = "-" if term.startswith("-") else ""
                name = _bare(term)
                ordering.append(prefix + aliases.get(name, name))

        if not ordering:
            ordering = list(self.get_default_ordering(view) or ())

        tiebreak: tuple[str, ...] = tuple(getattr(view, "ordering_tiebreak", ()))
        if not ordering and not tiebreak:
            return None
        present = {_bare(term) for term in ordering}
        for term in tiebreak:
            if _bare(term) not in present:
                ordering.append(term)
                present.add(_bare(term))
        return ordering
