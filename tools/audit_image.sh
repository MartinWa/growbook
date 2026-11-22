#!/usr/bin/env bash
# Audit an image by tag or digest; optionally write its SPDX SBOM for one platform.
set -euo pipefail
image=$1
sbom=${2:-}
platform=${3:-}
trivy_version=0.76.1
# The audit runs on the release runner for the platform it describes, so the pinned
# scanner archive has to match the runner architecture.
case "$(uname -m)" in
  x86_64)
    trivy_archive=Linux-64bit.tar.gz
    trivy_sha256=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0
    ;;
  aarch64|arm64)
    trivy_archive=Linux-ARM64.tar.gz
    trivy_sha256=e174dff2a6dba5e3ae41f8d0537a6324405bd08132497340304c6b20e0f16a1f
    ;;
  *)
    echo "Unsupported audit architecture: $(uname -m)" >&2
    exit 2
    ;;
esac
platform_args=()
if [ -n "$platform" ]; then
  platform_args=(--platform "$platform")
fi
audit_dir=$(mktemp -d)
trap 'rm -rf -- "$audit_dir"' EXIT
curl --fail --silent --show-error --location --retry 3 \
  "https://github.com/aquasecurity/trivy/releases/download/v${trivy_version}/trivy_${trivy_version}_${trivy_archive}" \
  --output "$audit_dir/trivy.tar.gz"
printf '%s  %s\n' "$trivy_sha256" "$audit_dir/trivy.tar.gz" | sha256sum --check
tar -xzf "$audit_dir/trivy.tar.gz" -C "$audit_dir" trivy
"$audit_dir/trivy" image --scanners vuln --severity HIGH,CRITICAL \
  --ignore-unfixed --exit-code 1 --timeout 10m "${platform_args[@]}" "$image"
if [ -n "$sbom" ]; then
  "$audit_dir/trivy" image --format spdx-json --output "$sbom" "${platform_args[@]}" "$image"
fi
