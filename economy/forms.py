from decimal import Decimal
from uuid import UUID, uuid4

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import DeleteForm, VersionField, describe_fields
from garden.forms import RecordIDChoiceField
from garden.models import Crop, PlantingCycle, Variety
from garden.queries import plantings_in_year

from .models import Allocation, Expense
from .services import allocation_uses_quantity


def _quantity_step(expense):
    """One whole item, or one percent of the recorded quantity for measured units."""
    if expense.unit == Expense.Unit.ITEMS:
        return "1"
    step = (expense.purchased_quantity / 100).quantize(Decimal("0.001"))
    return f"{max(step, Decimal('0.001')):f}"


def amount_field(label, **kwargs):
    return forms.DecimalField(
        label=label,
        max_digits=12,
        decimal_places=2,
        min_value=Decimal(0),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        **kwargs,
    )


def _cycle_ids(values):
    identifiers = []
    for value in values:
        try:
            identifiers.append(CycleChoiceField().clean(value))
        except forms.ValidationError:
            continue
    return identifiers


def cycle_choices(year=None, keep=()):
    """Cycles with their member labels; ``year`` limits them to one growing year.

    Every cycle active in the year is offered, whatever its status: costs are commonly
    allocated after a cycle ends. Plantings have no archive state of their own.
    """
    grouped = {}
    if year is None:
        for cycle in PlantingCycle.objects.prefetch_related("members__crop", "members__variety"):
            grouped[cycle.pk] = sorted(cycle.members.all(), key=lambda member: member.pk)
    else:
        records = plantings_in_year(year).select_related("cycle", "crop", "variety").order_by("pk")
        for planting in records:
            grouped.setdefault(planting.cycle_id, []).append(planting)
    kept = set(_cycle_ids(keep)) - set(grouped)
    if kept:
        for cycle in PlantingCycle.objects.filter(pk__in=kept).prefetch_related(
            "members__crop", "members__variety"
        ):
            grouped[cycle.pk] = sorted(cycle.members.all(), key=lambda member: member.pk)
    choices, sources, details = [], {}, {}
    for key, members in grouped.items():
        if not members:
            continue
        first = members[0]
        varieties = ", ".join(
            str(member.variety.name) if member.variety else str(_("Unknown variety"))
            for member in members
        )
        name = first.label or first.crop.name
        choices.append((str(key), f"{name} · {first.start_year} · {varieties}"))
        details[str(key)] = {
            "status": first.cycle.status,
            "name": name,
            "meta": f"{first.start_year} · {varieties}",
        }
        sources[str(key)] = {
            "crop": first.crop_id,
            "varieties": [member.variety_id for member in members if member.variety_id is not None],
            "default_variety": first.variety_id if len(members) == 1 else None,
        }
    return sorted(choices, key=lambda choice: choice[1].casefold()), sources, details


class RangeInput(forms.TextInput):
    """A range control; Django ships no range widget."""

    input_type = "range"


class CycleChoiceField(forms.ChoiceField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            return UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise forms.ValidationError(_("Choose a valid record.")) from exc

    def validate(self, value):
        # Writers resolve receipts before checking whether a cycle still exists.
        forms.Field.validate(self, value)


class CycleMultipleChoiceField(forms.MultipleChoiceField):
    def clean(self, value):
        if value in self.empty_values:
            return []
        if not isinstance(value, (list, tuple)):
            raise forms.ValidationError(_("Choose a valid record."))
        return sorted({CycleChoiceField().clean(item) for item in value}, key=str)


def preserve_cycles(field, values):
    choices = list(field.choices)
    known = {str(key) for key, _label in choices}
    for value in values:
        try:
            identifier = CycleChoiceField().clean(value)
        except forms.ValidationError:
            continue
        if identifier is not None and str(identifier) not in known:
            choices.append((str(identifier), _("Unavailable selection")))
            known.add(str(identifier))
    field.choices = choices


def taxonomy_choices(form, record, *, has_crop):
    crop_id = record.crop_id if record is not None and has_crop else None
    variety_id = record.variety_id if record is not None else None
    if has_crop:
        form.fields["crop"].queryset = Crop.objects.filter(Q(is_archived=False) | Q(pk=crop_id))
    form.fields["variety"].queryset = Variety.objects.select_related("crop").filter(
        Q(is_archived=False, crop__is_archived=False) | Q(pk=variety_id)
    )
    for name in ("crop", "variety") if has_crop else ("variety",):
        value = form.data.get(name) if form.is_bound else form.initial.get(name)
        form.fields[name].preserve_selection(value)


class SaveForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)


class ExpenseForm(SaveForm):
    description = forms.CharField(label=_("Description"), max_length=200)
    purchased_on = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    total_price = amount_field(_("Price"))
    category = forms.ChoiceField(
        label=_("Category"),
        choices=Expense.Category.choices,
        required=False,
        initial=Expense.Category.OTHER,
    )
    purchased_quantity = forms.DecimalField(
        label=_("Quantity"),
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    unit = forms.ChoiceField(
        label=_("Quantity unit"),
        choices=[("", _("Choose a unit")), *Expense.Unit.choices],
    )
    useful_life_years = forms.IntegerField(
        label=_("Useful life (whole years)"),
        required=False,
        min_value=1,
        max_value=9999,
        help_text=_(
            "Equipment only. Enter whole years to spread the cost from the purchase year; "
            "leave blank to charge the purchase year in full."
        ),
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        if record is None:
            self.initial.setdefault("purchased_on", timezone.localdate())
        else:
            year = record.purchase_year
            self.fields["purchased_on"].widget.attrs.update(
                {"min": f"{year:04d}-01-01", "max": f"{year:04d}-12-31"}
            )
        describe_fields(self)

    def clean(self):
        values = super().clean()
        if not values.get("category"):
            values["category"] = Expense.Category.OTHER
        if values.get("useful_life_years") and values.get("category") != Expense.Category.EQUIPMENT:
            self.add_error(
                "useful_life_years", _("Choose Equipment to spread a cost across years.")
            )
        return values

    @property
    def details_fields(self):
        return [self[name] for name in ("category", "useful_life_years")]

    @property
    def details_open(self):
        return any(
            field.errors
            or field.value()
            and (field.name != "category" or field.value() != Expense.Category.OTHER)
            for field in self.details_fields
        )


class AllocationValuesForm(forms.Form):
    """Share or quantity, group and split choices shared by the saved form and its preview."""

    growing_year = forms.IntegerField(label=_("Growing year"), min_value=1, max_value=9999)
    share = forms.DecimalField(
        label=_("Share used"),
        max_digits=6,
        decimal_places=3,
        min_value=Decimal("0"),
        max_value=100,
        initial=Decimal("100"),
        widget=RangeInput(attrs={"min": 0, "max": 100, "step": "5"}),
    )
    quantity_used = forms.DecimalField(
        label=_("Quantity used"),
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0"),
        widget=RangeInput(attrs={"min": 0, "step": "1"}),
    )
    scope = forms.ChoiceField(
        label=_("Planting group"),
        choices=Allocation.Scope.choices,
        initial=Allocation.Scope.SELECTED,
        widget=forms.RadioSelect,
    )
    cycle_ids = CycleMultipleChoiceField(
        label=_("Planting cycles"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    method = forms.ChoiceField(
        label=_("Divide the chosen amount"),
        choices=Allocation.Method.choices,
        initial=Allocation.Method.PLANTS,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, expense=None, year=None, keep=(), minimum_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.expense = expense
        self.quantity_mode = expense is not None and allocation_uses_quantity(expense)
        if self.quantity_mode:
            self.fields.pop("share")
            quantity = expense.purchased_quantity
            field = self.fields["quantity_used"]
            field.max_value = quantity
            field.widget.attrs.update(
                {
                    "max": f"{quantity:f}",
                    "min": 0,
                    "step": _quantity_step(expense),
                }
            )
        else:
            self.fields.pop("quantity_used")
        self.minimum_year = minimum_year
        if minimum_year is not None:
            self.fields["growing_year"].widget.attrs["min"] = minimum_year
        if year is None:
            year = (
                self.data.get("growing_year") if self.is_bound else self.initial.get("growing_year")
            )
        try:
            year = int(year)
        except TypeError, ValueError:
            year = None
        year = year if year is not None and 1 <= year <= 9999 else None
        selected = (
            self.data.getlist("cycle_ids")
            if self.is_bound and hasattr(self.data, "getlist")
            else self.data.get("cycle_ids", [])
            if self.is_bound
            else self.initial.get("cycle_ids", [])
        )
        selected = [*selected, *keep]
        choices, _sources, details = cycle_choices(year=year, keep=selected)
        field = self.fields["cycle_ids"]
        field.choices = choices
        preserve_cycles(field, selected)
        chosen = {str(value) for value in _cycle_ids(selected)}
        field.cycle_entries = [
            {
                "value": value,
                "label": label,
                "status": details.get(value, {}).get("status", ""),
                "name": details.get(value, {}).get("name", label),
                "meta": details.get(value, {}).get("meta", ""),
                "checked": value in chosen,
            }
            for value, label in field.choices
        ]
        field.completed_count = sum(entry["status"] == "completed" for entry in field.cycle_entries)
        field.open_completed = any(
            entry["status"] == "completed" and entry["checked"] for entry in field.cycle_entries
        )
        describe_fields(self)

    def clean_growing_year(self):
        value = self.cleaned_data["growing_year"]
        if self.minimum_year is not None and value < self.minimum_year:
            raise forms.ValidationError(_("Choose the purchase year or a later growing year."))
        return value

    def clean_quantity_used(self):
        value = self.cleaned_data["quantity_used"]
        if self.expense.unit == Expense.Unit.ITEMS and value != value.to_integral_value():
            raise forms.ValidationError(_("Items must be a whole number."))
        return value

    def clean(self):
        values = super().clean()
        if values.get("scope") == Allocation.Scope.YEAR:
            values["cycle_ids"] = []
        elif not values.get("cycle_ids"):
            self.add_error("cycle_ids", _("Choose at least one existing planting cycle."))
        return values


class AllocationPreviewForm(AllocationValuesForm):
    """The same value fields, validated without a save key or a record version."""


class AllocationForm(SaveForm, AllocationValuesForm):
    expected_expense_updated_at = VersionField()


class SaleForm(SaveForm):
    description = forms.CharField(label=_("Description"), max_length=200)
    sold_on = forms.DateField(
        label=_("Sale date"),
        initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    quantity = forms.IntegerField(label=_("Plants sold"), min_value=1, max_value=2**31 - 1)
    unit_price = amount_field(_("Unit price"), required=False)
    total_price = amount_field(_("Total sale amount"), required=False)
    cycle = CycleChoiceField(
        label=_("Planting cycle"),
        required=False,
    )
    variety = RecordIDChoiceField(
        label=_("Variety"),
        queryset=Variety.objects.none(),
        required=False,
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        if record is None:
            self.initial.setdefault("sold_on", timezone.localdate())
        choices, self.sources, _details = cycle_choices()
        if record is None and not self.is_bound:
            source = self.sources.get(str(self.initial.get("cycle", "")))
            if source and source["default_variety"] is not None:
                self.initial.setdefault("variety", source["default_variety"])
        self.fields["cycle"].choices = [("", _("Choose a planting cycle")), *choices]
        preserve_cycles(
            self.fields["cycle"],
            [self.data.get("cycle") if self.is_bound else self.initial.get("cycle")],
        )
        taxonomy_choices(self, record, has_crop=False)
        # Linked older plantings remain valid sources, including archived varieties.
        linked_varieties = list(
            Variety.objects.select_related("crop").filter(plantings__isnull=False).distinct()
        )
        linked_labels = {str(variety.pk): str(variety) for variety in linked_varieties}
        existing_choices = list(self.fields["variety"].choices)
        keys = {str(key) for key, _label in existing_choices}
        self.fields["variety"].choices = [
            *((key, linked_labels.get(str(key), label)) for key, label in existing_choices),
            *(
                (variety.pk, str(variety))
                for variety in linked_varieties
                if str(variety.pk) not in keys
            ),
        ]
        describe_fields(self)

    def clean(self):
        values = super().clean()
        if (values.get("unit_price") is None) == (values.get("total_price") is None):
            self.add_error(None, _("Enter either a unit price or a total sale amount."))
        if not values.get("cycle") and not values.get("variety"):
            self.add_error("cycle", _("Choose a planting cycle or a variety for the sale."))
        return values


class EconomyFilterForm(forms.Form):
    year = forms.IntegerField(label=_("Year"), min_value=1, max_value=9999, required=False)
    all_years = forms.BooleanField(required=False, widget=forms.HiddenInput)
    crop = forms.ModelChoiceField(label=_("Crop"), required=False, queryset=Crop.objects.all())
    variety = forms.ModelChoiceField(
        label=_("Variety"), required=False, queryset=Variety.objects.select_related("crop")
    )
    cycle = CycleChoiceField(label=_("Planting cycle"), required=False)
    show_varieties = forms.BooleanField(label=_("Compare varieties"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices, _sources, _details = cycle_choices()
        self.fields["cycle"].choices = [("", _("All planting cycles")), *choices]
        describe_fields(self)

    def clean(self):
        values = super().clean()
        if values.get("all_years"):
            values["year"] = None
        elif values.get("year") is None:
            values["year"] = timezone.localdate().year
        return values

    def clean_cycle(self):
        cycle = self.cleaned_data["cycle"]
        if cycle is not None and not PlantingCycle.objects.filter(pk=cycle).exists():
            raise forms.ValidationError(_("Choose a valid record."))
        return cycle


class AllocationDeleteForm(DeleteForm):
    expected_expense_updated_at = VersionField()
