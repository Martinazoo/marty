#!/usr/bin/env bash
# Dump the MARTY database to a compressed, timestamped file and prune old dumps.
#
# Usage: scripts/backup_db.sh [backup_dir]
#   BACKUP_RETENTION_DAYS (default 14) controls how long dumps are kept.
set -euo pipefail

cd "$(dirname "$0")/.."

backup_dir="${1:-${BACKUP_DIR:-./backups}}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

target="$backup_dir/marty_$(date -u +%Y%m%dT%H%M%SZ).dump"

umask 077
trap 'rm -f "$target.partial"' ERR
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
    > "$target.partial"
mv "$target.partial" "$target"

find "$backup_dir" -maxdepth 1 -name 'marty_*.dump' -type f -mtime +"$retention_days" -delete

echo "Backup written to $target ($(du -h "$target" | cut -f1))"
