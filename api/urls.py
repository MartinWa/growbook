"""URLs for the version 1 API.

Every view is wrapped in ``login_not_required`` because Django's login-required
middleware runs before Django REST Framework: without the wrap an anonymous caller
would receive a redirect to the login page instead of a JSON 401.
"""

from django.contrib.auth.decorators import login_not_required
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("", login_not_required(views.DiscoveryView.as_view()), name="discovery"),
    path("schema/", login_not_required(views.schema_document), name="schema"),
    path("crops/", login_not_required(views.CropList.as_view()), name="crop-list"),
    path("crops/<int:pk>/", login_not_required(views.CropDetail.as_view()), name="crop-detail"),
    path("varieties/", login_not_required(views.VarietyList.as_view()), name="variety-list"),
    path(
        "varieties/<int:pk>/",
        login_not_required(views.VarietyDetail.as_view()),
        name="variety-detail",
    ),
    path("areas/", login_not_required(views.AreaList.as_view()), name="area-list"),
    path("areas/<int:pk>/", login_not_required(views.AreaDetail.as_view()), name="area-detail"),
    path("plantings/", login_not_required(views.PlantingList.as_view()), name="planting-list"),
    path(
        "plantings/<int:pk>/",
        login_not_required(views.PlantingDetail.as_view()),
        name="planting-detail",
    ),
    path("events/", login_not_required(views.EventList.as_view()), name="event-list"),
    path("events/<int:pk>/", login_not_required(views.EventDetail.as_view()), name="event-detail"),
    path(
        "evaluations/",
        login_not_required(views.EvaluationList.as_view()),
        name="evaluation-list",
    ),
    path(
        "observations/",
        login_not_required(views.ObservationList.as_view()),
        name="observation-list",
    ),
    path(
        "observations/<int:pk>/",
        login_not_required(views.ObservationDetail.as_view()),
        name="observation-detail",
    ),
]
