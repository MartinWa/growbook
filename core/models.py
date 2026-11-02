from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .writes import ValidatedModel


class User(AbstractUser):
    """Django's standard user, defined before the first migration."""


class Photo(ValidatedModel):
    original = models.FileField(max_length=300)
    display = models.FileField(max_length=300)
    thumbnail = models.FileField(max_length=300)
    original_sha256 = models.CharField(max_length=64)
    original_format = models.CharField(max_length=10)
    original_bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    caption = models.CharField(_("Caption"), max_length=250, blank=True)
    position = models.PositiveIntegerField(_("Order"), default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    entry = models.ForeignKey(
        "diary.DiaryEntry", on_delete=models.CASCADE, related_name="photos", null=True, blank=True
    )
    area = models.ForeignKey(
        "garden.GrowingArea",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name=_("Growing area"),
    )
    variety = models.ForeignKey(
        "garden.Variety",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name=_("Variety"),
    )

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(entry__isnull=False, area__isnull=True, variety__isnull=True)
                    | Q(entry__isnull=True, area__isnull=False, variety__isnull=True)
                    | Q(entry__isnull=True, area__isnull=True, variety__isnull=False)
                ),
                name="photo_exactly_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(width__gt=0, height__gt=0, original_bytes__gt=0),
                name="photo_positive_dimensions_and_bytes",
            ),
        ]

    @property
    def owner(self):
        if self.entry_id is not None:
            return self.entry
        if self.area_id is not None:
            return self.area
        return self.variety

    def get_absolute_url(self):
        return reverse("photos:detail", args=[self.pk])

    def variant_url(self, variant):
        return reverse("photos:file", args=[self.pk, variant])

    @property
    def display_url(self):
        return self.variant_url("display")

    @property
    def thumbnail_url(self):
        return self.variant_url("thumbnail")

    @property
    def original_url(self):
        return self.variant_url("original")
