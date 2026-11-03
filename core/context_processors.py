from django.conf import settings

MIB = 1024 * 1024


def photo_limits(request):
    """Expose the configured upload limits to the shared photo picker."""
    return {
        "photo_max_count": settings.PHOTO_MAX_COUNT,
        "photo_max_file_mib": settings.PHOTO_MAX_FILE_BYTES // MIB,
        "photo_max_total_mib": settings.PHOTO_MAX_TOTAL_BYTES // MIB,
    }
