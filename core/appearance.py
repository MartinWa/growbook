"""Browser-local seasonal appearance, independent of diary dates and filters."""

from django.utils import timezone

SEASON_COOKIE = "growbook_season"
SEASON_COOKIE_AGE = 365 * 24 * 60 * 60
SEASON_MODES = ("auto", "spring", "summer", "autumn", "winter")


def current_season():
    today = timezone.localdate()
    return ("winter", "spring", "summer", "autumn")[(today.month % 12) // 3]


def appearance_context(request):
    mode = request.COOKIES.get(SEASON_COOKIE, "auto")
    if mode not in SEASON_MODES:
        mode = "auto"
    automatic = current_season()
    return {
        "season_mode": mode,
        "season": automatic if mode == "auto" else mode,
        "garden_timezone": timezone.get_default_timezone_name(),
    }
