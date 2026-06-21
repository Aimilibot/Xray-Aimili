#!/usr/bin/env python3
import sys
import os
import socket
import subprocess
import time
import json
import shutil
import urllib.request
import urllib.parse
from pathlib import Path

# Force UTF-8 encoding for stdout/stderr to prevent crashes on non-UTF-8 terminals
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Terminal Colors
C_HEADER = '\033[95m'
C_BLUE = '\033[94m'
C_CYAN = '\033[96m'
C_GREEN = '\033[92m'
C_WARNING = '\033[93m'
C_FAIL = '\033[91m'
C_END = '\033[0m'
C_BOLD = '\033[1m'

INSTALL_DIR = os.environ.get("AIMILI_INSTALL_DIR", "/opt/aimilivpn")
DATA_DIR = Path(os.environ.get("VPNGATE_DATA_DIR", str(Path(INSTALL_DIR) / "vpngate_data")))
STATE_FILE = DATA_DIR / "state.json"
NODES_FILE = DATA_DIR / "nodes.json"
AUTH_FILE = DATA_DIR / "ui_auth.json"
LOG_FILE = DATA_DIR / "vpngate.log"
DOCKER_INSTALL_DIR = os.environ.get("AIMILI_DOCKER_INSTALL_DIR", "/opt/aimilivpn-docker")
LEGACY_INSTALL_DIRS = ["/opt/aimili-xray", "/etc/aimili-xray"]
SERVICE_NAMES = ["aimilivpn.service", "aimili-xray.service", "aimili-vpn.service"]
COMMAND_LINKS = ["/usr/bin/ml", "/usr/local/bin/ml", "/usr/bin/ml-x", "/usr/local/bin/ml-x"]
XRAY_PATHS = [
    "/usr/local/bin/xray",
    "/usr/bin/xray",
    "/bin/xray",
    "/etc/xray",
    "/usr/local/etc/xray",
    "/var/log/xray",
    "/usr/local/share/xray",
    "/etc/systemd/system/xray.service",
    "/etc/systemd/system/xray@.service",
    "/lib/systemd/system/xray.service",
    "/lib/systemd/system/xray@.service",
    "/usr/lib/systemd/system/xray.service",
    "/usr/lib/systemd/system/xray@.service",
    "/etc/init.d/xray",
]

def is_docker_install():
    runtime = os.environ.get("AIMILI_RUNTIME")
    if runtime:
        return runtime == "docker"
    return Path(INSTALL_DIR).resolve(strict=False) == Path(DOCKER_INSTALL_DIR).resolve(strict=False)

def run_quiet(cmd, cwd=None, check=False):
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return None

def safe_remove_path(path):
    target = Path(path)
    resolved = target.resolve(strict=False)
    if str(resolved) in ("", "/", "/opt", "/etc", "/usr", "/var", "/lib", "/bin"):
        print(f"{C_WARNING}跳过异常路径: {path}{C_END}")
        return
    try:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except Exception as e:
        print(f"{C_WARNING}清理失败 {path}: {e}{C_END}")

def ensure_global_launcher():
    launcher = Path("/usr/bin/ml")
    runtime = "docker" if is_docker_install() else "host"
    data_dir = str(DATA_DIR)
    content = (
        "#!/bin/bash\n"
        f"cd {INSTALL_DIR}\n"
        f"export AIMILI_INSTALL_DIR={INSTALL_DIR}\n"
        f"export VPNGATE_DATA_DIR={data_dir}\n"
        f"export AIMILI_RUNTIME={runtime}\n"
        'exec /usr/bin/python3 cli/menu.py "$@"\n'
    )
    try:
        launcher.write_text(content, encoding="utf-8")
        launcher.chmod(0o755)
        return True
    except Exception as e:
        print(f"{C_WARNING}写入 /usr/bin/ml 失败: {e}{C_END}")
        return False

def compose_available():
    return is_docker_install() and shutil.which("docker") and (Path(INSTALL_DIR) / "docker-compose.yml").exists()

def docker_compose(args, check=False, quiet=False):
    if not compose_available():
        return None
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    return subprocess.run(["docker", "compose", *args], cwd=INSTALL_DIR, check=check, stdout=stdout, stderr=stderr, text=True)

def docker_stack_active():
    if not shutil.which("docker"):
        return False
    for name in ("aimili-vpn-panel", "aimilivpn-full", "aimili-vpngate"):
        res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip().lower() == "true":
            return True
    return False

def clear_screen():
    print("\033[H\033[J", end="", flush=True)

def getch():
    import sys
    fd = sys.stdin.fileno()
    try:
        import tty
        import termios
        old_settings = termios.tcgetattr(fd)
    except Exception:
        # Fallback for non-tty environments or windows debugging
        return sys.stdin.read(1)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def load_ui_cfg():
    cfg = {
        "host": "::",
        "port": 8787,
        "secret_path": "EJsW2EeBo9lY",
        "password": "",
        "username": "admin",
        "proxy_port": 7928,
        "routing_mode": "auto",
        "force_country": "",
    }
    if AUTH_FILE.exists():
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    cfg[k] = v
        except Exception:
            pass
    return cfg

def save_ui_cfg(cfg):
    try:
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def load_state():
    state = {
        "active_openvpn_node_id": "",
        "last_check_message": "",
        "is_connecting": False,
        "active_node_latency": "无活动连接",
        "proxy_ip": "-",
        "proxy_latency_ms": 0,
        "proxy_ok": False,
        "routing_mode": "auto",
        "force_country": ""
    }
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    state[k] = v
        except Exception:
            pass
    return state

def save_state(state):
    try:
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def check_port_listening(port):
    for host, family in [("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)]:
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            pass
    return False

def get_service_pid():
    try:
        for pid_dir in os.listdir('/proc'):
            if pid_dir.isdigit():
                try:
                    with open(os.path.join('/proc', pid_dir, 'cmdline'), 'r') as f:
                        cmd = f.read()
                        if 'vpngate_manager.py' in cmd:
                            return pid_dir
                except Exception:
                    continue
    except Exception:
        pass
    return None

def check_service_active():
    if is_docker_install():
        return docker_stack_active()
    return get_service_pid() is not None

def check_openvpn_process():
    try:
        for pid_dir in os.listdir('/proc'):
            if pid_dir.isdigit():
                try:
                    with open(os.path.join('/proc', pid_dir, 'cmdline'), 'r') as f:
                        cmd = f.read().split('\x00')[0]
                        if 'openvpn' in cmd:
                            return True
                except Exception:
                    continue
    except Exception:
        pass
    return False

def get_bbr_status():
    try:
        res = subprocess.run(["sysctl", "net.ipv4.tcp_congestion_control"], capture_output=True, text=True)
        if "bbr" in res.stdout.lower():
            return "active"
    except Exception:
        pass
    return "inactive"

def make_api_request(endpoint, method="POST", payload=None):
    cfg = load_ui_cfg()
    port = cfg.get("port", 8787)
    secret_path = cfg.get("secret_path", "EJsW2EeBo9lY")
    username = cfg.get("username", "admin")
    password = cfg.get("password", "")
    
    cfg_host = cfg.get("host", "::")
    if cfg_host in ("::", "0.0.0.0", ""):
        local_host = "127.0.0.1"
    elif ":" in cfg_host:
        local_host = f"[{cfg_host}]"
    else:
        local_host = cfg_host

    # 1. Login to get cookie
    login_url = f"http://{local_host}:{port}/{secret_path}/api/login"
    login_data = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=login_data,
        headers={"Content-Type": "application/json", "User-Agent": "aimilivpn-cli"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            headers = resp.info()
            cookie = headers.get("Set-Cookie")
            if not cookie:
                raise RuntimeError("No cookie returned")
            session = cookie.split(";")[0]
    except Exception as e:
        raise RuntimeError(f"未成功连接本地 API (请确保服务已启动) | 详情: {e}")

    # 2. Make request
    url = f"http://{local_host}:{port}/{secret_path}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Cookie": session, "User-Agent": "aimilivpn-cli"}
    if payload:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"请求本地 API {endpoint} 失败: {e}")

def get_active_node_info():
    state = load_state()
    active_id = state.get("active_openvpn_node_id")
    if not active_id:
        return None, None
    if NODES_FILE.exists():
        try:
            with open(NODES_FILE, "r", encoding="utf-8") as f:
                nodes = json.load(f)
                for n in nodes:
                    if n.get("id") == active_id:
                        ip = n.get("ip") or n.get("remote_host")
                        loc = n.get("location") or n.get("country") or "未知"
                        return ip, loc
        except Exception:
            pass
    return None, None

def get_public_ip():
    path = DATA_DIR / "public_ip.txt"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                ip = f.read().strip()
                if ip:
                    return ip
        except Exception:
            pass
    # Try dual-stack first, then IPv6, then IPv4
    for api_url in ["https://api64.ipify.org", "https://api6.ipify.org", "https://api.ipify.org"]:
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=2) as r:
                ip = r.read().decode().strip()
                if ip:
                    try:
                        DATA_DIR.mkdir(exist_ok=True, parents=True)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(ip)
                    except Exception:
                        pass
                    return ip
        except Exception:
            pass
    return "您的服务器公网IP"

def get_display_width(s):
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*[mGKH]')
    s_clean = ansi_escape.sub('', s)
    width = 0
    for char in s_clean:
        if ord(char) > 127:
            width += 2
        else:
            width += 1
    return width

def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

def pad_to_width(s, target_width, align='left'):
    s_str = str(s)
    w = get_display_width(s_str)
    padding = max(0, target_width - w)
    if align == 'left':
        return s_str + ' ' * padding
    elif align == 'right':
        return ' ' * padding + s_str
    else: # center
        left_pad = padding // 2
        right_pad = padding - left_pad
        return ' ' * left_pad + s_str + ' ' * right_pad

def format_line(label, value, target_width=24):
    prefix = "  ● "
    w = get_display_width(label)
    padding = " " * max(0, target_width - w)
    return f"{prefix}{label}{padding}:  {value}"

def print_header():
    runtime = "Docker Stack" if is_docker_install() else "未绑定 Docker"
    print(f"{C_BOLD}{C_CYAN}============================================================{C_END}")
    print(f"{C_BOLD}{C_CYAN}                  AimiliVPN 独立管理终端                    {C_END}")
    print(f"{C_CYAN}                    Runtime: {runtime:<22}{C_END}")
    print(f"{C_BOLD}{C_CYAN}============================================================{C_END}")

def print_status_summary():
    cfg = load_ui_cfg()
    state = load_state()
    
    proxy_port = cfg.get("proxy_port", 7928)
    service_ok = check_service_active()
    is_connecting = state.get("is_connecting", False)
    
    if is_connecting:
        status_text = "切换中..."
        status_color = C_WARNING
    elif service_ok:
        status_text = "已激活"
        status_color = C_GREEN
    else:
        status_text = "未启动"
        status_color = C_FAIL
        
    active_ip, active_loc = get_active_node_info()
    if is_connecting:
        active_node_text = "正在连接中"
    elif active_ip:
        active_node_text = f"{active_loc} ({active_ip})"
    else:
        active_node_text = "无"
        
    # Get web UI login address
    ui_port = cfg.get("port", 8787)
    secret_path = cfg.get("secret_path", "EJsW2EeBo9lY")
    h = cfg.get("host", "::")
    if h in ("127.0.0.1", "localhost"):
        login_ip = "127.0.0.1"
    elif h == "::1":
        login_ip = "[::1]"
    elif h in ("::", "0.0.0.0", ""):
        login_ip = get_public_ip()
    else:
        login_ip = f"[{h}]" if ":" in h else h
    web_url = f"http://{login_ip}:{ui_port}/{secret_path}/"
        
    runtime = "Docker" if is_docker_install() else "宿主机"
    print(f"{C_BOLD}{C_CYAN}============================================================{C_END}")
    print(f" AimiliVPN 状态 : {status_color}{status_text}{C_END}    运行模式: {C_BOLD}{runtime}{C_END}")
    print(f" 活动节点       : {C_BOLD}{active_node_text}{C_END}")
    print(f" 管理网页       : {C_CYAN}{web_url}{C_END}")
    print()
    print(f" 💻 [终端代理命令] :")
    print(f" export http_proxy=\"socks5://127.0.0.1:{proxy_port}\"")
    print(f"{C_BOLD}{C_CYAN}============================================================{C_END}")

def show_system_status():
    clear_screen()
    print_header()
    
    cfg = load_ui_cfg()
    state = load_state()
    proxy_port = cfg.get("proxy_port", 7928)
    ui_port = cfg.get("port", 8787)
    secret_path = cfg.get("secret_path", "EJsW2EeBo9lY")
    
    service_ok = check_service_active()
    openvpn_ok = check_openvpn_process()
    gateway_ok = check_port_listening(proxy_port)
    pid = get_service_pid()
    
    bbr = get_bbr_status()
    bbr_colored = f"{C_GREEN}已启用{C_END}" if bbr == "active" else f"{C_FAIL}未启用{C_END}"
    gateway_status = f"{C_GREEN}已激活{C_END}" if gateway_ok else f"{C_FAIL}未启动{C_END}"
    if is_docker_install():
        panel_status = f"{C_GREEN}已激活 (Docker){C_END}" if service_ok else f"{C_FAIL}未启动{C_END}"
    else:
        panel_status = f"{C_GREEN}已激活 (PID: {pid}){C_END}" if (service_ok and pid) else f"{C_FAIL}未启动{C_END}"
    
    if state.get("is_connecting"):
        openvpn_status = f"{C_WARNING}切换连接中...{C_END}"
    else:
        openvpn_status = f"{C_GREEN}已连接{C_END}" if openvpn_ok else f"{C_FAIL}未连接{C_END}"
        
    # Get Uptime, CPU, Memory, Disk info
    uptime_str = "未知"
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
            days = int(uptime_seconds // (3600 * 24))
            hours = int((uptime_seconds % (3600 * 24)) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
    except Exception:
        pass
        
    cpu_percent = "未知"
    try:
        # Quick CPU utilization check via /proc/stat
        with open("/proc/stat", "r") as f:
            fields1 = [float(column) for column in f.readline().strip().split()[1:]]
        time.sleep(0.2)
        with open("/proc/stat", "r") as f:
            fields2 = [float(column) for column in f.readline().strip().split()[1:]]
        delta = [fields2[i] - fields1[i] for i in range(len(fields1))]
        total = sum(delta)
        if total > 0:
            cpu_percent = f"{int((total - delta[3]) / total * 100)}%"
    except Exception:
        pass
        
    mem_str = "未知"
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].split()[0])
        total_mem = meminfo.get("MemTotal", 0) / 1024 / 1024 # GB
        free_mem = (meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)) / 1024 / 1024 # GB
        used_mem = total_mem - free_mem
        mem_percent = int((used_mem / total_mem) * 100) if total_mem > 0 else 0
        mem_str = f"{mem_percent}% ({used_mem:.1f} GB / {total_mem:.1f} GB)"
    except Exception:
        pass
        
    disk_str = "未知"
    try:
        total, used, free = shutil.disk_usage("/")
        total_gb = total / 1024 / 1024 / 1024
        used_gb = used / 1024 / 1024 / 1024
        disk_percent = int((used / total) * 100)
        disk_str = f"{disk_percent}% ({used_gb:.1f} GB / {total_gb:.1f} GB)"
    except Exception:
        pass

    print(f"\n{C_BOLD}--- 核心服务状态 ---{C_END}")
    print(format_line("BBR 拥塞控制", bbr_colored))
    print(format_line(f"代理网关 (Port {proxy_port})", gateway_status))
    print(format_line(f"管理后台 (Port {ui_port})", panel_status))
    print(format_line("连接核心 (OpenVPN)", openvpn_status))
    
    print(f"\n{C_BOLD}--- 系统硬件指标 ---{C_END}")
    print(format_line("CPU 使用率", cpu_percent))
    print(format_line("内存使用率", mem_str))
    print(format_line("硬盘使用率", disk_str))
    print(format_line("系统运行时间", uptime_str))
    
    print(f"\n{C_BOLD}--- 活动节点状态 ---{C_END}")
    active_ip, active_loc = get_active_node_info()
    if state.get("is_connecting"):
        connecting_msg = state.get('last_check_message') or '正在建立加密隧道并验证路由规则...'
        print(format_line("节点状态", f"{C_WARNING}{connecting_msg}{C_END}"))
    elif active_ip:
        proxy_ip = state.get("proxy_ip", "-")
        proxy_latency = state.get("proxy_latency_ms", 0)
        proxy_ok = state.get("proxy_ok", False)
        
        print(format_line("节点 IP (入口)", active_ip))
        print(format_line("节点地区", active_loc))
        print(format_line("节点延迟 (直连)", state.get("active_node_latency", "测试中...")))
        if proxy_ok and proxy_ip and proxy_ip != "-":
            print(format_line("出口 IP (出站)", proxy_ip))
            print(format_line("代理延迟", f"{proxy_latency} ms" if proxy_latency else "检测中..."))
        else:
            proxy_err = state.get("proxy_error") or "检测中/未就绪"
            print(format_line("出口 IP (出站)", f"{C_FAIL}[不可用 - {proxy_err}]{C_END}"))
    else:
        print(format_line("节点状态", "无活动连接"))
        
    print(f"\n{C_BOLD}--- 出站路由模式 ---{C_END}")
    rmode = state.get("routing_mode", "auto")
    rmode_zh = "自动模式 (延迟低自动切换)"
    if rmode == "fixed_region":
        rmode_zh = f"固定地区 ({state.get('force_country', '未知')})"
    elif rmode == "fixed_ip":
        rmode_zh = "固定 IP (不断开锁定)"
    print(format_line("出站路由模式", rmode_zh))

    input(f"\n按 {C_BOLD}回车键{C_END} 返回主菜单...")

def run_service_cmd(cmd):
    if is_docker_install() and compose_available():
        if cmd == "start":
            docker_compose(["up", "-d"], check=False)
        elif cmd == "stop":
            docker_compose(["down", "--remove-orphans"], check=False)
        elif cmd == "restart":
            docker_compose(["restart"], check=False)
        return

    print("当前安装未绑定 Docker Stack。为保证网关安全，请使用 install-docker.sh 重新部署；宿主机只保留 ml 管理菜单。", flush=True)
    return

    if shutil.which("systemctl"):
        subprocess.run(["systemctl", cmd, "aimilivpn.service"])
    elif shutil.which("rc-service"):
        subprocess.run(["rc-service", "aimilivpn", cmd])
    else:
        pid = get_service_pid()
        if pid:
            if cmd in ("stop", "restart"):
                print(f"未检测到服务管理器，正在向主进程 (PID {pid}) 发送终止信号...", flush=True)
                import signal
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    print("已向主进程发送终止信号。", flush=True)
                except Exception as e:
                    print(f"发送信号失败: {e}", flush=True)
            elif cmd == "start":
                print("容器环境中后台服务应当已由 Entrypoint 自动运行。", flush=True)
        else:
            print("未检测到运行中的 AimiliVPN 管理后台进程，且未检测到 systemd/OpenRC 服务管理器。", flush=True)

def restart_service():
    print("正在重启 AimiliVPN 服务...", flush=True)
    run_service_cmd("restart")
    print("已发送重启指令。")
    time.sleep(1.5)

def show_logs():
    print(f"\n正在实时查看运行日志 (按 {C_BOLD}Ctrl+C{C_END} 退出)...\n", flush=True)
    if not LOG_FILE.exists():
        if is_docker_install() and compose_available():
            try:
                docker_compose(["logs", "-f", "--tail", "80"], check=False)
            except KeyboardInterrupt:
                pass
            return
        print(f"日志文件不存在: {LOG_FILE}")
        time.sleep(2)
        return

    if shutil.which("tail"):
        try:
            subprocess.run(["tail", "-f", "-n", "50", str(LOG_FILE)])
        except KeyboardInterrupt:
            pass
    else:
        # Python-based fallback tail -f
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                for line in lines[-50:]:
                    print(line, end="", flush=True)
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    print(line, end="", flush=True)
        except KeyboardInterrupt:
            pass

def fetch_and_test_nodes():
    clear_screen()
    print_header()
    print("\n正在请求本地后台服务重新拉取并测试节点，请稍候...", flush=True)
    try:
        res = make_api_request("/api/refresh_nodes", "POST")
        if res.get("ok"):
            print(f"\n{C_GREEN}成功刷新节点！{C_END} 后台已启动并发延迟测试进程。")
        else:
            print(f"\n{C_FAIL}同步失败：{C_END} {res.get('error', '未知错误')}")
    except Exception as e:
        print(f"\n{C_FAIL}请求出错：{C_END} {e}")
    input("\n按回车键返回主菜单...")

def list_and_switch_nodes():
    page = 0
    page_size = 8
    while True:
        if not NODES_FILE.exists():
            print(f"\n{C_FAIL}未找到节点缓存文件。请先选择选项 4 手动同步节点！{C_END}")
            time.sleep(2.5)
            break
        try:
            with open(NODES_FILE, "r", encoding="utf-8") as f:
                nodes = json.load(f)
        except Exception as e:
            print(f"\n{C_FAIL}读取节点列表失败：{C_END} {e}")
            time.sleep(2)
            break
            
        if not nodes:
            print(f"\n{C_WARNING}暂无节点数据。请先执行同步操作。{C_END}")
            time.sleep(2)
            break
            
        # Sort nodes by active first, then score descending, ping ascending
        nodes = sorted(nodes, key=lambda x: (not x.get("active", False), -safe_int(x.get("score")), safe_int(x.get("ping"), 9999)))
        total_pages = (len(nodes) + page_size - 1) // page_size
        if page < 0:
            page = 0
        if page >= total_pages:
            page = total_pages - 1
            
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(nodes))
        
        clear_screen()
        print_status_summary()
        
        print(f"{C_BOLD}【可用节点列表 (页码 {page+1}/{total_pages})】{C_END}")
        print(f"{C_CYAN}{pad_to_width('序号', 6)}{pad_to_width('国家/位置', 16)}{pad_to_width('IP地址', 18)}{pad_to_width('直连延迟', 12)}{pad_to_width('状态', 10)}{C_END}")
        
        for idx in range(start_idx, end_idx):
            n = nodes[idx]
            nid = n.get("id")
            country = n.get("country") or "未知"
            if len(country) > 6:
                country = country[:5] + ".."
            ip = n.get("ip") or n.get("remote_host") or "未知"
            ping = f"{n.get('ping', '-')} ms" if n.get('ping') else "-"
            active = n.get("active", False)
            
            # latency check color
            if active:
                status_str = f"{C_GREEN}[已连接]{C_END}"
            else:
                p_val = safe_int(n.get('ping'), 9999)
                if p_val < 80:
                    status_str = f"{C_GREEN}极速{C_END}"
                elif p_val < 200:
                    status_str = f"{C_BLUE}良好{C_END}"
                elif p_val >= 9999:
                    status_str = f"{C_WARNING}未知{C_END}"
                else:
                    status_str = f"{C_WARNING}一般{C_END}"
                    
            col1 = f" {C_BOLD}{pad_to_width(idx+1, 5)}{C_END}"
            col2 = pad_to_width(country, 16)
            col3 = pad_to_width(ip, 18)
            col4 = pad_to_width(ping, 12)
            col5 = pad_to_width(status_str, 10)
            print(f"{col1}{col2}{col3}{col4}{col5}")
            
        print(f"\n{C_BOLD}{C_CYAN}============================================================{C_END}")
        print("  操作指令：")
        print("  - 输入 序号 (1-300) 快速发起连接该节点")
        print("  - 输入 t序号 (例如: t1) 触发单节点并发测试")
        print("  - 输入 n 下一页 | p 上一页")
        print("  - 输入 0 或回车 返回主菜单")
        print(f"{C_BOLD}{C_CYAN}============================================================{C_END}")
        
        cmd = input("请选择您的指令：").strip()
        if not cmd or cmd == '0':
            break
        elif cmd.lower() == 'n':
            page += 1
        elif cmd.lower() == 'p':
            page -= 1
        elif cmd.lower().startswith('t'):
            try:
                if len(cmd) < 2:
                    raise ValueError("序号不能为空")
                target_idx = int(cmd[1:]) - 1
                if 0 <= target_idx < len(nodes):
                    node_id = nodes[target_idx]["id"]
                    print(f"正在对节点 {nodes[target_idx].get('country')} ({nodes[target_idx].get('ip')}) 发起延迟与连通性测试...")
                    res = make_api_request("/api/test_node", "POST", {"id": node_id})
                    if res.get("ok"):
                        print(f"测试完成！状态: {res.get('probe_status')}, 延迟: {res.get('latency_ms')}ms, 运营商: {res.get('as_name')}")
                    else:
                        print(f"测试失败: {res.get('error')}")
                    input("\n按回车键继续...")
                else:
                    print("序号超出范围！")
                    time.sleep(1.5)
            except Exception as e:
                print(f"输入格式错误：{e}")
                time.sleep(1.5)
        else:
            try:
                target_idx = int(cmd) - 1
                if 0 <= target_idx < len(nodes):
                    node_id = nodes[target_idx]["id"]
                    print(f"\n正在尝试连接至节点 {nodes[target_idx].get('country')} ({nodes[target_idx].get('ip')})...")
                    res = make_api_request("/api/connect", "POST", {"id": node_id})
                    if res.get("ok"):
                        print(f"{C_GREEN}连接指令已下发！{C_END} 请稍后返回状态面板查看握手进度。")
                    else:
                        print(f"{C_FAIL}连接失败：{C_END} {res.get('error')}")
                    time.sleep(2)
                    break
                else:
                    print("序号超出范围！")
                    time.sleep(1.5)
            except Exception as e:
                print(f"无效输入指令！")
                time.sleep(1.5)

def configure_routing_mode():
    while True:
        state = load_state()
        curr_mode = state.get("routing_mode", "auto")
        curr_country = state.get("force_country", "")
        
        mode_str = "自动模式 (选择低延迟可用节点)"
        if curr_mode == "fixed_region":
            mode_str = f"固定地区 ({curr_country})"
        elif curr_mode == "fixed_ip":
            mode_str = "固定 IP"
            
        clear_screen()
        print_status_summary()
        
        print(f"{C_BOLD}【切换出站路由模式】{C_END}")
        print(f"当前模式: {C_BOLD}{mode_str}{C_END}\n")
        print(f"  {C_GREEN}[1]{C_END} 自动模式")
        print(f"  {C_GREEN}[2]{C_END} 固定地区 (仅连接指定国家的节点)")
        print(f"  {C_GREEN}[3]{C_END} 固定 IP (断线只连当前相同节点)")
        print(f"  {C_GREEN}[0]{C_END} 返回主菜单")
        print(f"{C_BOLD}{C_CYAN}============================================================{C_END}\n")
        key = input("请选择模式 [0-3]：").strip()
        
        if key == '1':
            try:
                res = make_api_request("/api/update_routing", "POST", {"routing_mode": "auto", "force_country": ""})
                if res.get("ok"):
                    print(f"\n{C_GREEN}出站路由模式已更新为 自动模式{C_END}")
                else:
                    print(f"\n{C_FAIL}修改失败: {C_END}{res.get('error')}")
                time.sleep(1.5)
            except Exception as e:
                print(f"\n请求出错: {e}")
                time.sleep(2)
            break
        elif key == '2':
            country = input("\n请输入想要锁定的国家名称 (例如: 日本, 韩国, 美国): ").strip()
            if country:
                try:
                    res = make_api_request("/api/update_routing", "POST", {"routing_mode": "fixed_region", "force_country": country})
                    if res.get("ok"):
                        print(f"\n{C_GREEN}已成功锁定连接国家为：{country}{C_END}")
                    else:
                        print(f"\n{C_FAIL}修改失败: {C_END}{res.get('error')}")
                    time.sleep(1.5)
                except Exception as e:
                    print(f"\n请求出错: {e}")
                    time.sleep(2)
            break
        elif key == '3':
            try:
                res = make_api_request("/api/update_routing", "POST", {"routing_mode": "fixed_ip", "force_country": ""})
                if res.get("ok"):
                    print(f"\n{C_GREEN}出站路由模式已更新为 固定IP模式{C_END}")
                else:
                    print(f"\n{C_FAIL}修改失败: {C_END}{res.get('error')}")
                time.sleep(1.5)
            except Exception as e:
                print(f"\n请求出错: {e}")
                time.sleep(2)
            break
        elif key == '0' or key == 'q' or key == '\x03':
            break

def ask_restart():
    ans = input("配置已修改保存。是否立即重启服务以应用生效？(Y/n): ").strip().lower()
    if ans in ('', 'y', 'yes'):
        print("正在重启 AimiliVPN 服务...", flush=True)
        restart_service()
        print("服务重启指令已下发。")
        time.sleep(1.5)

def configure_port():
    while True:
        cfg = load_ui_cfg()
        clear_screen()
        print_header()
        print(f"{C_BOLD}【管理与代理端口配置】{C_END}\n")
        print(f"  {C_GREEN}[1]{C_END} 网页管理端口 (当前: {cfg.get('port', 8787)})")
        print(f"  {C_GREEN}[2]{C_END} 代理出站端口 (当前: {cfg.get('proxy_port', 7928)})")
        print(f"  {C_GREEN}[0]{C_END} 返回主菜单")
        print(f"{C_BOLD}{C_CYAN}============================================================{C_END}\n")
        key = input("请输入您的选择 [0-2]：").strip()
        
        if key == '1':
            val = input(f"\n请输入新的网页管理端口 (1-65535, 默认{cfg.get('port')}, 回车取消): ").strip()
            if val:
                try:
                    port = int(val)
                    if 1 <= port <= 65535:
                        if port == int(cfg.get("proxy_port", 7928)):
                            print("错误：网页管理端口不能与代理出站端口相同。")
                            time.sleep(1.5)
                            continue
                        cfg["port"] = port
                        if save_ui_cfg(cfg):
                            print(f"{C_GREEN}管理端口已更新为: {port}{C_END}")
                            ask_restart()
                        else:
                            print("写入配置文件失败")
                            time.sleep(1.5)
                    else:
                        print("错误：端口需在 1 到 65535 之间。")
                        time.sleep(1.5)
                except ValueError:
                    print("错误：请输入有效数字！")
                    time.sleep(1.5)
            break
        elif key == '2':
            val = input(f"\n请输入新的代理出站端口 (1024-65535, 默认{cfg.get('proxy_port')}, 回车取消): ").strip()
            if val:
                try:
                    port = int(val)
                    if 1024 <= port <= 65535:
                        if port == int(cfg.get("port", 8787)):
                            print("错误：代理出站端口不能与网页管理端口相同。")
                            time.sleep(1.5)
                            continue
                        cfg["proxy_port"] = port
                        if save_ui_cfg(cfg):
                            print(f"{C_GREEN}代理出站端口已更新为: {port}{C_END}")
                            ask_restart()
                        else:
                            print("写入配置文件失败")
                            time.sleep(1.5)
                    else:
                        print("错误：端口需在 1024 到 65535 之间。")
                        time.sleep(1.5)
                except ValueError:
                    print("错误：请输入有效数字！")
                    time.sleep(1.5)
            break
        elif key == '0' or key == 'q' or key == '\x03':
            break

def configure_credentials():
    while True:
        cfg = load_ui_cfg()
        clear_screen()
        print_header()
        
        uname = cfg.get("username", "admin")
        pwd = cfg.get("password", "")
        masked_pwd = pwd if len(pwd) <= 4 else pwd[:3] + "********" + pwd[-2:]
        
        print(f"{C_BOLD}【网页管理账户及密码配置】{C_END}")
        print(f"当前管理账号: {C_BOLD}{uname}{C_END}")
        print(f"当前管理密码: {C_BOLD}{masked_pwd}{C_END}\n")
        print(f"  {C_GREEN}[1]{C_END} 自定义修改管理员账户与密码")
        print(f"  {C_GREEN}[2]{C_END} 随机安全重置密码")
        print(f"  {C_GREEN}[0]{C_END} 返回主菜单")
        print(f"{C_BOLD}{C_CYAN}============================================================{C_END}\n")
        key = input("请选择操作 [0-2]：").strip()
        
        if key == '1':
            new_uname = input(f"\n请输入新管理账号 (默认 {uname}, 回车不改): ").strip()
            if not new_uname:
                new_uname = uname
            new_pwd = input("请输入新管理密码 (不能为空): ").strip()
            if not new_pwd:
                print("错误：密码不能为空！")
                time.sleep(1.5)
                continue
            cfg["username"] = new_uname
            cfg["password"] = new_pwd
            if save_ui_cfg(cfg):
                print(f"{C_GREEN}管理员身份修改成功！{C_END}")
                print(f"账号: {new_uname} | 密码: {new_pwd}")
                input("\n按回车键继续...")
            else:
                print("写入配置文件失败")
                time.sleep(1.5)
            break
        elif key == '2':
            import random
            import string
            chars = string.ascii_letters + string.digits
            new_pwd = ""
            while True:
                new_pwd = "".join(random.choices(chars, k=12))
                if any(c.islower() for c in new_pwd) and any(c.isupper() for c in new_pwd) and any(c.isdigit() for c in new_pwd):
                    break
            cfg["password"] = new_pwd
            if save_ui_cfg(cfg):
                print(f"{C_GREEN}密码随机安全重置成功！{C_END}")
                print(f"全新安全密码：{new_pwd}")
                print("配置已实时写入生效，请刷新网页登录页面即可。")
                input("\n按回车键继续...")
            else:
                print("写入配置文件失败")
                time.sleep(1.5)
            break
        elif key == '0' or key == 'q' or key == '\x03':
            break

def configure_web():
    while True:
        cfg = load_ui_cfg()
        clear_screen()
        print_header()
        
        print(f"{C_BOLD}【网页服务端绑定及访问后缀配置】{C_END}\n")
        print(f"  {C_GREEN}[1]{C_END} 切换服务监听IP (当前: {cfg.get('host', '::')})")
        print(f"  {C_GREEN}[2]{C_END} 随机重置安全登录路径后缀 (当前: {cfg.get('secret_path', '')})")
        print(f"  {C_GREEN}[0]{C_END} 返回主菜单")
        print(f"{C_BOLD}{C_CYAN}============================================================{C_END}\n")
        key = input("请选择操作 [0-2]：").strip()
        
        if key == '1':
            print("\n选择网页登录绑定地址：")
            print("  1. 仅限本地 IPv4 登录 (127.0.0.1 - 最安全)")
            print("  2. 允许 IPv4 公网登录 (0.0.0.0)")
            print("  3. 允许双栈公网登录 (:: - 推荐)")
            print("  4. 仅限本地 IPv6 登录 (::1)")
            sel = input("请选择 (1-4, 默认3): ").strip()
            if sel == '1':
                cfg['host'] = "127.0.0.1"
            elif sel == '2':
                cfg['host'] = "0.0.0.0"
            elif sel == '4':
                cfg['host'] = "::1"
            else:
                cfg['host'] = "::"
            if save_ui_cfg(cfg):
                print(f"{C_GREEN}服务端绑定监听地址已修改为: {cfg['host']}{C_END}")
                ask_restart()
            else:
                print("写入配置文件失败")
                time.sleep(1.5)
            break
        elif key == '2':
            import random
            import string
            new_suffix = "".join(random.choices(string.ascii_letters + string.digits, k=12))
            cfg["secret_path"] = new_suffix
            if save_ui_cfg(cfg):
                print(f"{C_GREEN}网页安全登录路径后缀已重置成功！{C_END}")
                print(f"新的后缀路径: {new_suffix}")
                h = cfg['host']
                display_host = f"[{h}]" if ":" in h else h
                print(f"新的访问完整路径: http://{display_host}:{cfg['port']}/{new_suffix}/")
                ask_restart()
            else:
                print("写入配置文件失败")
                time.sleep(1.5)
            break
        elif key == '0' or key == 'q' or key == '\x03':
            break

def show_panel_access():
    clear_screen()
    print_header()
    cfg = load_ui_cfg()
    ui_port = cfg.get("port", 8787)
    secret_path = cfg.get("secret_path", "EJsW2EeBo9lY")
    
    h = cfg.get("host", "::")
    if h in ("127.0.0.1", "localhost"):
        login_ip = "127.0.0.1"
    elif h == "::1":
        login_ip = "[::1]"
    elif h in ("::", "0.0.0.0", ""):
        login_ip = get_public_ip()
    else:
        login_ip = f"[{h}]" if ":" in h else h
        
    print(f"\n{C_BOLD}【网页面板访问控制台详情】{C_END}\n")
    print(format_line("网页访问地址", f"{C_WARNING}http://{login_ip}:{ui_port}/{secret_path}/{C_END}"))
    print(format_line("默认管理账户", cfg.get("username", "admin")))
    print(format_line("默认安全密码", cfg.get("password", "未配置")))
    print(format_line("服务绑定IP", cfg.get("host", "::")))
    print(format_line("网关出站端口", cfg.get("proxy_port", 7928)))
    print(f"\n{C_BOLD}{C_CYAN}============================================================{C_END}")
    print(" 提示: 请妥善保管此登录路径及管理员凭据，防止泄漏。")
    input("\n按回车键返回主菜单...")

def detect_remote_branch():
    candidates = []
    current = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    if current.returncode == 0 and current.stdout.strip() not in ("", "HEAD"):
        candidates.append(current.stdout.strip())

    head = subprocess.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], capture_output=True, text=True)
    if head.returncode == 0 and head.stdout.strip().startswith("origin/"):
        candidates.append(head.stdout.strip().split("/", 1)[1])

    candidates.extend(["main", "master", "bate"])
    for branch in dict.fromkeys(candidates):
        chk = subprocess.run(["git", "rev-parse", "--verify", f"origin/{branch}"], capture_output=True, text=True)
        if chk.returncode == 0:
            return branch
    return "main"

def cleanup_python_cache():
    for root, dirs, files in os.walk(INSTALL_DIR):
        dirs[:] = [d for d in dirs if d != ".git"]
        for dirname in list(dirs):
            if dirname == "__pycache__":
                shutil.rmtree(Path(root) / dirname, ignore_errors=True)
                dirs.remove(dirname)
        for filename in files:
            if filename.endswith((".pyc", ".pyo")):
                safe_remove_path(Path(root) / filename)

def ensure_host_service():
    if shutil.which("systemctl"):
        service = Path("/etc/systemd/system/aimilivpn.service")
        service.write_text(
            "[Unit]\n"
            "Description=AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"WorkingDirectory={INSTALL_DIR}\n"
            "ExecStart=/usr/bin/python3 backend/vpngate_manager.py\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "EnvironmentFile=-/etc/default/aimilivpn\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n",
            encoding="utf-8",
        )
        run_quiet(["systemctl", "daemon-reload"])
        run_quiet(["systemctl", "enable", "aimilivpn.service"])
        return

    if shutil.which("rc-service"):
        service = Path("/etc/init.d/aimilivpn")
        service.write_text(
            "#!/sbin/openrc-run\n\n"
            "description=\"AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy\"\n"
            "command=\"/usr/bin/python3\"\n"
            f"command_args=\"{INSTALL_DIR}/backend/vpngate_manager.py\"\n"
            "command_background=\"yes\"\n"
            f"directory=\"{INSTALL_DIR}\"\n"
            "pidfile=\"/run/aimilivpn.pid\"\n\n"
            "depend() {\n"
            "    need net\n"
            "    after firewall\n"
            "}\n",
            encoding="utf-8",
        )
        service.chmod(0o755)
        run_quiet(["rc-update", "add", "aimilivpn", "default"])

def cleanup_service_files():
    for svc in SERVICE_NAMES:
        base = svc[:-8] if svc.endswith(".service") else svc
        if shutil.which("systemctl"):
            run_quiet(["systemctl", "stop", svc])
            run_quiet(["systemctl", "disable", svc])
            run_quiet(["systemctl", "reset-failed", svc])
            for path in (
                f"/etc/systemd/system/{svc}",
                f"/lib/systemd/system/{svc}",
                f"/usr/lib/systemd/system/{svc}",
                f"/etc/systemd/system/multi-user.target.wants/{svc}",
            ):
                safe_remove_path(path)
        if shutil.which("rc-service"):
            run_quiet(["rc-service", base, "stop"])
            run_quiet(["rc-update", "del", base, "default"])
            safe_remove_path(f"/etc/init.d/{base}")
    if shutil.which("systemctl"):
        run_quiet(["systemctl", "daemon-reload"])

def cleanup_processes():
    for pattern in ("vpngate_manager.py", "aimili-xray"):
        run_quiet(["pkill", "-TERM", "-f", pattern])
    run_quiet(["pkill", "-TERM", "-x", "xray"])
    time.sleep(1)
    for pattern in ("vpngate_manager.py", "aimili-xray"):
        run_quiet(["pkill", "-KILL", "-f", pattern])
    run_quiet(["pkill", "-KILL", "-x", "xray"])

def cleanup_docker_residuals():
    if not shutil.which("docker"):
        return
    compose_dirs = []
    for path in (INSTALL_DIR, DOCKER_INSTALL_DIR, "/opt/aimilivpn-docker"):
        p = Path(path)
        if p not in compose_dirs and (p / "docker-compose.yml").exists():
            compose_dirs.append(p)

    for compose_dir in compose_dirs:
        run_quiet(["docker", "compose", "down", "-v", "--remove-orphans", "--rmi", "local"], cwd=str(compose_dir))

    for name in ("aimilivpn-full", "aimili-vpn-panel", "aimili-vpngate"):
        run_quiet(["docker", "rm", "-f", name])
    for image in ("aimili-vpn-panel:latest", "aimili-vpngate:latest", "aimilivpn-full:latest"):
        run_quiet(["docker", "image", "rm", "-f", image])

def cleanup_xray_residuals():
    if shutil.which("systemctl"):
        run_quiet(["systemctl", "stop", "xray.service", "xray@*.service"])
        run_quiet(["systemctl", "disable", "xray.service", "xray@*.service"])
    if shutil.which("rc-service"):
        run_quiet(["rc-service", "xray", "stop"])
        run_quiet(["rc-update", "del", "xray", "default"])
    run_quiet(["pkill", "-TERM", "-x", "xray"])
    time.sleep(0.5)
    run_quiet(["pkill", "-KILL", "-x", "xray"])
    for path in XRAY_PATHS:
        safe_remove_path(path)
    if shutil.which("systemctl"):
        run_quiet(["systemctl", "daemon-reload"])

def update_panel():
    clear_screen()
    print_header()
    print("\n正在检查远程版本并准备更新...", flush=True)
    if os.path.exists(INSTALL_DIR):
        try:
            os.chdir(INSTALL_DIR)
            if not os.path.exists(".git"):
                print(f"{C_FAIL}当前安装目录不是 Git 仓库，无法直接更新。{C_END}")
                time.sleep(2)
                return
                
            subprocess.run(["git", "fetch", "--all"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            branch = detect_remote_branch()
                    
            local_commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            remote_commit = subprocess.run(["git", "rev-parse", f"origin/{branch}"], capture_output=True, text=True).stdout.strip()
            
            if local_commit == remote_commit:
                print(f"\n{C_GREEN}当前已是最新版本！{C_END}")
                ans = input("是否强制刷新源码并重建运行环境？(y/N): ").strip().lower()
                if ans != 'y':
                    return
            else:
                print(f"\n检测到新版本！本地: {local_commit[:8]}，远程最新: {remote_commit[:8]}")
                ans = input("是否开始下载并更新面板？(Y/n): ").strip().lower()
                if ans not in ('', 'y', 'yes'):
                    return

            print("\n正在停止当前运行实例...")
            run_service_cmd("stop")

            print(f"\n正在强制重置源码至 origin/{branch} ...")
            subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], check=True)
            
            print("清理 Python 编译缓存...")
            cleanup_python_cache()

            if not is_docker_install():
                print(f"{C_FAIL}当前不是 Docker 安装。为保证网关安全，已停止宿主机服务更新路径。请使用 install-docker.sh 重新部署。{C_END}")
                time.sleep(3)
                return

            print("正在重建并启动 Docker Stack...")
            docker_compose(["up", "-d", "--build"], check=True)

            ensure_global_launcher()
            print(f"\n{C_GREEN}面板更新完成，配置与运行数据已保留。{C_END}")
            time.sleep(2)
        except Exception as e:
            print(f"升级中发生错误: {e}")
            time.sleep(3)
    else:
        print(f"找不到安装目录：{INSTALL_DIR}")
        time.sleep(2)

def uninstall_panel():
    clear_screen()
    print_header()
    print(f"\n{C_FAIL}{C_BOLD}警告：完全卸载将删除 AimiliVPN 服务、Docker Stack、Xray、命令入口、配置、日志、缓存和源码目录。{C_END}")
    print(f"将清理：{INSTALL_DIR}、{DOCKER_INSTALL_DIR}、/usr/bin/ml、AimiliVPN 服务文件、Xray 残留。")
    ans = input("如确认完全卸载，请输入 YES：").strip()
    if ans != 'YES':
        print("已取消卸载。")
        time.sleep(1)
        return

    print("\n正在停止服务与残留进程...", flush=True)
    run_service_cmd("stop")
    cleanup_service_files()
    cleanup_processes()

    print("正在清理 Docker 容器、镜像与编排目录...")
    cleanup_docker_residuals()

    print("正在清理 Xray Core 与历史配置...")
    cleanup_xray_residuals()

    print("正在删除命令入口、运行配置、日志和源码目录...")
    for path in COMMAND_LINKS:
        safe_remove_path(path)
    for path in (
        "/etc/default/aimilivpn",
        "/etc/sysctl.d/99-aimilivpn.conf",
        INSTALL_DIR,
        DOCKER_INSTALL_DIR,
        "/opt/aimilivpn-docker",
        *LEGACY_INSTALL_DIRS,
    ):
        safe_remove_path(path)

    print(f"\n{C_GREEN}完全卸载及痕迹清理完成。{C_END}")
    sys.exit(0)

def main():
    if os.getuid() != 0:
        print("错误：此命令必须以 root 用户权限执行。")
        sys.exit(1)
        
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "status":
            show_system_status()
        elif cmd == "logs":
            show_logs()
        elif cmd == "refresh":
            fetch_and_test_nodes()
        elif cmd == "nodes":
            list_and_switch_nodes()
        elif cmd == "route":
            configure_routing_mode()
        elif cmd == "update":
            update_panel()
        elif cmd == "uninstall":
            uninstall_panel()
        elif cmd == "web":
            configure_web()
        elif cmd == "port":
            configure_port()
        elif cmd == "password":
            configure_credentials()
        elif cmd == "access":
            show_panel_access()
        else:
            print("未知命令。可用子命令: status, logs, refresh, nodes, route, update, uninstall, web, port, password, access")
        sys.exit(0)
        
    # Interactive CLI Menu loop
    while True:
        clear_screen()
        print_status_summary()
        print(f"\n{C_BOLD}{C_CYAN}+----------------------------------------------------------+{C_END}")
        print(f"{C_BOLD}{C_CYAN}|                    AimiliVPN Menu                       |{C_END}")
        print(f"{C_BOLD}{C_CYAN}+----------------------------------------------------------+{C_END}")
        print(f"{C_BOLD}  状态与日志{C_END}")
        print(f"    {C_GREEN}[1]{C_END} 查看系统状态              {C_GREEN}[2]{C_END} 实时运行日志")
        print(f"{C_BOLD}  节点与路由{C_END}")
        print(f"    {C_GREEN}[3]{C_END} 刷新测试 VPNGate          {C_GREEN}[4]{C_END} 查看/切换节点")
        print(f"    {C_GREEN}[5]{C_END} 切换出站路由模式")
        print(f"{C_BOLD}  面板安全{C_END}")
        print(f"    {C_GREEN}[6]{C_END} 修改网页/代理端口         {C_GREEN}[7]{C_END} 修改管理员密码")
        print(f"    {C_GREEN}[8]{C_END} 修改网页登录设置          {C_GREEN}[9]{C_END} 查看登录信息")
        print(f"{C_BOLD}  维护{C_END}")
        print(f"    {C_GREEN}[10]{C_END} 在线更新                 {C_FAIL}[11]{C_END} 完全卸载")
        print(f"    {C_GREEN}[0]{C_END} 退出")
        print(f"{C_BOLD}{C_CYAN}+----------------------------------------------------------+{C_END}")
        choice = input("请选择操作 [0-11]: ").strip()
        if choice in ('q', 'Q', '0'):
            print(f"\n退出终端菜单，再见！")
            break
        elif choice == '':
            continue
            
        if choice == '1':
            show_system_status()
        elif choice == '2':
            show_logs()
        elif choice == '3':
            fetch_and_test_nodes()
        elif choice == '4':
            list_and_switch_nodes()
        elif choice == '5':
            configure_routing_mode()
        elif choice == '6':
            configure_port()
        elif choice == '7':
            configure_credentials()
        elif choice == '8':
            configure_web()
        elif choice == '9':
            show_panel_access()
        elif choice == '10':
            update_panel()
        elif choice == '11':
            uninstall_panel()

if __name__ == "__main__":
    main()
