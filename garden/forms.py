from uuid import uuid4

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput

from .models import Crop, GrowingArea
from .queries import area_paths, descendant_ids


class VersionField(forms.DateTimeField):
    """Preserve microseconds/timezone in the optimistic-concurrency token."""

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Record version"))
        widget = forms.HiddenInput()
        widget.supports_microseconds = True
        super().__init__(widget=widget, **kwargs)

    def prepare_value(self, value):
        return value.isoformat() if hasattr(value, "isoformat") else value


class CropForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    name = forms.CharField(label=_("Name"), max_length=100)
    notes = forms.CharField(
        label=_("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )


class VarietyForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    crop = forms.ModelChoiceField(label=_("Crop"), queryset=Crop.objects.none())
    name = forms.CharField(label=_("Name"), max_length=150)
    category = forms.CharField(label=_("Category"), max_length=150, required=False)
    description = forms.CharField(
        label=_("Description"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    source = forms.CharField(label=_("Source"), max_length=250, required=False)
    experiences = forms.CharField(
        label=_("Experiences"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    photos = MultipleFileField(
        label=_("Add photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        current = record.crop_id if record else None
        self.fields["crop"].queryset = Crop.objects.filter(Q(is_archived=False) | Q(pk=current))


class AreaChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return self.paths.get(obj.pk, obj.name)


class AreaForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    name = forms.CharField(label=_("Name"), max_length=150)
    parent = AreaChoiceField(
        label=_("Parent area"), queryset=GrowingArea.objects.none(), required=False
    )
    area_type = forms.CharField(
        label=_("Type"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"list": "area-types"}),
    )
    length_cm = forms.IntegerField(label=_("Length (cm)"), min_value=1, required=False)
    width_cm = forms.IntegerField(label=_("Width (cm)"), min_value=1, required=False)
    description = forms.CharField(
        label=_("Description"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    notes = forms.CharField(
        label=_("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    photos = MultipleFileField(
        label=_("Add photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        areas = list(GrowingArea.objects.all())
        excluded = descendant_ids(areas, record.pk) if record else set()
        eligible = [
            area.pk
            for area in areas
            if area.pk not in excluded
            and (not area.is_archived or (record and area.pk == record.parent_id))
        ]
        self.fields["parent"].paths = area_paths(areas)
        self.fields["parent"].queryset = GrowingArea.objects.filter(pk__in=eligible)


class ArchiveForm(forms.Form):
    archived = forms.TypedChoiceField(
        label=_("Archived"),
        choices=[("true", "true"), ("false", "false")],
        coerce=lambda value: value == "true",
    )


class DeleteForm(forms.Form):
    expected_updated_at = VersionField()
    confirm = forms.BooleanField(label=_("I confirm this deletion."))


class RegisterPhotoForm(forms.Form):
    expected_updated_at = VersionField()
    caption = forms.CharField(label=_("Caption"), max_length=250, required=False)
    position = forms.IntegerField(label=_("Order"), min_value=0)
