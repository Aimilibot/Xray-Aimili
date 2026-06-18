#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
PLAIN='\033[0m'

# 1. Check root permissions
if [ "$(id -u)" != "0" ]; then
    echo -e "${RED}错误: 必须以 root 权限运行此脚本。请使用: sudo bash $0${PLAIN}"
    exit 1
fi

# 2. Check OS distribution and set package manager
OS_TYPE=""
PKG_MGR=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_TYPE=$ID
fi

case "$OS_TYPE" in
    ubuntu|debian)
        PKG_MGR="apt-get"
        export DEBIAN_FRONTEND=noninteractive
        ;;
    alpine)
        PKG_MGR="apk"
        ;;
    centos|rhel|rocky|almalinux|fedora)
        if command -v dnf >/dev/null 2>&1; then
            PKG_MGR="dnf"
        else
            PKG_MGR="yum"
        fi
        ;;
    *)
        echo -e "${RED}错误: 不支持的操作系统 ($OS_TYPE)！目前仅支持 Ubuntu/Debian/Alpine/CentOS/RHEL/Rocky/AlmaLinux/Fedora。${PLAIN}"
        exit 1
        ;;
esac

echo -e "${BLUE}==========================================================${PLAIN}"
echo -e "${BLUE}        欢迎使用 AimiliVPN 一键源码部署与管理脚本${PLAIN}"
echo -e "${BLUE}==========================================================${PLAIN}"

# 3. Configure GitHub Repository URL
# Default to the official repository (Aimilibot/Xray-Aimili)
DEFAULT_USER="Aimilibot"
DEFAULT_REPO="Xray-Aimili"

# Allow custom repository override via command line arguments
GITHUB_USER="${1:-${DEFAULT_USER}}"
GITHUB_REPO="${2:-${DEFAULT_REPO}}"

GITHUB_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"
INSTALL_DIR="/opt/aimilivpn"
DEFAULT_DEPLOY_BRANCH="main"
DEPLOY_BRANCH="${AIMILI_DEPLOY_BRANCH:-${3:-$DEFAULT_DEPLOY_BRANCH}}"
if [ -z "$DEPLOY_BRANCH" ]; then
    DEPLOY_BRANCH="$DEFAULT_DEPLOY_BRANCH"
fi

show_install_terms() {
    echo -e "\n${YELLOW}安装前用户确认条例${PLAIN}"
    echo -e "${YELLOW}请仔细阅读以下内容。只有输入 y 同意后，脚本才会继续安装。${PLAIN}"
    cat <<EOF

本脚本将对当前 VPS 执行以下操作：

1. 安装或更新系统依赖
   包括 openvpn、curl、git、ca-certificates、iptables、iproute/iproute2、psmisc、python3、unzip 等。

2. 全面重装 Xray Core
   - 如果机器里已经安装过 Xray，本脚本会先停止 Xray 服务与进程。
   - 会删除旧的 Xray 二进制文件、systemd/OpenRC 服务文件、配置目录和日志目录。
   - 可能被删除的路径包括：
     /usr/local/bin/xray
     /usr/bin/xray
     /etc/xray
     /usr/local/etc/xray
     /var/log/xray
     /usr/local/share/xray
     /etc/systemd/system/xray.service
   - 然后会重新下载并安装官方 Xray Core。
   - 安装后会停用官方 xray.service，Xray 将由 AimiliVPN 面板托管启动，避免端口占用冲突。

3. 部署或更新 AimiliVPN 源码
   - 默认部署目录为：${INSTALL_DIR}
   - 目标分支为：${DEPLOY_BRANCH}
   - 默认目标分支固定为 main，不会因为 ${INSTALL_DIR} 当前停留在其他分支而强制修改。
   - 如果该目录已存在且不是本地开发模式，脚本会拉取 GitHub 仓库并强制重置源码到目标分支。
   - 这可能覆盖 ${INSTALL_DIR} 目录内未提交或手动修改过的源码文件。

4. 检测并清除老旧本地缓存
   - 会先停止旧的 AimiliVPN 管理服务，避免旧进程继续写入缓存。
   - 会删除 ${INSTALL_DIR}/vpngate_data 目录内的运行数据与缓存。
   - 会删除旧节点列表、OpenVPN 临时配置、面板状态、代理检测结果、日志、流量统计、订阅链接、订阅节点、出站节点、路由规则、Xray 面板缓存配置等本地数据。
   - 会清理 Python __pycache__、.pyc、.pyo、.pytest_cache 等缓存。
   - 清理后会按全新安装重新生成网页面板账号、密码、安全后缀和运行状态。

5. 创建或覆盖系统服务与命令
   - 会创建/覆盖 aimilivpn.service 或 OpenRC 服务配置。
   - 会创建/覆盖全局命令：/usr/bin/ml
   - 会重启 AimiliVPN 管理服务。

6. 修改网络相关配置
   - 会写入 /etc/sysctl.d/99-aimilivpn.conf 或修改 /etc/sysctl.conf。
   - 会把 rp_filter 调整为 2，以支持策略路由和代理出口检测。
   - 安装或重启过程中，当前 SSH 网络通常不会受影响，但 VPN/Xray/代理相关连接可能短暂中断。

7. 创建网页面板账号配置
   - 首次安装会生成或写入网页端口、安全后缀、登录账号和密码。
   - 配置会保存在 ${INSTALL_DIR}/vpngate_data/ui_auth.json。

可能造成的影响：

- 旧 Xray 配置、证书引用、日志、systemd 服务和手动安装的 Xray 文件可能被删除。
- 如果你当前正在使用机器上已有的 Xray 服务，安装过程会中断它，并改由 AimiliVPN 面板托管。
- ${INSTALL_DIR} 里的本地源码改动可能被 Git 强制覆盖，除非存在 ${INSTALL_DIR}/.local_dev。
- ${INSTALL_DIR}/vpngate_data 内的旧面板配置、登录账号密码、订阅节点、订阅链接、路由规则、日志、流量统计和历史状态会被清空并重新生成。
- 系统依赖和网络参数会被调整。
- OpenVPN 安装后默认不会自动连接，需要进入网页手动启动。

如果这台 VPS 上有重要的旧 Xray 配置、订阅配置、面板账号或运行日志，请先自行备份后再继续。

EOF
}

show_install_terms
while true; do
    if ! read -r -p "请输入 y 同意并继续安装，输入 n 拒绝并退出 [y/N]: " USER_ACCEPT; then
        echo -e "\n${RED}未读取到用户输入，已取消安装。${PLAIN}"
        exit 1
    fi
    case "$USER_ACCEPT" in
        [Yy])
            echo -e "${GREEN}已确认，同意继续安装。${PLAIN}"
            break
            ;;
        [Nn]|"")
            echo -e "${YELLOW}用户拒绝安装，脚本已退出，未执行任何系统修改。${PLAIN}"
            exit 0
            ;;
        *)
            echo -e "${RED}请输入 y 或 n。${PLAIN}"
            ;;
    esac
done

echo -e "\n${YELLOW}[1/6] 正在安装系统基础依赖...${PLAIN}"
if [ "$PKG_MGR" = "apt-get" ]; then
    echo -e "  -> 正在运行 apt-get update 更新软件源清单..."
    apt-get update -q || true
    echo -e "  -> 正在运行 apt-get install 安装基础依赖包..."
    apt-get install -y openvpn curl git ca-certificates iptables iproute2 psmisc python3 unzip
elif [ "$PKG_MGR" = "apk" ]; then
    echo -e "  -> 正在运行 apk update 更新软件源清单..."
    apk update || true
    echo -e "  -> 正在运行 apk add 安装基础依赖包..."
    # bash is required for this script itself and some internal logic
    apk add openvpn curl git ca-certificates iptables iproute2 psmisc python3 bash unzip
elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
    echo -e "  -> 正在运行 $PKG_MGR 安装基础依赖包..."
    if [ "$OS_TYPE" != "fedora" ]; then
        echo -e "     -> 正在安装 EPEL 软件源 (以支持 openvpn)..."
        $PKG_MGR install -y epel-release || true
    fi
    # Try installing packages. Note: iproute or iproute2
    $PKG_MGR install -y openvpn curl git ca-certificates iptables iproute psmisc python3 unzip || \
    $PKG_MGR install -y openvpn curl git ca-certificates iptables iproute2 psmisc python3 unzip
fi

download_xray_install_script() {
    local target="$1"
    local urls=(
        "https://github.com/XTLS/Xray-install/raw/main/install-release.sh"
        "https://fastly.jsdelivr.net/gh/XTLS/Xray-install@main/install-release.sh"
        "https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh"
    )
    local url
    for url in "${urls[@]}"; do
        echo -e "  -> 正在尝试下载 Xray 官方安装脚本: ${url}"
        if curl -L --connect-timeout 10 --max-time 30 -fsS "$url" -o "$target"; then
            if [ -s "$target" ]; then
                return 0
            fi
        fi
    done
    return 1
}

cleanup_existing_xray() {
    echo -e "  -> 正在全面清理旧 Xray Core、服务与残留配置..."

    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop xray.service xray@*.service >/dev/null 2>&1 || true
        systemctl disable xray.service xray@*.service >/dev/null 2>&1 || true
        systemctl reset-failed xray.service xray@*.service >/dev/null 2>&1 || true
    fi
    if command -v rc-service >/dev/null 2>&1; then
        rc-service xray stop >/dev/null 2>&1 || true
        rc-update del xray default >/dev/null 2>&1 || true
    fi

    pkill -TERM -x xray >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -x xray >/dev/null 2>&1 || true

    if [ "$PKG_MGR" = "apt-get" ] && command -v dpkg >/dev/null 2>&1; then
        dpkg -s xray >/dev/null 2>&1 && apt-get purge -y xray || true
        dpkg -s xray-core >/dev/null 2>&1 && apt-get purge -y xray-core || true
    elif { [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; } && command -v rpm >/dev/null 2>&1; then
        rpm -q xray >/dev/null 2>&1 && $PKG_MGR remove -y xray || true
        rpm -q xray-core >/dev/null 2>&1 && $PKG_MGR remove -y xray-core || true
    elif [ "$PKG_MGR" = "apk" ] && command -v apk >/dev/null 2>&1; then
        apk info -e xray >/dev/null 2>&1 && apk del xray || true
        apk info -e xray-core >/dev/null 2>&1 && apk del xray-core || true
    fi

    rm -f /usr/local/bin/xray /usr/bin/xray /bin/xray
    rm -f /etc/systemd/system/xray.service /etc/systemd/system/xray@.service
    rm -f /lib/systemd/system/xray.service /lib/systemd/system/xray@.service
    rm -f /usr/lib/systemd/system/xray.service /usr/lib/systemd/system/xray@.service
    rm -f /etc/init.d/xray
    rm -rf /usr/local/etc/xray /etc/xray /var/log/xray /usr/local/share/xray
    rm -rf /etc/systemd/system/multi-user.target.wants/xray.service

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
}

stop_aimilivpn_service_if_running() {
    echo -e "  -> 正在停止旧 AimiliVPN 管理服务..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop aimilivpn.service >/dev/null 2>&1 || true
        systemctl reset-failed aimilivpn.service >/dev/null 2>&1 || true
    fi
    if command -v rc-service >/dev/null 2>&1; then
        rc-service aimilivpn stop >/dev/null 2>&1 || true
    fi
    pkill -TERM -f "${INSTALL_DIR}/backend/vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -TERM -f "${INSTALL_DIR}/vpngate_manager.py" >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -f "${INSTALL_DIR}/backend/vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -KILL -f "${INSTALL_DIR}/vpngate_manager.py" >/dev/null 2>&1 || true
}

cleanup_old_residuals_and_conflicts() {
    echo -e "  -> 正在全面清理旧版本残留与可能冲突的配置与服务..."

    # 1. Stop and disable old host-level services
    local old_services=("aimilivpn.service" "aimili-xray.service" "aimili-vpn.service")
    local svc
    for svc in "${old_services[@]}"; do
        if command -v systemctl >/dev/null 2>&1; then
            if systemctl is-active --quiet "$svc" || systemctl is-enabled --quiet "$svc" 2>/dev/null; then
                echo -e "     -> 停止并禁用系统服务 $svc ..."
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

    # 2. Kill leftover python manager/core processes
    pkill -TERM -f "vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -TERM -f "aimili-xray" >/dev/null 2>&1 || true
    pkill -TERM -x xray >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -f "vpngate_manager.py" >/dev/null 2>&1 || true
    pkill -KILL -f "aimili-xray" >/dev/null 2>&1 || true
    pkill -KILL -x xray >/dev/null 2>&1 || true

    # 3. Clean up old script links/binaries
    local old_commands=("/usr/local/bin/ml-x" "/usr/bin/ml-x" "/usr/local/bin/ml" "/usr/bin/ml")
    local cmd_path
    for cmd_path in "${old_commands[@]}"; do
        if [ -f "$cmd_path" ] || [ -L "$cmd_path" ]; then
            echo -e "     -> 删除旧命令脚本 $cmd_path"
            rm -f "$cmd_path"
        fi
    done

    # 4. Clean up old residual directories
    local old_dirs=("/etc/aimili-xray" "/opt/aimili-xray")
    local dir_path
    for dir_path in "${old_dirs[@]}"; do
        if [ -d "$dir_path" ]; then
            echo -e "     -> 删除残留目录 $dir_path"
            rm -rf "$dir_path"
        fi
    done

    # 5. Stop conflicting Docker containers to free up ports
    if command -v docker >/dev/null 2>&1; then
        echo -e "     -> 检测到 Docker 运行，正在清理可能冲突的容器..."
        # If aimilivpn-docker compose directory exists, stop it
        if [ -d "/opt/aimilivpn-docker" ] && [ -f "/opt/aimilivpn-docker/docker-compose.yml" ]; then
            (
                cd "/opt/aimilivpn-docker"
                docker compose down --remove-orphans >/dev/null 2>&1 || true
            )
        fi
        docker stop aimilivpn-full aimili-vpn-panel aimili-vpngate >/dev/null 2>&1 || true
        docker rm -f aimilivpn-full aimili-vpn-panel aimili-vpngate >/dev/null 2>&1 || true
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
}

echo -e "\n${YELLOW}正在清理旧版本残留与冲突...${PLAIN}"
cleanup_old_residuals_and_conflicts

cleanup_local_runtime_cache() {
    if [ -z "${INSTALL_DIR}" ] || [ "${INSTALL_DIR}" = "/" ]; then
        echo -e "${RED}  -> 错误: 安装目录异常，拒绝清理本地缓存。${PLAIN}"
        exit 1
    fi
    if [ ! -d "${INSTALL_DIR}" ]; then
        echo -e "  -> 安装目录尚不存在，跳过本地缓存清理。"
        return
    fi

    stop_aimilivpn_service_if_running

    echo -e "  -> 正在检测并清除老旧本地缓存..."
    local cache_paths=(
        "${INSTALL_DIR}/vpngate_data"
        "${INSTALL_DIR}/configs"
        "${INSTALL_DIR}/nodes.json"
        "${INSTALL_DIR}/state.json"
        "${INSTALL_DIR}/traffic.json"
        "${INSTALL_DIR}/traffic_history.json"
        "${INSTALL_DIR}/client_traffic.json"
        "${INSTALL_DIR}/xray_cfg.json"
        "${INSTALL_DIR}/xray_config.json"
        "${INSTALL_DIR}/subscription_nodes.json"
        "${INSTALL_DIR}/subscription_links.json"
        "${INSTALL_DIR}/outbound_nodes.json"
        "${INSTALL_DIR}/routing_rules.json"
        "${INSTALL_DIR}/blacklist.json"
        "${INSTALL_DIR}/vpngate_auth.txt"
        "${INSTALL_DIR}/.pytest_cache"
    )
    local path
    for path in "${cache_paths[@]}"; do
        if [ -e "$path" ] || [ -L "$path" ]; then
            echo -e "     -> 删除 $path"
            rm -rf -- "$path"
        fi
    done

    find "${INSTALL_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "${INSTALL_DIR}" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true

    mkdir -p "${INSTALL_DIR}/vpngate_data"
    echo -e "${GREEN}  -> 老旧本地缓存已清理完成。${PLAIN}"
}

install_fresh_xray() {
    local tmp_script
    tmp_script="$(mktemp /tmp/aimilivpn-xray-install.XXXXXX.sh)"
    if ! download_xray_install_script "$tmp_script"; then
        rm -f "$tmp_script"
        echo -e "${RED}  -> 错误: 无法下载 Xray 官方安装脚本，请检查 VPS 到 GitHub/jsDelivr 的网络。${PLAIN}"
        exit 1
    fi
    chmod +x "$tmp_script"

    cleanup_existing_xray

    echo -e "  -> 正在全新安装 Xray Core..."
    if ! bash "$tmp_script" install; then
        rm -f "$tmp_script"
        echo -e "${RED}  -> 错误: Xray Core 安装失败。${PLAIN}"
        exit 1
    fi
    rm -f "$tmp_script"

    # AimiliVPN 由 vpngate_manager.py 托管 Xray 进程，避免官方 systemd 服务占用端口。
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop xray.service xray@*.service >/dev/null 2>&1 || true
        systemctl disable xray.service xray@*.service >/dev/null 2>&1 || true
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if command -v rc-service >/dev/null 2>&1; then
        rc-service xray stop >/dev/null 2>&1 || true
        rc-update del xray default >/dev/null 2>&1 || true
    fi

    if command -v xray >/dev/null 2>&1; then
        echo -e "${GREEN}  -> Xray Core 已全新安装: $(xray version 2>/dev/null | head -n 1)${PLAIN}"
    else
        echo -e "${RED}  -> 错误: 安装完成后仍未检测到 xray 命令。${PLAIN}"
        exit 1
    fi
}

echo -e "\n${YELLOW}[2/6] 正在重置并安装 Xray Core...${PLAIN}"
install_fresh_xray

# 4. Clone or pull the repository

echo -e "\n${YELLOW}[3/6] 正在从 GitHub 部署源代码到 ${INSTALL_DIR} (目标分支: ${DEPLOY_BRANCH})...${PLAIN}"
if [ -f "${INSTALL_DIR}/.local_dev" ]; then
    echo -e "${GREEN}检测到本地开发模式 (.local_dev)，跳过 git pull/reset 保持本地修改。${PLAIN}"
else
    if [ -d "${INSTALL_DIR}" ] && [ ! -d "${INSTALL_DIR}/.git" ]; then
        echo -e "  -> 目录 ${INSTALL_DIR} 已存在但不是 Git 仓库，正在删除以重新克隆..."
        rm -rf "${INSTALL_DIR}"
    fi

    if [ -d "${INSTALL_DIR}" ]; then
        echo -e "  -> 目录 ${INSTALL_DIR} 已存在，正在更新并强制覆盖本地源码..."
        cd "${INSTALL_DIR}"
        CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [ -n "$CURRENT_BRANCH" ] && [ "$CURRENT_BRANCH" != "$DEPLOY_BRANCH" ]; then
            echo -e "  -> 检测到当前本地分支为 ${YELLOW}${CURRENT_BRANCH}${PLAIN}，将切换到目标分支 ${GREEN}${DEPLOY_BRANCH}${PLAIN}。"
        fi
        git fetch origin "${DEPLOY_BRANCH}" || git fetch --all || true
        if git rev-parse --verify "origin/${DEPLOY_BRANCH}" >/dev/null 2>&1; then
            git checkout -B "${DEPLOY_BRANCH}" "origin/${DEPLOY_BRANCH}"
        else
            git checkout "${DEPLOY_BRANCH}" || git checkout -b "${DEPLOY_BRANCH}" || true
        fi
        echo -e "  -> 正在强制重置本地源码至 origin/${DEPLOY_BRANCH} ..."
        if git reset --hard "origin/${DEPLOY_BRANCH}"; then
            echo -e "${GREEN}  -> 源码更新成功！${PLAIN}"
        else
            if git pull origin "${DEPLOY_BRANCH}"; then
                echo -e "${GREEN}  -> 源码更新成功！${PLAIN}"
            else
                echo -e "${YELLOW}  -> 警告: git pull/reset 失败，将保留当前本地源码并继续安装。${PLAIN}"
            fi
        fi
    else
        echo -e "  -> 正在克隆 GitHub 仓库 ${GITHUB_URL} (分支: ${DEPLOY_BRANCH}) ..."
        if git clone -b "${DEPLOY_BRANCH}" "${GITHUB_URL}" "${INSTALL_DIR}"; then
            echo -e "${GREEN}  -> 克隆成功！${PLAIN}"
        else
            echo -e "  -> 尝试默认克隆..."
            if git clone "${GITHUB_URL}" "${INSTALL_DIR}"; then
                cd "${INSTALL_DIR}"
                git checkout "${DEPLOY_BRANCH}" || git checkout -b "${DEPLOY_BRANCH}" "origin/${DEPLOY_BRANCH}" || true
                echo -e "${GREEN}  -> 克隆成功！${PLAIN}"
            else
                echo -e "${RED}  -> 错误: 无法克隆仓库 ${GITHUB_URL}，请检查网络！${PLAIN}"
                exit 1
            fi
        fi
    fi
fi

echo -e "\n${YELLOW}[4/6] 正在清理老旧本地缓存...${PLAIN}"
cleanup_local_runtime_cache

# 5. Configure Service
echo -e "\n${YELLOW}[5/6] 正在配置系统服务...${PLAIN}"
if command -v systemctl >/dev/null 2>&1; then
    echo -e "  -> 检测到 systemd，正在创建服务配置 /etc/systemd/system/aimilivpn.service ..."
    cat > /etc/systemd/system/aimilivpn.service <<EOF
[Unit]
Description=AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 backend/vpngate_manager.py
Restart=always
RestartSec=5
EnvironmentFile=-/etc/default/aimilivpn

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable aimilivpn.service
elif command -v rc-service >/dev/null 2>&1; then
    echo -e "  -> 检测到 OpenRC，正在创建服务配置 /etc/init.d/aimilivpn ..."
    cat > /etc/init.d/aimilivpn <<EOF
#!/sbin/openrc-run

description="AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy"
command="/usr/bin/python3"
command_args="${INSTALL_DIR}/backend/vpngate_manager.py"
command_background="yes"
directory="${INSTALL_DIR}"
pidfile="/run/aimilivpn.pid"

depend() {
    need net
    after firewall
}
EOF
    chmod +x /etc/init.d/aimilivpn
    rc-update add aimilivpn default
else
    echo -e "${YELLOW}警告: 未能检测到 systemd 或 OpenRC，请手动管理服务。${PLAIN}"
fi

# 6. Configure global command shortcut "ml"
echo -e "\n${YELLOW}[6/6] 正在创建全局命令快捷接口 'ml'...${PLAIN}"
echo -e "  -> 正在写入管理脚本 /usr/bin/ml ..."
cat > /usr/bin/ml <<EOF
#!/bin/bash
cd ${INSTALL_DIR}
export AIMILI_INSTALL_DIR=${INSTALL_DIR}
export VPNGATE_DATA_DIR=${INSTALL_DIR}/vpngate_data
export AIMILI_RUNTIME=host
exec /usr/bin/python3 cli/menu.py "\$@"
EOF
chmod +x /usr/bin/ml

# 7. Configure Custom parameters (First-time installation check)
AUTH_FILE="${INSTALL_DIR}/vpngate_data/ui_auth.json"
mkdir -p "${INSTALL_DIR}/vpngate_data"

if [ ! -f "$AUTH_FILE" ]; then
    echo -e "\n${YELLOW}检测到是首次安装，是否需要自定义配置网页端参数（端口/安全后缀/登录账号密码）？${PLAIN}"
    read -p "是否自定义配置？[y/N]: " is_custom
    
    # Initialize defaults
    UI_PORT=8787
    # generate random secret suffix (12 chars alphanumeric)
    SECRET_PATH=$(python3 -c "import random, string; print(''.join(random.choices(string.ascii_letters + string.digits, k=12)))")
    # generate random password
    UI_PASSWORD=$(python3 -c "
import random, string
chars = string.ascii_letters + string.digits
while True:
    pwd = ''.join(random.choices(chars, k=12))
    if any(c.islower() for c in pwd) and any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd):
        print(pwd)
        break
")
    UI_USERNAME=$(python3 -c "
import random, string
chars = string.ascii_letters + string.digits
while True:
    uname = ''.join(random.choices(chars, k=12))
    if uname[0].isalpha() and any(c.islower() for c in uname) and any(c.isupper() for c in uname) and any(c.isdigit() for c in uname):
        print(uname)
        break
")

    if [[ "$is_custom" =~ ^[Yy]$ ]]; then
        # Step-by-step custom inputs
        # 1. Custom port
        while true; do
            read -p "请输入自定义管理端口 [1-65535, 默认 8787]: " input_port
            if [ -z "$input_port" ]; then
                UI_PORT=8787
                break
            fi
            if [[ "$input_port" =~ ^[0-9]+$ ]] && [ "$input_port" -ge 1 ] && [ "$input_port" -le 65535 ]; then
                UI_PORT=$input_port
                break
            else
                echo -e "${RED}输入错误: 端口必须是 1 到 65535 之间的数字！${PLAIN}"
            fi
        done
        
        # 2. Custom suffix
        while true; do
            read -p "请输入网页登录自定义安全后缀 [字母与数字组合, 默认随机]: " input_suffix
            if [ -z "$input_suffix" ]; then
                break
            fi
            if [[ "$input_suffix" =~ ^[A-Za-z0-9]+$ ]]; then
                SECRET_PATH=$input_suffix
                break
            else
                echo -e "${RED}输入错误: 后缀仅能由英文字母和数字组成！${PLAIN}"
            fi
        done
        
        # 3. Custom login username and password
        read -p "请输入登录账号 [默认 $UI_USERNAME]: " input_user
        if [ -n "$input_user" ]; then
            UI_USERNAME=$input_user
        fi
        
        while true; do
            read -p "请输入登录密码 [默认随机生成, 建议包含字母、数字与符号]: " input_pass
            if [ -z "$input_pass" ]; then
                break
            fi
            if [ ${#input_pass} -ge 4 ]; then
                UI_PASSWORD=$input_pass
                break
            else
                echo -e "${RED}输入错误: 密码长度不能少于 4 位！${PLAIN}"
            fi
        done
    fi

    # Write config JSON
    UI_PORT="$UI_PORT" SECRET_PATH="$SECRET_PATH" UI_USERNAME="$UI_USERNAME" UI_PASSWORD="$UI_PASSWORD" AUTH_FILE="$AUTH_FILE" python3 -c "
import os, json
cfg = {
    'host': '::',
    'port': int(os.environ.get('UI_PORT', '8787')),
    'secret_path': os.environ.get('SECRET_PATH', ''),
    'username': os.environ.get('UI_USERNAME', 'admin'),
    'password': os.environ.get('UI_PASSWORD', ''),
    'proxy_port': 7928,
    'routing_mode': 'auto',
    'force_country': ''
}
with open(os.environ.get('AUTH_FILE'), 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
"
fi

# 8. Start service
# 8.5 Optimize network parameters (rp_filter for policy routing)
echo -e "\n正在优化网络参数 (配置反向路径过滤 rp_filter=2 以支持策略路由)..."
if [ -d "/etc/sysctl.d" ]; then
    cat > /etc/sysctl.d/99-aimilivpn.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOF
    sysctl -p /etc/sysctl.d/99-aimilivpn.conf >/dev/null 2>&1 || true
else
    # Fallback to appending to /etc/sysctl.conf
    for param in "net.ipv4.ip_forward = 1" "net.ipv6.conf.all.forwarding = 1" "net.ipv4.conf.all.rp_filter = 2" "net.ipv4.conf.default.rp_filter = 2"; do
        key=$(echo "$param" | cut -d' ' -f1)
        if ! grep -q "$key" /etc/sysctl.conf; then
            echo "" >> /etc/sysctl.conf
            echo "$param" >> /etc/sysctl.conf
        else
            val=$(echo "$param" | cut -d' ' -f3)
            sed -i "s/$key\s*=\s*[0-9]/$key = $val/g" /etc/sysctl.conf
        fi
    done
    sysctl -p >/dev/null 2>&1 || true
fi
# Apply to currently active interfaces dynamically
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.default.rp_filter=2 >/dev/null 2>&1 || true
if [ -d "/proc/sys/net/ipv4/conf" ]; then
    for dev_dir in /proc/sys/net/ipv4/conf/*; do
        dev_name=$(basename "$dev_dir")
        sysctl -w net.ipv4.conf.${dev_name}.rp_filter=2 >/dev/null 2>&1 || true
    done
fi

echo -e "\n正在启动 AimiliVPN 服务并初始化网络..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl restart aimilivpn.service || true
elif command -v rc-service >/dev/null 2>&1; then
    rc-service aimilivpn restart || true
fi

# Wait and poll for service initialization
echo -e "\n正在等待 AimiliVPN 管理服务初始化 (OpenVPN 默认不自动启动)..."
SERVICE_READY=0
LAST_MSG=""
for i in {1..60}; do
    if [ -f "${INSTALL_DIR}/vpngate_data/state.json" ]; then
        CUR_MSG=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/vpngate_data/state.json')).get('last_check_message', ''))" 2>/dev/null || echo "")
        OPENVPN_ENABLED=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/vpngate_data/state.json')).get('openvpn_enabled', False))" 2>/dev/null || echo "False")

        SERVICE_READY=1
        if [ -n "$CUR_MSG" ] && [ "$CUR_MSG" != "$LAST_MSG" ]; then
            echo -e "  -> 状态: ${YELLOW}${CUR_MSG}${PLAIN}"
            LAST_MSG="$CUR_MSG"
        fi
        if [ "$OPENVPN_ENABLED" = "False" ] || [ "$OPENVPN_ENABLED" = "false" ]; then
            echo -e "  -> ${GREEN}[已就绪]${PLAIN} 管理服务已启动，Xray 将由面板托管；OpenVPN 需在网页手动启动。"
            break
        fi
    else
        echo -n "."
    fi
    sleep 1
done
if [ "$SERVICE_READY" -ne 1 ]; then
    echo -e "  -> ${YELLOW}[等待超时]${PLAIN} 暂未读取到服务状态文件，请稍后使用 ${YELLOW}ml status${PLAIN} 或 ${YELLOW}ml logs${PLAIN} 查看。"
fi

SECRET_PATH="EJsW2EeBo9lY"
USERNAME="未配置"
PASSWORD="未配置"
UI_PORT=8787
AUTH_FILE="${INSTALL_DIR}/vpngate_data/ui_auth.json"
if [ -f "$AUTH_FILE" ]; then
    SECRET_PATH=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('secret_path', 'EJsW2EeBo9lY'))" 2>/dev/null || echo "EJsW2EeBo9lY")
    USERNAME=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('username', '未配置'))" 2>/dev/null || echo "未配置")
    PASSWORD=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('password', '未配置'))" 2>/dev/null || echo "未配置")
    UI_PORT=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('port', 8787))" 2>/dev/null || echo "8787")
fi

# Get VPS public IP
echo -e "正在获取 VPS 公网 IP..."
PUBLIC_IP=$(curl -s --max-time 3 https://api.ipify.org || curl -s --max-time 3 https://ifconfig.me || curl -s --max-time 3 icanhazip.com || echo "您的服务器公网IP")
echo -n "$PUBLIC_IP" > "${INSTALL_DIR}/vpngate_data/public_ip.txt"

# Get VPS public IPv6
echo -e "正在获取 VPS 公网 IPv6..."
PUBLIC_IPV6=$(curl -6 -s --max-time 3 https://api.ipify.org || curl -6 -s --max-time 3 https://ifconfig.me || curl -6 -s --max-time 3 icanhazip.com || echo "")

echo -e "\n${GREEN}==========================================================${PLAIN}"
echo -e "${GREEN}             AimiliVPN 源码一键部署已完成！${PLAIN}"
echo -e "${GREEN}==========================================================${PLAIN}"
echo -e "  * 网页控制面板:  ${BLUE}http://${PUBLIC_IP}:${UI_PORT}/${SECRET_PATH}/${PLAIN}"
if [ -n "$PUBLIC_IPV6" ]; then
    echo -e "  * 网页控制面板(IPv6):  ${BLUE}http://[${PUBLIC_IPV6}]:${UI_PORT}/${SECRET_PATH}/${PLAIN}"
fi
echo -e "  * 网页管理账号:  ${YELLOW}${USERNAME}${PLAIN}"
echo -e "  * 网页管理密码:  ${YELLOW}${PASSWORD}${PLAIN}"
echo -e "  * HTTP/SOCKS5 代理端口:  ${BLUE}http://127.0.0.1:7928/${PLAIN}  或  ${BLUE}http://[::1]:7928/${PLAIN}"
echo -e " --------------------------------------------------------"
echo -e "  * 打开管理菜单:   ${YELLOW}ml${PLAIN}"
echo -e "  * 快速状态指令:   ${YELLOW}ml status${PLAIN}"
echo -e "  * 查看实时日志:   ${YELLOW}ml logs${PLAIN}"
echo -e "  * 在线更新面板:   ${YELLOW}ml update${PLAIN}"
echo -e "  * 完全卸载清理:   ${YELLOW}ml uninstall${PLAIN}"
echo -e "=========================================================="
echo
