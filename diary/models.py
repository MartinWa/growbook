from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.writes import ValidatedModel


class DiaryEntry(ValidatedModel):
    date = models.DateField(_("Date"), default=timezone.localdate, db_index=True)
    body = models.TextField(_("Note"), blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    submission_key = models.UUIDField(unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        verbose_name = _("Diary entry")
        verbose_name_plural = _("Diary entries")

    def get_absolute_url(self):
        return reverse("diary:detail", args=[self.pk])


class DiarySubmission(ValidatedModel):
    """Immutable creation receipt, retained as a tombstone after eventual deletion."""

    key = models.UUIDField(primary_key=True, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload_sha256 = models.CharField(max_length=64)
    entry = models.OneToOneField(DiaryEntry, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
