from django.contrib import admin

from .models import DiaryEntry


@admin.register(DiaryEntry)
class DiaryEntryAdmin(admin.ModelAdmin):
    """Read-only register: entries are managed through validated application flows."""

    list_display = ["date", "author", "created_at"]
    readonly_fields = [field.name for field in DiaryEntry._meta.fields]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
