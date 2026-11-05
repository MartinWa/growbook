# syntax=docker/dockerfile:1
# Build native dependencies from checksum-pinned sources. Do not ship the GPL x265 wheel.
FROM python:3.14.10-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527 AS os-base
# Apply Debian fixes to inherited packages before building development or production.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

FROM os-base AS native-build
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ADD https://www.sqlite.org/2026/sqlite-autoconf-3510300.tar.gz /tmp/sqlite.tar.gz
ADD https://github.com/strukturag/libde265/releases/download/v1.1.3/libde265-1.1.3.tar.gz /tmp/libde265.tar.gz
ADD https://github.com/strukturag/libheif/releases/download/v1.23.6/libheif-1.23.6.tar.gz /tmp/libheif.tar.gz
RUN echo '81f5be397049b0cae1b167f2225af7646fc0f82e4a9b3c48c9ea3a533e21d77a  /tmp/sqlite.tar.gz' | sha256sum -c - \
    && echo '554228bd17788c99a7e63b37ab5634722190e6e2bf60c1dcb01cef328e133905  /tmp/libde265.tar.gz' | sha256sum -c - \
    && echo '4484346dc5995319dbc11e3a1c35d0a2ec46511ce370900869337fd2c7033125  /tmp/libheif.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/sqlite.tar.gz -C /tmp \
    && tar -xzf /tmp/libde265.tar.gz -C /tmp \
    && tar -xzf /tmp/libheif.tar.gz -C /tmp
RUN cd /tmp/sqlite-autoconf-3510300 \
    && ./configure --prefix=/opt/native --enable-threadsafe --disable-static \
    && make -j2 && make install
RUN cmake -S /tmp/libde265-1.1.3 -B /tmp/de265-build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/native -DCMAKE_INSTALL_LIBDIR=lib \
      -DBUILD_SHARED_LIBS=ON -DENABLE_DECODER=OFF -DENABLE_ENCODER=OFF -DENABLE_SDL=OFF \
    && cmake --build /tmp/de265-build --parallel 2 \
    && cmake --install /tmp/de265-build
RUN cmake -S /tmp/libheif-1.23.6 -B /tmp/heif-build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/native -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_PREFIX_PATH=/opt/native -DBUILD_SHARED_LIBS=ON \
      -DWITH_LIBDE265=ON -DWITH_LIBDE265_PLUGIN=OFF -DENABLE_PLUGIN_LOADING=OFF \
      -DWITH_X265=OFF -DWITH_X264=OFF -DWITH_KVAZAAR=OFF -DWITH_UVG266=OFF \
      -DWITH_VVDEC=OFF -DWITH_VVENC=OFF -DWITH_OpenH264_DECODER=OFF \
      -DWITH_AOM_DECODER=OFF -DWITH_AOM_ENCODER=OFF -DWITH_DAV1D=OFF \
      -DWITH_SvtEnc=OFF -DWITH_RAV1E=OFF -DWITH_FFMPEG_DECODER=OFF \
      -DWITH_JPEG_DECODER=OFF -DWITH_JPEG_ENCODER=OFF \
      -DWITH_OpenJPEG_DECODER=OFF -DWITH_OpenJPEG_ENCODER=OFF -DWITH_OPENJPH_ENCODER=OFF \
      -DWITH_LIBSHARPYUV=OFF -DWITH_UNCOMPRESSED_CODEC=OFF -DWITH_HEADER_COMPRESSION=OFF \
      -DWITH_EXAMPLES=OFF -DWITH_GDK_PIXBUF=OFF -DBUILD_TESTING=OFF -DBUILD_DOCUMENTATION=OFF \
      -DENABLE_EXPERIMENTAL_FEATURES=OFF -DENABLE_PARALLEL_TILE_DECODING=OFF \
    && cmake --build /tmp/heif-build --parallel 2 \
    && cmake --install /tmp/heif-build
# Retain corresponding LGPL sources/licenses for any distribution of native binaries.
RUN mkdir -p /opt/native/share/sources \
    && cp /tmp/libheif.tar.gz /tmp/libde265.tar.gz /opt/native/share/sources/ \
    && chmod 644 /opt/native/share/sources/*.tar.gz

FROM native-build AS python-build
ENV LD_LIBRARY_PATH=/opt/native/lib LIBHEIF_ROOT=/opt/native
COPY requirements.txt requirements-build.txt /tmp/
COPY locale /tmp/translations/locale
COPY tools/compile_translations.py /tmp/translations/tools/compile_translations.py
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-build.txt \
    && python /tmp/translations/tools/compile_translations.py \
    && python -m pip install --no-cache-dir --require-hashes --no-build-isolation \
      --no-binary=pillow-heif --prefix=/opt/python -r /tmp/requirements.txt

FROM os-base AS runtime-base
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/native/lib
COPY --from=native-build /opt/native/lib /opt/native/lib
COPY --from=native-build /opt/native/share/sources /opt/native/share/sources
COPY --from=python-build /opt/python /usr/local
WORKDIR /app
COPY tools/check_runtime.py /usr/local/bin/growbook-check-runtime.py
RUN python /usr/local/bin/growbook-check-runtime.py

FROM runtime-base AS development
# Only the dev group is installed, without dependency resolution. The shared
# source-built pillow-heif stays in the base interpreter's site-packages.
COPY requirements-dev.txt /opt/requirements-dev.txt
RUN python -m venv --system-site-packages /state/venv \
    && /state/venv/bin/python -m pip install --no-cache-dir --require-hashes --no-deps \
       -r /opt/requirements-dev.txt \
    && /state/venv/bin/python /usr/local/bin/growbook-check-runtime.py \
    && mv /state/venv /opt/venv-seed \
    && tar -C /opt/venv-seed -cf - . | sha256sum > /opt/venv-seed.sha256
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
# PowerShell Core keeps the host-side release launcher integration tests on Linux.
ADD https://github.com/PowerShell/PowerShell/releases/download/v7.6.9/powershell-7.6.9-linux-x64.tar.gz /tmp/powershell.tar.gz
RUN echo '1da6d6d6035d651f20e70ddee7c4c15ac6ab8b9fb40ee92240d91263f28a884b  /tmp/powershell.tar.gz' | sha256sum -c - \
    && mkdir -p /opt/powershell \
    && tar -xzf /tmp/powershell.tar.gz -C /opt/powershell \
    && chmod +x /opt/powershell/pwsh \
    && ln -s /opt/powershell/pwsh /usr/local/bin/pwsh \
    && rm /tmp/powershell.tar.gz
RUN apt-get update && apt-get install -y --no-install-recommends git libicu72 libgssapi-krb5-2 \
    && git config --system --add safe.directory /app \
    && /opt/venv-seed/bin/python -m playwright install --with-deps chromium --no-shell \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home developer \
    && mkdir -p /state/tmp && chown -R 1000:1000 /state
ENV PATH=/state/venv/bin:$PATH \
    GROWBOOK_DATA_DIR=/state/development GROWBOOK_STATE_DIR=/state \
    GROWBOOK_LOCALE_DIR=/state/locale \
    TMPDIR=/state/tmp RUFF_CACHE_DIR=/state/ruff-cache \
    PYTEST_ADDOPTS="-o cache_dir=/state/pytest-cache" \
    PIP_REQUIRE_VIRTUALENV=true
COPY tools/dev_entrypoint.py /usr/local/bin/growbook-dev.py
COPY . /app
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/python", "/usr/local/bin/growbook-dev.py"]
CMD ["python", "tools/dev_server.py"]

# Keep production last so plain docker build still produces the shipped image.
FROM runtime-base AS production
ARG GROWBOOK_VERSION=development
RUN python -m pip uninstall --yes pip
ENV GROWBOOK_VERSION=${GROWBOOK_VERSION} \
    GROWBOOK_DATA_DIR=/data \
    DJANGO_SETTINGS_MODULE=growbook.settings.production
COPY . /app
COPY --from=python-build /tmp/translations/locale /app/locale
RUN python -c 'import sqlite3; assert sqlite3.sqlite_version_info >= (3,51,3)' \
    && python -c 'import pillow_heif; info=pillow_heif.libheif_info(); assert "libde265" in info["decoders"]; assert "x265" not in info["encoders"]' \
    && python -c "from PIL import Image; import pillow_heif; pillow_heif.register_heif_opener(); im=Image.open('tests/fixtures/synthetic.heic'); im.load(); assert im.size == (80,60)" \
    && python -c "import gettext, pathlib; [gettext.GNUTranslations(p.open('rb')) for p in pathlib.Path('locale').glob('*/LC_MESSAGES/*.mo')]" \
    && GROWBOOK_DATA_DIR=/tmp/build-data GROWBOOK_SECRET_KEY=build-only-synthetic-key-not-for-runtime-000000000000000000 \
       GROWBOOK_ALLOWED_HOSTS=build.invalid python manage.py collectstatic --noinput \
    && rm -rf /tmp/build-data && mkdir -p /data && chown -R 1000:1000 /data
USER 1000:1000
# Native sources and notices must also be readable by recipients running as non-root.
RUN sha256sum /opt/native/share/sources/*.tar.gz \
    && test -r third_party/libheif/COPYING && test -r third_party/libde265/COPYING
# Catch build-time static permissions that would prevent a non-root WSGI startup.
RUN GROWBOOK_DATA_DIR=/tmp/build-data GROWBOOK_SECRET_KEY=build-only-synthetic-key-not-for-runtime-000000000000000000 \
    GROWBOOK_ALLOWED_HOSTS=build.invalid python -c "from growbook.wsgi import application; assert application"
# One process with two threads. Proxy trust is explicitly configured by the operator.
CMD ["python", "-m", "growbook.server"]
