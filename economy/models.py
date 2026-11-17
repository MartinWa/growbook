"""Purchases, reserved allocation choices and plant sales; money uses integer cents."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Cast, Substr
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.writes import ValidatedModel
from garden.models import Planting

from .money import MAX_CENTS, money


class Expense(ValidatedModel):
    class Category(models.TextChoices):
        SEEDS = "seeds", _("Seeds")
        SOIL = "soil", _("Soil")
        FERTILIZER = "fertilizer", _("Fertilizer")
        SUPPLIES = "supplies", _("Supplies")
        EQUIPMENT = "equipment", _("Equipment")
        OTHER = "other", _("Other")

    class Unit(models.TextChoices):
        LITRES = "litres", _("Litres")
        KILOGRAMS = "kilograms", _("Kilograms")
        ITEMS = "items", _("Items")

    description = models.CharField(_("Description"), max_length=200)
    purchased_on = models.DateField(_("Purchase date"), default=timezone.localdate)
    purchase_year = models.PositiveIntegerField(editable=False)
    total_cents = models.PositiveBigIntegerField(validators=[MaxValueValidator(MAX_CENTS)])
    category = models.CharField(
        _("Expense category"), max_length=12, choices=Category.choices, default=Category.OTHER
    )
    purchased_quantity = models.DecimalField(
        _("Purchased quantity"), max_digits=14, decimal_places=3, null=True, blank=True
    )
    unit = models.CharField(_("Quantity unit"), max_length=10, choices=Unit.choices, blank=True)
    useful_life_years = models.PositiveSmallIntegerField(
        _("Useful life (whole years)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(9999)],
    )
    crop = models.ForeignKey(
        "garden.Crop", on_delete=models.PROTECT, null=True, blank=True, related_name="expenses"
    )
    variety = models.ForeignKey(
        "garden.Variety", on_delete=models.PROTECT, null=True, blank=True, related_name="expenses"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-purchased_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(description=""), name="expense_description_not_blank"
            ),
            models.CheckConstraint(
                condition=Q(total_cents__lte=MAX_CENTS), name="expense_amount_range"
            ),
            models.CheckConstraint(
                condition=Q(purchase_year__gte=1, purchase_year__lte=9999),
                name="expense_year_range",
            ),
            models.CheckConstraint(
                # Standalone SQLite backup integrity checks cannot use Django SQL functions.
                condition=Q(
                    purchase_year=Cast(Substr("purchased_on", 1, 4), models.IntegerField())
                ),
                name="expense_date_in_purchase_year",
            ),
            models.CheckConstraint(
                condition=Q(
                    category__in=["seeds", "soil", "fertilizer", "supplies", "equipment", "other"]
                ),
                name="expense_known_category",
            ),
            models.CheckConstraint(
                condition=Q(purchased_quantity__isnull=True, unit="")
                | Q(
                    purchased_quantity__isnull=False,
                    purchased_quantity__gt=0,
                    unit__in=["litres", "kilograms", "items"],
                ),
                name="expense_quantity_unit",
            ),
            models.CheckConstraint(
                condition=~Q(unit="items")
                | Q(purchased_quantity=Cast("purchased_quantity", models.BigIntegerField())),
                name="expense_whole_items",
            ),
            models.CheckConstraint(
                condition=Q(useful_life_years__isnull=True)
                | Q(category="equipment", useful_life_years__gte=1, useful_life_years__lte=9999),
                name="expense_equipment_period",
            ),
            models.CheckConstraint(
                condition=Q(useful_life_years__isnull=True)
                | Q(useful_life_years__lte=10000 - models.F("purchase_year")),
                name="expense_period_end_range",
            ),
            models.CheckConstraint(
                condition=Q(variety__isnull=True) | Q(crop__isnull=False),
                name="expense_variety_has_crop",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (self.purchased_quantity is None) != (not self.unit):
            errors["unit"] = _(
                "Enter both the purchased quantity and its unit, or leave both blank."
            )
        if self.purchased_on and self.purchased_on.year != self.purchase_year:
            errors["purchased_on"] = _("The purchase date must be in the recorded purchase year.")
        if (
            self.purchased_quantity is not None
            and self.unit == self.Unit.ITEMS
            and self.purchased_quantity != self.purchased_quantity.to_integral_value()
        ):
            errors["purchased_quantity"] = _("Items must be a whole number.")
        if self.useful_life_years and self.purchase_year + self.useful_life_years - 1 > 9999:
            errors["useful_life_years"] = _("The equipment period must end by year 9999.")
        if errors:
            raise ValidationError(errors)

    @property
    def total_price(self):
        return money(self.total_cents)

    def __str__(self):
        return self.description

    def get_absolute_url(self):
        return reverse("economy:expense-detail", args=[self.pk])


class Allocation(ValidatedModel):
    class Scope(models.TextChoices):
        SELECTED = "selected", _("Selected planting cycles")
        YEAR = "year", _("All planting cycles in the growing year")

    class Method(models.TextChoices):
        PLANTS = "plants", _("By current plant quantities")
        EQUAL = "equal", _("Equally between cycles")

    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="allocations")
    growing_year = models.PositiveIntegerField(
        _("Growing year"), validators=[MinValueValidator(1), MaxValueValidator(9999)]
    )
    amount_cents = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_CENTS)]
    )
    scope = models.CharField(max_length=8, choices=Scope.choices, default=Scope.SELECTED)
    method = models.CharField(max_length=6, choices=Method.choices, default=Method.PLANTS)
    cycles = models.ManyToManyField(
        "garden.PlantingCycle", through="AllocationCycle", related_name="expense_allocations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["growing_year", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_cents__gte=1, amount_cents__lte=MAX_CENTS),
                name="allocation_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(growing_year__gte=1, growing_year__lte=9999),
                name="allocation_year_range",
            ),
            models.CheckConstraint(
                condition=Q(scope__in=["selected", "year"]), name="allocation_known_scope"
            ),
            models.CheckConstraint(
                condition=Q(method__in=["plants", "equal"]), name="allocation_known_method"
            ),
        ]

    @property
    def amount(self):
        return money(self.amount_cents)

    def get_absolute_url(self):
        return self.expense.get_absolute_url() + f"?year={self.growing_year}#allocation-{self.pk}"


class AllocationCycle(ValidatedModel):
    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE, related_name="cycle_links")
    cycle = models.ForeignKey(
        "garden.PlantingCycle", on_delete=models.PROTECT, related_name="economy_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["allocation", "cycle"], name="allocation_cycle_unique")
        ]


class Sale(ValidatedModel):
    description = models.CharField(_("Description"), max_length=200)
    sold_on = models.DateField(_("Sale date"), default=timezone.localdate)
    quantity = models.PositiveIntegerField(_("Plants sold"), validators=[MinValueValidator(1)])
    total_cents = models.PositiveBigIntegerField(validators=[MaxValueValidator(MAX_CENTS)])
    unit_price_cents = models.PositiveBigIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(MAX_CENTS)]
    )
    cycle = models.ForeignKey(
        "garden.PlantingCycle",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="plant_sales",
    )
    variety = models.ForeignKey(
        "garden.Variety",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="plant_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sold_on", "-id"]
        constraints = [
            models.CheckConstraint(condition=~Q(description=""), name="sale_description_not_blank"),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="sale_quantity_positive"),
            models.CheckConstraint(
                condition=Q(total_cents__lte=MAX_CENTS), name="sale_amount_range"
            ),
            models.CheckConstraint(
                condition=Q(cycle__isnull=False) | Q(variety__isnull=False), name="sale_has_source"
            ),
            models.CheckConstraint(
                condition=Q(unit_price_cents__isnull=True)
                | Q(total_cents=models.F("unit_price_cents") * models.F("quantity")),
                name="sale_unit_price_total",
            ),
        ]

    @property
    def total_price(self):
        return money(self.total_cents)

    @property
    def unit_price(self):
        return money(self.unit_price_cents) if self.unit_price_cents is not None else None

    def source_member(self):
        if self.cycle_id is None:
            return None
        return min(self.cycle.members.all(), key=lambda member: member.pk, default=None)

    @property
    def crop(self):
        member = self.source_member()
        return member.crop if member else self.variety.crop

    @property
    def growing_year(self):
        member = self.source_member()
        if member is None:
            return self.sold_on.year
        if member.lifespan == Planting.Lifespan.ANNUAL:
            return member.start_year
        return max(member.start_year, min(self.sold_on.year, member.end_year or 9999))

    def __str__(self):
        return self.description

    def get_absolute_url(self):
        return reverse(
            "economy:list", query={"year": self.sold_on.year}, fragment=f"sale-{self.pk}"
        )


class EconomySubmission(ValidatedModel):
    """Immutable save receipts; deleted targets remain tombstones."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=10)
    payload_sha256 = models.CharField(max_length=64)
    expense = models.ForeignKey(
        Expense, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    allocation = models.ForeignKey(
        Allocation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sale = models.ForeignKey(
        Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="expense", allocation__isnull=True, sale__isnull=True)
                | Q(kind="allocation", expense__isnull=True, sale__isnull=True)
                | Q(kind="sale", expense__isnull=True, allocation__isnull=True),
                name="economy_receipt_target_kind",
            )
        ]
