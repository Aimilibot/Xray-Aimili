#!/bin/sh
set -eu

DATA_DIR="${VPNGATE_DATA_DIR:-/data}"
AUTH_FILE="${DATA_DIR}/ui_auth.json"

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

mkdir -p "$DATA_DIR" "$DATA_DIR/configs" "$DATA_DIR/logs"

if [ ! -f "$AUTH_FILE" ]; then
  UI_HOST="${UI_HOST:-0.0.0.0}" \
  UI_PORT="${UI_PORT:-8787}" \
  LOCAL_PROXY_PORT="${LOCAL_PROXY_PORT:-7928}" \
  SECRET_PATH="${SECRET_PATH:-}" \
  UI_USERNAME="${UI_USERNAME:-}" \
  UI_PASSWORD="${UI_PASSWORD:-}" \
  AUTH_FILE="$AUTH_FILE" \
  python - <<'PY'
import json
import os
import random
import string

def random_token(length=12):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

def random_username():
    while True:
        value = random_token()
        if value[0].isalpha() and any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value

def random_password():
    while True:
        value = random_token()
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value

cfg = {
    "host": os.environ.get("UI_HOST", "0.0.0.0"),
    "port": int(os.environ.get("UI_PORT", "8787")),
    "secret_path": os.environ.get("SECRET_PATH") or random_token(),
    "username": os.environ.get("UI_USERNAME") or random_username(),
    "password": os.environ.get("UI_PASSWORD") or random_password(),
    "proxy_port": int(os.environ.get("LOCAL_PROXY_PORT", "7928")),
    "routing_mode": "auto",
    "force_country": "",
    "domain": "",
    "tls_cert_file": "",
    "tls_key_file": "",
    "domain_certs": [],
}

with open(os.environ["AUTH_FILE"], "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print("已生成首次启动面板登录配置。")
print(f"URL path: /{cfg['secret_path']}/")
print(f"Username: {cfg['username']}")
print(f"Password: {cfg['password']}")
PY
fi

exec "$@"
