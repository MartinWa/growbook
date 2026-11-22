from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PlannerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "planner"
    verbose_name = _("Calendar")
