from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.writes import ValidatedModel

from .names import normalize_name


class Crop(ValidatedModel):
    name = models.CharField(_("Name"), max_length=100)
    name_key = models.CharField(max_length=300, editable=False)
    notes = models.TextField(_("Notes"), blank=True)
    is_archived = models.BooleanField(_("Archived"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        verbose_name = _("Crop")
        verbose_name_plural = _("Crops")
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="crop_name_key_unique"),
            models.CheckConstraint(
                condition=~Q(name="") & ~Q(name_key=""), name="crop_name_not_blank"
            ),
        ]

    def clean(self):
        super().clean()
        self.name, self.name_key = normalize_name(self.name)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("garden:crop-detail", args=[self.pk])


class Variety(ValidatedModel):
    crop = models.ForeignKey(
        Crop, on_delete=models.PROTECT, related_name="varieties", verbose_name=_("Crop")
    )
    name = models.CharField(_("Name"), max_length=150)
    name_key = models.CharField(max_length=450, editable=False)
    category = models.CharField(_("Category"), max_length=150, blank=True)
    description = models.TextField(_("Description"), blank=True)
    source = models.CharField(_("Source"), max_length=250, blank=True)
    experiences = models.TextField(_("Experiences"), blank=True)
    is_archived = models.BooleanField(_("Archived"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        verbose_name = _("Variety")
        verbose_name_plural = _("Varieties")
        constraints = [
            models.UniqueConstraint(fields=["crop", "name_key"], name="variety_crop_name_unique"),
            models.CheckConstraint(
                condition=~Q(name="") & ~Q(name_key=""), name="variety_name_not_blank"
            ),
        ]

    def clean(self):
        super().clean()
        self.name, self.name_key = normalize_name(self.name)

    def __str__(self):
        return f"{self.name} ({self.crop})"

    def get_absolute_url(self):
        return reverse("garden:variety-detail", args=[self.pk])


class GrowingArea(ValidatedModel):
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
        verbose_name=_("Parent area"),
    )
    name = models.CharField(_("Name"), max_length=150)
    name_key = models.CharField(max_length=450, editable=False)
    area_type = models.CharField(_("Type"), max_length=100, blank=True)
    length_cm = models.PositiveIntegerField(_("Length (cm)"), null=True, blank=True)
    width_cm = models.PositiveIntegerField(_("Width (cm)"), null=True, blank=True)
    description = models.TextField(_("Description"), blank=True)
    notes = models.TextField(_("Notes"), blank=True)
    is_archived = models.BooleanField(_("Archived"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        verbose_name = _("Growing area")
        verbose_name_plural = _("Growing areas")
        constraints = [
            models.UniqueConstraint(
                fields=["name_key"],
                condition=Q(parent__isnull=True),
                name="area_top_level_name_unique",
            ),
            models.UniqueConstraint(
                fields=["parent", "name_key"],
                condition=Q(parent__isnull=False),
                name="area_sibling_name_unique",
            ),
            models.CheckConstraint(
                condition=~Q(name="") & ~Q(name_key=""), name="area_name_not_blank"
            ),
            models.CheckConstraint(condition=~Q(parent=F("id")), name="area_not_own_parent"),
            models.CheckConstraint(
                condition=Q(length_cm__isnull=True) | Q(length_cm__gt=0),
                name="area_length_positive",
            ),
            models.CheckConstraint(
                condition=Q(width_cm__isnull=True) | Q(width_cm__gt=0),
                name="area_width_positive",
            ),
        ]

    def clean(self):
        super().clean()
        self.name, self.name_key = normalize_name(self.name)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("garden:area-detail", args=[self.pk])


class RegisterSubmission(ValidatedModel):
    """Immutable create/edit receipts; nullable targets retain deletion tombstones."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload_sha256 = models.CharField(max_length=64)
    kind = models.CharField(max_length=10)
    crop = models.ForeignKey(
        Crop, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    variety = models.ForeignKey(
        Variety, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    area = models.ForeignKey(
        GrowingArea, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Only the matching target may be set. All-null means the record was deleted.
            models.CheckConstraint(
                condition=(
                    Q(kind="crop", variety__isnull=True, area__isnull=True)
                    | Q(kind="variety", crop__isnull=True, area__isnull=True)
                    | Q(kind="area", crop__isnull=True, variety__isnull=True)
                ),
                name="register_receipt_target_kind",
            ),
        ]
