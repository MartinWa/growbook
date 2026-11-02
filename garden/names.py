"""Unicode comparison keys, without translating or conflating user-entered names."""

import unicodedata

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def normalize_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValidationError({"name": _("Enter a name that is not blank.")})
    display = name.strip()
    key = unicodedata.normalize("NFC", " ".join(display.split())).casefold()
    # Case folding can introduce combining characters; keep the final key in NFC too.
    return display, unicodedata.normalize("NFC", key)
