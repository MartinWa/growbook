from decimal import Decimal
from uuid import UUID, uuid4

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import DeleteForm, VersionField, describe_fields
from garden.forms import RecordIDChoiceField
from garden.models import Crop, PlantingCycle, Variety

from .models import Allocation, Expense


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


def cycle_choices():
    choices, sources = [], {}
    for cycle in PlantingCycle.objects.prefetch_related("members__crop", "members__variety"):
        members = sorted(cycle.members.all(), key=lambda member: member.pk)
        if not members:
            continue
        first = members[0]
        varieties = ", ".join(
            str(member.variety.name) if member.variety else str(_("Unknown variety"))
            for member in members
        )
        choices.append(
            (str(cycle.pk), f"{first.label or first.crop.name} · {first.start_year} · {varieties}")
        )
        sources[str(cycle.pk)] = {
            "crop": first.crop_id,
            "varieties": [member.variety_id for member in members if member.variety_id is not None],
            "default_variety": first.variety_id if len(members) == 1 else None,
        }
    return sorted(choices, key=lambda choice: choice[1].casefold()), sources


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
        label=_("Purchase date"),
        initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    total_price = amount_field(_("Total price"))
    category = forms.ChoiceField(
        label=_("Expense category"),
        choices=Expense.Category.choices,
        required=False,
        initial=Expense.Category.OTHER,
    )
    purchased_quantity = forms.DecimalField(
        label=_("Purchased quantity"),
        required=False,
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        help_text=_("Optional amount purchased; this is separate from the total price."),
    )
    unit = forms.ChoiceField(
        label=_("Quantity unit"),
        required=False,
        choices=[("", _("Choose a unit")), *Expense.Unit.choices],
    )
    crop = RecordIDChoiceField(
        label=_("Crop"),
        queryset=Crop.objects.none(),
        required=False,
        help_text=_("Attribute the remaining cost to a crop without selecting a planting."),
    )
    variety = RecordIDChoiceField(
        label=_("Variety"), queryset=Variety.objects.none(), required=False
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
        taxonomy_choices(self, record, has_crop=True)
        today = timezone.localdate()
        if record is None:
            self.initial.setdefault("purchased_on", today)
        year = record.purchase_year if record else today.year
        self.fields["purchased_on"].widget.attrs.update(
            {"min": f"{year:04d}-01-01", "max": f"{year:04d}-12-31"}
        )
        describe_fields(self)

    def clean(self):
        values = super().clean()
        if not values.get("category"):
            values["category"] = Expense.Category.OTHER
        if (values.get("purchased_quantity") is None) != (not values.get("unit")):
            self.add_error(
                "unit", _("Enter both the purchased quantity and its unit, or leave both blank.")
            )
        if values.get("useful_life_years") and values.get("category") != Expense.Category.EQUIPMENT:
            self.add_error(
                "useful_life_years", _("Choose Equipment to spread a cost across years.")
            )
        return values

    @property
    def details_fields(self):
        return [
            self[name]
            for name in (
                "category",
                "purchased_quantity",
                "unit",
                "crop",
                "variety",
                "useful_life_years",
            )
        ]

    @property
    def details_open(self):
        return any(
            field.errors
            or field.value()
            and (field.name != "category" or field.value() != Expense.Category.OTHER)
            for field in self.details_fields
        )


class AllocationForm(SaveForm):
    expected_expense_updated_at = VersionField()
    growing_year = forms.IntegerField(label=_("Growing year"), min_value=1, max_value=9999)
    amount = amount_field(_("Amount to allocate"), required=False)
    share = forms.DecimalField(
        label=_("Share of cost (%)"),
        required=False,
        max_digits=6,
        decimal_places=3,
        min_value=Decimal("0.001"),
        max_value=100,
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        help_text=_("For equipment, the percentage applies to this year's cost share."),
    )
    quantity_used = forms.DecimalField(
        label=_("Quantity used"),
        required=False,
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        help_text=_("Uses the purchase's recorded unit and quantity. Estimated use is enough."),
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
        help_text=_("Select whole cycles. Varieties later added to these cycles are included."),
    )
    method = forms.ChoiceField(
        label=_("Divide the chosen amount"),
        choices=Allocation.Method.choices,
        initial=Allocation.Method.PLANTS,
        widget=forms.RadioSelect,
        help_text=_(
            "Plant quantities redistribute only the chosen amount. "
            "Equal division also divides each cycle's share equally between its varieties."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices, _sources = cycle_choices()
        self.fields["cycle_ids"].choices = choices
        selected = (
            self.data.getlist("cycle_ids")
            if self.is_bound and hasattr(self.data, "getlist")
            else self.data.get("cycle_ids", [])
            if self.is_bound
            else self.initial.get("cycle_ids", [])
        )
        preserve_cycles(self.fields["cycle_ids"], selected)
        describe_fields(self)

    def clean(self):
        values = super().clean()
        if (
            sum(values.get(field) is not None for field in ("amount", "share", "quantity_used"))
            != 1
        ):
            self.add_error(
                None, _("Enter exactly one allocation amount, percentage or quantity used.")
            )
        if values.get("scope") == Allocation.Scope.YEAR:
            values["cycle_ids"] = []
        elif not values.get("cycle_ids"):
            self.add_error("cycle_ids", _("Choose at least one existing planting cycle."))
        return values


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
        help_text=_(
            "A cycle supplies its crop, growing year and, when possible, variety. "
            "Selling plants does not reduce its recorded quantity."
        ),
    )
    variety = RecordIDChoiceField(
        label=_("Variety"),
        queryset=Variety.objects.none(),
        required=False,
        help_text=_(
            "Choose a variety when no cycle is selected, "
            "or to identify a variety within a shared cycle."
        ),
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        if record is None:
            self.initial.setdefault("sold_on", timezone.localdate())
        choices, self.sources = cycle_choices()
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
    year = forms.IntegerField(
        label=_("Year"), min_value=1, max_value=9999, initial=lambda: timezone.localdate().year
    )
    crop = forms.ModelChoiceField(label=_("Crop"), required=False, queryset=Crop.objects.all())
    variety = forms.ModelChoiceField(
        label=_("Variety"), required=False, queryset=Variety.objects.select_related("crop")
    )
    cycle = CycleChoiceField(label=_("Planting cycle"), required=False)
    show_varieties = forms.BooleanField(label=_("Compare varieties"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices, _sources = cycle_choices()
        self.fields["cycle"].choices = [("", _("All planting cycles")), *choices]
        describe_fields(self)

    def clean_cycle(self):
        cycle = self.cleaned_data["cycle"]
        if cycle is not None and not PlantingCycle.objects.filter(pk=cycle).exists():
            raise forms.ValidationError(_("Choose a valid record."))
        return cycle


class AllocationDeleteForm(DeleteForm):
    expected_expense_updated_at = VersionField()
