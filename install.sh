#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
PLAIN='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_INSTALLER="${SCRIPT_DIR}/install-docker.sh"

echo -e "${YELLOW}AimiliVPN 现在只支持 Docker 安全网关部署。${PLAIN}"
echo -e "${YELLOW}宿主机仅保留 ml 管理菜单；OpenVPN、Xray、代理网关均运行在 Docker 容器内。${PLAIN}"

if [ ! -f "$DOCKER_INSTALLER" ]; then
    echo -e "${RED}错误: 未找到 install-docker.sh，无法继续 Docker 安装。${PLAIN}"
    exit 1
fi

echo -e "${GREEN}正在切换到 Docker 一键安装脚本...${PLAIN}"
exec bash "$DOCKER_INSTALLER" "$@"
