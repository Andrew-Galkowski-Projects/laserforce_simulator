import mimetypes
from typing import Literal

from django.contrib.auth.decorators import login_not_required
from django.db.models import Exists, OuterRef, QuerySet, Subquery
from django.http import FileResponse, Http404
from django.utils.decorators import method_decorator
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from core import services
from core.api_forms import HeatmapParamsForm, SingleCellParamsForm, ZoneSizeParamsForm
from core.filters import ArenaMapFilterSet
from core.forms import (
    ComputeSightLinesForm,
    MapUploadForm,
    SightLinesSaveForm,
    StrongSpotsForm,
    ZoneConfigForm,
)
from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
from core.serializers import ArenaMapSerializer
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import plain_form_data
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin


class MapInUse(APIException):
    """ANG-01c — 409: a non-completed Season references the map (delete refused)."""

    status_code = 409
    default_code = "map_in_use"
    default_detail = "This map is used by a Season that is not completed."


def _zone_size_param(request: Request) -> int:
    """``?zone_size=`` validated by ``ZoneSizeParamsForm`` (400 on a bad value)."""
    form = ZoneSizeParamsForm(
        data=plain_form_data(ZoneSizeParamsForm, request.query_params)
    )
    validate_form_or_raise(form)
    return form.cleaned_data["zone_size"]


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
#
# ANG-01a — ArenaMap is shared reference data (ADR-0038): NOT ownership
# filtered, every authenticated Account sees every map.
#
# ANG-01c — writable with the HTML editor's shared posture: any authenticated
# Account may upload / edit / delete any map. No `update` / `partial_update`,
# so PUT / PATCH on the detail stay 405. Every editor endpoint is a detail
# action resolved through `get_object()` and delegates to `core.services`.
@method_decorator(login_not_required, name="dispatch")
class ArenaMapViewSet(
    ListOnlyFilterMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ArenaMap.objects.all()
    serializer_class = ArenaMapSerializer
    filterset_class = ArenaMapFilterSet
    ordering_fields = ("name", "created_at", "id")
    ordering = ("name",)
    ordering_tiebreak = ("id",)
    # `create` is multipart; the JSON actions override per action.
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self) -> QuerySet[ArenaMap]:
        confirmed = MapZoneConfig.objects.filter(
            arena_map=OuterRef("pk"), confirmed=True
        )
        # ANG-06h — `confirmed_zone_size` is `latest_confirmed_config().zone_size`
        # (newest confirmed row; pk breaks a created_at tie), null without one.
        # ANG-06-14 B7 — `simulation_ready` mirrors
        # `matches.forms._maps_with_confirmed_config`: a confirmed config AND a
        # red base AND a blue base. Filter only; not a serializer field.
        bases = MapBaseConfig.objects.filter(arena_map=OuterRef("pk"))
        return ArenaMap.objects.annotate(
            has_confirmed_config=Exists(confirmed),
            simulation_ready=Exists(confirmed)
            & Exists(bases.filter(base_type="red"))
            & Exists(bases.filter(base_type="blue")),
            confirmed_zone_size=Subquery(
                confirmed.order_by("-created_at", "-pk").values("zone_size")[:1]
            ),
        )

    def list(self, request: Request, *args: object, **kwargs: object) -> Response:
        """ANG-06h — seed the default maps first, exactly as the HTML list does."""
        services.ensure_default_maps()
        return super().list(request, *args, **kwargs)

    def create(self, request: Request) -> Response:
        """Upload a map image (multipart ``name`` + ``image``); 201 with the map."""
        form = MapUploadForm(data=request.data, files=request.FILES)
        validate_form_or_raise(form)
        try:
            arena_map = services.upload_map(
                form.cleaned_data["name"], form.cleaned_data["image"]
            )
        except services.InvalidMapImage:
            raise EnvelopeValidationError(
                [
                    {
                        "code": "invalid_image",
                        "message": str(
                            MapUploadForm.base_fields["image"].error_messages[
                                "invalid_image"
                            ]
                        ),
                        "field": "image",
                    }
                ]
            )
        arena_map = self.get_queryset().get(pk=arena_map.pk)
        return Response(
            self.get_serializer(arena_map).data, status=status.HTTP_201_CREATED
        )

    def perform_destroy(self, instance: ArenaMap) -> None:
        try:
            services.delete_map(instance)
        except services.MapInUseError:
            raise MapInUse()

    # -- image / zones ------------------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path="processed-image",
        url_name="processed-image",
    )
    def processed_image(self, request: Request, pk: str | None = None) -> FileResponse:
        """The B&W processed image — a raw ``image/png``, never enveloped."""
        arena_map = self.get_object()
        return FileResponse(
            open(services.processed_image_path(arena_map), "rb"),
            content_type="image/png",
        )

    @action(detail=True, methods=["get"], url_path="image", url_name="image")
    def image(self, request: Request, pk: str | None = None) -> FileResponse:
        """ANG-06h — the original uploaded image, raw (never enveloped), its
        content type guessed from the file; 404 when the map has no image file.
        The SPA cannot pass the ``/media/`` session login gate."""
        arena_map = self.get_object()
        path = services.original_image_path(arena_map)
        if path is None:
            raise Http404("This map has no image file.")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(open(path, "rb"), content_type=content_type)

    @action(detail=True, methods=["get"], url_path="zones", url_name="zones")
    def zones(self, request: Request, pk: str | None = None) -> Response:
        """Zone detection for ``?zone_size=``, overlaid with the confirmed config."""
        arena_map = self.get_object()
        zone_size = _zone_size_param(request)
        return Response(services.detect_zones_with_overlay(arena_map, zone_size))

    @action(
        detail=True,
        methods=["get"],
        url_path="zone-config",
        url_name="zone-config",
        parser_classes=[JSONParser],
    )
    def zone_config(self, request: Request, pk: str | None = None) -> Response:
        """The confirmed zone config plus the map's bases."""
        arena_map = self.get_object()
        return Response(services.get_zone_config(arena_map))

    @zone_config.mapping.put
    def replace_zone_config(self, request: Request, pk: str | None = None) -> Response:
        """Full replace of the confirmed config + bases; returns the saved state."""
        arena_map = self.get_object()
        form = ZoneConfigForm(data=plain_form_data(ZoneConfigForm, request.data))
        validate_form_or_raise(form)
        services.save_zone_config(arena_map, **form.cleaned_data)
        return Response(services.get_zone_config(arena_map))

    # -- sight lines --------------------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path="sight-lines",
        url_name="sight-lines",
        parser_classes=[JSONParser],
    )
    def sight_lines(self, request: Request, pk: str | None = None) -> Response:
        """Stored sight lines + base sights for ``?zone_size=``."""
        arena_map = self.get_object()
        zone_size = _zone_size_param(request)
        return Response(services.get_sight_lines(arena_map, zone_size))

    def _save_sight_lines(
        self, request: Request, mode: Literal["replace", "merge"]
    ) -> Response:
        arena_map = self.get_object()
        form = SightLinesSaveForm(
            data=plain_form_data(SightLinesSaveForm, request.data)
        )
        validate_form_or_raise(form)
        zone_size = form.cleaned_data["zone_size"]
        services.save_sight_lines(
            arena_map,
            zone_size,
            sight_data=form.cleaned_data["sight_data"],
            base_sights=form.cleaned_data["base_sights"],
            mode=mode,
        )
        return Response(services.get_sight_lines(arena_map, zone_size))

    @sight_lines.mapping.put
    def replace_sight_lines(self, request: Request, pk: str | None = None) -> Response:
        """Replace the stored sight lines (re-ranks + reseeds strong spots)."""
        return self._save_sight_lines(request, "replace")

    @sight_lines.mapping.patch
    def merge_sight_lines(self, request: Request, pk: str | None = None) -> Response:
        """Merge a batch into the stored sight lines (re-ranks only)."""
        return self._save_sight_lines(request, "merge")

    @action(
        detail=True,
        methods=["post"],
        url_path="sight-lines/compute",
        url_name="sight-lines-compute",
        parser_classes=[JSONParser],
    )
    def compute_sight_lines(self, request: Request, pk: str | None = None) -> Response:
        """Synchronous all-pairs LOS compute; returns ``{sight_data, zone_size}``."""
        arena_map = self.get_object()
        form = ComputeSightLinesForm(
            data=plain_form_data(ComputeSightLinesForm, request.data)
        )
        validate_form_or_raise(form)
        return Response(
            services.compute_and_store_sight_lines(
                arena_map,
                form.cleaned_data["zone_size"],
                red_spawn=form.cleaned_data["red_spawn"],
                blue_spawn=form.cleaned_data["blue_spawn"],
            )
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="sight-lines/cell",
        url_name="sight-lines-cell",
    )
    def sight_line_cell(self, request: Request, pk: str | None = None) -> Response:
        """Single-cell LOS for ``?zone_size=&r=&c=`` (``[]`` off-grid)."""
        arena_map = self.get_object()
        form = SingleCellParamsForm(
            data=plain_form_data(SingleCellParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        cleaned = form.cleaned_data
        return Response(
            services.compute_single_cell(
                arena_map, cleaned["zone_size"], cleaned["r"], cleaned["c"]
            )
        )

    # -- ranking / strong spots / spawns ------------------------------------

    @action(
        detail=True, methods=["get"], url_path="ranked-cells", url_name="ranked-cells"
    )
    def ranked_cells(self, request: Request, pk: str | None = None) -> Response:
        """``MapCellRankingConfig`` cells for ``?zone_size=``."""
        arena_map = self.get_object()
        zone_size = _zone_size_param(request)
        return Response(services.get_ranked_cells(arena_map, zone_size))

    @action(
        detail=True,
        methods=["get"],
        url_path="strong-spots",
        url_name="strong-spots",
        parser_classes=[JSONParser],
    )
    def strong_spots(self, request: Request, pk: str | None = None) -> Response:
        """Heavy strong spots for ``?zone_size=``."""
        arena_map = self.get_object()
        zone_size = _zone_size_param(request)
        return Response(services.get_strong_spots(arena_map, zone_size))

    @strong_spots.mapping.put
    def replace_strong_spots(self, request: Request, pk: str | None = None) -> Response:
        """Replace the Heavy strong spots; returns the saved ``{cells}``."""
        arena_map = self.get_object()
        form = StrongSpotsForm(data=plain_form_data(StrongSpotsForm, request.data))
        validate_form_or_raise(form)
        zone_size = form.cleaned_data["zone_size"]
        services.save_strong_spots(arena_map, zone_size, form.cleaned_data["cells"])
        return Response(services.get_strong_spots(arena_map, zone_size))

    @action(
        detail=True, methods=["get"], url_path="spawn-cells", url_name="spawn-cells"
    )
    def spawn_cells(self, request: Request, pk: str | None = None) -> Response:
        """``{red_spawn, blue_spawn}`` from the confirmed config (no params)."""
        arena_map = self.get_object()
        return Response(services.get_spawn_cells(arena_map))

    # -- heatmap ------------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="heatmap", url_name="heatmap")
    def heatmap(self, request: Request, pk: str | None = None) -> Response:
        """RES-04 multi-round aggregate, scoped to the requester's visible rounds."""
        arena_map = self.get_object()
        form = HeatmapParamsForm(
            data=plain_form_data(HeatmapParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        return Response(
            services.aggregate_map_heatmap(
                arena_map,
                form.cleaned_data["zone_size"],
                form.cleaned_data["team_color"],
                request.user,
            )
        )
