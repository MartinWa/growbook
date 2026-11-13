from uuid import uuid4

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput, VersionField, describe_fields

from .models import (
    Crop,
    GrowingArea,
    Planting,
    PlantingCategory,
    PlantingCycle,
    PlantingEvent,
    Variety,
)
from .queries import area_paths, descendant_ids


def clean_record_id(value, *, required=False):
    """Validate positive signed 64-bit IDs without checking record eligibility."""
    message = _("Choose a valid record.")
    return forms.IntegerField(
        min_value=1,
        max_value=2**63 - 1,
        required=required,
        error_messages={"invalid": message, "min_value": message, "max_value": message},
    ).clean(getattr(value, "pk", value))


class RecordIDChoiceField(forms.ModelChoiceField):
    """Render model choices but clean IDs only; writers resolve receipts before eligibility."""

    def label_from_instance(self, obj):
        return getattr(self, "paths", {}).get(obj.pk, str(obj))

    def to_python(self, value):
        return clean_record_id(value, required=self.required)

    def preserve_selection(self, value):
        """Keep an unavailable ID in a draft without granting it eligibility."""
        try:
            selected = clean_record_id(value)
        except forms.ValidationError:
            return
        if selected is None:
            return
        choices = list(self.choices)
        if str(selected) not in {str(value) for value, _label in choices}:
            choices.append(
                (selected, _("Unavailable selection (%(number)s)") % {"number": selected})
            )
        self.choices = choices


class CropForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    category = forms.IntegerField(
        min_value=1, max_value=2**63 - 1, required=False, widget=forms.HiddenInput
    )
    name = forms.CharField(label=_("Name"), max_length=100)
    notes = forms.CharField(
        label=_("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        describe_fields(self)


class CropCategoryForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    name = forms.CharField(label=_("Name"), max_length=150)
    crop_ids = forms.MultipleChoiceField(
        label=_("Crops"), required=False, widget=forms.CheckboxSelectMultiple
    )
    remove_image = forms.BooleanField(label=_("Remove current image"), required=False)
    photos = MultipleFileField(
        label=_("Category image"),
        required=False,
        help_text=_("Choose one image. A new image replaces the current image."),
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        current = set(record.crops.values_list("pk", flat=True)) if record else set()
        if record is None or not record.photos.exists():
            self.fields["remove_image"].widget = forms.HiddenInput()
        crops = Crop.objects.filter(Q(is_archived=False) | Q(pk__in=current))
        choices = [(str(crop.pk), str(crop)) for crop in crops]
        # Domain writers resolve immutable retries before current link eligibility.
        if self.is_bound:
            selected = (
                self.data.getlist("crop_ids")
                if hasattr(self.data, "getlist")
                else self.data.get("crop_ids", [])
            )
            for value in selected:
                try:
                    identifier = clean_record_id(value)
                except forms.ValidationError:
                    continue
                if identifier and str(identifier) not in {key for key, _ in choices}:
                    choices.append(
                        (
                            str(identifier),
                            _("Unavailable selection (%(number)s)") % {"number": identifier},
                        )
                    )
        self.fields["crop_ids"].choices = choices
        describe_fields(self)


class VarietyForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    crop = RecordIDChoiceField(label=_("Crop"), queryset=Crop.objects.none())
    name = forms.CharField(label=_("Name"), max_length=150)
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
        describe_fields(self)
        current = record.crop_id if record else None
        self.fields["crop"].queryset = Crop.objects.filter(Q(is_archived=False) | Q(pk=current))
        if self.is_bound:
            self.fields["crop"].preserve_selection(self.data.get("crop"))


class AreaChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return self.paths.get(obj.pk, obj.name)


class AreaForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    name = forms.CharField(label=_("Name"), max_length=150)
    parent = RecordIDChoiceField(
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
        describe_fields(self)
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
        if self.is_bound:
            self.fields["parent"].preserve_selection(self.data.get("parent"))


def planting_variety_choices(crop_id, record=None):
    current = record.variety_id if record and record.crop_id == crop_id else None
    return (
        Variety.objects.filter(crop_id=crop_id)
        .filter(Q(is_archived=False, crop__is_archived=False) | Q(pk=current))
        .select_related("crop")
    )


class RecordNameSelect(forms.Select):
    """Keep label generation independent of a record's display text."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if instance := getattr(value, "instance", None):
            option["attrs"]["data-record-name"] = instance.name
        return option


class CycleFieldsForm(forms.Form):
    status = forms.ChoiceField(
        label=_("Current status"),
        choices=PlantingCycle.Status.choices,
        initial=PlantingCycle.Status.PLANNED,
        required=False,
    )
    started_on = forms.DateField(
        label=_("Actual start date"),
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    completed_on = forms.DateField(
        label=_("Completion date"),
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    category_ids = forms.MultipleChoiceField(
        label=_("Custom categories"), required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, cycle=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        choices = [(str(category.pk), category.name) for category in PlantingCategory.objects.all()]
        if self.is_bound:
            selected = (
                self.data.getlist("category_ids")
                if hasattr(self.data, "getlist")
                else self.data.get("category_ids", [])
            )
            for value in selected:
                try:
                    identifier = clean_record_id(value)
                except forms.ValidationError:
                    continue
                if identifier and str(identifier) not in {key for key, _label in choices}:
                    choices.append(
                        (
                            str(identifier),
                            _("Unavailable selection (%(number)s)") % {"number": identifier},
                        )
                    )
        self.fields["category_ids"].choices = choices
        describe_fields(self)

    def clean(self):
        data = super().clean()
        if "status" not in self.fields:
            return data
        if "category_ids" in data:
            data["category_ids"] = [
                clean_record_id(value, required=True) for value in data["category_ids"]
            ]
        status = data.get("status") or PlantingCycle.Status.PLANNED
        data["status"] = status
        if status == PlantingCycle.Status.PLANNED:
            data["started_on"] = data["completed_on"] = None
        elif status == PlantingCycle.Status.ONGOING:
            data["completed_on"] = None
        # Edit requirements depend on saved status; the writer checks them after receipts.
        needs_dates = self.cycle is None
        if needs_dates and status == PlantingCycle.Status.ONGOING and not data.get("started_on"):
            self.add_error("started_on", _("Set the actual start date for an ongoing cycle."))
        if (
            needs_dates
            and status == PlantingCycle.Status.COMPLETED
            and not data.get("completed_on")
        ):
            self.add_error("completed_on", _("Set the completion date for a completed cycle."))
        return data


class CycleEditForm(CycleFieldsForm):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField()


class CycleTransitionForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField()
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        describe_fields(self)


class PlantingCategoryForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    name = forms.CharField(label=_("Name"), max_length=150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        describe_fields(self)


class PlantingForm(CycleFieldsForm):
    detail_names = ("variety", "area", "lifespan", "end_year", "quantity")
    note_names = ("notes", "evaluation", "photos")
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    batch_planting = forms.IntegerField(
        min_value=1, max_value=2**63 - 1, required=False, widget=forms.HiddenInput
    )
    # The last automatic value distinguishes a generated label from a custom draft on reload.
    generated_label = forms.CharField(required=False, max_length=251, widget=forms.HiddenInput)
    crop = RecordIDChoiceField(
        label=_("Crop"), queryset=Crop.objects.none(), widget=RecordNameSelect
    )
    start_year = forms.IntegerField(
        label=_("Start year"),
        min_value=1,
        max_value=9999,
        initial=lambda: timezone.localdate().year,
    )
    variety = RecordIDChoiceField(
        label=_("Variety"),
        queryset=Variety.objects.none(),
        required=False,
        empty_label=_("Unknown variety"),
        widget=RecordNameSelect,
    )
    area = RecordIDChoiceField(
        label=_("Growing area"),
        queryset=GrowingArea.objects.none(),
        required=False,
        empty_label=_("Unassigned area"),
    )
    lifespan = forms.ChoiceField(
        label=_("Lifespan"),
        choices=Planting.Lifespan.choices,
        initial=Planting.Lifespan.ANNUAL,
        help_text=_(
            "Annual: start year only. Persistent: every year through the inclusive end year."
        ),
    )
    end_year = forms.IntegerField(
        label=_("End year"),
        min_value=1,
        max_value=9999,
        required=False,
        help_text=_("Persistent plantings only. Leave blank for ongoing presence."),
    )
    label = forms.CharField(
        label=_("Planting label"),
        max_length=251,
        required=False,
        help_text=_("Defaults to crop-variety. You can change it."),
    )
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=0,
        required=False,
        help_text=_("Plant count: blank means unknown; zero means none."),
    )
    notes = forms.CharField(
        label=_("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    evaluation = forms.CharField(
        label=_("Evaluation"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    photos = MultipleFileField(
        label=_("Add photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )

    def __init__(self, *args, record=None, batch_member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if (
            record is not None
            or batch_member is not None
            or self.initial.get("batch_planting")
            or self.data.get("batch_planting")
        ):
            for name in ("status", "started_on", "completed_on", "category_ids"):
                self.fields.pop(name)
        describe_fields(self)
        variety_attrs = self.fields["variety"].widget.attrs
        variety_attrs["aria-describedby"] = (
            f"{variety_attrs.get('aria-describedby', '')} variety-status".strip()
        )
        self.fields["crop"].queryset = Crop.objects.filter(
            Q(is_archived=False) | Q(pk=record.crop_id if record else None)
        )
        raw_crop = self.data.get("crop") if self.is_bound else self.initial.get("crop")
        try:
            crop_id = clean_record_id(raw_crop)
        except forms.ValidationError:
            crop_id = None
        self.fields["variety"].queryset = planting_variety_choices(crop_id, record)
        if batch_member is not None:
            self.fields["variety"].queryset = self.fields["variety"].queryset.exclude(
                pk__in=Planting.objects.filter(
                    batch_key=batch_member.batch_key, variety_id__isnull=False
                ).values("variety_id")
            )
        if (
            batch_member is not None
            or self.initial.get("batch_planting")
            or self.data.get("batch_planting")
            or (
                record is not None
                and record.variety_id is not None
                and Planting.objects.filter(batch_key=record.batch_key)
                .exclude(pk=record.pk)
                .exists()
            )
        ):
            self.fields["variety"].required = True
            self.fields["variety"].empty_label = _("Choose a variety")
        areas = list(GrowingArea.objects.all())
        self.fields["area"].paths = area_paths(areas)
        self.fields["area"].queryset = GrowingArea.objects.filter(
            Q(is_archived=False, children__isnull=True) | Q(pk=record.area_id if record else None)
        ).distinct()
        if not self.is_bound:
            # An unavailable inherited area must not silently become an unassigned choice.
            self.fields["area"].preserve_selection(self.initial.get("area"))
        if self.is_bound:
            # Keep submitted selections in a failed ordinary-page draft, even when
            # choices are archived/deleted during a save. These IDs gain no eligibility:
            # the serialized writer checks the immutable receipt, then current records.
            self.data = self.data.copy()
            for name in ("crop", "variety", "area"):
                field = self.fields[name]
                choices = list(field.choices)
                try:
                    selected = int(self.data.get(name, ""))
                except ValueError, TypeError:
                    selected = None
                offered = {str(value) for value, _label in choices}
                if selected is not None and selected > 0:
                    if str(selected) not in offered:
                        if name == "variety" and self.data.get("choose_crop") == "1":
                            self.data[name] = ""
                        else:
                            choices.append(
                                (
                                    selected,
                                    _("Unavailable selection (%(number)s)") % {"number": selected},
                                )
                            )
                    field.choices = choices

        values = self.data if self.is_bound else self.initial
        self._details_open = (
            bool(batch_member)
            or self.data.get("choose_crop") == "1"
            or any(
                values.get(name) not in (None, "", Planting.Lifespan.ANNUAL)
                for name in self.detail_names
            )
        )
        self._notes_open = any(values.get(name) for name in self.note_names) or bool(self.files)
        if record is not None and record.photos.exists():
            self._notes_open = True

    @property
    def details_open(self):
        return self._details_open or any(self[name].errors for name in self.detail_names)

    @property
    def notes_open(self):
        return self._notes_open or any(self[name].errors for name in self.note_names)

    @property
    def detail_fields(self):
        return [self[name] for name in self.detail_names]

    @property
    def note_fields(self):
        return [self[name] for name in self.note_names]

    def clean(self):
        data = super().clean()
        start_year, end_year = data.get("start_year"), data.get("end_year")
        if data.get("lifespan") == Planting.Lifespan.ANNUAL and end_year is not None:
            self.add_error("end_year", _("An annual planting has no end year."))
        elif start_year is not None and end_year is not None and end_year < start_year:
            self.add_error("end_year", _("The end year cannot precede the start year."))
        return data


class PlantingEventForm(forms.Form):
    submission_key = forms.UUIDField(label=_("Save key"), widget=forms.HiddenInput, initial=uuid4)
    expected_updated_at = VersionField(required=False)
    kind = forms.ChoiceField(label=_("Event type"), choices=PlantingEvent.Kind.choices)
    planned_date = forms.DateField(
        label=_("Planned date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    actual_date = forms.DateField(
        label=_("Actual date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    note = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        describe_fields(self)

    def clean(self):
        data = super().clean()
        if not data.get("planned_date") and not data.get("actual_date"):
            raise forms.ValidationError(_("Set a planned date, an actual date, or both."))
        return data


class PlantingFilterForm(forms.Form):
    year = forms.IntegerField(label=_("Growing year"), min_value=1, max_value=9999, required=False)
    crop = forms.ModelChoiceField(label=_("Crop"), queryset=Crop.objects.all(), required=False)
    variety = forms.ModelChoiceField(
        label=_("Variety"), queryset=Variety.objects.all(), required=False
    )
    area = AreaChoiceField(
        label=_("Growing area"), queryset=GrowingArea.objects.all(), required=False
    )
    area_only = forms.BooleanField(required=False, widget=forms.HiddenInput)
    unassigned = forms.BooleanField(label=_("Unassigned area only"), required=False)
    unknown_variety = forms.BooleanField(label=_("Unknown variety only"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["area"]
        field.paths = area_paths(list(GrowingArea.objects.all()))
        self.fields["variety"].queryset = Variety.objects.select_related("crop")
        # Repeated sibling names need distinct full paths in filters as well as rows.
        field.queryset = GrowingArea.objects.all()
        describe_fields(self)


class VarietyFilterForm(forms.Form):
    q = forms.CharField(
        label=_("Search crops and varieties"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "placeholder": _("Search crop or variety"),
                "aria-describedby": "id_q_help id_q_errors",
            }
        ),
    )
    archived = forms.BooleanField(
        label=_("Include archived records"),
        required=False,
        widget=forms.CheckboxInput(attrs={"value": "1"}),
    )


class ArchiveForm(forms.Form):
    archived = forms.TypedChoiceField(
        label=_("Archived"),
        choices=[("true", "true"), ("false", "false")],
        coerce=lambda value: value == "true",
    )


class RegisterPhotoForm(forms.Form):
    expected_updated_at = VersionField()
    caption = forms.CharField(label=_("Caption"), max_length=250, required=False)
    position = forms.IntegerField(label=_("Order"), min_value=0)
