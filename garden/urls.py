from django.urls import path

from . import views

app_name = "garden"

urlpatterns = [
    path("", views.garden_home, name="home"),
    path("crops/", views.register_list, {"kind": "crop"}, name="crop-list"),
    path("crops/new/", views.register_form, {"kind": "crop"}, name="crop-create"),
    path("crops/<int:pk>/", views.register_detail, {"kind": "crop"}, name="crop-detail"),
    path("crops/<int:pk>/edit/", views.register_form, {"kind": "crop"}, name="crop-edit"),
    path("crops/<int:pk>/archive/", views.register_archive, {"kind": "crop"}, name="crop-archive"),
    path("crops/<int:pk>/delete/", views.register_delete, {"kind": "crop"}, name="crop-delete"),
    path("varieties/", views.register_list, {"kind": "variety"}, name="variety-list"),
    path("varieties/new/", views.register_form, {"kind": "variety"}, name="variety-create"),
    path("varieties/<int:pk>/", views.register_detail, {"kind": "variety"}, name="variety-detail"),
    path("varieties/<int:pk>/edit/", views.register_form, {"kind": "variety"}, name="variety-edit"),
    path(
        "varieties/<int:pk>/archive/",
        views.register_archive,
        {"kind": "variety"},
        name="variety-archive",
    ),
    path(
        "varieties/<int:pk>/delete/",
        views.register_delete,
        {"kind": "variety"},
        name="variety-delete",
    ),
    path("areas/", views.register_list, {"kind": "area"}, name="area-list"),
    path("areas/new/", views.register_form, {"kind": "area"}, name="area-create"),
    path("areas/<int:pk>/", views.register_detail, {"kind": "area"}, name="area-detail"),
    path("areas/<int:pk>/edit/", views.register_form, {"kind": "area"}, name="area-edit"),
    path("areas/<int:pk>/archive/", views.register_archive, {"kind": "area"}, name="area-archive"),
    path("areas/<int:pk>/delete/", views.register_delete, {"kind": "area"}, name="area-delete"),
    path("plantings/", views.planting_list, name="planting-list"),
    path("plantings/history/", views.planting_list, {"history": True}, name="planting-history"),
    path("plantings/new/", views.planting_form, name="planting-create"),
    path("plantings/varieties/", views.planting_varieties, name="planting-varieties"),
    path("plantings/<int:pk>/", views.planting_detail, name="planting-detail"),
    path("plantings/<int:pk>/edit/", views.planting_form, name="planting-edit"),
    path("plantings/<int:pk>/delete/", views.planting_delete, name="planting-delete"),
    path("plantings/<int:planting_pk>/events/new/", views.planting_event_form, name="event-create"),
    path(
        "plantings/<int:planting_pk>/events/<int:pk>/edit/",
        views.planting_event_form,
        name="event-edit",
    ),
    path(
        "plantings/<int:planting_pk>/events/<int:pk>/delete/",
        views.planting_event_delete,
        name="event-delete",
    ),
    path(
        "crops/<int:pk>/history/",
        views.planting_list,
        {"history": True, "kind": "crop"},
        name="crop-history",
    ),
    path(
        "varieties/<int:pk>/history/",
        views.planting_list,
        {"history": True, "kind": "variety"},
        name="variety-history",
    ),
    path(
        "areas/<int:pk>/history/",
        views.planting_list,
        {"history": True, "kind": "area"},
        name="area-history",
    ),
    path("photos/<int:pk>/edit/", views.photo_edit, name="photo-edit"),
    path("photos/<int:pk>/delete/", views.photo_delete, name="photo-delete"),
]
