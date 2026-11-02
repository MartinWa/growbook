from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.writes import ValidatedModel


class DiaryEntry(ValidatedModel):
    date = models.DateField(_("Date"), default=timezone.localdate, db_index=True)
    body = models.TextField(_("Note"), blank=True)
    search_text = models.TextField(editable=False, blank=True, default="")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    submission_key = models.UUIDField(unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        verbose_name = _("Diary entry")
        verbose_name_plural = _("Diary entries")

    def get_absolute_url(self):
        return reverse("diary:detail", args=[self.pk])


class DiaryLink(ValidatedModel):
    """One explicit connection from an entry to exactly one garden record."""

    entry = models.ForeignKey(DiaryEntry, on_delete=models.CASCADE, related_name="links")
    area = models.ForeignKey(
        "garden.GrowingArea",
        on_delete=models.PROTECT,
        related_name="diary_links",
        null=True,
        blank=True,
        verbose_name=_("Growing area"),
    )
    crop = models.ForeignKey(
        "garden.Crop",
        on_delete=models.PROTECT,
        related_name="diary_links",
        null=True,
        blank=True,
        verbose_name=_("Crop"),
    )
    variety = models.ForeignKey(
        "garden.Variety",
        on_delete=models.PROTECT,
        related_name="diary_links",
        null=True,
        blank=True,
        verbose_name=_("Variety"),
    )
    planting = models.ForeignKey(
        "garden.Planting",
        on_delete=models.PROTECT,
        related_name="diary_links",
        null=True,
        blank=True,
        verbose_name=_("Planting"),
    )

    class Meta:
        ordering = ["id"]
        verbose_name = _("Diary link")
        verbose_name_plural = _("Diary links")
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
                name="diary_link_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=["entry", "area"],
                condition=Q(area__isnull=False),
                name="diary_link_unique_area",
            ),
            models.UniqueConstraint(
                fields=["entry", "crop"],
                condition=Q(crop__isnull=False),
                name="diary_link_unique_crop",
            ),
            models.UniqueConstraint(
                fields=["entry", "variety"],
                condition=Q(variety__isnull=False),
                name="diary_link_unique_variety",
            ),
            models.UniqueConstraint(
                fields=["entry", "planting"],
                condition=Q(planting__isnull=False),
                name="diary_link_unique_planting",
            ),
        ]

    @property
    def target(self):
        return self.area or self.crop or self.variety or self.planting

    def __str__(self):
        return str(self.target)


class DiarySubmission(ValidatedModel):
    """Immutable creation receipt, retained when its entry is removed."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload_sha256 = models.CharField(max_length=64)
    entry = models.OneToOneField(DiaryEntry, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
