from django.apps import AppConfig


class TeamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "teams"

    def ready(self) -> None:
        # HX-02 — register PlayerRoundState post_save/post_delete handlers
        # that bump the global role-benchmark cache version.
        from teams import signals  # noqa: F401
