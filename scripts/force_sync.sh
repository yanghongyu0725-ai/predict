#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-main}"

echo "[INFO] 强制同步到 origin/${BRANCH}（会丢弃本地未提交修改）"

git fetch origin
# 保存一个应急快照分支，防止误操作
SNAPSHOT="backup-before-force-sync-$(date +%Y%m%d-%H%M%S)"
git branch "$SNAPSHOT" >/dev/null 2>&1 || true
echo "[INFO] 已创建本地快照分支: $SNAPSHOT"

git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd

echo "[OK] 已同步到 origin/$BRANCH"
