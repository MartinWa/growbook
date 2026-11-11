from django.urls import path

from .views import events, photos, plantings, registers

app_name = "garden"

urlpatterns = [
    path("", registers.garden_home, name="home"),
    path("categories/new/", registers.register_form, {"kind": "category"}, name="category-create"),
    path(
        "categories/uncategorized/",
        registers.register_list,
        {"kind": "variety", "uncategorized": True},
        name="uncategorized",
    ),
    path(
        "categories/all/",
        registers.register_list,
        {"kind": "variety", "all_crops": True},
        name="all-crops",
    ),
    path(
        "categories/<int:category_pk>/",
        registers.register_list,
        {"kind": "variety"},
        name="category-detail",
    ),
    path(
        "categories/<int:pk>/edit/",
        registers.register_form,
        {"kind": "category"},
        name="category-edit",
    ),
    path(
        "categories/<int:pk>/delete/",
        registers.register_delete,
        {"kind": "category"},
        name="category-delete",
    ),
    path("crops/", registers.register_list, {"kind": "crop"}, name="crop-list"),
    path("crops/new/", registers.register_form, {"kind": "crop"}, name="crop-create"),
    path("crops/<int:pk>/", registers.register_detail, {"kind": "crop"}, name="crop-detail"),
    path("crops/<int:pk>/edit/", registers.register_form, {"kind": "crop"}, name="crop-edit"),
    path(
        "crops/<int:pk>/archive/", registers.register_archive, {"kind": "crop"}, name="crop-archive"
    ),
    path("crops/<int:pk>/delete/", registers.register_delete, {"kind": "crop"}, name="crop-delete"),
    path("varieties/", registers.register_list, {"kind": "variety"}, name="variety-list"),
    path("varieties/new/", registers.register_form, {"kind": "variety"}, name="variety-create"),
    path(
        "varieties/<int:pk>/", registers.register_detail, {"kind": "variety"}, name="variety-detail"
    ),
    path(
        "varieties/<int:pk>/edit/",
        registers.register_form,
        {"kind": "variety"},
        name="variety-edit",
    ),
    path(
        "varieties/<int:pk>/archive/",
        registers.register_archive,
        {"kind": "variety"},
        name="variety-archive",
    ),
    path(
        "varieties/<int:pk>/delete/",
        registers.register_delete,
        {"kind": "variety"},
        name="variety-delete",
    ),
    path("areas/", registers.register_list, {"kind": "area"}, name="area-list"),
    path("areas/new/", registers.register_form, {"kind": "area"}, name="area-create"),
    path("areas/<int:pk>/", registers.register_detail, {"kind": "area"}, name="area-detail"),
    path("areas/<int:pk>/edit/", registers.register_form, {"kind": "area"}, name="area-edit"),
    path(
        "areas/<int:pk>/archive/", registers.register_archive, {"kind": "area"}, name="area-archive"
    ),
    path("areas/<int:pk>/delete/", registers.register_delete, {"kind": "area"}, name="area-delete"),
    path("plantings/", plantings.planting_list, name="planting-list"),
    path("plantings/history/", plantings.planting_list, {"history": True}, name="planting-history"),
    path("plantings/new/", plantings.planting_form, name="planting-create"),
    path("plantings/varieties/", plantings.planting_varieties, name="planting-varieties"),
    path("plantings/<int:pk>/", plantings.planting_detail, name="planting-detail"),
    path("plantings/<int:pk>/edit/", plantings.planting_form, name="planting-edit"),
    path("plantings/<int:pk>/delete/", plantings.planting_delete, name="planting-delete"),
    path(
        "plantings/<int:planting_pk>/events/new/", events.planting_event_form, name="event-create"
    ),
    path(
        "plantings/<int:planting_pk>/events/<int:pk>/edit/",
        events.planting_event_form,
        name="event-edit",
    ),
    path(
        "plantings/<int:planting_pk>/events/<int:pk>/delete/",
        events.planting_event_delete,
        name="event-delete",
    ),
    path(
        "crops/<int:pk>/history/",
        plantings.planting_list,
        {"history": True, "kind": "crop"},
        name="crop-history",
    ),
    path(
        "varieties/<int:pk>/history/",
        plantings.planting_list,
        {"history": True, "kind": "variety"},
        name="variety-history",
    ),
    path(
        "areas/<int:pk>/history/",
        plantings.planting_list,
        {"history": True, "kind": "area"},
        name="area-history",
    ),
    path("photos/<int:pk>/edit/", photos.photo_edit, name="photo-edit"),
    path("photos/<int:pk>/delete/", photos.photo_delete, name="photo-delete"),
]
