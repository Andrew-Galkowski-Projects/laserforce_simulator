from rest_framework import serializers

from core.models import ArenaMap


class ArenaMapSerializer(serializers.ModelSerializer):
    """ANG-01a — read-only ArenaMap representation for ``/api/maps/``.

    ``has_confirmed_config`` reads the viewset's ``Exists`` annotation, so it is
    true exactly when ``latest_confirmed_config()`` would return a row.
    """

    # Absolute URL when the request is in the serializer context, else null.
    image = serializers.ImageField(read_only=True, use_url=True)
    has_confirmed_config = serializers.BooleanField(read_only=True)
    # ANG-06h — the viewset's `Subquery` annotation: the latest confirmed
    # config's `zone_size`, null exactly when `has_confirmed_config` is false.
    confirmed_zone_size = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = ArenaMap
        fields = [
            "id",
            "name",
            "image",
            "is_default",
            "img_width",
            "img_height",
            "created_at",
            "has_confirmed_config",
            "confirmed_zone_size",
        ]
