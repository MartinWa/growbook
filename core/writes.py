"""Reject accidental ORM writers that bypass the explicit domain operations."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db import models

_writing = ContextVar("growbook_validated_write", default=False)


@contextmanager
def validated_write():
    token = _writing.set(True)
    try:
        yield
    finally:
        _writing.reset(token)


def require_validated_write():
    if not _writing.get():
        raise RuntimeError("Use a validated domain operation, not a direct ORM write.")


class ValidatedQuerySet(models.QuerySet):
    def update(self, **kwargs):
        require_validated_write()
        return super().update(**kwargs)

    def bulk_create(self, *args, **kwargs):
        require_validated_write()
        return super().bulk_create(*args, **kwargs)

    def bulk_update(self, *args, **kwargs):
        require_validated_write()
        return super().bulk_update(*args, **kwargs)

    def delete(self):
        require_validated_write()
        return super().delete()

    def _raw_delete(self, using):
        require_validated_write()
        return super()._raw_delete(using)


class ValidatedModel(models.Model):
    objects = ValidatedQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        require_validated_write()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        require_validated_write()
        return super().delete(*args, **kwargs)
