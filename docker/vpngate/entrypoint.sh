#!/bin/sh
set -eu

if [ ! -c /dev/net/tun ]; then
  echo "未检测到 /dev/net/tun，尝试自动创建..."
  mkdir -p /dev/net
  mknod /dev/net/tun c 10 200 || true
  chmod 600 /dev/net/tun || true
fi

if [ ! -c /dev/net/tun ]; then
  echo "[启动失败] 未检测到 /dev/net/tun，且自动创建失败。请在 docker compose 中挂载 /dev/net/tun 并添加 NET_ADMIN 权限。"
  exit 1
fi

mkdir -p "${VPNGATE_DATA_DIR:-/data}"
exec "$@"
