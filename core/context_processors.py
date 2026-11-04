from django.conf import settings

from .releases import RELEASES_URL, REPOSITORY_URL, version_number

MIB = 1024 * 1024


def app_release(request):
    version = settings.APP_VERSION
    release_number = version_number(version.split("-", 1)[0])
    return {
        "app_version": version,
        "app_version_label": ".".join(map(str, release_number)) if release_number else "",
        "app_is_release": version_number(version) is not None,
        "app_repository_url": REPOSITORY_URL,
        "app_releases_url": RELEASES_URL,
        "app_check_updates": settings.UPDATE_CHECK_ENABLED and request.user.is_authenticated,
    }


def photo_limits(request):
    """Expose the configured upload limits to the shared photo picker."""
    return {
        "photo_max_count": settings.PHOTO_MAX_COUNT,
        "photo_max_file_mib": settings.PHOTO_MAX_FILE_BYTES // MIB,
        "photo_max_total_mib": settings.PHOTO_MAX_TOTAL_BYTES // MIB,
    }
