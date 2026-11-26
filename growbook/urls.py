from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from django.views.i18n import set_language

from core.views import (
    api_token_delete,
    api_token_revoke,
    api_tokens,
    favicon,
    release_status,
    set_season,
)

urlpatterns = [
    path("favicon.ico", favicon, name="favicon"),
    path("release-status/", release_status, name="release-status"),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("accounts/language/", login_not_required(set_language), name="set_language"),
    path("accounts/season/", set_season, name="set_season"),
    path("accounts/tokens/", api_tokens, name="api-tokens"),
    path("accounts/tokens/<int:pk>/revoke/", api_token_revoke, name="api-token-revoke"),
    path("accounts/tokens/<int:pk>/delete/", api_token_delete, name="api-token-delete"),
    path("operator/", admin.site.urls),
    path("api/v1/", include("api.urls")),
    path("photos/", include("core.urls")),
    path("garden/", include("garden.urls")),
    path("economy/", include("economy.urls")),
    path("calendar/", include("planner.urls")),
    path("", include("diary.urls")),
]
