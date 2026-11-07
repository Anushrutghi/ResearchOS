#!/bin/sh
set -eu

base_image=${1:?Usage: build.sh python:3.12-slim@sha256:<digest> local/researchos-python:3.12}
tag=${2:?Usage: build.sh python:3.12-slim@sha256:<digest> local/researchos-python:3.12}
case "$base_image" in
  *@sha256:*) ;;
  *) echo "Base image must be pinned by a full SHA-256 digest." >&2; exit 2 ;;
esac
digest=${base_image##*@sha256:}
case "$digest" in
  *[!0-9a-f]*|'') echo "Base image digest must contain 64 lowercase hexadecimal characters." >&2; exit 2 ;;
esac
[ "${#digest}" -eq 64 ] || { echo "Base image digest must contain 64 lowercase hexadecimal characters." >&2; exit 2; }

docker build --pull=false --build-arg "PYTHON_BASE_IMAGE=$base_image" --tag "$tag" --file "$(dirname "$0")/Dockerfile" "$(dirname "$0")"
docker image inspect --format 'Built local image ID: {{.Id}}' "$tag"
printf '%s\n' "Review and push this image to a registry, then configure its returned RepoDigest (repository@sha256:...) and a matching sandbox manifest."
