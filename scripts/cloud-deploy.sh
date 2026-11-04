#!/bin/sh
set -eu
npx wrangler deploy --containers-rollout=immediate
