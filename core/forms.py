from django import forms
from django.utils.translation import gettext_lazy as _


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


class DeleteForm(forms.Form):
    expected_updated_at = VersionField()
    confirm = forms.BooleanField(label=_("I confirm this deletion."))


def describe_fields(form):
    """Keep help and validation IDs stable when only the error summary is replaced."""
    for name, field in form.fields.items():
        if field.widget.is_hidden:
            continue
        field_id = form[name].auto_id
        descriptions = [f"{field_id}_errors"]
        if field.help_text or name == "photos":
            descriptions.append(f"{field_id}_helptext")
        field.widget.attrs["aria-describedby"] = " ".join(descriptions)
