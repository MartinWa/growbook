"""Age-bounded orphan cleanup coordinated with all media writers and snapshots."""

import shutil
import time
from pathlib import Path

from django.conf import settings

from .media import media_operation
from .models import Photo


def cleanup_orphans(*, apply=False, minimum_age=86400, limit=1000):
    if minimum_age < 0 or limit < 1:
        raise ValueError("Cleanup age must be nonnegative and limit must be positive.")
    removed = []
    with media_operation():
        root = Path(settings.MEDIA_ROOT)
        references = set()
        for original, display, thumbnail in Photo.objects.values_list(
            "original", "display", "thumbnail"
        ):
            for relative in (original, display, thumbnail):
                references.add(str(Path(relative).parent).replace("\\", "/"))
        candidates = []
        candidates.extend((root / ".staging").glob("capture-*"))
        candidates.extend((root / ".staging").glob("register-*"))
        candidates.extend((root / "photos").glob("*/*/*"))
        threshold = time.time() - minimum_age
        for folder in sorted(candidates):
            if len(removed) >= limit:
                break
            if not folder.is_dir() or folder.is_symlink() or folder.stat().st_mtime > threshold:
                continue
            relative = folder.relative_to(root).as_posix()
            if relative in references:
                continue
            if apply:
                shutil.rmtree(folder)
            removed.append(relative)
    return removed
