import io
import os
import select
import subprocess
from pathlib import Path

import playwright
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener(thumbnails=False)


@pytest.fixture(scope="session")
def browser_endpoint(tmp_path_factory):
    """Reuse Chromium without keeping a Python asyncio loop active between tests."""
    driver = Path(playwright.__file__).parent / "driver"
    log_path = tmp_path_factory.mktemp("browser-server") / "stderr.log"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [
                str(driver / "node"),
                str(Path(__file__).with_name("browser_server.cjs")),
                str(driver / "package"),
                os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium"),
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            if not select.select([server.stdout], [], [], 30)[0]:
                raise RuntimeError(f"Browser server startup timed out: {log_path.read_text()}")
            endpoint = server.stdout.readline().strip()
            if not endpoint.startswith("ws://127.0.0.1:"):
                raise RuntimeError(f"Browser server failed to start: {log_path.read_text()}")
            yield endpoint
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
            server.stdout.close()


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


@pytest.fixture
def corrupt_apple_hdr_upload(apple_hdr_upload):
    """Damage an auxiliary JPEG header while keeping the primary image and MPF index."""

    def make(*, header_byte=0):
        data = bytearray(apple_hdr_upload().read())
        auxiliary_start = data.index(b"\xff\xd8\xff", 3)
        data[auxiliary_start + header_byte] = 1
        return SimpleUploadedFile("synthetic-corrupt-hdr.jpeg", bytes(data), "image/jpeg")

    return make


@pytest.fixture
def planting_catalog(user):
    """Seed valid synthetic records for both write tests and isolated read tests."""
    from core.writes import validated_write
    from garden.models import Crop, GrowingArea, Variety

    with validated_write():
        crop = Crop.objects.create(name="Tomato", name_key="tomato")
        first = Variety.objects.create(crop=crop, name="Golden", name_key="golden")
        second = Variety.objects.create(crop=crop, name="Red", name_key="red")
        area = GrowingArea.objects.create(name="Bed", name_key="bed")
    return crop, first, second, area
