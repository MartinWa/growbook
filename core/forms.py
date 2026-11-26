from datetime import datetime

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from .api_tokens import EXPIRY_NAIVE, EXPIRY_PAST, NAME_REQUIRED, NAME_TAKEN
from .models import ApiToken


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(MultipleFileField, self).clean(item, initial) for item in data]
        value = super().clean(data, initial)
        return [value] if value else []


class VersionField(forms.DateTimeField):
    """Preserve microseconds/timezone in the optimistic-concurrency token."""

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Record version"))
        widget = forms.HiddenInput()
        widget.supports_microseconds = True
        super().__init__(widget=widget, **kwargs)

    def prepare_value(self, value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            parsed = value if isinstance(value, datetime) else parse_datetime(value.strip())
        except AttributeError, TypeError, ValueError:
            parsed = None
        if parsed is None or timezone.is_naive(parsed):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        return parsed


class PhotoDetailsForm(forms.Form):
    expected_updated_at = VersionField()
    caption = forms.CharField(label=_("Caption"), max_length=250, required=False)
    position = forms.IntegerField(label=_("Order"), min_value=0)


class AreaPathChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return self.paths.get(obj.pk, obj.name)


class DeleteForm(forms.Form):
    expected_updated_at = VersionField()
    confirm = forms.BooleanField(label=_("I confirm this deletion."))


class ConfirmForm(forms.Form):
    """A confirmation without a version token, for records nothing else edits."""

    confirm = forms.BooleanField(label=_("I confirm this deletion."))


class ApiTokenForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=255)
    expires_at = forms.DateTimeField(
        label=_("Expiry"),
        required=False,
        # A datetime-local input sends a T separator; Django's defaults only parse a space.
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text=_("Optional. The token stops working after this time."),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        describe_fields(self)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError(NAME_REQUIRED)
        if self.user is not None and ApiToken.objects.filter(user=self.user, name=name).exists():
            raise forms.ValidationError(NAME_TAKEN)
        return name

    def clean_expires_at(self):
        value = self.cleaned_data["expires_at"]
        if value is None:
            return None
        if timezone.is_naive(value):
            raise forms.ValidationError(EXPIRY_NAIVE)
        if value <= timezone.now():
            raise forms.ValidationError(EXPIRY_PAST)
        return value


def describe_fields(form):
    """Keep help and validation IDs stable when only the error summary is replaced."""
    for name, field in form.fields.items():
        if field.widget.is_hidden:
            continue
        field_id = form[name].auto_id
        descriptions = [f"{field_id}_errors"]
        if field.help_text or name == "photos":
            descriptions.append(f"{field_id}_helptext")
        for identifier in field.widget.attrs.get("aria-describedby", "").split():
            if identifier not in descriptions:
                descriptions.append(identifier)
        field.widget.attrs["aria-describedby"] = " ".join(descriptions)


def form_error(form, error):
    """Attach domain validation errors to known fields, or the form summary."""
    if hasattr(error, "error_dict"):
        for field, errors in error.error_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, error)


def valid_post(request, form, photo_fields=(), *, unexpected_photo_message=None):
    """Validate the form and multipart guard errors, including unexpected uploads."""
    valid = form.is_valid()
    if getattr(request, "photo_upload_error", None):
        form.add_error(None, request.photo_upload_error)
        valid = False
    if set(request.FILES) - set(photo_fields):
        form.add_error(
            None,
            unexpected_photo_message
            or _("An unexpected photo field was submitted. Nothing was saved."),
        )
        valid = False
    return valid
