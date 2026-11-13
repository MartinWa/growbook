import re
from uuid import uuid4

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput, VersionField, describe_fields
from garden.forms import CycleEditForm, PlantingEventForm, PlantingForm, RecordIDChoiceField
from garden.models import Crop, GrowingArea, Planting, Variety
from garden.queries import area_paths

from .actions import CycleStatusAction, NewPlantingAction, PlantingEventAction

TAG_FIELDS = {
    "link_plantings": (Planting, "planting", _("Plantings")),
    "link_areas": (GrowingArea, "area", _("Growing areas")),
    "link_crops": (Crop, "crop", _("Crops")),
    "link_varieties": (Variety, "variety", _("Varieties")),
}


class TagIdsField(forms.TypedMultipleChoiceField):
    """Validate typed IDs; association eligibility belongs after immutable receipt lookup."""

    def __init__(self, **kwargs):
        super().__init__(required=False, coerce=int, widget=forms.CheckboxSelectMultiple, **kwargs)

    def validate(self, value):
        forms.Field.validate(self, value)
        if any(re.fullmatch(r"[1-9][0-9]*", item) is None for item in value):
            raise forms.ValidationError(_("Choose a valid record."))
        if len(set(value)) != len(value):
            raise forms.ValidationError(_("A record can only be linked once."))


class PhotoIdsField(forms.TypedMultipleChoiceField):
    """Validate photo IDs without rejecting removals already applied by a saved edit."""

    def validate(self, value):
        forms.Field.validate(self, value)
        if any(re.fullmatch(r"[1-9][0-9]*", item) is None for item in value):
            raise forms.ValidationError(
                _("A selected photo is no longer available. Reload the entry.")
            )


class TaggedForm(forms.Form):
    link_plantings = TagIdsField(label=_("Plantings"))
    link_areas = TagIdsField(label=_("Growing areas"))
    link_crops = TagIdsField(label=_("Crops"))
    link_varieties = TagIdsField(label=_("Varieties"))

    @property
    def selected_links(self):
        return [
            (kind, pk)
            for name, (_model, kind, _label) in TAG_FIELDS.items()
            for pk in self.cleaned_data[name]
        ]

    def setup_tags(self, current=None):
        self.current_tags = current

    @cached_property
    def tag_groups(self):
        """Render the same native checkboxes in capture/edit, progressively enhanced."""
        areas = list(GrowingArea.objects.all())
        paths = area_paths(areas)
        try:
            value = self["date"].value()
            observed = value if hasattr(value, "year") else parse_date(str(value))
        except ValueError:
            observed = None
        year = observed.year if observed else timezone.localdate().year
        groups = []
        for name, (model, kind, label) in TAG_FIELDS.items():
            selected = {str(pk) for pk in self[name].value() or []}
            current = self.current_tags[name] if self.current_tags is not None else ()
            queryset = model.objects.all() if model is Planting else _eligible(model, current)
            if model is Planting:
                queryset = queryset.select_related("crop", "variety", "area")
            elif model is Variety:
                queryset = queryset.select_related("crop")
            options = []
            for record in queryset:
                context, derived = "", ""
                in_year = True
                if model is Planting:
                    variety = record.variety.name if record.variety_id else _("Unknown variety")
                    title = f"{record.crop.name} · {variety}"
                    span = str(record.start_year)
                    if record.lifespan == Planting.Lifespan.PERSISTENT:
                        span += f"–{record.end_year or _('Ongoing')}"
                    place = paths.get(record.area_id, str(_("Unassigned area")))
                    planting_label = record.label or _("Planting %(number)s") % {
                        "number": record.pk
                    }
                    context = " · ".join([place, span, planting_label])
                    in_year = record.start_year == year or (
                        record.lifespan == Planting.Lifespan.PERSISTENT
                        and record.start_year <= year
                        and (record.end_year is None or record.end_year >= year)
                    )
                    related = [record.crop.name]
                    if record.variety_id:
                        related.append(record.variety.name)
                    if record.area_id:
                        related.append(paths[record.area_id])
                    derived = " · ".join(related)
                else:
                    title = paths[record.pk] if model is GrowingArea else record.name
                    if model is Variety:
                        context = record.crop.name
                        derived = record.crop.name
                    if record.is_archived:
                        context = " · ".join(filter(None, [context, str(_("Archived"))]))
                options.append(
                    {
                        "pk": record.pk,
                        "title": title,
                        "context": context,
                        "selected": str(record.pk) in selected,
                        "in_year": in_year,
                        "derived": derived,
                        "start_year": getattr(record, "start_year", ""),
                        "end_year": getattr(record, "end_year", "") or "",
                        "persistent": getattr(record, "lifespan", "")
                        == Planting.Lifespan.PERSISTENT,
                    }
                )
            shown = {str(option["pk"]) for option in options}
            for pk in sorted(selected - shown):
                if re.fullmatch(r"[1-9][0-9]*", pk):
                    options.append(
                        {
                            "pk": pk,
                            "title": _("Unavailable record (%(number)s)") % {"number": pk},
                            "context": label,
                            "selected": True,
                            "in_year": False,
                        }
                    )
            groups.append(
                {
                    "name": name,
                    "kind": kind,
                    "label": label,
                    "options": sorted(options, key=lambda option: not option["in_year"]),
                }
            )
        return groups


class CaptureForm(TaggedForm):
    date_overrides = forms.CharField(required=False, widget=forms.HiddenInput)
    default_date = forms.DateField(
        required=False, widget=forms.HiddenInput, initial=timezone.localdate
    )
    planting_action = forms.ChoiceField(
        label=_("Planting action"),
        required=False,
        choices=[
            ("", _("No planting action")),
            ("new", _("Start a new planting")),
            ("event", _("Add an event")),
            ("cycle", _("Change cycle status")),
        ],
    )
    action_planting = RecordIDChoiceField(
        label=_("Existing planting"),
        required=False,
        queryset=Planting.objects.select_related("crop", "variety", "area", "cycle"),
    )
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "aria-describedby": "capture-date-help capture-date-errors",
            },
            format="%Y-%m-%d",
        ),
    )
    title = forms.CharField(
        label=_("Title"),
        max_length=200,
        required=False,
        help_text=_("A short heading for the observation (up to 200 characters)."),
        widget=forms.TextInput(
            attrs={"aria-describedby": "capture-title-help capture-title-errors"}
        ),
    )
    body = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 4, "aria-describedby": "capture-note-help capture-body-errors"}
        ),
    )
    camera = MultipleFileField(
        label=_("Take a photo"),
        required=False,
        widget=MultipleFileInput(
            attrs={
                "accept": "image/*",
                "capture": "environment",
                "aria-describedby": "capture-photo-help capture-camera-errors",
            }
        ),
    )
    library = MultipleFileField(
        label=_("Choose photos"),
        required=False,
        widget=MultipleFileInput(
            attrs={
                "accept": "image/*",
                "aria-describedby": "capture-photo-help capture-library-errors",
            }
        ),
    )

    def __init__(self, *args, quick=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_tags()
        paths = area_paths(list(GrowingArea.objects.all()))
        self.fields["action_planting"].label_from_instance = lambda obj: " · ".join(
            [
                obj.crop.name,
                obj.variety.name if obj.variety_id else str(_("Unknown variety")),
                paths.get(obj.area_id, str(_("Unassigned area"))),
                str(obj.start_year),
                obj.cycle.get_status_display(),
                obj.label or str(obj.pk),
                str(obj.batch_key)[:8],
            ]
        )
        self.fields["action_planting"].preserve_selection(self["action_planting"].value())
        data = self.data.copy() if self.is_bound else None
        raw_date = self["date"].value()
        try:
            observed = raw_date if hasattr(raw_date, "year") else parse_date(str(raw_date))
        except ValueError:
            observed = None
        observed = observed or timezone.localdate()
        if self.is_bound:
            try:
                previous_default = parse_date(str(data.get("default_date", "")))
            except ValueError:
                previous_default = None
            overridden = set(data.get("date_overrides", "").split(","))
            if previous_default is not None:
                for name in ("planting-started_on", "planting-completed_on", "event-actual_date"):
                    if name not in overridden and data.get(name) == previous_default.isoformat():
                        data[name] = observed.isoformat()
                if "planting-start_year" not in overridden and data.get(
                    "planting-start_year"
                ) == str(previous_default.year):
                    data["planting-start_year"] = str(observed.year)
                data["default_date"] = observed.isoformat()
                self.data = data
        new_initial = {
            "status": "ongoing",
            "started_on": observed,
            "completed_on": observed,
            "start_year": observed.year,
        }
        self.planting_form = PlantingForm(
            data,
            self.files if self.is_bound else None,
            prefix="planting",
            initial=new_initial,
            use_required_attribute=False,
        )
        for name in ("submission_key", "expected_updated_at", "batch_planting"):
            self.planting_form.fields.pop(name, None)
        self.planting_form.fields["crop"].required = False
        variety_attrs = self.planting_form.fields["variety"].widget.attrs
        variety_attrs["aria-describedby"] = " ".join(
            identifier
            for identifier in variety_attrs["aria-describedby"].split()
            if identifier != "variety-status"
        )
        self.planting_form.fields["variety"].queryset = Variety.objects.filter(
            is_archived=False, crop__is_archived=False
        ).select_related("crop")
        if self.is_bound:
            self.planting_form.fields["variety"].preserve_selection(
                self.data.get("planting-variety")
            )
        self.event_form = PlantingEventForm(
            data, prefix="event", initial={"actual_date": observed}, use_required_attribute=False
        )
        for name in ("submission_key", "expected_updated_at"):
            self.event_form.fields.pop(name)
        try:
            selected = int(self["action_planting"].value() or 0)
        except TypeError, ValueError:
            selected = 0
        planting = Planting.objects.select_related("cycle").filter(pk=selected).first()
        cycle = planting.cycle if planting else None
        cycle_initial = {
            "status": cycle.status if cycle else "ongoing",
            "started_on": cycle.started_on if cycle else None,
            "completed_on": cycle.completed_on if cycle else None,
            "expected_updated_at": cycle.updated_at if cycle else None,
            "category_ids": list(cycle.categories.values_list("pk", flat=True)) if cycle else [],
        }
        self.cycle_form = CycleEditForm(
            data, prefix="cycle", cycle=cycle, initial=cycle_initial, use_required_attribute=False
        )
        self.cycle_form.fields.pop("submission_key")
        self.action_forms = {
            "new": self.planting_form,
            "event": self.event_form,
            "cycle": self.cycle_form,
        }
        if self.is_bound:
            from django.forms.utils import ErrorDict

            for name, subform in self.action_forms.items():
                if name != self.data.get("planting_action"):
                    subform._errors = ErrorDict()
        if quick:
            self.fields["body"].widget.attrs["rows"] = 3

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("planting_action")
        cleaned["action"] = None
        cleaned["planting_uploads"] = []
        if self.files.getlist("planting-photos") and action != "new":
            self.add_error(None, _("Planting photos require a new planting."))
        if action in self.action_forms:
            selected_form = self.action_forms[action]
            if not selected_form.is_valid():
                self.add_error(None, _("Check the planting action fields."))
            else:
                if action != "new" and not cleaned.get("action_planting"):
                    self.add_error("action_planting", _("Choose a valid record."))
                elif action == "new":
                    values = selected_form.cleaned_data.copy()
                    values.pop("generated_label")
                    cleaned["planting_uploads"] = values.pop("photos")
                    cleaned["action"] = NewPlantingAction(**values)
                elif action == "event":
                    cleaned["action"] = PlantingEventAction(
                        planting=cleaned["action_planting"], **selected_form.cleaned_data
                    )
                else:
                    evaluation_values = selected_form.evaluation_values()
                    cleaned["action"] = CycleStatusAction(
                        planting=cleaned["action_planting"],
                        **selected_form.cleaned_data,
                        **evaluation_values,
                    )
        if not cleaned.get("body") and not cleaned.get("camera") and not cleaned.get("library"):
            raise forms.ValidationError(_("Write a note or add at least one photo."))
        return cleaned

    def action_error(self, error):
        from core.forms import form_error

        action = self.data.get("planting_action")
        target = self.action_forms.get(action)
        if target is not None and hasattr(error, "error_dict"):
            for field, errors in error.error_dict.items():
                if field in target.fields:
                    target.add_error(field, errors)
                    self.add_error(None, _("Check the planting action fields."))
                else:
                    self.add_error(field if field in self.fields else None, errors)
        else:
            form_error(self, error)

    @property
    def selected_action_form(self):
        return self.action_forms.get(self["planting_action"].value())

    @property
    def action_open(self):
        return bool(self["planting_action"].value())


def _eligible(model, current_ids):
    return model.objects.filter(Q(is_archived=False) | Q(pk__in=current_ids))


class EntryEditForm(TaggedForm):
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField()
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    title = forms.CharField(
        label=_("Title"),
        max_length=200,
        required=False,
        help_text=_("A short heading for the observation (up to 200 characters)."),
    )
    body = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 5})
    )
    photos = MultipleFileField(
        label=_("Add photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )
    remove_photos = PhotoIdsField(
        label=_("Remove photos"),
        coerce=int,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Removed photos are deleted from private storage."),
    )

    def __init__(self, *args, entry, **kwargs):
        self.entry = entry
        super().__init__(*args, **kwargs)
        describe_fields(self)
        photos = list(entry.photos.all())
        self.fields["remove_photos"].choices = [
            (str(photo.pk), photo.caption or _("Photo %(number)s") % {"number": number})
            for number, photo in enumerate(photos, 1)
        ]
        selected = self["remove_photos"].value() or []
        available = {str(photo.pk) for photo in photos}
        self.unavailable_photo_ids = [
            str(pk)
            for pk in selected
            if re.fullmatch(r"[1-9][0-9]*", str(pk)) and str(pk) not in available
        ]
        links = list(entry.links.all())
        self.setup_tags(
            {
                name: {getattr(link, f"{kind}_id") for link in links if getattr(link, f"{kind}_id")}
                for name, (_model, kind, _label) in TAG_FIELDS.items()
            }
        )

    def clean(self):
        data = super().clean()
        # Current photo counts and links are validated by the writer after receipt lookup.
        data["body"] = (data.get("body") or "").strip()
        return data


class EntryPhotoForm(forms.Form):
    expected_updated_at = VersionField()
    caption = forms.CharField(label=_("Caption"), max_length=250, required=False)
    position = forms.IntegerField(label=_("Order"), min_value=0)


class AreaFilterField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return self.paths.get(obj.pk, obj.name)


class PlantingFilterField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        parts = [str(obj.crop)]
        if obj.variety_id:
            parts.append(obj.variety.name)
        if obj.area_id:
            parts.append(self.paths.get(obj.area_id, obj.area.name))
        parts.append(str(obj.start_year))
        parts.append(obj.label or _("Planting %(number)s") % {"number": obj.pk})
        return " · ".join(parts)


class DiaryFilterForm(forms.Form):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"type": "search", "autocomplete": "off"}),
    )
    year = forms.TypedChoiceField(
        label=_("Year"), required=False, coerce=int, empty_value=None, choices=()
    )
    area = AreaFilterField(
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

    def __init__(self, *args, years=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["year"].choices = [("", _("All years"))] + [
            (str(year), str(year)) for year in years
        ]
        paths = area_paths(list(GrowingArea.objects.all()))
        self.fields["area"].paths = paths
        self.fields["planting"].paths = paths
        self.fields["planting"].queryset = Planting.objects.select_related(
            "crop", "variety", "area"
        ).order_by("-start_year", "crop__name_key", "variety__name_key", "id")
        describe_fields(self)
