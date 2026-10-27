#!/bin/sh
set -eu
npx wrangler deploy -c wrangler.pms.jsonc --containers-rollout=immediate
