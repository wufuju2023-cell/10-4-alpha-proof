#!/usr/bin/env bash
# 把本仓库同步到云 GPU 运行副本（云端无 rsync，用 tar 管道）。
# 用法：bash scripts/deploy_cloud.sh
set -eu
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLOUD="${CLOUD_HOST:-root@100.91.25.4}"
DEST="${CLOUD_DEST:-/mnt/workspace/alphaproof-aligned/repo}"
KEY="${SSH_KEY:-$HOME/.ssh/agent-root-mgmt}"
tar czf - -C "$REPO_DIR" --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' . \
  | ssh -i "$KEY" -o ConnectTimeout=20 "$CLOUD" "mkdir -p '$DEST' && tar xzf - -C '$DEST'"
echo "synced -> $CLOUD:$DEST"
