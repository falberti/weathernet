#!/usr/bin/env bash
# Pull and apply the latest probe code. Refuses to run over uncommitted
# local changes so it never silently clobbers them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PROBE_DIR}/.." && pwd)"
VENV_DIR="${PROBE_DIR}/venv"

log() { echo "==> $*"; }

cd "${REPO_ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree has uncommitted changes. Commit, stash, or discard them before deploying." >&2
  git status --short >&2
  exit 1
fi

log "Pulling latest changes"
git pull

log "Reinstalling dependencies (pip no-ops on anything unchanged)"
"${VENV_DIR}/bin/pip" install --quiet -r "${PROBE_DIR}/requirements.txt"

log "Restarting weathernet-probe"
sudo systemctl restart weathernet-probe

sleep 1
log "Recent log lines:"
journalctl -u weathernet-probe -n 20 --no-pager
