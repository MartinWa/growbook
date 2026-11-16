from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Photo, User


class ReadOnlyAdmin(admin.ModelAdmin):
    """Inspection screens leave mutations to validated application operations."""

    actions = None

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User)
class GardenUserAdmin(UserAdmin):
    """Account deletion loses identity; deactivate accounts instead."""

    actions = None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Photo)
class PhotoAdmin(ReadOnlyAdmin):
    list_display = ["id", "photo_owner", "original_format", "position", "uploaded_at"]
    list_select_related = ["entry", "area", "variety__crop", "planting__crop"]

    @admin.display(description=_("Owner"))
    def photo_owner(self, obj):
        return obj.owner
