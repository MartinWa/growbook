from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GardenConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "garden"
    verbose_name = _("Garden")
