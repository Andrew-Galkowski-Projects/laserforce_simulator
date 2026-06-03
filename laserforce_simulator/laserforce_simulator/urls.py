"""
URL configuration for laserforce_simulator project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("laserforce_simulator.api_urls")),
    path("help/", include("core.help_urls")),
    path("teams/", include("teams.urls")),
    path("matches/", include("matches.urls")),
    path("tournaments/", include("matches.tournament_urls")),
    path("seasons/", include("matches.season_urls")),
    path("leagues/", include("matches.league_urls")),
    path("maps/", include("core.urls")),
    path("tools/", include("core.tools_urls")),
    path(
        "players/", include("teams.player_urls")
    ),  # HX-01: must be above the "" include
    path("", core_views.landing, name="landing"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
