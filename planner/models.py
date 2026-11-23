"""Planned tasks, their links and one shared note per month."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.writes import ValidatedModel


class PlannedTask(ValidatedModel):
    """A task noted ahead of time, placed on the day it was planned or done."""

    title = models.CharField(_("Title"), max_length=200, blank=True, default="")
    note = models.TextField(_("Note"), blank=True)
    planned_date = models.DateField(_("Planned date"), db_index=True)
    completed_on = models.DateField(_("Completed on"), null=True, blank=True, db_index=True)
    submission_key = models.UUIDField(default=uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["planned_date", "id"]
        verbose_name = _("Planned task")
        verbose_name_plural = _("Planned tasks")
        constraints = [
            models.CheckConstraint(condition=~Q(title="") | ~Q(note=""), name="task_title_or_note"),
        ]

    def __str__(self):
        return self.title or self.note.strip().splitlines()[0] or str(self.planned_date)

    def get_absolute_url(self):
        return reverse("planner:task-edit", args=[self.pk])

    @property
    def is_done(self):
        return self.completed_on is not None

    @property
    def display_day(self):
        return self.completed_on or self.planned_date


class TaskLink(ValidatedModel):
    """One explicit connection from a task to exactly one garden record."""

    task = models.ForeignKey(PlannedTask, on_delete=models.CASCADE, related_name="links")
    area = models.ForeignKey(
        "garden.GrowingArea",
        on_delete=models.PROTECT,
        related_name="task_links",
        null=True,
        blank=True,
        verbose_name=_("Growing area"),
    )
    crop = models.ForeignKey(
        "garden.Crop",
        on_delete=models.PROTECT,
        related_name="task_links",
        null=True,
        blank=True,
        verbose_name=_("Crop"),
    )
    variety = models.ForeignKey(
        "garden.Variety",
        on_delete=models.PROTECT,
        related_name="task_links",
        null=True,
        blank=True,
        verbose_name=_("Variety"),
    )
    planting = models.ForeignKey(
        "garden.Planting",
        on_delete=models.PROTECT,
        related_name="task_links",
        null=True,
        blank=True,
        verbose_name=_("Planting"),
    )

    class Meta:
        ordering = ["id"]
        verbose_name = _("Task link")
        verbose_name_plural = _("Task links")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        area__isnull=False,
                        crop__isnull=True,
                        variety__isnull=True,
                        planting__isnull=True,
                    )
                    | Q(
                        area__isnull=True,
                        crop__isnull=False,
                        variety__isnull=True,
                        planting__isnull=True,
                    )
                    | Q(
                        area__isnull=True,
                        crop__isnull=True,
                        variety__isnull=False,
                        planting__isnull=True,
                    )
                    | Q(
                        area__isnull=True,
                        crop__isnull=True,
                        variety__isnull=True,
                        planting__isnull=False,
                    )
                ),
                name="task_link_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=["task", "area"],
                condition=Q(area__isnull=False),
                name="task_link_unique_area",
            ),
            models.UniqueConstraint(
                fields=["task", "crop"],
                condition=Q(crop__isnull=False),
                name="task_link_unique_crop",
            ),
            models.UniqueConstraint(
                fields=["task", "variety"],
                condition=Q(variety__isnull=False),
                name="task_link_unique_variety",
            ),
            models.UniqueConstraint(
                fields=["task", "planting"],
                condition=Q(planting__isnull=False),
                name="task_link_unique_planting",
            ),
        ]

    @property
    def target(self):
        return self.area or self.crop or self.variety or self.planting

    def __str__(self):
        return str(self.target)


class MonthNote(ValidatedModel):
    """The one note shared by everyone who reads a month."""

    year = models.PositiveIntegerField(_("Year"))
    month = models.PositiveSmallIntegerField(_("Month"))
    body = models.TextField(_("Note"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = _("Month note")
        verbose_name_plural = _("Month notes")
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="month_note_unique_month"),
            models.CheckConstraint(
                condition=Q(month__gte=1, month__lte=12), name="month_note_month_range"
            ),
        ]

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class PlannerSubmission(ValidatedModel):
    """Immutable task/note receipts, retaining deleted-target tombstones."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload_sha256 = models.CharField(max_length=64)
    kind = models.CharField(max_length=10)
    task = models.ForeignKey(
        PlannedTask, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.ForeignKey(
        MonthNote, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Calendar submission")
        verbose_name_plural = _("Calendar submissions")
        constraints = [
            models.CheckConstraint(
                condition=Q(task__isnull=True) | Q(note__isnull=True),
                name="planner_receipt_one_target",
            ),
            models.CheckConstraint(
                condition=Q(kind="task") | Q(kind="note"), name="planner_receipt_kind"
            ),
        ]

    @property
    def target(self):
        return self.task if self.kind == "task" else self.note
