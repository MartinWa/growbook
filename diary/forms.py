from uuid import uuid4

from django import forms
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput, VersionField, describe_fields
from garden.models import Crop, GrowingArea, Planting, Variety
from garden.queries import area_paths


class CaptureForm(forms.Form):
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
        if quick:
            self.fields["body"].widget.attrs["rows"] = 3

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("body") and not cleaned.get("camera") and not cleaned.get("library"):
            raise forms.ValidationError(_("Write a note or add at least one photo."))
        return cleaned


class AreaMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return self.paths.get(obj.pk, obj.name)


class PlantingMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        parts = [str(obj.crop)]
        if obj.variety_id:
            parts.append(obj.variety.name)
        if obj.area_id:
            parts.append(self.paths.get(obj.area_id, obj.area.name))
        parts.append(str(obj.start_year))
        parts.append(obj.label or _("Batch %(number)s") % {"number": obj.pk})
        return " · ".join(parts)


def _eligible(model, current_ids):
    return model.objects.filter(Q(is_archived=False) | Q(pk__in=current_ids))


class EntryEditForm(forms.Form):
    expected_updated_at = VersionField()
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    body = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 5})
    )
    link_areas = AreaMultipleChoiceField(
        label=_("Growing areas"),
        queryset=GrowingArea.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 5}),
    )
    link_crops = forms.ModelMultipleChoiceField(
        label=_("Crops"),
        queryset=Crop.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 5}),
    )
    link_varieties = forms.ModelMultipleChoiceField(
        label=_("Varieties"),
        queryset=Variety.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 5}),
    )
    link_plantings = PlantingMultipleChoiceField(
        label=_("Plantings"),
        queryset=Planting.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 5}),
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
        current = {
            "area": {link.area_id for link in links if link.area_id},
            "crop": {link.crop_id for link in links if link.crop_id},
            "variety": {link.variety_id for link in links if link.variety_id},
        }
        areas = list(GrowingArea.objects.all())
        paths = area_paths(areas)
        self.fields["link_areas"].paths = paths
        self.fields["link_plantings"].paths = paths
        self.fields["link_areas"].queryset = _eligible(GrowingArea, current["area"])
        self.fields["link_crops"].queryset = _eligible(Crop, current["crop"])
        self.fields["link_varieties"].queryset = _eligible(
            Variety, current["variety"]
        ).select_related("crop")
        self.fields["link_plantings"].queryset = Planting.objects.select_related(
            "crop", "variety", "area"
        ).order_by("-start_year", "crop__name_key", "variety__name_key", "id")

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
