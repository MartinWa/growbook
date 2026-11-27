from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Read-only JSON access to the garden registers, plantings and observations."""

    name = "api"
