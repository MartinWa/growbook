"""Routing metadata shared by register pages and scoped planting histories."""

from urllib.parse import urlsplit

from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme
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


def return_url(request):
    """Accept only local garden list/detail destinations, including their filters."""
    value = request.POST.get("next", request.GET.get("next", ""))
    if not value.startswith("/") or not url_has_allowed_host_and_scheme(value, allowed_hosts=set()):
        return ""
    try:
        match = resolve(urlsplit(value).path)
    except Resolver404:
        return ""
    allowed = {
        "garden:area-detail",
        "garden:category-detail",
        "garden:uncategorized",
        "garden:all-crops",
        "garden:variety-list",
        "garden:planting-detail",
        "garden:planting-list",
        "garden:planting-history",
        "garden:planting-status",
        "garden:planting-category-detail",
        "garden:cycle-detail",
        "garden:crop-history",
        "garden:variety-history",
        "garden:area-history",
    }
    return value if match.view_name in allowed else ""
