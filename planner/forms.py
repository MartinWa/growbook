"""Calendar filters that reuse the diary's area, planting and taxonomy semantics."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import AreaPathChoiceField, describe_fields
from diary.forms import PlantingFilterField
from garden.models import Crop, GrowingArea, Planting, Variety
from garden.queries import area_paths

TYPE_CHOICES = (
    ("event", _("Planting events")),
    ("entry", _("Diary observations")),
)


class CalendarFilterForm(forms.Form):
    type = forms.MultipleChoiceField(
        label=_("Item type"),
        choices=TYPE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    area = AreaPathChoiceField(
        label=_("Growing area"),
        queryset=GrowingArea.objects.all(),
        required=False,
        empty_label=_("All areas"),
    )
    crop = forms.ModelChoiceField(
        label=_("Crop"),
        queryset=Crop.objects.all(),
        required=False,
        empty_label=_("All crops"),
    )
    variety = forms.ModelChoiceField(
        label=_("Variety"),
        queryset=Variety.objects.select_related("crop"),
        required=False,
        empty_label=_("All varieties"),
    )
    planting = PlantingFilterField(
        label=_("Planting"),
        queryset=Planting.objects.select_related("crop", "variety", "area"),
        required=False,
        empty_label=_("All plantings"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        paths = area_paths(list(GrowingArea.objects.all()))
        self.fields["area"].paths = paths
        self.fields["planting"].paths = paths
        self.fields["planting"].queryset = Planting.objects.select_related(
            "crop", "variety", "area"
        ).order_by("-start_year", "crop__name_key", "variety__name_key", "id")
        describe_fields(self)

    @property
    def values(self):
        """Cleaned values in the shape the month query takes, after ``is_valid()``."""
        kinds = frozenset(self.cleaned_data.get("type") or ())
        return {
            "area": self.cleaned_data.get("area"),
            "crop": self.cleaned_data.get("crop"),
            "variety": self.cleaned_data.get("variety"),
            "planting": self.cleaned_data.get("planting"),
            "kinds": kinds or None,
        }
