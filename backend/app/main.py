import os
import sys
import time
import socket
import signal
import threading
from pathlib import Path

# Add workspace root to sys.path to allow imports from proxy and utils
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.app import state
from backend.app.config import (
    DATA_DIR, STATE_FILE, LOCAL_PROXY_HOST, LOCAL_PROXY_PORT,
    UI_HOST, UI_PORT, API_URL, TARGET_VALID_NODES, FETCH_INTERVAL_SECONDS,
    CHECK_INTERVAL_SECONDS, VPNGATE_ONLY_MODE, SERVICE_MODE
)
from backend.app.db import (
    ensure_dirs, load_ui_config, write_json
)
from backend.app.core.vpn import (
    kill_existing_openvpn_processes, kill_existing_xray_processes,
    stop_active_openvpn, ensure_xray_default_start_config,
    collector_loop, background_proxy_checker, active_node_pinger
)
from backend.app.core.xray import start_xray
from backend.app.api.server import DualStackHTTPServer
from backend.app.api.handler import Handler
from proxy import server as proxy_server

class Tee:
    def __init__(self, file_path: str):
        Path(file_path).parent.mkdir(exist_ok=True, parents=True)
        self.file = open(file_path, "a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, data: str) -> None:
        try:
            self.stdout.write(data)
        except Exception:
            pass
        try:
            self.file.write(data)
            self.file.flush()
        except Exception:
            pass

    def flush(self) -> None:
        try:
            self.stdout.flush()
        except Exception:
            pass
        try:
            self.file.flush()
        except Exception:
            pass


def main() -> None:
    ensure_dirs()
    state.active_openvpn_node_id = ""
    state.openvpn_enabled = False
    state.is_connecting = False
    
    ui_cfg = load_ui_config()
    try:
        local_proxy_port = int(ui_cfg.get("proxy_port", LOCAL_PROXY_PORT))
    except (TypeError, ValueError):
        local_proxy_port = int(os.environ.get("LOCAL_PROXY_PORT", os.environ.get("PROXY_PORT", "7928")))
        
    ui_host = str(ui_cfg.get("host", UI_HOST) or UI_HOST)
    try:
        ui_port = int(ui_cfg.get("port", UI_PORT))
    except (TypeError, ValueError):
        ui_port = int(os.environ.get("UI_PORT", "8787"))

    kill_existing_openvpn_processes()
    kill_existing_xray_processes()

    def handle_exit_signals(signum, frame) -> None:
        print(f"[信号捕获] 接收到终止信号 {signum}，正在清理子进程并退出...", flush=True)
        stop_active_openvpn(clear_connecting=True, stop_xray_service=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit_signals)
    signal.signal(signal.SIGINT, handle_exit_signals)

    log_file = DATA_DIR / "vpngate.log"
    tee = Tee(str(log_file))
    sys.stdout = tee
    sys.stderr = tee

    write_json(
        STATE_FILE,
        {
            "service_mode": SERVICE_MODE,
            "api_url": API_URL,
            "target_valid_nodes": TARGET_VALID_NODES,
            "fetch_interval_seconds": FETCH_INTERVAL_SECONDS,
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "local_proxy": f"http://{'[' + LOCAL_PROXY_HOST + ']' if ':' in LOCAL_PROXY_HOST else LOCAL_PROXY_HOST}:{local_proxy_port}",
            "proxy_port": local_proxy_port,
            "active_openvpn_node_id": "",
            "openvpn_enabled": False,
            "openvpn_running": False,
            "last_fetch_status": "starting",
            "last_check_message": "VPNGate 独立服务已启动，OpenVPN 等待网页手动启动。" if VPNGATE_ONLY_MODE else "服务已启动，Xray 将默认启动；OpenVPN 等待网页手动启动。",
            "is_connecting": False,
            "active_node_latency": "OpenVPN 未启动",
            "proxy_ok": False,
            "proxy_ip": "-",
            "proxy_latency_ms": 0,
            "proxy_error": "OpenVPN 未启动",
            "blacklisted_nodes": 0,
        },
    )
    threading.Thread(target=proxy_server.start_proxy_server, args=(LOCAL_PROXY_HOST, local_proxy_port), daemon=True).start()

    # Wait for the gateway to officially start
    print("[网关] 正在启动代理网关...", flush=True)
    gateway_ready = False
    is_ipv6 = ":" in LOCAL_PROXY_HOST
    af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
    for _ in range(30):
        s = socket.socket(af, socket.SOCK_STREAM)
        try:
            s.settimeout(0.5)
            connect_host = LOCAL_PROXY_HOST
            if connect_host in ("::", "0.0.0.0", ""):
                connect_host = "::1" if is_ipv6 else "127.0.0.1"
            try:
                s.connect((connect_host, local_proxy_port))
                gateway_ready = True
                break
            except Exception:
                if connect_host == "::1":
                    try:
                        s.close()
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(0.5)
                        s.connect(("127.0.0.1", local_proxy_port))
                        gateway_ready = True
                        break
                    except Exception:
                        pass
                raise
        except Exception:
            time.sleep(0.5)
        finally:
            try:
                s.close()
            except Exception:
                pass

    if gateway_ready:
        print("[网关] 代理网关已成功启动监听，启动同步与检测脚本...", flush=True)
    else:
        print("[警告] 代理网关启动超时，继续执行脚本...", flush=True)

    if VPNGATE_ONLY_MODE:
        print("[模式] VPNGate 独立模式已启用，跳过 Xray 默认配置与启动。", flush=True)
    else:
        ensure_xray_default_start_config()
        threading.Thread(target=start_xray, daemon=True).start()
    threading.Thread(target=collector_loop, daemon=True).start()
    threading.Thread(target=background_proxy_checker, daemon=True).start()
    threading.Thread(target=active_node_pinger, daemon=True).start()

    secret_path = ui_cfg.get("secret_path", "")

    # Clean display of UI address (handle IPv6 host formatting)
    ui_host_display = f"[{ui_host}]" if ":" in ui_host else ui_host

    print(f"==========================================================", flush=True)
    print(f"  AimiliVPN Web Control Panel is running!", flush=True)
    print(f"  URL: http://{ui_host_display}:{ui_port}/{secret_path}/", flush=True)
    print(f"  Username: {ui_cfg.get('username')}", flush=True)
    print(f"  Password: {ui_cfg.get('password')}", flush=True)
    print(f"==========================================================", flush=True)
    print(f"Proxy: http://{LOCAL_PROXY_HOST}:{local_proxy_port}", flush=True)
    DualStackHTTPServer((ui_host, ui_port), Handler).serve_forever()

if __name__ == "__main__":
    main()
