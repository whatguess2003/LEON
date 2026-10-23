#!/bin/sh
set -eu
DOCKERFILE="$1"
CONFIG="$2"
REPO="$3"
cd "$(dirname "$0")/.."

ACCOUNT="${CLOUDFLARE_ACCOUNT_ID:-332c5e41cdaf724cedc69e812890fa6d}"
REGISTRY="registry.cloudflare.com/${ACCOUNT}"
CACHE="${REGISTRY}/${REPO}:cache"

npx --yes wrangler@4.152.0 containers registries credentials registry.cloudflare.com --push --pull --json > /tmp/reg.json
node <<'EOF'
const fs = require("fs");
const { spawnSync } = require("child_process");
const raw = fs.readFileSync("/tmp/reg.json", "utf8");
const start = raw.indexOf("{");
const end = raw.lastIndexOf("}");
const creds = JSON.parse(raw.slice(start, end + 1));
fs.unlinkSync("/tmp/reg.json");
if (!creds.username || !creds.password) process.exit(1);
const login = spawnSync(
  "docker",
  ["login", "registry.cloudflare.com", "--username", creds.username, "--password-stdin"],
  { input: creds.password, stdio: ["pipe", "inherit", "inherit"] }
);
process.exit(login.status === 0 ? 0 : login.status || 1);
EOF

docker pull "$CACHE" || true
DOCKER_BUILDKIT=1 docker build \
  --platform linux/amd64 \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  --cache-from "$CACHE" \
  -f "$DOCKERFILE" \
  -t "$CACHE" \
  .
docker push "$CACHE"

WRANGLER_FILE="$CONFIG" IMAGE_FROM="\"./${DOCKERFILE}\"" IMAGE_TO="\"${CACHE}\"" node <<'EOF'
const fs = require("fs");
const file = process.env.WRANGLER_FILE;
const from = process.env.IMAGE_FROM;
const to = process.env.IMAGE_TO;
const text = fs.readFileSync(file, "utf8");
if (!text.includes(from)) process.exit(1);
fs.writeFileSync(file, text.split(from).join(to));
EOF

npx --yes wrangler@4.152.0 deploy -c "$CONFIG"
