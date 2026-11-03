#!/usr/bin/env bash
# Regenerate requirements.lock (exact runtime pins used by the Docker image)
# from the dependency ranges in pyproject.toml. Resolution runs inside the same
# base image as the Dockerfile, so the pins are right for Linux even when this
# script is run on macOS.
set -euo pipefail

cd "$(dirname "$0")/.."

base_image="$(sed -n 's/^FROM \([^ ]*\).*/\1/p' Dockerfile | head -1)"

docker run --rm -v "$PWD:/src:ro" "$base_image" sh -ec '
    mkdir /tmp/marty
    cp -r /src/pyproject.toml /src/README.md /src/app /tmp/marty/
    python -m venv /tmp/venv
    /tmp/venv/bin/pip install -q --disable-pip-version-check /tmp/marty
    echo "# Runtime dependency pins. Regenerate with: scripts/lock_requirements.sh"
    /tmp/venv/bin/pip freeze --exclude marty
' > requirements.lock.new

mv requirements.lock.new requirements.lock
echo "requirements.lock updated"
