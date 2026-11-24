from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import MonthNote, PlannedTask, PlannerSubmission, TaskLink


@admin.register(PlannedTask)
class PlannedTaskAdmin(ReadOnlyAdmin):
    """Operator inspection only. Calendar tasks change through their validated forms."""

    list_display = ["title", "planned_date", "completed_on", "updated_at"]
    list_filter = ["completed_on"]


@admin.register(TaskLink)
class TaskLinkAdmin(ReadOnlyAdmin):
    list_display = ["task", "area", "crop", "variety", "planting"]
    list_select_related = ["task", "area", "crop", "variety", "planting"]


@admin.register(MonthNote)
class MonthNoteAdmin(ReadOnlyAdmin):
    list_display = ["year", "month", "updated_at"]


@admin.register(PlannerSubmission)
class PlannerSubmissionAdmin(ReadOnlyAdmin):
    list_display = ["key", "kind", "author", "task", "note", "created_at"]
    list_select_related = ["author", "task", "note"]
