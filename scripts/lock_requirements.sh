#!/usr/bin/env bash
# Regenerate requirements.lock (exact runtime pins used by the Docker image)
# from the dependency ranges in pyproject.toml, using a throwaway virtualenv.
set -euo pipefail

cd "$(dirname "$0")/.."

venv="$(mktemp -d)"
trap 'rm -rf "$venv" build ./*.egg-info' EXIT

python3 -m venv "$venv"
"$venv/bin/pip" install -q --upgrade pip
"$venv/bin/pip" install -q .

{
    echo "# Runtime dependency pins. Regenerate with: scripts/lock_requirements.sh"
    "$venv/bin/pip" freeze --exclude marty
} > requirements.lock

echo "requirements.lock updated"
