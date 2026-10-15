from django.contrib.auth.decorators import login_not_required
from django.db.models import Exists, OuterRef, QuerySet
from django.utils.decorators import method_decorator
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.filters import ArenaMapFilterSet
from core.models import ArenaMap, MapZoneConfig
from core.serializers import ArenaMapSerializer
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 403 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
#
# ANG-01a — ArenaMap is shared reference data (ADR-0038): NOT ownership
# filtered, every authenticated Account sees every map. Read-only.
@method_decorator(login_not_required, name="dispatch")
class ArenaMapViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet):
    queryset = ArenaMap.objects.all()
    serializer_class = ArenaMapSerializer
    filterset_class = ArenaMapFilterSet
    ordering_fields = ("name", "created_at", "id")
    ordering = ("name",)
    ordering_tiebreak = ("id",)

    def get_queryset(self) -> QuerySet[ArenaMap]:
        return ArenaMap.objects.annotate(
            has_confirmed_config=Exists(
                MapZoneConfig.objects.filter(arena_map=OuterRef("pk"), confirmed=True)
            )
        )
