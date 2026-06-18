#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
PLAIN='\033[0m'

DEFAULT_USER="Aimilibot"
DEFAULT_REPO="Xray-Aimili"
DEFAULT_DEPLOY_BRANCH="main"
INSTALL_DIR="${AIMILI_DOCKER_INSTALL_DIR:-/opt/aimilivpn-docker}"
GITHUB_USER="${1:-${DEFAULT_USER}}"
GITHUB_REPO="${2:-${DEFAULT_REPO}}"
DEPLOY_BRANCH="${AIMILI_DEPLOY_BRANCH:-${3:-$DEFAULT_DEPLOY_BRANCH}}"
GITHUB_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"
RESET_DOCKER_DATA="${AIMILI_RESET_DOCKER_DATA:-0}"

TTY_DEVICE=""
if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    TTY_DEVICE="/dev/tty"
fi
PERSIST_BACKUP_DIR=""

ensure_safe_install_dir() {
    case "$INSTALL_DIR" in
        ""|"/"|"/opt"|"/usr"|"/etc"|"/var"|"/root")
            echo -e "${RED}错误: Docker 安装目录异常 (${INSTALL_DIR})，拒绝继续。${PLAIN}"
            exit 1
            ;;
    esac
}

backup_persistent_data() {
    if [ "$RESET_DOCKER_DATA" = "1" ] || [ ! -d "$INSTALL_DIR" ]; then
        return
    fi
    if [ ! -d "${INSTALL_DIR}/vpngate_data" ] && [ ! -f "${INSTALL_DIR}/.env" ]; then
        return
    fi

    PERSIST_BACKUP_DIR="$(mktemp -d /tmp/aimilivpn-docker-data.XXXXXX)"
    if [ -d "${INSTALL_DIR}/vpngate_data" ]; then
        cp -a "${INSTALL_DIR}/vpngate_data" "${PERSIST_BACKUP_DIR}/vpngate_data"
    fi
    if [ -f "${INSTALL_DIR}/.env" ]; then
        cp -a "${INSTALL_DIR}/.env" "${PERSIST_BACKUP_DIR}/.env"
    fi
    echo -e "${GREEN}  -> 已临时保护现有 Docker 配置与数据。${PLAIN}"
}

restore_persistent_data() {
    if [ -z "$PERSIST_BACKUP_DIR" ] || [ ! -d "$PERSIST_BACKUP_DIR" ]; then
        return
    fi
    mkdir -p "$INSTALL_DIR"
    if [ -d "${PERSIST_BACKUP_DIR}/vpngate_data" ]; then
        rm -rf "${INSTALL_DIR}/vpngate_data"
        cp -a "${PERSIST_BACKUP_DIR}/vpngate_data" "${INSTALL_DIR}/vpngate_data"
    fi
    if [ -f "${PERSIST_BACKUP_DIR}/.env" ]; then
        cp -a "${PERSIST_BACKUP_DIR}/.env" "${INSTALL_DIR}/.env"
    fi
    rm -rf "$PERSIST_BACKUP_DIR"
    PERSIST_BACKUP_DIR=""
    echo -e "${GREEN}  -> 已恢复原有 Docker 配置与数据。${PLAIN}"
}

prompt_read() {
    local prompt="$1"
    local __resultvar="$2"
    local value=""
    if [ -n "$TTY_DEVICE" ]; then
        read -r -p "$prompt" value < "$TTY_DEVICE" || true
    elif [ -t 0 ]; then
        read -r -p "$prompt" value || true
    else
        value=""
    fi
    printf -v "$__resultvar" '%s' "$value"
}

if [ "$(id -u)" != "0" ]; then
    echo -e "${RED}错误: 必须以 root 权限运行此脚本。请使用: sudo bash $0${PLAIN}"
    exit 1
fi

OS_TYPE=""
PKG_MGR=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_TYPE="$ID"
fi

case "$OS_TYPE" in
    ubuntu|debian)
        PKG_MGR="apt-get"
        export DEBIAN_FRONTEND=noninteractive
        ;;
    centos|rhel|rocky|almalinux|fedora)
        if command -v dnf >/dev/null 2>&1; then
            PKG_MGR="dnf"
        else
            PKG_MGR="yum"
        fi
        ;;
    alpine)
        PKG_MGR="apk"
        ;;
    *)
        echo -e "${RED}错误: 不支持的操作系统 ($OS_TYPE)。Docker 一键安装目前支持 Ubuntu/Debian/Alpine/CentOS/RHEL/Rocky/AlmaLinux/Fedora。${PLAIN}"
        exit 1
        ;;
esac

echo -e "${BLUE}==========================================================${PLAIN}"
echo -e "${BLUE}        AimiliVPN Docker 一键完整面板安装脚本${PLAIN}"
echo -e "${BLUE}==========================================================${PLAIN}"
cat <<EOF

本脚本将执行：
1. 安装或检测 Docker Engine 与 Docker Compose plugin。
2. 部署源码到 ${INSTALL_DIR}，目标分支 ${DEPLOY_BRANCH}。
3. 生成 Docker .env 和持久化数据目录。
4. 启动完整面板容器，包含 Web 面板、Xray、VPNGate/OpenVPN、本地代理和 WARP/自定义出站能力。

本脚本不会清理宿主机已有 Xray，不会删除宿主机 systemd 服务。
默认会保留 ${INSTALL_DIR}/vpngate_data 和 ${INSTALL_DIR}/.env，避免覆盖已有面板账号、端口、节点与订阅配置。
如需彻底重置 Docker 面板数据，请显式使用：AIMILI_RESET_DOCKER_DATA=1

EOF

if [ -z "$TTY_DEVICE" ] && [ ! -t 0 ]; then
    echo -e "${YELLOW}检测到管道安装模式，自动使用默认配置继续安装。${PLAIN}"
else
    while true; do
        prompt_read "请输入 y 同意并继续安装，输入 n 退出 [Y/n]: " USER_ACCEPT
        case "$USER_ACCEPT" in
            [Yy]|"") break ;;
            [Nn]) echo -e "${YELLOW}已取消安装。${PLAIN}"; exit 0 ;;
            *) echo -e "${RED}请输入 y 或 n。${PLAIN}" ;;
        esac
    done
fi

install_base_packages() {
    echo -e "\n${YELLOW}[1/5] 正在安装基础依赖...${PLAIN}"
    if [ "$PKG_MGR" = "apt-get" ]; then
        apt-get update -q || true
        apt-get install -y ca-certificates curl git iproute2 python3
    elif [ "$PKG_MGR" = "apk" ]; then
        apk update || true
        apk add ca-certificates curl git iproute2 bash python3
    else
        "$PKG_MGR" install -y ca-certificates curl git iproute python3 || "$PKG_MGR" install -y ca-certificates curl git iproute2 python3
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        echo -e "${GREEN}  -> Docker 与 Docker Compose plugin 已安装。${PLAIN}"
        return
    fi

    echo -e "\n${YELLOW}[2/5] 正在安装 Docker Engine 与 Compose plugin...${PLAIN}"
    if [ "$PKG_MGR" = "apt-get" ]; then
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "https://download.docker.com/linux/${OS_TYPE}/gpg" -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        . /etc/os-release
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${OS_TYPE} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
        apt-get update -q
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    elif [ "$PKG_MGR" = "apk" ]; then
        apk add docker docker-cli-compose
        rc-update add docker boot >/dev/null 2>&1 || true
        service docker start >/dev/null 2>&1 || true
    else
        DOCKER_REPO_OS="centos"
        if [ "$OS_TYPE" = "fedora" ]; then
            DOCKER_REPO_OS="fedora"
        fi
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y dnf-plugins-core || true
            dnf config-manager --add-repo "https://download.docker.com/linux/${DOCKER_REPO_OS}/docker-ce.repo" || true
            dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        else
            yum install -y yum-utils || true
            yum-config-manager --add-repo "https://download.docker.com/linux/${DOCKER_REPO_OS}/docker-ce.repo" || true
            yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        fi
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable --now docker
    elif command -v service >/dev/null 2>&1; then
        service docker start >/dev/null 2>&1 || true
    fi

    if ! docker compose version >/dev/null 2>&1; then
        echo -e "${RED}错误: Docker Compose plugin 安装后仍不可用，请检查 Docker 安装日志。${PLAIN}"
        exit 1
    fi
}

ensure_tun_device() {
    echo -e "\n${YELLOW}[3/5] 正在检测 /dev/net/tun...${PLAIN}"
    if [ ! -c /dev/net/tun ]; then
        modprobe tun >/dev/null 2>&1 || true
        mkdir -p /dev/net
        mknod /dev/net/tun c 10 200 >/dev/null 2>&1 || true
        chmod 600 /dev/net/tun >/dev/null 2>&1 || true
    fi
    if [ -c /dev/net/tun ]; then
        echo -e "${GREEN}  -> /dev/net/tun 可用。${PLAIN}"
    else
        echo -e "${YELLOW}  -> 警告: 宿主机暂未检测到 /dev/net/tun。容器启动可能失败，请确认 VPS 内核支持 TUN。${PLAIN}"
    fi
}

cleanup_host_conflicts_and_residuals() {
    echo -e "\n${YELLOW}正在清理宿主机旧版本残留与可能冲突的服务/配置...${PLAIN}"

    # 1. Stop and disable old host-level services
    local old_services=("aimilivpn.service" "aimili-xray.service" "aimili-vpn.service")
    local svc
    for svc in "${old_services[@]}"; do
        if command -v systemctl >/dev/null 2>&1; then
            if systemctl is-active --quiet "$svc" || systemctl is-enabled --quiet "$svc" 2>/dev/null; then
                echo -e "  -> 停止并禁用宿主机系统服务 $svc ..."
                systemctl stop "$svc" >/dev/null 2>&1 || true
                systemctl disable "$svc" >/dev/null 2>&1 || true
                systemctl reset-failed "$svc" >/dev/null 2>&1 || true
            fi
            # Remove systemd service files
            rm -f "/etc/systemd/system/$svc" "/lib/systemd/system/$svc" "/usr/lib/systemd/system/$svc"
        fi
        if command -v rc-service >/dev/null 2>&1; then
            rc-service "$svc" stop >/dev/null 2>&1 || true
            rc-update del "$svc" default >/dev/null 2>&1 || true
            rm -f "/etc/init.d/$svc"
        fi
    done

    # 2. Kill leftover host processes (avoiding port conflicts)
    pkill -TERM -f "vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -TERM -f "aimili-xray" >/dev/null 2>&1 || true
    pkill -TERM -x xray >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -f "vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -KILL -f "aimili-xray" >/dev/null 2>&1 || true
    pkill -KILL -x xray >/dev/null 2>&1 || true

    # 3. Clean up host-level script command links
    local old_commands=("/usr/local/bin/ml-x" "/usr/bin/ml-x" "/usr/local/bin/ml" "/usr/bin/ml")
    local cmd_path
    for cmd_path in "${old_commands[@]}"; do
        if [ -f "$cmd_path" ] || [ -L "$cmd_path" ]; then
            echo -e "  -> 删除宿主机旧命令脚本 $cmd_path"
            rm -f "$cmd_path"
        fi
    done

    # 4. Clean up old residual directories
    local old_dirs=("/etc/aimili-xray" "/opt/aimili-xray")
    local dir_path
    for dir_path in "${old_dirs[@]}"; do
        if [ -d "$dir_path" ]; then
            echo -e "  -> 删除宿主机残留目录 $dir_path"
            rm -rf "$dir_path"
        fi
    done

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
}

cleanup_existing_install() {
    # Call the host conflict cleanup first
    cleanup_host_conflicts_and_residuals

    echo -e "\n${YELLOW}[4/6] 正在停止旧 Docker 面板容器...${PLAIN}"
    if [ -d "$INSTALL_DIR" ]; then
        if [ -f "${INSTALL_DIR}/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
            (
                cd "$INSTALL_DIR"
                docker compose down --remove-orphans >/dev/null 2>&1 || true
            )
        fi
    fi
    docker rm -f aimilivpn-full aimili-vpn-panel aimili-vpngate >/dev/null 2>&1 || true

    if [ "$RESET_DOCKER_DATA" = "1" ]; then
        echo -e "${YELLOW}  -> 已启用 AIMILI_RESET_DOCKER_DATA=1，正在删除 ${INSTALL_DIR} 全部数据。${PLAIN}"
        ensure_safe_install_dir
        rm -rf "$INSTALL_DIR"
    else
        echo -e "${GREEN}  -> 已停止旧容器，并保留 ${INSTALL_DIR}/vpngate_data 与 .env。${PLAIN}"
    fi
}

deploy_source() {
    echo -e "\n${YELLOW}[5/6] 正在部署源码到 ${INSTALL_DIR}...${PLAIN}"
    
    if [ -d "$INSTALL_DIR" ] && [ ! -d "${INSTALL_DIR}/.git" ] && [ ! -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        echo -e "  -> 检测到非空且非有效安装目录 ${INSTALL_DIR}，正在清理以重新克隆..."
        ensure_safe_install_dir
        rm -rf "$INSTALL_DIR"
    fi

    if [ -d "${INSTALL_DIR}/.git" ]; then
        cd "$INSTALL_DIR"
        git fetch origin "$DEPLOY_BRANCH" || git fetch --all || true
        if git rev-parse --verify "origin/${DEPLOY_BRANCH}" >/dev/null 2>&1; then
            git checkout -B "$DEPLOY_BRANCH" "origin/${DEPLOY_BRANCH}"
            git reset --hard "origin/${DEPLOY_BRANCH}"
        else
            git checkout "$DEPLOY_BRANCH" || true
            git pull origin "$DEPLOY_BRANCH" || true
        fi
    elif [ -d "$INSTALL_DIR" ] && [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        echo -e "${YELLOW}  -> 检测到已有非 Git 安装目录，将直接复用。${PLAIN}"
        cd "$INSTALL_DIR"
    else
        mkdir -p "$(dirname "$INSTALL_DIR")"
        git clone -b "$DEPLOY_BRANCH" "$GITHUB_URL" "$INSTALL_DIR" || git clone "$GITHUB_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
        git checkout "$DEPLOY_BRANCH" >/dev/null 2>&1 || true
    fi
}

random_token() {
    python3 -c "import random,string; print(''.join(random.choices(string.ascii_letters + string.digits, k=12)))" 2>/dev/null || tr -dc A-Za-z0-9 </dev/urandom | head -c 12
}

json_value_or_default() {
    local file="$1"
    local key="$2"
    local default_value="$3"
    if [ -f "$file" ]; then
        python3 -c "import json,sys; data=json.load(open(sys.argv[1], encoding='utf-8')); print(data.get(sys.argv[2], sys.argv[3]))" "$file" "$key" "$default_value" 2>/dev/null || echo "$default_value"
    else
        echo "$default_value"
    fi
}

env_value_or_default() {
    local file="$1"
    local key="$2"
    local default_value="$3"
    if [ -f "$file" ]; then
        local line
        line="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 || true)"
        if [ -n "$line" ]; then
            printf '%s\n' "${line#*=}"
            return
        fi
    fi
    echo "$default_value"
}

write_env_file() {
    echo -e "\n${YELLOW}[6/6] 正在生成 Docker 环境配置...${PLAIN}"
    mkdir -p "${INSTALL_DIR}/vpngate_data"
    AUTH_FILE="${INSTALL_DIR}/vpngate_data/ui_auth.json"
    ENV_FILE="${INSTALL_DIR}/.env"

    DEFAULT_UI_PORT="${AIMILI_UI_PORT:-$(json_value_or_default "$AUTH_FILE" port "$(env_value_or_default "$ENV_FILE" UI_PORT 8787)")}"
    DEFAULT_PROXY_PORT="${AIMILI_PROXY_PORT:-$(json_value_or_default "$AUTH_FILE" proxy_port "$(env_value_or_default "$ENV_FILE" LOCAL_PROXY_PORT 7928)")}"
    DEFAULT_SECRET="${AIMILI_SECRET_PATH:-$(json_value_or_default "$AUTH_FILE" secret_path "$(env_value_or_default "$ENV_FILE" SECRET_PATH "$(random_token)")")}"
    DEFAULT_USER="${AIMILI_UI_USERNAME:-$(json_value_or_default "$AUTH_FILE" username "$(env_value_or_default "$ENV_FILE" UI_USERNAME "$(random_token)")")}"
    DEFAULT_PASS="${AIMILI_UI_PASSWORD:-$(json_value_or_default "$AUTH_FILE" password "$(env_value_or_default "$ENV_FILE" UI_PASSWORD "$(random_token)")")}"

    if [ -z "$TTY_DEVICE" ] && [ ! -t 0 ]; then
        echo -e "${YELLOW}  -> 管道安装模式：端口、后缀、账号和密码将自动生成或使用环境变量。${PLAIN}"
        INPUT_UI_PORT=""
        INPUT_PROXY_PORT=""
        INPUT_SECRET=""
        INPUT_USER=""
        INPUT_PASS=""
    else
        prompt_read "请输入网页端口 [默认 ${DEFAULT_UI_PORT}]: " INPUT_UI_PORT
        prompt_read "请输入代理端口 [默认 ${DEFAULT_PROXY_PORT}]: " INPUT_PROXY_PORT
        prompt_read "请输入安全后缀 [默认随机 ${DEFAULT_SECRET}]: " INPUT_SECRET
        prompt_read "请输入登录账号 [默认随机 ${DEFAULT_USER}]: " INPUT_USER
        prompt_read "请输入登录密码 [默认随机 ${DEFAULT_PASS}]: " INPUT_PASS
    fi

    UI_PORT="${INPUT_UI_PORT:-$DEFAULT_UI_PORT}"
    LOCAL_PROXY_PORT="${INPUT_PROXY_PORT:-$DEFAULT_PROXY_PORT}"
    SECRET_PATH="${INPUT_SECRET:-$DEFAULT_SECRET}"
    UI_USERNAME="${INPUT_USER:-$DEFAULT_USER}"
    UI_PASSWORD="${INPUT_PASS:-$DEFAULT_PASS}"

    cat > "${INSTALL_DIR}/.env" <<EOF
UI_PORT=${UI_PORT}
LOCAL_PROXY_PORT=${LOCAL_PROXY_PORT}
SECRET_PATH=${SECRET_PATH}
UI_USERNAME=${UI_USERNAME}
UI_PASSWORD=${UI_PASSWORD}
FETCH_INTERVAL_SECONDS=${AIMILI_FETCH_INTERVAL_SECONDS:-960}
CHECK_INTERVAL_SECONDS=${AIMILI_CHECK_INTERVAL_SECONDS:-960}
TARGET_VALID_NODES=${AIMILI_TARGET_VALID_NODES:-3}
EOF

    UI_PORT="$UI_PORT" LOCAL_PROXY_PORT="$LOCAL_PROXY_PORT" SECRET_PATH="$SECRET_PATH" UI_USERNAME="$UI_USERNAME" UI_PASSWORD="$UI_PASSWORD" AUTH_FILE="$AUTH_FILE" python3 - <<'PY'
import json
import os

path = os.environ["AUTH_FILE"]
try:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
except Exception:
    cfg = {}

cfg.update({
    "host": "0.0.0.0",
    "port": int(os.environ["UI_PORT"]),
    "secret_path": os.environ["SECRET_PATH"],
    "username": os.environ["UI_USERNAME"],
    "password": os.environ["UI_PASSWORD"],
    "proxy_port": int(os.environ["LOCAL_PROXY_PORT"]),
})
cfg.setdefault("routing_mode", "auto")
cfg.setdefault("force_country", "")
cfg.setdefault("domain", "")
cfg.setdefault("tls_cert_file", "")
cfg.setdefault("tls_key_file", "")
cfg.setdefault("domain_certs", [])

with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
PY
}

start_stack() {
    cd "$INSTALL_DIR"
    echo -e "\n${YELLOW}正在构建并启动 AimiliVPN Docker 完整面板...${PLAIN}"
    docker compose up -d --build
}

create_host_menu_launcher() {
    echo -e "\n${YELLOW}正在创建宿主机全局管理命令 'ml'...${PLAIN}"
    cat > /usr/bin/ml <<EOF
#!/bin/bash
cd ${INSTALL_DIR}
export AIMILI_INSTALL_DIR=${INSTALL_DIR}
export VPNGATE_DATA_DIR=${INSTALL_DIR}/vpngate_data
export AIMILI_RUNTIME=docker
exec /usr/bin/python3 cli/menu.py "\$@"
EOF
    chmod +x /usr/bin/ml
}

public_ip() {
    curl -s --max-time 3 https://api.ipify.org || curl -s --max-time 3 https://ifconfig.me || curl -s --max-time 3 icanhazip.com || echo "您的服务器公网IP"
}

install_base_packages
install_docker
ensure_tun_device
backup_persistent_data
cleanup_existing_install
deploy_source
restore_persistent_data
write_env_file
start_stack
create_host_menu_launcher

PUBLIC_IP="$(public_ip)"

echo -e "\n${GREEN}==========================================================${PLAIN}"
echo -e "${GREEN}             AimiliVPN Docker 完整面板已启动！${PLAIN}"
echo -e "${GREEN}==========================================================${PLAIN}"
echo -e "  * 网页控制面板:  ${BLUE}http://${PUBLIC_IP}:${UI_PORT}/${SECRET_PATH}/${PLAIN}"
echo -e "  * 网页管理账号:  ${YELLOW}${UI_USERNAME}${PLAIN}"
echo -e "  * 网页管理密码:  ${YELLOW}${UI_PASSWORD}${PLAIN}"
echo -e "  * HTTP/SOCKS5 代理端口: ${BLUE}${LOCAL_PROXY_PORT}${PLAIN}"
echo -e " --------------------------------------------------------"
echo -e "  * 打开管理菜单: ${YELLOW}ml${PLAIN}"
echo -e "  * 查看状态:     ${YELLOW}ml status${PLAIN}"
echo -e "  * 查看日志:     ${YELLOW}ml logs${PLAIN}"
echo -e "  * 在线更新:     ${YELLOW}ml update${PLAIN}"
echo -e "  * 完全卸载:     ${YELLOW}ml uninstall${PLAIN}"
echo -e "=========================================================="
