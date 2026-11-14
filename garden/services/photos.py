"""Validated caption, order and deletion operations for garden-owned photos."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from core.media import media_operation
from core.models import Photo
from core.photo_storage import discard_unreferenced_bundles, photo_bundles
from core.writes import validated_write

from ..models import CropCategory, GrowingArea, Planting, PlantingCategory, Variety
from ._shared import _active_actor, _current, _identifier, _outside_transaction, _version


def _garden_photo_owner(photo, expected_updated_at):
    if photo.entry_id is not None:
        raise PermissionDenied
    if photo.area_id is not None:
        model, owner_id = GrowingArea, photo.area_id
    elif photo.variety_id is not None:
        model, owner_id = Variety, photo.variety_id
    elif photo.category_id is not None:
        model, owner_id = CropCategory, photo.category_id
    elif photo.planting_category_id is not None:
        model, owner_id = PlantingCategory, photo.planting_category_id
    else:
        model, owner_id = Planting, photo.planting_id
    return _current(model, owner_id, expected_updated_at)


def edit_register_photo(*, actor, pk, caption, position, expected_updated_at):
    """Edit caption/order only; owner/file reassignment is deliberately not supported."""
    _outside_transaction(actor)
    _version(expected_updated_at)
    if not isinstance(caption, str):
        raise ValidationError({"caption": _("Enter text, or leave this field blank.")})
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        photo = _current(Photo, _identifier(pk, "id"))
        owner = _garden_photo_owner(photo, expected_updated_at)
        if type(position) is not int or position < 0:
            raise ValidationError({"position": _("Enter a nonnegative whole number.")})
        photo.caption, photo.position = caption, position
        photo.full_clean()
        photo.save(update_fields=["caption", "position"])
        owner.save(update_fields=["updated_at"])
        return photo


def delete_register_photo(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    _version(expected_updated_at)
    with media_operation():
        folders = []
        try:
            with transaction.atomic(), validated_write():
                _active_actor(actor)
                photo = _current(Photo, _identifier(pk, "id"))
                owner = _garden_photo_owner(photo, expected_updated_at)
                folders.extend(photo_bundles([photo]))
                photo.delete()
                owner.save(update_fields=["updated_at"])
        finally:
            discard_unreferenced_bundles(folders)
