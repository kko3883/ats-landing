#!/bin/sh
# Re-deploy the ATS FX stack on the NAS (IB Gateway + Nautilus daemon).
# Uses dgit (containerized alpine/git, since the NAS has no native git).
# First-time clone is in DEPLOY.md; run THIS for subsequent updates.
#
#   sh nas_redeploy.sh [branch]     # default branch below
set -eu

BRANCH="${1:-claude/nautilus-fx-deploy}"
DGIT=/volume1/docker/dgit
PROJECT=/volume1/docker/ats-landing
APP="$PROJECT/trading/nautilus_port"

# Pull latest. dgit runs `git pull` in a container mounted on $PWD,
# so we must cd into the repo first. It will sudo (docker.sock needs root).
cd "$PROJECT"
"$DGIT" pull origin "$BRANCH"

cd "$APP"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env 2>/dev/null || true   # /volume1/docker is ACL-gated; POSIX chmod EPERMs (harmless)
  echo ">> Created $APP/.env — edit it with your IBKR Gateway creds + DU account id, then re-run."
  exit 1
fi

sudo docker-compose up -d --build
sudo docker-compose ps
