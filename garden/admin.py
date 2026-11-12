from django.contrib import admin

from .models import Crop, CropCategory, GrowingArea, Planting, PlantingEvent, Variety


class ReadOnlyRegisterAdmin(admin.ModelAdmin):
    """Operator inspection only. All register mutations use the validated garden UI."""

    list_display = ["name", "is_archived", "updated_at"]
    list_filter = ["is_archived"]
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
