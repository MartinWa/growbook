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


@pytest.fixture
def apple_hdr_upload():
    """Generate a solid-color primary JPEG and a synthetic Apple HDR gain map."""

    def make(
        *, xmp=None, primary_xmp=None, aux_mode="L", aux_size=(40, 30), extra_images=(), **options
    ):
        if xmp is None:
            xmp = (
                b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                b'<rdf:Description xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/" '
                b'xmlns:hdr="http://ns.apple.com/HDRGainMap/1.0/">'
                b"<apdi:AuxiliaryImageType>urn:com:apple:photo:2020:aux:hdrgainmap</apdi:AuxiliaryImageType>"
                b"<hdr:HDRGainMapVersion>131072</hdr:HDRGainMapVersion>"
                b"</rdf:Description></rdf:RDF></x:xmpmeta>"
            )
        buffer = io.BytesIO()
        with (
            Image.new("RGB", (80, 60), (35, 110, 50)) as primary,
            Image.new(aux_mode, aux_size, 128) as auxiliary,
        ):
            auxiliary.encoderinfo = {"xmp": xmp}
            primary.save(
                buffer,
                format="MPO",
                save_all=True,
                append_images=[auxiliary, *extra_images],
                xmp=primary_xmp,
                **options,
            )
        return SimpleUploadedFile("synthetic-hdr.jpeg", buffer.getvalue(), "image/jpeg")

    return make
