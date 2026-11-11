"""Routing metadata shared by register pages and scoped planting histories."""

from django.utils.translation import gettext_lazy as _

from ..forms import AreaForm, CropCategoryForm, CropForm, VarietyForm
from ..models import Crop, CropCategory, GrowingArea, Variety

# Routing metadata only. Models/forms/domain writers remain explicit rather than a register engine.
REGISTERS = {
    "category": (
        CropCategory,
        CropCategoryForm,
        _("Categories"),
        _("New category"),
        _("Edit category"),
    ),
    "crop": (Crop, CropForm, _("Crops"), _("New crop"), _("Edit crop")),
    "variety": (Variety, VarietyForm, _("Varieties"), _("New variety"), _("Edit variety")),
    "area": (
        GrowingArea,
        AreaForm,
        _("Growing areas"),
        _("New growing area"),
        _("Edit growing area"),
    ),
}
