from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleFileField, MultipleFileInput


class CaptureForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    date = forms.DateField(
        label=_("Date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    body = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 4})
    )
    camera = MultipleFileField(
        label=_("Take a photo"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*", "capture": "environment"}),
    )
    library = MultipleFileField(
        label=_("Choose photos"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*"}),
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("body") and not cleaned.get("camera") and not cleaned.get("library"):
            raise ValidationError(_("Write a note or add at least one photo."))
        return cleaned
