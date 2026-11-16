from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import (
    Crop,
    CropCategory,
    GrowingArea,
    Planting,
    PlantingCategory,
    PlantingCycle,
    PlantingEvent,
    Variety,
)


class ReadOnlyRegisterAdmin(ReadOnlyAdmin):
    """Operator inspection only. All register mutations use the validated garden UI."""

    list_display = ["name", "is_archived", "updated_at"]
    list_filter = ["is_archived"]


@admin.register(Crop)
class CropAdmin(ReadOnlyRegisterAdmin):
    pass


@admin.register(CropCategory)
class CropCategoryAdmin(ReadOnlyRegisterAdmin):
    list_display = ["name", "updated_at"]
    list_filter = []


@admin.register(Variety)
class VarietyAdmin(ReadOnlyRegisterAdmin):
    list_display = ["name", "crop", "is_archived", "updated_at"]
    list_select_related = ["crop"]


@admin.register(GrowingArea)
class GrowingAreaAdmin(ReadOnlyRegisterAdmin):
    list_display = ["name", "parent", "area_type", "is_archived", "updated_at"]
    list_select_related = ["parent"]


@admin.register(Planting)
class PlantingAdmin(ReadOnlyRegisterAdmin):
    list_display = ["id", "crop", "variety", "area", "start_year", "lifespan", "end_year"]
    list_select_related = ["crop", "variety", "area"]
    list_filter = ["lifespan", "start_year"]


@admin.register(PlantingEvent)
class PlantingEventAdmin(ReadOnlyRegisterAdmin):
    list_display = ["id", "planting", "kind", "planned_date", "actual_date"]
    list_select_related = ["planting__crop"]
    list_filter = ["kind"]


@admin.register(PlantingCycle)
class PlantingCycleAdmin(ReadOnlyRegisterAdmin):
    list_display = ["id", "status", "started_on", "completed_on", "updated_at"]
    list_filter = ["status"]


@admin.register(PlantingCategory)
class PlantingCategoryAdmin(ReadOnlyRegisterAdmin):
    list_display = ["name", "updated_at"]
    list_filter = []
