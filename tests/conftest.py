import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener(thumbnails=False)


@pytest.fixture(autouse=True)
def isolated_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_LOCK_TIMEOUT = 5
    settings.WHITENOISE_USE_FINDERS = True


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        username="gardener", password="Synthetic-password-42"
    )


@pytest.fixture
def other_user(django_user_model):
    return django_user_model.objects.create_user(
        username="other-gardener", password="Synthetic-password-42"
    )


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="garden-admin", password="Synthetic-password-42", is_staff=True
    )


@pytest.fixture
def image_upload():
    def make(*, format="JPEG", size=(80, 60), color=(35, 110, 50), exif=None, **options):
        if format == "HEIF":
            fixture = Path(__file__).parent / "fixtures" / "synthetic.heic"
            return SimpleUploadedFile("synthetic.heic", fixture.read_bytes(), "image/heic")
        buffer = io.BytesIO()
        image = Image.new("RGB", size, color)
        if exif is not None:
            options["exif"] = exif
        image.save(buffer, format=format, **options)
        return SimpleUploadedFile("synthetic-photo.upload", buffer.getvalue(), "image/jpeg")

    return make
