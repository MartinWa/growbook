"""Calendar filters, task forms and the shared month note."""

from uuid import uuid4

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import AreaPathChoiceField, VersionField, describe_fields
from diary.forms import PlantingFilterField
from garden.models import Crop, GrowingArea, Planting, Variety
from garden.queries import area_paths

TYPE_CHOICES = (
    ("event", _("Planting events")),
    ("entry", _("Diary observations")),
    ("task", _("Planned tasks")),
)

LINK_GROUPS = (
    ("area", _("Growing areas")),
    ("crop", _("Crops")),
    ("variety", _("Varieties")),
    ("planting", _("Plantings")),
)


def link_choices(current=()):
    """Grouped optional link targets, keeping an archived current link selectable."""
    current_keys = set(current)
    areas = list(GrowingArea.objects.all())
    paths = area_paths(areas)
    crops = Crop.objects.all()
    varieties = Variety.objects.select_related("crop")
    plantings = Planting.objects.select_related("crop", "variety", "area")

    def kept(field, record):
        return not record.is_archived or (field, record.pk) in current_keys

    def area_label(area):
        return paths.get(area.pk, area.name)

    def planting_label(planting):
        parts = [str(planting.crop)]
        if planting.variety_id:
            parts.append(planting.variety.name)
        if planting.area_id:
            parts.append(paths.get(planting.area_id, planting.area.name))
        parts.append(str(planting.start_year))
        parts.append(planting.label or _("Planting %(number)s") % {"number": planting.pk})
        return " · ".join(parts)

    rows = {
        "area": [(f"area-{area.pk}", area_label(area)) for area in areas if kept("area", area)],
        "crop": [(f"crop-{crop.pk}", crop.name) for crop in crops if kept("crop", crop)],
        "variety": [
            (f"variety-{variety.pk}", f"{variety.crop.name} · {variety.name}")
            for variety in varieties
            if kept("variety", variety)
        ],
        "planting": [
            (f"planting-{planting.pk}", planting_label(planting)) for planting in plantings
        ],
    }
    return [(label, rows[field]) for field, label in LINK_GROUPS]


def parse_link(value):
    """``area-12`` in the form, ``("area", 12)`` in the service."""
    if not value:
        return ()
    field, _separator, number = value.partition("-")
    if field not in {name for name, _label in LINK_GROUPS} or not number.isdigit():
        raise forms.ValidationError(_("Choose a valid record to link."))
    return (field, int(number))


class ChoiceToggleField(forms.MultipleChoiceField):
    """Blank query values mean no toggle; only real choices count as selections."""

    def clean(self, value):
        return super().clean([item for item in (value or []) if item])


class CalendarFilterForm(forms.Form):
    type = ChoiceToggleField(
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


class TaskForm(forms.Form):
    """One planned task with optional text and at most one linked record."""

    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    planned_date = forms.DateField(
        label=_("Planned date"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    title = forms.CharField(
        label=_("Title"),
        max_length=200,
        required=False,
        help_text=_("A short heading. Leave it blank and write a note instead."),
    )
    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    link = forms.ChoiceField(label=_("Linked record"), required=False, choices=())

    def __init__(self, *args, current_link=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_link = current_link or ()
        self.fields["link"].choices = [
            ("", _("No linked record")),
            *link_choices({self.current_link} if self.current_link else ()),
        ]
        describe_fields(self)

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("title") or "").strip() and not (cleaned.get("note") or "").strip():
            raise forms.ValidationError(_("Enter a title or a note for this task."))
        return cleaned

    @property
    def links(self):
        link = parse_link(self.cleaned_data.get("link"))
        return (link,) if link else ()


class TaskActionForm(forms.Form):
    """Tick a task off or reopen it, with a keepable completion date."""

    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField()
    completed_on = forms.DateField(
        required=False,
        widget=forms.HiddenInput(attrs={"format": "%Y-%m-%d"}),
        initial=timezone.localdate,
    )


class MonthNoteForm(forms.Form):
    """The one shared note for the shown month."""

    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    body = forms.CharField(
        label=_("Note for this month"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
