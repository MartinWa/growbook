import re
from uuid import uuid4

from django import forms
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput, VersionField, describe_fields
from garden.models import Crop, GrowingArea, Planting, Variety
from garden.queries import area_paths

TAG_FIELDS = {
    "link_plantings": (Planting, "planting", _("Plantings")),
    "link_areas": (GrowingArea, "area", _("Growing areas")),
    "link_crops": (Crop, "crop", _("Crops")),
    "link_varieties": (Variety, "variety", _("Varieties")),
}


class TagIdsField(forms.TypedMultipleChoiceField):
    """Validate typed IDs; capture eligibility belongs after immutable receipt lookup."""

    def __init__(self, **kwargs):
        super().__init__(required=False, coerce=int, widget=forms.CheckboxSelectMultiple, **kwargs)
        self.eligible_ids = None

    def validate(self, value):
        forms.Field.validate(self, value)
        if any(re.fullmatch(r"[1-9][0-9]*", item) is None for item in value):
            raise forms.ValidationError(_("Choose a valid record."))
        if len(set(value)) != len(value):
            raise forms.ValidationError(_("A record can only be linked once."))
        if self.eligible_ids is not None and not {int(item) for item in value} <= self.eligible_ids:
            raise forms.ValidationError(_("Choose a valid record."))


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
        if current is not None:
            for name, (model, _kind, _label) in TAG_FIELDS.items():
                queryset = (
                    model.objects.all() if model is Planting else _eligible(model, current[name])
                )
                self.fields[name].eligible_ids = set(queryset.values_list("pk", flat=True))

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
                    batch = record.label or _("Batch %(number)s") % {"number": record.pk}
                    context = " · ".join([place, span, batch])
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
        if quick:
            self.fields["body"].widget.attrs["rows"] = 3

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("body") and not cleaned.get("camera") and not cleaned.get("library"):
            raise forms.ValidationError(_("Write a note or add at least one photo."))
        return cleaned


def _eligible(model, current_ids):
    return model.objects.filter(Q(is_archived=False) | Q(pk__in=current_ids))


class EntryEditForm(TaggedForm):
    expected_updated_at = VersionField()
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    body = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 5})
    )
    photos = MultipleFileField(
        label=_("Add photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )
    remove_photos = forms.TypedMultipleChoiceField(
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
        links = list(entry.links.all())
        self.setup_tags(
            {
                name: {getattr(link, f"{kind}_id") for link in links if getattr(link, f"{kind}_id")}
                for name, (_model, kind, _label) in TAG_FIELDS.items()
            }
        )

    def clean(self):
        data = super().clean()
        body = (data.get("body") or "").strip()
        data["body"] = body
        removed = set(data.get("remove_photos") or [])
        remaining = self.entry.photos.exclude(pk__in=removed).count()
        additions = data.get("photos") or []
        if not body and not remaining and not additions:
            raise forms.ValidationError(_("Write a note or keep at least one photo."))
        if remaining + len(additions) > settings.PHOTO_MAX_COUNT:
            raise forms.ValidationError(
                _("An entry can have no more than %(count)s photos."),
                params={"count": settings.PHOTO_MAX_COUNT},
            )
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
        parts.append(obj.label or _("Batch %(number)s") % {"number": obj.pk})
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
