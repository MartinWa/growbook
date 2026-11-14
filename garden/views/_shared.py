"""Routing metadata shared by register pages and scoped planting histories."""

from urllib.parse import urlsplit

from django.http import QueryDict
from django.urls import Resolver404, resolve, reverse
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
        "garden:area-list",
        "garden:area-manage",
        "garden:category-manage",
        "garden:planting-category-manage",
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


def management_url(request, route, *, selected=None, deleted_path=None):
    """Keep management context, or wrap a safe browsing origin for the return link."""
    destination = return_url(request)
    if destination and urlsplit(destination).path == deleted_path:
        destination = ""
    if destination and resolve(urlsplit(destination).path).view_name == route:
        if deleted_path:
            query = QueryDict(urlsplit(destination).query, mutable=True)
            if urlsplit(query.get("next", "")).path == deleted_path:
                query.pop("next", None)
                return reverse(route, query=query)
        return destination
    query = {}
    if route == "garden:area-manage":
        if request.GET.get("archived") == "1":
            query["archived"] = "1"
        if selected is not None:
            query["selected"] = selected
    elif destination:
        query["next"] = destination
    return reverse(route, query=query)


def deletion_return_url(request, kind, pk):
    """Only surviving lists/histories can be record deletion destinations."""
    destination = return_url(request)
    if not destination:
        return ""
    match = resolve(urlsplit(destination).path)
    allowed = {
        "garden:category-detail",
        "garden:uncategorized",
        "garden:all-crops",
        "garden:variety-list",
        "garden:planting-list",
        "garden:planting-history",
        "garden:planting-status",
        "garden:planting-category-detail",
        "garden:crop-history",
        "garden:variety-history",
        "garden:area-history",
    }
    if kind in ("crop", "variety"):
        allowed -= {
            "garden:planting-list",
            "garden:planting-history",
            "garden:planting-status",
            "garden:planting-category-detail",
        }
    if match.view_name not in allowed:
        return ""
    if match.view_name == f"garden:{kind}-history" and match.kwargs.get("pk") == pk:
        return ""
    return destination
