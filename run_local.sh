#!/usr/bin/env bash
# ============================================================
# 本机实时盯盘入口（由 crontab 调用）
#   用法: run_local.sh monitor   → 每分钟快扫 + 实时微信推送
#         run_local.sh digest    → 收盘后发送汇总邮件（15:12）
# 凭据从 .env 读取（.env 已被 gitignore，不会提交）
# ============================================================
set -a
source "$(dirname "$0")/.env" 2>/dev/null || { echo "[run_local] 缺少 .env"; exit 1; }
set +a
cd "$(dirname "$0")" || exit 1

case "$1" in
  monitor)
    exec python3 monitor.py --fast
    ;;
  digest)
    exec python3 digest.py
    ;;
  *)
    echo "用法: $0 monitor|digest"
    exit 1
    ;;
esac
