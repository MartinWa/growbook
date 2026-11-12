from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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


class CropCategory(ValidatedModel):
    name = models.CharField(_("Name"), max_length=150)
    name_key = models.CharField(max_length=450, editable=False)
    crops = models.ManyToManyField(Crop, through="CropCategoryLink", related_name="categories")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="category_name_key_unique"),
            models.CheckConstraint(
                condition=~Q(name="") & ~Q(name_key=""), name="category_name_not_blank"
            ),
        ]

    def clean(self):
        super().clean()
        self.name, self.name_key = normalize_name(self.name)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("garden:category-detail", args=[self.pk])


class CropCategoryLink(ValidatedModel):
    category = models.ForeignKey(CropCategory, on_delete=models.CASCADE)
    crop = models.ForeignKey(Crop, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["category", "crop"], name="category_crop_unique"),
        ]


class Variety(ValidatedModel):
    crop = models.ForeignKey(
        Crop, on_delete=models.PROTECT, related_name="varieties", verbose_name=_("Crop")
    )
    name = models.CharField(_("Name"), max_length=150)
    name_key = models.CharField(max_length=450, editable=False)
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


class Planting(ValidatedModel):
    class Lifespan(models.TextChoices):
        ANNUAL = "annual", _("Annual")
        PERSISTENT = "persistent", _("Persistent")

    batch_key = models.UUIDField(default=uuid4, db_index=True, editable=False)
    start_year = models.PositiveIntegerField(
        _("Start year"), db_index=True, validators=[MinValueValidator(1), MaxValueValidator(9999)]
    )
    lifespan = models.CharField(
        _("Lifespan"), max_length=20, choices=Lifespan.choices, default=Lifespan.ANNUAL
    )
    end_year = models.PositiveIntegerField(
        _("End year"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(9999)],
    )
    crop = models.ForeignKey(
        Crop, on_delete=models.PROTECT, related_name="plantings", verbose_name=_("Crop")
    )
    variety = models.ForeignKey(
        Variety,
        on_delete=models.PROTECT,
        related_name="plantings",
        null=True,
        blank=True,
        verbose_name=_("Variety"),
    )
    area = models.ForeignKey(
        GrowingArea,
        on_delete=models.PROTECT,
        related_name="plantings",
        null=True,
        blank=True,
        verbose_name=_("Growing area"),
    )
    label = models.CharField(_("Planting label"), max_length=251, blank=True)
    quantity = models.PositiveIntegerField(_("Quantity"), null=True, blank=True)
    notes = models.TextField(_("Notes"), blank=True)
    evaluation = models.TextField(_("Evaluation"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_year", "crop__name_key", "variety__name_key", "id"]
        verbose_name = _("Planting")
        verbose_name_plural = _("Plantings")
        constraints = [
            models.UniqueConstraint(
                fields=["batch_key", "variety"],
                condition=Q(variety__isnull=False),
                name="planting_batch_variety_unique",
            ),
            models.UniqueConstraint(
                fields=["batch_key"],
                condition=Q(variety__isnull=True),
                name="planting_batch_unknown_unique",
            ),
            models.CheckConstraint(
                condition=Q(start_year__gte=1, start_year__lte=9999),
                name="planting_start_year_range",
            ),
            models.CheckConstraint(
                condition=Q(end_year__isnull=True) | Q(end_year__gte=1, end_year__lte=9999),
                name="planting_end_year_range",
            ),
            models.CheckConstraint(
                condition=Q(lifespan__in=["annual", "persistent"]),
                name="planting_known_lifespan",
            ),
            models.CheckConstraint(
                condition=~Q(lifespan="annual") | Q(end_year__isnull=True),
                name="planting_annual_no_end_year",
            ),
            models.CheckConstraint(
                condition=Q(end_year__isnull=True) | Q(end_year__gte=F("start_year")),
                name="planting_end_not_before_start",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.lifespan == self.Lifespan.ANNUAL and self.end_year is not None:
            errors["end_year"] = _("An annual planting has no end year.")
        if (
            isinstance(self.start_year, int)
            and isinstance(self.end_year, int)
            and self.end_year < self.start_year
        ):
            errors["end_year"] = _("The end year cannot precede the start year.")
        if self.variety_id is not None:
            # Fetch current taxonomy, not a possibly stale cached variety instance.
            variety_crop = (
                Variety.objects.filter(pk=self.variety_id).values_list("crop_id", flat=True).first()
            )
            if variety_crop is not None and variety_crop != self.crop_id:
                errors["variety"] = _("Choose a variety of the selected crop, or leave it blank.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.label or f"{self.crop} · {self.start_year}"

    def get_absolute_url(self):
        return reverse("garden:planting-detail", args=[self.pk])


class PlantingEvent(ValidatedModel):
    class Kind(models.TextChoices):
        SOWING = "sowing", _("Sowing")
        TRANSPLANT = "transplant", _("Transplanting")
        HARVEST_START = "harvest_start", _("Harvest start")
        HARVEST_END = "harvest_end", _("Harvest end")
        OTHER = "other", _("Other")

    planting = models.ForeignKey(Planting, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(_("Event type"), max_length=30, choices=Kind.choices)
    planned_date = models.DateField(_("Planned date"), null=True, blank=True)
    actual_date = models.DateField(_("Actual date"), null=True, blank=True)
    note = models.TextField(_("Note"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("Planting event")
        verbose_name_plural = _("Planting events")
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    kind__in=["sowing", "transplant", "harvest_start", "harvest_end", "other"]
                ),
                name="planting_event_known_kind",
            ),
            models.CheckConstraint(
                condition=Q(planned_date__isnull=False) | Q(actual_date__isnull=False),
                name="planting_event_has_date",
            ),
        ]

    def clean(self):
        super().clean()
        if self.planned_date is None and self.actual_date is None:
            raise ValidationError(_("Set a planned date, an actual date, or both."))

    def get_absolute_url(self):
        return self.planting.get_absolute_url() + f"#event-{self.pk}"

    def __str__(self):
        return f"{self.get_kind_display()} · {self.actual_date or self.planned_date}"


class PlantingSubmission(ValidatedModel):
    """Immutable planting/event save receipts; deletions retain all-null tombstones."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload_sha256 = models.CharField(max_length=64)
    kind = models.CharField(max_length=10)
    planting = models.ForeignKey(
        Planting, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    event = models.ForeignKey(
        PlantingEvent, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="planting", event__isnull=True) | Q(kind="event", planting__isnull=True)
                ),
                name="planting_receipt_target_kind",
            ),
        ]


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
    category = models.ForeignKey(
        CropCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Only the matching target may be set. All-null means the record was deleted.
            models.CheckConstraint(
                condition=(
                    Q(kind="crop", variety__isnull=True, area__isnull=True, category__isnull=True)
                    | Q(kind="variety", crop__isnull=True, area__isnull=True, category__isnull=True)
                    | Q(kind="area", crop__isnull=True, variety__isnull=True, category__isnull=True)
                    | Q(kind="category", crop__isnull=True, variety__isnull=True, area__isnull=True)
                ),
                name="register_receipt_target_kind",
            ),
        ]
