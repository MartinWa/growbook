"""Saved/default values for explicitly opted-in forms, including failed POSTs."""

from django import forms, template
from django.utils.html import json_script

register = template.Library()


@register.simple_tag
def draft_defaults(form):
    values = {}
    for current_form in (form, *getattr(form, "action_forms", {}).values()):
        for field in current_form:
            widget = field.field.widget
            if isinstance(widget, (forms.FileInput, forms.PasswordInput)):
                continue
            value = field.field.prepare_value(field.initial)
            if isinstance(widget, forms.CheckboxInput):
                values[field.html_name] = ["on"] if value else []
            else:
                formatted = widget.format_value(value)
                values[field.html_name] = (
                    [str(item) for item in formatted]
                    if isinstance(formatted, (list, tuple))
                    else [str(formatted)]
                    if formatted is not None
                    else [""]
                )
    return json_script(values, "draft-defaults")
