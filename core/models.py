from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .writes import ValidatedModel


class User(AbstractUser):
    """Django's standard user, defined before the first migration."""


# The stored prefix narrows a lookup; the hash comparison decides. Both the issuer and
# the resolver use this one value, so raising it cannot silently orphan issued tokens.
PREFIX_LENGTH = 8


class ApiToken(ValidatedModel):
    """A named API credential; only a hash and a lookup prefix are stored.

    The secret itself is shown once when it is created and cannot be recovered.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    name = models.CharField(_("Name"), max_length=255)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=16, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(_("Expiry"), null=True, blank=True)
    revoked_at = models.DateTimeField(_("Revoked"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("API token")
        verbose_name_plural = _("API tokens")
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="api_token_name_not_blank"),
            models.UniqueConstraint(fields=["user", "name"], name="api_token_user_name_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_active(self):
        return self.revoked_at is None and not self.is_expired


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
    planting = models.ForeignKey(
        "garden.Planting",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name=_("Planting"),
    )
    category = models.ForeignKey(
        "garden.CropCategory",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name=_("Category"),
    )
    planting_category = models.ForeignKey(
        "garden.PlantingCategory",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name=_("Planting category"),
    )

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        entry__isnull=False,
                        area__isnull=True,
                        variety__isnull=True,
                        planting__isnull=True,
                        category__isnull=True,
                        planting_category__isnull=True,
                    )
                    | Q(
                        entry__isnull=True,
                        area__isnull=False,
                        variety__isnull=True,
                        planting__isnull=True,
                        category__isnull=True,
                        planting_category__isnull=True,
                    )
                    | Q(
                        entry__isnull=True,
                        area__isnull=True,
                        variety__isnull=False,
                        planting__isnull=True,
                        category__isnull=True,
                        planting_category__isnull=True,
                    )
                    | Q(
                        entry__isnull=True,
                        area__isnull=True,
                        variety__isnull=True,
                        planting__isnull=False,
                        category__isnull=True,
                        planting_category__isnull=True,
                    )
                    | Q(
                        entry__isnull=True,
                        area__isnull=True,
                        variety__isnull=True,
                        planting__isnull=True,
                        category__isnull=False,
                        planting_category__isnull=True,
                    )
                    | Q(
                        entry__isnull=True,
                        area__isnull=True,
                        variety__isnull=True,
                        planting__isnull=True,
                        category__isnull=True,
                        planting_category__isnull=False,
                    )
                ),
                name="photo_exactly_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(width__gt=0, height__gt=0, original_bytes__gt=0),
                name="photo_positive_dimensions_and_bytes",
            ),
            models.UniqueConstraint(fields=["category"], name="category_single_photo"),
            models.UniqueConstraint(
                fields=["planting_category"], name="planting_category_single_photo"
            ),
        ]

    @property
    def owner(self):
        if self.entry_id is not None:
            return self.entry
        if self.area_id is not None:
            return self.area
        if self.variety_id is not None:
            return self.variety
        if self.category_id is not None:
            return self.category
        if self.planting_category_id is not None:
            return self.planting_category
        return self.planting

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
