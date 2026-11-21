#!/usr/bin/env bash
# Check one release platform image by digest and write its SBOM.
set -euo pipefail
: "${IMAGE:?IMAGE must name the release image by digest}"
: "${VERSION:?VERSION must be the release version}"
: "${SBOM:?SBOM must be the SBOM output path}"
: "${PLATFORM:?PLATFORM must be a linux platform such as linux/arm64}"
docker pull "$IMAGE"
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp \
  --env EXPECTED_VERSION="$VERSION" \
  "$IMAGE" python tools/check_image.py
bash "$(dirname "$0")/audit_image.sh" "$IMAGE" "$SBOM" "$PLATFORM"
