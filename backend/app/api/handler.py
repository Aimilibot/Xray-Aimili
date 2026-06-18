import base64
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import ctypes
import shutil
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
import uuid

# Import from backend.app
from backend.app import state
from backend.app.config import (
    ROOT_DIR, DATA_DIR, WEB_DIR, LOCAL_PROXY_HOST, LOCAL_PROXY_PORT,
    UI_HOST, UI_PORT, SUBSCRIPTION_NODES_FILE, SUBSCRIPTION_LINKS_FILE,
    OUTBOUND_NODES_FILE, ROUTING_RULES_FILE, NODES_FILE, CHECK_INTERVAL_SECONDS
)
from backend.app.db import (
    load_ui_config, read_json, write_json, read_json_list,
    load_panel_framework_state, ensure_panel_framework_files,
    load_client_traffic, load_traffic_stats, get_state, set_state, log_to_json,
    load_feature_flags, save_feature_flags
)
from backend.app.core.xray import (
    build_panel_subscription_content, load_xray_cfg, save_xray_cfg,
    generate_xray_share_link, check_xray_installed, active_xray_running,
    enrich_subscription_links, save_subscription_link, delete_subscription_link,
    set_subscription_link_enabled, save_subscription_node, delete_subscription_node,
    set_subscription_node_enabled, generate_panel_node_share_link,
    save_routing_rule, delete_routing_rule, set_routing_rule_enabled,
    save_outbound_node, delete_outbound_node, set_outbound_node_enabled,
    parse_share_link, test_outbound_node_via_temp_xray, register_warp_account,
    sync_panel_subscription_nodes_to_xray, test_warp_via_proxy, stop_xray, start_xray,
    bg_install_xray, query_xray_client_stats, clean_hostname, xray_event,
    get_public_ip_or_domain
)
from backend.app.core.vpn import (
    active_openvpn_running, get_tun_stats, maintain_valid_nodes,
    test_multiple_nodes, start_openvpn_service, stop_openvpn_service,
    connect_node, test_node_by_id, check_proxy_health, normalize_force_country,
    parse_int
)
from utils import vpn as vpn_utils

def read_web_html(name: str) -> str:
    path = WEB_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing web page file: {path}") from exc

LOGIN_HTML = read_web_html("login.html")
INDEX_HTML = read_web_html("index.html")


class Handler(BaseHTTPRequestHandler):
    def get_secret_path(self) -> str:
        ui_cfg = load_ui_config()
        return ui_cfg.get("secret_path", "EJsW2EeBo9lY")

    def is_authorized(self) -> bool:
        ui_cfg = load_ui_config()
        pwd = ui_cfg.get("password")
        if not pwd:
            return True

        cookie_header = self.headers.get("Cookie", "")
        cookies = {}
        if cookie_header:
            for item in cookie_header.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    cookies[k.strip()] = v.strip()

        session_token = cookies.get("session")
        if not session_token:
            return False

        with state.lock:
            exp_time = state.active_sessions.get(session_token)
            if exp_time is not None and exp_time > time.time():
                return True
        return False

    def validate_path(self) -> str:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == "/api/xray/subscribe":
            return "/api/xray/subscribe"

        secret_path = self.get_secret_path()
        if not secret_path:
            return self.path
        if self.path == f"/{secret_path}":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"/{secret_path}/")
            self.end_headers()
            return ""
        prefix = f"/{secret_path}/"
        if self.path.startswith(prefix):
            return "/" + self.path[len(prefix):]
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        return ""

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def handle_xray_subscription(self) -> None:
        try:
            token = ""
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "token" in query:
                token = query["token"][0].strip()
            elif "id" in query:
                token = query["id"][0].strip()

            if not token:
                self.send_bytes("Missing subscription token".encode("utf-8"), "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return

            matched_panel_subscription, panel_body, panel_status = build_panel_subscription_content(token)
            if matched_panel_subscription:
                self.send_bytes(panel_body, "text/plain; charset=utf-8", panel_status)
                return

            cfg = load_xray_cfg()
            found_client_configs = []
            for inbound in cfg.get("inbounds", []):
                if not inbound.get("clients"):
                    continue
                for client in inbound["clients"]:
                    if client.get("status") == "active":
                        c_secret = client.get("uuid") or client.get("password") or ""
                        if c_secret == token:
                            found_client_configs.append((inbound, client))

            if not found_client_configs:
                self.send_bytes("Invalid or inactive subscription token".encode("utf-8"), "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return

            host = get_public_ip_or_domain()
            links = []
            for inbound, client in found_client_configs:
                link = generate_xray_share_link(inbound, client, host)
                if link:
                    links.append(link)

            sub_content = "\n".join(links) + "\n"
            encoded_sub = base64.b64encode(sub_content.encode("utf-8")).decode("utf-8")

            self.send_bytes(encoded_sub.encode("utf-8"), "text/plain; charset=utf-8", HTTPStatus.OK)
        except Exception as exc:
            self.send_bytes(f"Internal error: {exc}".encode("utf-8"), "text/plain; charset=utf-8", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:
        effective_path = self.validate_path()
        if effective_path == "": return

        if effective_path == "/api/xray/subscribe":
            self.handle_xray_subscription()
            return

        if not self.is_authorized():
            if effective_path in ("/", "/index.html"):
                try:
                    content = (WEB_DIR / "login.html").read_text(encoding="utf-8")
                    self.send_bytes(content.encode("utf-8"), "text/html; charset=utf-8")
                except Exception:
                    self.send_bytes(LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            elif effective_path.startswith("/css/") or effective_path.startswith("/js/"):
                pass
            else:
                self.send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return

        if effective_path in ("/", "/index.html"):
            try:
                content = (WEB_DIR / "index.html").read_text(encoding="utf-8")
                self.send_bytes(content.encode("utf-8"), "text/html; charset=utf-8")
            except Exception:
                self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif effective_path.startswith("/css/") or effective_path.startswith("/js/"):
            rel_path = effective_path.lstrip("/")
            normalized_rel_path = os.path.normpath(rel_path)
            if normalized_rel_path.startswith("..") or os.path.isabs(normalized_rel_path):
                self.send_response(HTTPStatus.FORBIDDEN)
                self.end_headers()
                return
            
            file_path = WEB_DIR / normalized_rel_path
            if not file_path.is_file():
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            
            content_type = "application/octet-stream"
            if file_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            elif file_path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif file_path.suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"):
                if file_path.suffix == ".svg":
                    content_type = "image/svg+xml"
                elif file_path.suffix == ".png":
                    content_type = "image/png"
                elif file_path.suffix in (".jpg", ".jpeg"):
                    content_type = "image/jpeg"
                elif file_path.suffix == ".webp":
                    content_type = "image/webp"
                elif file_path.suffix == ".ico":
                    content_type = "image/x-icon"
            
            try:
                content = file_path.read_bytes()
                self.send_bytes(content, content_type)
            except Exception as e:
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.end_headers()
                return
        elif effective_path == "/api/xray/status":
            cfg = load_xray_cfg()
            self.send_json({
                "installed": check_xray_installed(),
                "running": active_xray_running(),
                "enabled": cfg.get("enabled", False),
                "require_vpn": cfg.get("require_vpn", True),
                "outbound_interface": cfg.get("outbound_interface", "tun0"),
                "last_error": state.xray_last_error,
                "last_command": " ".join(state.xray_last_command),
                "log_tail": state.xray_log_tail[-20:]
            })
        elif effective_path == "/api/xray/config":
            self.send_json(load_xray_cfg())
        elif effective_path == "/api/xray/install_status":
            with state.xray_install_lock:
                self.send_json(state.xray_install_status)
        elif effective_path == "/api/panel/framework":
            ensure_panel_framework_files()
            self.send_json(load_panel_framework_state())
        elif effective_path == "/api/features":
            self.send_json({"ok": True, "features": load_feature_flags()})
        elif effective_path == "/api/panel/subscription-nodes":
            ensure_panel_framework_files()
            self.send_json({"ok": True, "nodes": read_json_list(SUBSCRIPTION_NODES_FILE)})
        elif effective_path == "/api/panel/subscription-links":
            ensure_panel_framework_files()
            links = read_json_list(SUBSCRIPTION_LINKS_FILE)
            nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
            self.send_json({"ok": True, "subscriptions": enrich_subscription_links(links, nodes)})
        elif effective_path == "/api/panel/outbound-nodes":
            ensure_panel_framework_files()
            flags = load_feature_flags()
            nodes = read_json_list(OUTBOUND_NODES_FILE)
            if not flags.get("warp_enabled", False):
                nodes = [item for item in nodes if item.get("type") != "warp"]
            if not flags.get("custom_enabled", False):
                nodes = [item for item in nodes if item.get("type") not in ("custom-node", "subscription", "json-config")]
            self.send_json({"ok": True, "nodes": nodes, "features": flags})
        elif effective_path == "/api/panel/routing-rules":
            ensure_panel_framework_files()
            self.send_json({"ok": True, "rules": read_json_list(ROUTING_RULES_FILE)})
        elif effective_path == "/api/stats":
            is_win = sys.platform.startswith("win")
            
            # 1. CPU Load
            cpu_percent = 0
            if is_win:
                try:
                    global _win_cpu_cache
                    if "_win_cpu_cache" not in globals():
                        _win_cpu_cache = {"idle": 0, "kernel": 0, "user": 0, "percent": 0}
                    
                    class FILETIME(ctypes.Structure):
                        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]
                    
                    idle = FILETIME()
                    kernel = FILETIME()
                    user = FILETIME()
                    
                    res = ctypes.windll.kernel32.GetSystemTimes(
                        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
                    )
                    if res:
                        def to_int(ft):
                            return (ft.dwHighDateTime << 32) + ft.dwLowDateTime
                        
                        idle_val = to_int(idle)
                        kernel_val = to_int(kernel)
                        user_val = to_int(user)
                        
                        prev_idle = _win_cpu_cache["idle"]
                        prev_kernel = _win_cpu_cache["kernel"]
                        prev_user = _win_cpu_cache["user"]
                        
                        _win_cpu_cache["idle"] = idle_val
                        _win_cpu_cache["kernel"] = kernel_val
                        _win_cpu_cache["user"] = user_val
                        
                        if prev_kernel > 0 or prev_user > 0:
                            idle_diff = idle_val - prev_idle
                            kernel_diff = kernel_val - prev_kernel
                            user_diff = user_val - prev_user
                            system_diff = kernel_diff + user_diff
                            if system_diff > 0:
                                percent = int((system_diff - idle_diff) * 100 / system_diff)
                                cpu_percent = max(0, min(100, percent))
                                _win_cpu_cache["percent"] = cpu_percent
                            else:
                                cpu_percent = _win_cpu_cache["percent"]
                        else:
                            time.sleep(0.05)
                            idle2 = FILETIME()
                            kernel2 = FILETIME()
                            user2 = FILETIME()
                            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle2), ctypes.byref(kernel2), ctypes.byref(user2)):
                                idle_val2 = to_int(idle2)
                                kernel_val2 = to_int(kernel2)
                                user_val2 = to_int(user2)
                                idle_diff = idle_val2 - idle_val
                                system_diff = (kernel_val2 - kernel_val) + (user_val2 - user_val)
                                if system_diff > 0:
                                    cpu_percent = max(0, min(100, int((system_diff - idle_diff) * 100 / system_diff)))
                                    _win_cpu_cache["percent"] = cpu_percent
                except Exception:
                    pass
            else:
                try:
                    global _linux_cpu_cache
                    if "_linux_cpu_cache" not in globals():
                        _linux_cpu_cache = {"idle": 0, "total": 0, "percent": 0}
                    
                    with open("/proc/stat", "r") as f:
                        parts = f.readline().strip().split()
                    
                    if len(parts) >= 5 and parts[0] == "cpu":
                        vals = [float(x) for x in parts[1:9]]
                        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                        total = sum(vals)
                        
                        prev_idle = _linux_cpu_cache["idle"]
                        prev_total = _linux_cpu_cache["total"]
                        
                        _linux_cpu_cache["idle"] = idle
                        _linux_cpu_cache["total"] = total
                        
                        if prev_total > 0:
                            idle_diff = idle - prev_idle
                            total_diff = total - prev_total
                            if total_diff > 0:
                                cpu_percent = max(0, min(100, int((total_diff - idle_diff) * 100 / total_diff)))
                                _linux_cpu_cache["percent"] = cpu_percent
                            else:
                                cpu_percent = _linux_cpu_cache["percent"]
                        else:
                            time.sleep(0.05)
                            with open("/proc/stat", "r") as f:
                                parts2 = f.readline().strip().split()
                            if len(parts2) >= 5 and parts2[0] == "cpu":
                                vals2 = [float(x) for x in parts2[1:9]]
                                idle2 = vals2[3] + (vals2[4] if len(vals2) > 4 else 0)
                                total2 = sum(vals2)
                                idle_diff = idle2 - idle
                                total_diff = total2 - total
                                if total_diff > 0:
                                    cpu_percent = max(0, min(100, int((total_diff - idle_diff) * 100 / total_diff)))
                                    _linux_cpu_cache["percent"] = cpu_percent
                except Exception:
                    pass
            
            # 2. Memory
            mem_percent, mem_used, mem_total = 0, 0.0, 0.0
            if is_win:
                try:
                    class MEMORYSTATUSEX(ctypes.Structure):
                        _fields_ = [
                            ("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]
                    stat = MEMORYSTATUSEX()
                    stat.dwLength = ctypes.sizeof(stat)
                    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                        total_bytes = stat.ullTotalPhys
                        avail_bytes = stat.ullAvailPhys
                        used_bytes = total_bytes - avail_bytes
                        mem_percent = int(stat.dwMemoryLoad)
                        mem_used = round(used_bytes / (1024 ** 3), 1)
                        mem_total = round(total_bytes / (1024 ** 3), 1)
                except Exception:
                    pass
            else:
                try:
                    meminfo = {}
                    with open("/proc/meminfo", "r") as f:
                        for line in f:
                            parts = line.split(":")
                            if len(parts) == 2:
                                meminfo[parts[0].strip()] = int(parts[1].split()[0])
                    total_mem = meminfo.get("MemTotal", 0) / 1024 / 1024
                    avail_mem = meminfo.get("MemAvailable", 0) / 1024 / 1024
                    if avail_mem == 0:
                        avail_mem = (meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)) / 1024 / 1024
                    used_mem = total_mem - avail_mem
                    mem_percent = int((used_mem / total_mem) * 100) if total_mem > 0 else 0
                    mem_used, mem_total = round(used_mem, 1), round(total_mem, 1)
                except Exception:
                    pass
            
            # 3. Disk
            disk_percent, disk_used, disk_total = 0, 0.0, 0.0
            try:
                disk_target = Path(sys.executable).anchor if is_win else "/"
                total, used, free = shutil.disk_usage(disk_target or ".")
                disk_percent = int((used / total) * 100) if total > 0 else 0
                disk_used = round(used / (1024 ** 3), 1)
                disk_total = round(total / (1024 ** 3), 1)
            except Exception:
                try:
                    total, used, free = shutil.disk_usage("/")
                    disk_percent = int((used / total) * 100) if total > 0 else 0
                    disk_used = round(used / (1024 ** 3), 1)
                    disk_total = round(total / (1024 ** 3), 1)
                except Exception:
                    pass
            
            # 4. Uptime
            uptime_seconds = 0
            if is_win:
                try:
                    uptime_ms = ctypes.windll.kernel32.GetTickCount64()
                    uptime_seconds = int(uptime_ms / 1000)
                except Exception:
                    pass
            else:
                try:
                    with open("/proc/uptime", "r") as f:
                        uptime_seconds = int(float(f.readline().split()[0]))
                except Exception:
                    pass
            
            # 5. Service Status
            bbr_status = "inactive"
            if not is_win:
                try:
                    res = subprocess.run(["sysctl", "net.ipv4.tcp_congestion_control"], capture_output=True, text=True)
                    if "bbr" in res.stdout.lower():
                        bbr_status = "active"
                except Exception:
                    pass
            
            # OpenVPN running status
            ovpn_status = "active" if active_openvpn_running() else "inactive"
            
            # Xray running status
            xray_status_str = "active" if active_xray_running() else "inactive"
            
            # Connections count
            connection_count = 1 if active_openvpn_running() else 0
            
            # Calculate traffic statistics
            tun_rx, tun_tx = get_tun_stats("tun0")
            session_rx = max(0, tun_rx - state.session_rx_start) if active_openvpn_running() else 0
            session_tx = max(0, tun_tx - state.session_tx_start) if active_openvpn_running() else 0
            
            traffic_history = load_traffic_stats()
            accumulated_rx = traffic_history.get("accumulated_rx", 0)
            accumulated_tx = traffic_history.get("accumulated_tx", 0)
            lifetime_rx = traffic_history.get("lifetime_rx", accumulated_rx)
            lifetime_tx = traffic_history.get("lifetime_tx", accumulated_tx)
            
            cycle_total_bytes = accumulated_rx + accumulated_tx + session_rx + session_tx
            cumulative_total_bytes = lifetime_rx + lifetime_tx + session_rx + session_tx

            history_file = DATA_DIR / "traffic_history.json"
            trend_history = read_json(history_file, [])
            if not isinstance(trend_history, list):
                trend_history = []

            self.send_json({
                "cpu_percent": cpu_percent,
                "cpu_cores": os.cpu_count() or 1,
                "memory_percent": mem_percent,
                "memory_used_gb": mem_used,
                "memory_total_gb": mem_total,
                "disk_percent": disk_percent,
                "disk_used_gb": disk_used,
                "disk_total_gb": disk_total,
                "uptime_seconds": uptime_seconds,
                "connection_count": connection_count,
                "bbr_status": bbr_status,
                "ovpn_status": ovpn_status,
                "xray_installed": check_xray_installed(),
                "xray_enabled": load_xray_cfg().get("enabled", False),
                "xray_status": xray_status_str,
                "traffic": {
                    "session_rx": session_rx,
                    "session_tx": session_tx,
                    "cycle_total": cycle_total_bytes,
                    "cumulative_total": cumulative_total_bytes
                },
                "traffic_history": trend_history,
                "client_traffic": load_client_traffic(),
                "web_status": "active"
            })
        elif effective_path == "/api/nodes":
            flags = load_feature_flags()
            nodes = read_json(NODES_FILE, [])
            if not flags.get("vpngate_enabled", False):
                nodes = []
            active_node = next((n for n in nodes if state.active_openvpn_node_id and n.get("id") == state.active_openvpn_node_id), None)
            for n in nodes:
                n["active"] = bool(state.active_openvpn_node_id and n.get("id") == state.active_openvpn_node_id)
            if active_node:
                ip = active_node.get("ip") or active_node.get("remote_host")
                if ip:
                    now = time.time()
                    if now - state.last_active_ping_time > 15.0:
                        state.last_active_ping_time = now
                        def bg_ping(ip_addr: str, port: int, fallback: int) -> None:
                            try:
                                latency = vpn_utils.ping_latency_ms(ip_addr, port, fallback)
                                if latency > 0:
                                    state.last_active_latency = latency
                            except Exception:
                                pass
                        threading.Thread(
                            target=bg_ping,
                            args=(ip, parse_int(active_node.get("remote_port")), parse_int(active_node.get("ping"))),
                            daemon=True
                        ).start()
                    if state.last_active_latency > 0:
                        active_node["latency_ms"] = state.last_active_latency
            stripped_nodes = []
            for n in nodes:
                stripped = n.copy()
                if "config_text" in stripped:
                    del stripped["config_text"]
                stripped_nodes.append(stripped)
            self.send_json({"nodes": stripped_nodes, "state": get_state()})
        elif effective_path.startswith("/configs/"):
            filename = urllib.parse.unquote(effective_path.removeprefix("/configs/"))
            with state.lock:
                nodes = read_json(NODES_FILE, [])
                node = next((n for n in nodes if Path(n.get("config_file", "")).name == filename), None)
            if node and node.get("config_text"):
                self.send_bytes(node["config_text"].encode("utf-8"), "application/x-openvpn-profile")
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        elif effective_path == "/api/gateway_status":
            web_ui_status = {
                "name": "Web 管理服务",
                "status": "running",
                "details": f"监听地址: {load_ui_config().get('host', UI_HOST)}:{load_ui_config().get('port', UI_PORT)}",
                "error": ""
            }
            proxy_ok = False
            proxy_err = ""
            is_ipv6 = ":" in LOCAL_PROXY_HOST
            af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
            s = socket.socket(af, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                connect_host = LOCAL_PROXY_HOST
                if connect_host in ("::", "0.0.0.0", ""):
                    connect_host = "::1" if is_ipv6 else "127.0.0.1"
                try:
                    s.connect((connect_host, LOCAL_PROXY_PORT))
                    proxy_ok = True
                except Exception:
                    if connect_host == "::1":
                        s.close()
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(0.5)
                        s.connect(("127.0.0.1", LOCAL_PROXY_PORT))
                        proxy_ok = True
                    else:
                        raise
            except Exception as e:
                diag = vpn_utils.diagnose_local_obstructions(LOCAL_PROXY_PORT, host=LOCAL_PROXY_HOST)
                proxy_err = diag[1] if diag else f"本地代理网关无法连通: {e}"
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            proxy_gateway_status = {
                "name": "本地代理网关",
                "status": "running" if proxy_ok else "stopped",
                "details": f"监听地址: {LOCAL_PROXY_HOST}:{LOCAL_PROXY_PORT}",
                "error": proxy_err
            }
            ovpn_ok = active_openvpn_running()
            ovpn_err = ""
            ovpn_details = "未连接"
            if ovpn_ok:
                ovpn_details = f"已连接节点: {state.active_openvpn_node_id}"
                if sys.platform.startswith("linux"):
                    if not Path("/sys/class/net/tun0").exists():
                        ovpn_err = "[警告] 虚拟网卡 (tun0) 未启用，可能存在策略路由配置问题。"
            else:
                if state.active_openvpn_node_id:
                    ovpn_err = "连接已中断或 OpenVPN 核心程序异常退出。"
                    ovpn_details = f"尝试连接节点 {state.active_openvpn_node_id} 失败"
            openvpn_status = {
                "name": "OpenVPN 核心连接",
                "status": "running" if ovpn_ok else "stopped",
                "details": ovpn_details,
                "error": ovpn_err
            }
            now = time.time()
            server_uptime = now - state.server_start_time
            collector_ok = (state.last_collector_heartbeat > 0.0 and now - state.last_collector_heartbeat < (CHECK_INTERVAL_SECONDS * 1.5)) or (server_uptime < 15.0)
            collector_status = {
                "name": "节点同步守护线程",
                "status": "running" if collector_ok else "stopped",
                "details": f"上次心跳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_collector_heartbeat)) if state.last_collector_heartbeat > 0 else '等待启动'}",
                "error": "" if collector_ok else "线程可能已异常终止，导致无法在后台拉取和测速新节点。"
            }
            checker_ok = (state.last_checker_heartbeat > 0.0 and now - state.last_checker_heartbeat < 90.0) or (server_uptime < 35.0)
            checker_status = {
                "name": "出口检测守护线程",
                "status": "running" if checker_ok else "stopped",
                "details": f"上次心跳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_checker_heartbeat)) if state.last_checker_heartbeat > 0 else '等待启动'}",
                "error": "" if checker_ok else "线程可能已挂起或终止，导致无法实时获取代理出口状态。"
            }
            pinger_ok = (state.last_pinger_heartbeat > 0.0 and now - state.last_pinger_heartbeat < 30.0) or (server_uptime < 15.0)
            pinger_status = {
                "name": "延迟测速守护线程",
                "status": "running" if pinger_ok else "stopped",
                "details": f"上次心跳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_pinger_heartbeat)) if state.last_pinger_heartbeat > 0 else '等待启动'}",
                "error": "" if pinger_ok else "线程可能已中止，无法实时刷新活动节点的 Ping 延迟。"
            }
            self.send_json({
                "ok": True,
                "services": [
                    web_ui_status,
                    proxy_gateway_status,
                    openvpn_status,
                    collector_status,
                    checker_status,
                    pinger_status
                ]
            })
        elif effective_path == "/api/logs":
            logs_dir = DATA_DIR / "logs"
            date_str = time.strftime("%Y-%m-%d", time.localtime())
            log_file = logs_dir / f"{date_str}.json"
            entries = []
            if log_file.exists():
                try:
                    with state.lock:
                        with open(log_file, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    try:
                                        entries.append(json.loads(line))
                                    except Exception:
                                        pass
                except Exception as e:
                    print(f"[API Logs] Error reading log file: {e}", flush=True)
            self.send_json({"logs": entries})
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        effective_path = self.validate_path()
        if effective_path == "": return

        if effective_path == "/api/login":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                input_pwd = str(payload.get("password") or "")
                input_uname = str(payload.get("username") or "")

                ui_cfg = load_ui_config()
                expected_pwd = ui_cfg.get("password", "")
                expected_uname = ui_cfg.get("username", "admin")

                if expected_pwd and input_pwd == expected_pwd and input_uname == expected_uname:
                    token = uuid.uuid4().hex
                    with state.lock:
                        state.active_sessions[token] = time.time() + 30 * 24 * 3600
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    secret_path = self.get_secret_path()
                    cookie_path = f"/{secret_path}/" if secret_path else "/"
                    self.send_header("Set-Cookie", f"session={token}; Path={cookie_path}; HttpOnly; SameSite=Lax; Max-Age=2592000")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                else:
                    self.send_json({"ok": False, "error": "用户名或密码不正确，请重新输入"}, HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/logout":
            try:
                cookie_header = self.headers.get("Cookie", "")
                cookies = {}
                if cookie_header:
                    for item in cookie_header.split(";"):
                        item = item.strip()
                        if "=" in item:
                            k, v = item.split("=", 1)
                            cookies[k.strip()] = v.strip()
                session_token = cookies.get("session")
                if session_token:
                    with state.lock:
                        state.active_sessions.pop(session_token, None)
                secret_path = self.get_secret_path()
                cookie_path = f"/{secret_path}/" if secret_path else "/"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", f"session=; Path={cookie_path}; HttpOnly; SameSite=Lax; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if not self.is_authorized():
            self.send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return

        if effective_path == "/api/features/toggle":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                key = str(payload.get("key") or "").strip()
                enabled = payload.get("enabled") is True
                valid_keys = {"vpngate_enabled", "warp_enabled", "custom_enabled"}
                if key not in valid_keys:
                    self.send_json({"ok": False, "error": "未知功能开关"}, HTTPStatus.BAD_REQUEST)
                    return

                flags = load_feature_flags()
                flags[key] = enabled
                flags = save_feature_flags(flags)

                if key == "vpngate_enabled":
                    if enabled:
                        threading.Thread(target=maintain_valid_nodes, args=(False,), daemon=True).start()
                        message = "VPNGate 公益节点已开启，正在后台加载节点资源。"
                    else:
                        stop_openvpn_service("VPNGate 功能已关闭")
                        message = "VPNGate 公益节点已关闭，OpenVPN 已停止。"
                elif key == "warp_enabled":
                    if not enabled:
                        nodes = read_json_list(OUTBOUND_NODES_FILE)
                        if any(node.get("type") == "warp" for node in nodes):
                            write_json(OUTBOUND_NODES_FILE, [node for node in nodes if node.get("type") != "warp"])
                            try:
                                sync_panel_subscription_nodes_to_xray(True)
                            except Exception as exc:
                                xray_event("WARNING", f"WARP 关闭后同步 Xray 失败: {exc}")
                    message = "Cloudflare WARP 已开启。" if enabled else "Cloudflare WARP 已关闭，出站配置已删除。"
                else:
                    if not enabled:
                        nodes = read_json_list(OUTBOUND_NODES_FILE)
                        changed = False
                        for node in nodes:
                            if node.get("type") in ("custom-node", "subscription", "json-config") and node.get("enabled") is not False:
                                node["enabled"] = False
                                changed = True
                        if changed:
                            write_json(OUTBOUND_NODES_FILE, nodes)
                            try:
                                sync_panel_subscription_nodes_to_xray(True)
                            except Exception as exc:
                                xray_event("WARNING", f"自定义节点关闭后同步 Xray 失败: {exc}")
                    message = "自定义节点已开启。" if enabled else "自定义节点已关闭。"

                self.send_json({"ok": True, "features": flags, "message": message})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-links":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                subscription, error = save_subscription_link(payload)
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"ok": True, "subscription": subscription, "message": "订阅链接已保存。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-links/delete":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                link_id = str(payload.get("id") or "").strip()
                if not link_id:
                    self.send_json({"ok": False, "error": "缺少订阅链接 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                deleted, deleted_nodes = delete_subscription_link(link_id)
                if not deleted:
                    self.send_json({"ok": False, "error": "订阅链接不存在"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "message": f"订阅链接已删除，{deleted_nodes} 个包含的节点链接已同步删除。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-links/toggle":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                link_id = str(payload.get("id") or "").strip()
                subscription, error = set_subscription_link_enabled(link_id, bool(payload.get("enabled", False)))
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "subscription": subscription, "message": "订阅链接状态已更新。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-nodes":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node, error = save_subscription_node(payload)
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"ok": True, "node": node, "message": "订阅节点已保存。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-nodes/delete":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                if not node_id:
                    self.send_json({"ok": False, "error": "缺少订阅节点 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                if not delete_subscription_node(node_id):
                    self.send_json({"ok": False, "error": "订阅节点不存在"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "message": "订阅节点已删除。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/subscription-nodes/toggle":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                node, error = set_subscription_node_enabled(node_id, bool(payload.get("enabled", False)))
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "node": node, "message": "订阅节点状态已更新。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        if effective_path == "/api/panel/subscription-nodes/share-link":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                if not node_id:
                    self.send_json({"ok": False, "error": "缺少订阅节点 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
                node = next((n for n in nodes if n.get("id") == node_id), None)
                if not node:
                    self.send_json({"ok": False, "error": "订阅节点不存在"}, HTTPStatus.NOT_FOUND)
                    return
                host = get_public_ip_or_domain()
                link = generate_panel_node_share_link(node, host)
                if link and (link.startswith("vless://") or link.startswith("socks://") or link.startswith("ss://") or link.startswith("trojan://")):
                    import base64
                    link = base64.b64encode(link.encode("utf-8")).decode("utf-8")
                self.send_json({
                    "ok": True,
                    "node": {
                        "name": node.get("name", ""),
                        "link": link
                    }
                })
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/routing-rules":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                rule, error = save_routing_rule(payload)
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"ok": True, "rule": rule, "message": "路由规则已创建。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/routing-rules/delete":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                rule_id = str(payload.get("id") or "").strip()
                if not rule_id:
                    self.send_json({"ok": False, "error": "缺少路由规则 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                if not delete_routing_rule(rule_id):
                    self.send_json({"ok": False, "error": "路由规则不存在"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "message": "路由规则已删除。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/routing-rules/toggle":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                rule_id = str(payload.get("id") or "").strip()
                rule, error = set_routing_rule_enabled(rule_id, bool(payload.get("enabled", False)))
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "rule": rule, "message": "路由规则状态已更新。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes":
            try:
                if not load_feature_flags().get("custom_enabled", False):
                    self.send_json({"ok": False, "error": "自定义节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node, error = save_outbound_node(payload)
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"ok": True, "node": node, "message": "出站节点已保存。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/delete":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                if not node_id:
                    self.send_json({"ok": False, "error": "缺少出站节点 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                if not delete_outbound_node(node_id):
                    self.send_json({"ok": False, "error": "出站节点不存在"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "message": "出站节点已删除。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/toggle":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                target_node = next((item for item in read_json_list(OUTBOUND_NODES_FILE) if str(item.get("id") or "") == node_id), None)
                flags = load_feature_flags()
                if target_node and target_node.get("type") == "warp" and not flags.get("warp_enabled", False):
                    self.send_json({"ok": False, "error": "Cloudflare WARP 功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                if target_node and target_node.get("type") in ("custom-node", "subscription", "json-config") and not flags.get("custom_enabled", False):
                    self.send_json({"ok": False, "error": "自定义节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                node, error = set_outbound_node_enabled(node_id, bool(payload.get("enabled", False)))
                if error:
                    self.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True, "node": node, "message": "出站节点状态已更新。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/parse-import":
            try:
                if not load_feature_flags().get("custom_enabled", False):
                    self.send_json({"ok": False, "error": "自定义节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                link = str(payload.get("input") or "").strip()
                proto, name, json_config = parse_share_link(link)
                self.send_json({"ok": True, "name": name, "json_config": json_config})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if effective_path == "/api/panel/outbound-nodes/test":
            try:
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "").strip()
                if not node_id:
                    self.send_json({"ok": False, "error": "缺少出站节点 ID"}, HTTPStatus.BAD_REQUEST)
                    return
                target_node = next((item for item in read_json_list(OUTBOUND_NODES_FILE) if str(item.get("id") or "") == node_id), None)
                flags = load_feature_flags()
                if target_node and target_node.get("type") == "warp" and not flags.get("warp_enabled", False):
                    self.send_json({"ok": False, "error": "Cloudflare WARP 功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                if target_node and target_node.get("type") in ("custom-node", "subscription", "json-config") and not flags.get("custom_enabled", False):
                    self.send_json({"ok": False, "error": "自定义节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                self.send_json(test_outbound_node_via_temp_xray(node_id))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/warp/register":
            try:
                if not load_feature_flags().get("warp_enabled", False):
                    self.send_json({"ok": False, "error": "Cloudflare WARP 功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                ensure_panel_framework_files()
                warp_node = register_warp_account()
                warp_node["enabled"] = True
                nodes = read_json_list(OUTBOUND_NODES_FILE)
                nodes = [n for n in nodes if n.get("type") != "warp"]
                nodes.append(warp_node)
                write_json(OUTBOUND_NODES_FILE, nodes)
                try:
                    sync_panel_subscription_nodes_to_xray(True)
                except Exception as e:
                    print(f"[ERROR] Syncing after WARP registration failed: {e}", flush=True)
                self.send_json({"ok": True, "node": warp_node, "message": "Cloudflare WARP 注册成功并生成接入配置！"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/warp/test":
            try:
                if not load_feature_flags().get("warp_enabled", False):
                    self.send_json({"ok": False, "error": "Cloudflare WARP 功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                ensure_panel_framework_files()
                res = test_warp_via_proxy()
                self.send_json(res)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/panel/outbound-nodes/warp/update-endpoint":
            try:
                if not load_feature_flags().get("warp_enabled", False):
                    self.send_json({"ok": False, "error": "Cloudflare WARP 功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                ensure_panel_framework_files()
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                endpoint = str(payload.get("endpoint") or "").strip()
                if not endpoint or ":" not in endpoint:
                    self.send_json({"ok": False, "error": "Endpoint 格式不正确，必须为 host:port"}, HTTPStatus.BAD_REQUEST)
                    return
                nodes = read_json_list(OUTBOUND_NODES_FILE)
                warp_node = next((n for n in nodes if n.get("type") == "warp"), None)
                if not warp_node:
                    self.send_json({"ok": False, "error": "WARP 节点未注册，请先注册"}, HTTPStatus.NOT_FOUND)
                    return
                warp_node["endpoint"] = endpoint
                warp_node["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                write_json(OUTBOUND_NODES_FILE, nodes)
                try:
                    sync_panel_subscription_nodes_to_xray(True)
                except Exception as e:
                    print(f"[ERROR] Syncing after WARP endpoint update failed: {e}", flush=True)
                self.send_json({"ok": True, "node": warp_node, "message": "Endpoint 已更新并应用！"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/clear_logs":
            try:
                logs_dir = DATA_DIR / "logs"
                deleted = 0
                cleared_text_log = False
                with state.lock:
                    if logs_dir.exists():
                        for path in logs_dir.glob("*.json"):
                            try:
                                path.unlink()
                                deleted += 1
                            except OSError:
                                pass
                    main_log = DATA_DIR / "vpngate.log"
                    if main_log.exists():
                        main_log.write_text("", encoding="utf-8")
                        cleared_text_log = True
                log_to_json("INFO", "Main", f"管理员已一键清除历史日志，共删除 {deleted} 个面板日志文件，主进程日志: {'已清空' if cleared_text_log else '不存在'}。")
                self.send_json({"ok": True, "deleted": deleted, "cleared_text_log": cleared_text_log})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/update_credentials":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                new_username = str(payload.get("username") or "").strip()
                new_password = str(payload.get("password") or "").strip()

                if not new_username or not new_password:
                    self.send_json({"ok": False, "error": "用户名和密码不能为空"}, HTTPStatus.BAD_REQUEST)
                    return

                ui_cfg = load_ui_config()
                ui_cfg["username"] = new_username
                ui_cfg["password"] = new_password

                auth_file = DATA_DIR / "ui_auth.json"
                with state.lock:
                    DATA_DIR.mkdir(exist_ok=True, parents=True)
                    auth_file.write_text(json.dumps(ui_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

                self.send_json({"ok": True, "message": "账号密码配置更新成功，已即时生效！"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        elif effective_path == "/api/update_settings":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

                ui_cfg = load_ui_config()

                new_port = payload.get("port") if "port" in payload else ui_cfg.get("port", 8787)
                new_suffix = payload.get("secret_path") if "secret_path" in payload else ui_cfg.get("secret_path", "EJsW2EeBo9lY")
                new_proxy_port = payload.get("proxy_port") if "proxy_port" in payload else ui_cfg.get("proxy_port", 7928)
                routing_mode = payload.get("routing_mode") if "routing_mode" in payload else ui_cfg.get("routing_mode", "auto")
                force_country = payload.get("force_country") if "force_country" in payload else ui_cfg.get("force_country", "")

                new_suffix = str(new_suffix or "").strip()
                routing_mode = str(routing_mode or "auto").strip()
                force_country = str(force_country or "").strip()

                try:
                    new_port_int = int(new_port)
                    if not (1 <= new_port_int <= 65535):
                        raise ValueError()
                except (TypeError, ValueError):
                    self.send_json({"ok": False, "error": "端口范围必须是 1 至 65535"}, HTTPStatus.BAD_REQUEST)
                    return

                try:
                    new_proxy_port_int = int(new_proxy_port)
                    if not (1024 <= new_proxy_port_int <= 65535):
                        raise ValueError()
                except (TypeError, ValueError):
                    self.send_json({"ok": False, "error": "代理出站端口范围必须是 1024 至 65535"}, HTTPStatus.BAD_REQUEST)
                    return

                if new_proxy_port_int == new_port_int:
                    self.send_json({"ok": False, "error": "代理出站端口不能与网页管理端口相同"}, HTTPStatus.BAD_REQUEST)
                    return

                if not new_suffix or not re.match(r"^[A-Za-z0-9]+$", new_suffix):
                    self.send_json({"ok": False, "error": "安全后缀仅能由英文字母和数字组成"}, HTTPStatus.BAD_REQUEST)
                    return

                if routing_mode not in ("auto", "fixed_ip", "fixed_region"):
                    self.send_json({"ok": False, "error": "无效的路由配置模式"}, HTTPStatus.BAD_REQUEST)
                    return

                domain = str(payload.get("domain") or "").strip() if "domain" in payload else ui_cfg.get("domain", "")
                tls_cert_file = str(payload.get("tls_cert_file") or "").strip() if "tls_cert_file" in payload else ui_cfg.get("tls_cert_file", "")
                tls_key_file = str(payload.get("tls_key_file") or "").strip() if "tls_key_file" in payload else ui_cfg.get("tls_key_file", "")
                domain_certs = payload.get("domain_certs") if "domain_certs" in payload else ui_cfg.get("domain_certs", [])

                if domain and not re.match(r"^[a-zA-Z0-9.-]+$", domain):
                    self.send_json({"ok": False, "error": "域名格式不正确"}, HTTPStatus.BAD_REQUEST)
                    return

                clean_domain_certs = []
                active_item = None
                for item in domain_certs:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    item_domain = str(item.get("domain") or "").strip()
                    item_cert = str(item.get("tls_cert_file") or "").strip()
                    item_key = str(item.get("tls_key_file") or "").strip()
                    item_cert_content = str(item.get("tls_cert_content") or "").strip()
                    item_key_content = str(item.get("tls_key_content") or "").strip()
                    item_active = item.get("active") is True

                    if not item_id:
                        item_id = uuid.uuid4().hex[:12]

                    if item_domain and not re.match(r"^[a-zA-Z0-9.-]+$", item_domain):
                        self.send_json({"ok": False, "error": f"证书条目域名 '{item_domain}' 格式不正确"}, HTTPStatus.BAD_REQUEST)
                        return

                    if item_cert_content and item_key_content:
                        certs_dir = DATA_DIR / "certs"
                        certs_dir.mkdir(parents=True, exist_ok=True)
                        cert_file_path = certs_dir / f"{item_id}_cert.pem"
                        key_file_path = certs_dir / f"{item_id}_key.pem"
                        try:
                            cert_file_path.write_text(item_cert_content, encoding="utf-8")
                            key_file_path.write_text(item_key_content, encoding="utf-8")
                            item_cert = str(cert_file_path.resolve())
                            item_key = str(key_file_path.resolve())
                        except Exception as e:
                            self.send_json({"ok": False, "error": f"写入证书内容失败: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                            return
                    else:
                        cert_file_path = DATA_DIR / "certs" / f"{item_id}_cert.pem"
                        key_file_path = DATA_DIR / "certs" / f"{item_id}_key.pem"
                        try:
                            if cert_file_path.exists(): cert_file_path.unlink()
                            if key_file_path.exists(): key_file_path.unlink()
                        except Exception:
                            pass

                    entry = {
                        "id": item_id,
                        "domain": item_domain,
                        "tls_cert_file": item_cert,
                        "tls_key_file": item_key,
                        "tls_cert_content": item_cert_content,
                        "tls_key_content": item_key_content,
                        "active": item_active
                    }
                    clean_domain_certs.append(entry)
                    if item_active:
                        active_item = entry

                certs_dir = DATA_DIR / "certs"
                if certs_dir.exists():
                    valid_ids = {x["id"] for x in clean_domain_certs}
                    for f in certs_dir.glob("*.pem"):
                        match = re.match(r"^([a-zA-Z0-9_-]+)_(cert|key)\.pem$", f.name)
                        if match:
                            fid = match.group(1)
                            if fid not in valid_ids:
                                try:
                                    f.unlink()
                                except Exception:
                                    pass

                if active_item:
                    domain = active_item["domain"]
                    tls_cert_file = active_item["tls_cert_file"]
                    tls_key_file = active_item["tls_key_file"]

                expected_port = ui_cfg.get("port", 8787)
                expected_suffix = ui_cfg.get("secret_path", "EJsW2EeBo9lY")
                expected_proxy_port = ui_cfg.get("proxy_port", 7928)

                certs_or_domain_changed = (
                    domain != ui_cfg.get("domain", "") or
                    tls_cert_file != ui_cfg.get("tls_cert_file", "") or
                    tls_key_file != ui_cfg.get("tls_key_file", "")
                )

                ui_cfg["port"] = new_port_int
                ui_cfg["secret_path"] = new_suffix
                ui_cfg["proxy_port"] = new_proxy_port_int
                ui_cfg["routing_mode"] = routing_mode
                ui_cfg["force_country"] = normalize_force_country(force_country)
                ui_cfg["domain"] = domain
                ui_cfg["tls_cert_file"] = tls_cert_file
                ui_cfg["tls_key_file"] = tls_key_file
                ui_cfg["domain_certs"] = clean_domain_certs

                auth_file = DATA_DIR / "ui_auth.json"
                with state.lock:
                    DATA_DIR.mkdir(exist_ok=True, parents=True)
                    auth_file.write_text(json.dumps(ui_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

                if certs_or_domain_changed:
                    try:
                        sync_panel_subscription_nodes_to_xray(True)
                    except Exception as e:
                        xray_event("error", f"同步Xray证书或域名配置失败: {e}")

                restart_now = payload.get("restart_now") is True
                restart_needed = (new_port_int != expected_port or new_suffix != expected_suffix or new_proxy_port_int != expected_proxy_port or restart_now)
                if restart_needed:
                    self.send_json({"ok": True, "restart_needed": True, "message": "配置更新成功，正在重启服务，网页即将自动载入..."})

                    def restart_server():
                        time.sleep(2)
                        print("[系统] 管理后台配置更新，进程即将退出以触发自动重启...", flush=True)
                        os._exit(0)

                    threading.Thread(target=restart_server, daemon=True).start()
                else:
                    self.send_json({"ok": True, "restart_needed": False, "message": "配置更新成功，已即时生效！"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        elif effective_path == "/api/update_routing":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                routing_mode = str(payload.get("routing_mode") or "auto").strip()
                force_country = str(payload.get("force_country") or "").strip()

                if routing_mode not in ("auto", "fixed_ip", "fixed_region"):
                    self.send_json({"ok": False, "error": "无效的路由配置模式"}, HTTPStatus.BAD_REQUEST)
                    return

                ui_cfg = load_ui_config()
                ui_cfg["routing_mode"] = routing_mode
                ui_cfg["force_country"] = normalize_force_country(force_country)
                ui_cfg.pop("enable_force_country", None)

                auth_file = DATA_DIR / "ui_auth.json"
                with state.lock:
                    DATA_DIR.mkdir(exist_ok=True, parents=True)
                    auth_file.write_text(json.dumps(ui_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

                self.send_json({"ok": True, "message": "出站路由配置更新成功，已即时生效！"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if effective_path == "/api/check":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                self.send_json({"ok": True, "message": maintain_valid_nodes(force=True)})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/refresh_nodes":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                threading.Thread(target=maintain_valid_nodes, args=(False,), daemon=True).start()
                self.send_json({"ok": True, "message": "已在后台启动节点更新流程"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/test_nodes":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_ids = payload.get("ids", [])
                tested_nodes = test_multiple_nodes(node_ids)
                self.send_json({"ok": True, "nodes": tested_nodes})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/openvpn/start":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                self.send_json({"ok": True, "message": start_openvpn_service()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/openvpn/stop":
            try:
                stop_openvpn_service("手动停止 OpenVPN")
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/disconnect":
            try:
                stop_openvpn_service("手动停止 OpenVPN")
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/connect":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                self.send_json({"ok": True, "message": connect_node(str(payload.get("id") or ""))})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/test_node":
            try:
                if not load_feature_flags().get("vpngate_enabled", False):
                    self.send_json({"ok": False, "error": "VPNGate 公益节点功能未开启，请先打开功能开关。"}, HTTPStatus.FORBIDDEN)
                    return
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                node_id = str(payload.get("id") or "")
                updated_node = test_node_by_id(node_id)
                self.send_json({"ok": True, "node": updated_node})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/test_proxy":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                if length > 0:
                    self.rfile.read(length)
                result = check_proxy_health()
                if result["ok"]:
                    set_state(
                        proxy_ok=True,
                        proxy_ip=result["ip"],
                        proxy_latency_ms=result["latency_ms"],
                        proxy_error=""
                    )
                else:
                    set_state(
                        proxy_ok=False,
                        proxy_ip="-",
                        proxy_latency_ms=0,
                        proxy_error=result.get("error", "未知错误")
                    )
                self.send_json(result)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/xray/config":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                saved, save_error = save_xray_cfg(payload)
                if saved:
                    enabled = payload.get("enabled", False)
                    if enabled:
                        stop_xray()
                        if check_xray_installed():
                            if start_xray():
                                self.send_json({"ok": True, "message": "Xray 配置保存成功，服务已启动。"})
                            else:
                                self.send_json({"ok": False, "error": state.xray_last_error or "Xray 启动失败，请查看日志。"}, HTTPStatus.BAD_REQUEST)
                            return
                    else:
                        stop_xray()
                    self.send_json({"ok": True, "message": "Xray 配置保存成功。"})
                else:
                    self.send_json({"ok": False, "error": save_error or "写入配置缓存文件失败"}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/xray/install":
            try:
                threading.Thread(target=bg_install_xray, daemon=True).start()
                self.send_json({"ok": True, "message": "已在后台启动安装进程"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/xray/action":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                action = str(payload.get("action") or "").lower()
                if action == "start":
                    if not check_xray_installed():
                        self.send_json({"ok": False, "error": "未检测到 xray 二进制程序"})
                    elif active_xray_running():
                        self.send_json({"ok": True, "message": "服务已在运行中"})
                    else:
                        success = start_xray()
                        self.send_json({"ok": success, "error": None if success else (state.xray_last_error or "服务启动失败，进程未正常工作")})
                elif action == "stop":
                    stop_xray()
                    self.send_json({"ok": True})
                elif action == "restart":
                    stop_xray()
                    success = start_xray()
                    self.send_json({"ok": success, "error": None if success else (state.xray_last_error or "重启后服务启动失败，请检查配置")})
                else:
                    self.send_json({"ok": False, "error": f"不支持的动作: {action}"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif effective_path == "/api/xray/reset_client_traffic":
            try:
                length = parse_int(self.headers.get("Content-Length"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                name = str(payload.get("name") or "").strip()
                if not name:
                    self.send_json({"ok": False, "error": "用户名不能为空"}, HTTPStatus.BAD_REQUEST)
                    return

                traffic = load_client_traffic()
                current_stats = query_xray_client_stats()
                client_curr = current_stats.get(name, {"uplink": 0, "downlink": 0})
                
                traffic[name] = {
                    "uploaded": 0,
                    "downloaded": 0,
                    "last_seen_uplink": client_curr.get("uplink", 0),
                    "last_seen_downlink": client_curr.get("downlink", 0)
                }
                save_client_traffic(traffic)

                cfg = load_xray_cfg()
                cfg_changed = False
                for inbound in cfg.get("inbounds", []):
                    for client in inbound.get("clients", []):
                        if client.get("name") == name and client.get("status") == "disabled":
                            client["status"] = "active"
                            cfg_changed = True
                if cfg_changed:
                    save_xray_cfg(cfg)
                    if active_xray_running():
                        stop_xray()
                        start_xray()

                self.send_json({"ok": True, "message": f"用户 {name} 流量已重置并恢复启用。"})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
