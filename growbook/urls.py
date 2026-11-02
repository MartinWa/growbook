from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from django.views.i18n import set_language

from core.views import favicon

urlpatterns = [
    path("favicon.ico", favicon, name="favicon"),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("accounts/language/", login_not_required(set_language), name="set_language"),
    path("operator/", admin.site.urls),
    path("photos/", include("core.urls")),
    path("garden/", include("garden.urls")),
    path("", include("diary.urls")),
]
