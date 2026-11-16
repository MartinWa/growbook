from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import DiaryEntry


@admin.register(DiaryEntry)
class DiaryEntryAdmin(ReadOnlyAdmin):
    """Read-only register: entries are managed through validated application flows."""

    list_display = ["date", "title", "author", "created_at"]
