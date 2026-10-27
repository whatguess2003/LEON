#!/bin/sh
set -eu

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo -n "$@"
  fi
}

if ! docker info >/dev/null 2>&1; then
  as_root apt-get update
  as_root apt-get install -y docker.io
fi

if ! docker info >/dev/null 2>&1; then
  as_root mkdir -p /var/run
  as_root dockerd --storage-driver=vfs --host=unix:///var/run/docker.sock >/tmp/dockerd.log 2>&1 &
  i=0
  while [ "$i" -lt 30 ]; do
    if docker info >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 2
  done
fi

docker info
npx wrangler deploy -c wrangler.pms.jsonc --containers-rollout=immediate
