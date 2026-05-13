from django.urls import path
from . import views

urlpatterns = [
    path("", views.map_list, name="map_list"),
    path("upload/", views.upload_map, name="upload_map"),
    path("<int:map_id>/editor/", views.map_editor, name="map_editor"),
    path("<int:map_id>/zones/", views.process_zones, name="process_zones"),
    path(
        "<int:map_id>/processed-image/", views.processed_image, name="processed_image"
    ),
    path("<int:map_id>/sight-lines/", views.get_sight_lines, name="get_sight_lines"),
    path(
        "<int:map_id>/sight-lines/compute/",
        views.compute_sight_lines_view,
        name="compute_sight_lines",
    ),
    path(
        "<int:map_id>/sight-lines/single-cell/",
        views.compute_single_cell_sight,
        name="compute_single_cell_sight",
    ),
    path(
        "<int:map_id>/sight-lines/save/",
        views.save_sight_lines,
        name="save_sight_lines",
    ),
    path("<int:map_id>/save/", views.save_zone_config, name="save_zone_config"),
    path("<int:map_id>/ranked-cells/", views.get_ranked_cells, name="get_ranked_cells"),
    path("<int:map_id>/strong-spots/", views.get_strong_spots, name="get_strong_spots"),
    path(
        "<int:map_id>/strong-spots/save/",
        views.save_strong_spots,
        name="save_strong_spots",
    ),
    path("<int:map_id>/spawn-cells/", views.get_spawn_cells, name="get_spawn_cells"),
]
