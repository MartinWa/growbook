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
