#!/usr/bin/env bash
# Pull and apply the latest server code. Refuses to run over
# uncommitted local changes so it never silently clobbers them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SERVER_DIR}/.." && pwd)"

log() { echo "==> $*"; }

cd "${REPO_ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree has uncommitted changes. Commit, stash, or discard them before deploying." >&2
  git status --short >&2
  exit 1
fi

log "Pulling latest changes"
git pull

cd "${SERVER_DIR}"

log "Building images"
docker compose build

log "Starting services (only changed ones are recreated)"
docker compose up -d

log "Running Django migrations"
docker compose exec -T django python manage.py migrate --noinput

log "Collecting static files"
docker compose exec -T django python manage.py collectstatic --noinput

# See the matching comment in scripts/setup.sh: WhiteNoise only indexes
# STATIC_ROOT at process startup, so a running django needs restarting
# to pick up whatever collectstatic just wrote.
log "Restarting django so it picks up the newly-collected static files"
docker compose restart django

COMMIT="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
log "Deployed commit ${COMMIT}"
