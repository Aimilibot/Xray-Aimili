from __future__ import annotations
import os
import sys
import time
import re
import queue
import shlex
import socket
import subprocess
import threading
import concurrent.futures
import urllib.request
import urllib.parse
import base64
from pathlib import Path
from typing import Any

from backend.app import state
from backend.app.config import (
    ROOT_DIR, DATA_DIR, CONFIG_DIR, NODES_FILE, OPENVPN_CMD, AUTH_FILE,
    OPENVPN_TEST_TIMEOUT_SECONDS, LOCAL_PROXY_HOST, LOCAL_PROXY_PORT,
    CHECK_INTERVAL_SECONDS, API_URL, TARGET_VALID_NODES, FETCH_INTERVAL_SECONDS
)
from backend.app.db import (
    read_json, write_json, read_json_list, log_to_json,
    load_blacklist, mark_blacklisted, load_ui_config,
    save_traffic_stats, load_traffic_stats, record_hourly_traffic,
    set_state, ensure_dirs, load_feature_flags
)
from utils import vpn as vpn_utils

_openvpn_version = None
active_test_indexes = set()
test_indexes_lock = threading.Lock()

def parse_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def normalize_force_country(c_name: str) -> str:
    if not c_name:
        return ""
    c_name_lower = c_name.strip().lower()
    for en_name, zh_name in vpn_utils.COUNTRY_TRANSLATIONS.items():
        if en_name.lower() == c_name_lower or zh_name.lower() == c_name_lower:
            return zh_name
    return c_name.strip()

def get_tun_stats(interface: str = "tun0") -> tuple[int, int]:
    try:
        rx_path = Path(f"/sys/class/net/{interface}/statistics/rx_bytes")
        tx_path = Path(f"/sys/class/net/{interface}/statistics/tx_bytes")
        if rx_path.exists() and tx_path.exists():
            return int(rx_path.read_text().strip()), int(tx_path.read_text().strip())
    except Exception:
        pass
    return 0, 0

def record_session_traffic_start(interface: str = "tun0") -> None:
    state.session_rx_start, state.session_tx_start = get_tun_stats(interface)

def save_session_traffic_to_total() -> None:
    if active_openvpn_running():
        active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
        rx, tx = get_tun_stats(active_dev)
        session_rx = max(0, rx - state.session_rx_start)
        session_tx = max(0, tx - state.session_tx_start)
        if session_rx > 0 or session_tx > 0:
            save_traffic_stats(session_rx, session_tx)
    state.session_rx_start = 0
    state.session_tx_start = 0

def fetch_api_text_via_proxy(url: str, ptype: str, phost: str, pport: int, use_ssl_verify: bool = True) -> str:
    import ssl
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    domain = parsed.hostname or "www.vpngate.net"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    is_https = parsed.scheme == "https"
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(12)
    try:
        s.connect((phost, pport))
        if ptype == "socks":
            s.sendall(b"\x05\x01\x00")
            
            def _recv_exact(sock, size):
                data = b""
                while len(data) < size:
                    chunk = sock.recv(size - len(data))
                    if not chunk:
                        raise RuntimeError("SOCKS5 connection closed prematurely")
                    data += chunk
                return data

            resp = _recv_exact(s, 2)
            if resp[0] != 5 or resp[1] != 0:
                raise RuntimeError("SOCKS5 authentication failed or unsupported")
            domain_bytes = domain.encode('ascii')
            req = b"\x05\x01\x00\x03" + bytes([len(domain_bytes)]) + domain_bytes + port.to_bytes(2, 'big')
            s.sendall(req)
            
            resp_header = _recv_exact(s, 4)
            if resp_header[0] != 5 or resp_header[1] != 0:
                raise RuntimeError(f"SOCKS5 connection request rejected: code {resp_header[1]}")
            
            atyp = resp_header[3]
            if atyp == 1:
                _recv_exact(s, 6) # 4 bytes IPv4 + 2 bytes Port
            elif atyp == 3:
                addr_len = _recv_exact(s, 1)[0]
                _recv_exact(s, addr_len + 2)
            elif atyp == 4:
                _recv_exact(s, 18) # 16 bytes IPv6 + 2 bytes Port
            else:
                raise RuntimeError(f"Unknown SOCKS5 ATYP: {atyp}")

            if is_https:
                ctx = ssl.create_default_context() if use_ssl_verify else ssl._create_unverified_context()
                s = ctx.wrap_socket(s, server_hostname=domain)
        else: # http proxy
            if is_https:
                req_str = f"CONNECT {domain}:{port} HTTP/1.1\r\nHost: {domain}:{port}\r\nUser-Agent: Mozilla/5.0 vpngate-openvpn-manager/2.0\r\nProxy-Connection: Keep-Alive\r\n\r\n"
                s.sendall(req_str.encode('ascii'))
                resp = s.recv(4096)
                if not (b"200" in resp or b"established" in resp.lower() or b"ok" in resp.lower()):
                    raise RuntimeError(f"HTTP CONNECT tunnel failed: {resp.decode('utf-8', errors='replace')}")
                ctx = ssl.create_default_context() if use_ssl_verify else ssl._create_unverified_context()
                s = ctx.wrap_socket(s, server_hostname=domain)

        if ptype == "http" and not is_https:
            request_uri = url
        else:
            request_uri = path

        req_headers = (
            f"GET {request_uri} HTTP/1.1\r\n"
            f"Host: {domain}\r\n"
            f"User-Agent: Mozilla/5.0 vpngate-openvpn-manager/2.0\r\n"
            f"Accept: text/plain,*/*\r\n"
            f"Connection: close\r\n\r\n"
        )
        s.sendall(req_headers.encode('utf-8'))

        response_data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response_data += chunk
            if len(response_data) > 10 * 1024 * 1024:
                break
    finally:
        try:
            s.close()
        except Exception:
            pass

    header_end = response_data.find(b"\r\n\r\n")
    if header_end == -1:
        raise RuntimeError("Invalid HTTP response format")

    headers_part = response_data[:header_end].decode('utf-8', errors='replace')
    body_part = response_data[header_end+4:]

    lines = headers_part.splitlines()
    if not lines:
        raise RuntimeError("Empty response headers")
    status_line = lines[0]
    status_parts = status_line.split()
    if len(status_parts) >= 2:
        try:
            status_code = int(status_parts[1])
            if status_code != 200:
                raise RuntimeError(f"HTTP Server returned status {status_code}: {status_line}")
        except ValueError:
            pass

    is_chunked = False
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip().lower() == "transfer-encoding" and "chunked" in v.lower():
                is_chunked = True
                break

    if is_chunked:
        decoded = b""
        idx = 0
        while idx < len(body_part):
            c_end = body_part.find(b"\r\n", idx)
            if c_end == -1:
                break
            chunk_size_str = body_part[idx:c_end].split(b";")[0].strip()
            try:
                chunk_size = int(chunk_size_str, 16)
            except ValueError:
                break
            if chunk_size == 0:
                break
            idx = c_end + 2
            decoded += body_part[idx : idx + chunk_size]
            idx += chunk_size + 2
        body_part = decoded

    return body_part.decode('utf-8', errors='replace')

def fetch_api_text(url: str | None = None, use_ssl_verify: bool = True) -> str:
    if url is None:
        urls = [
            API_URL,
            "http://www.vpngate.net/api/iphone/",
            "https://mirror.vpngate.net/api/iphone/"
        ]
    else:
        urls = [url]

    ptype, phost, pport = vpn_utils.get_upstream_proxy()
    last_err = ""
    
    for u in urls:
        if ptype and phost and pport:
            try:
                print(f"[fetch_api_text] 监测到上游代理 ({ptype}://{phost}:{pport})，尝试通过代理从 {u} 获取 API...", flush=True)
                return fetch_api_text_via_proxy(u, ptype, phost, pport, use_ssl_verify)
            except Exception as e:
                print(f"[fetch_api_text] 通过代理从 {u} 获取 API 失败: {e}，尝试使用直连...", flush=True)
                log_to_json("WARNING", "Main", f"使用代理 {ptype}://{phost}:{pport} 从 {u} 获取 API 失败: {e}")
                last_err = str(e)
        
        try:
            print(f"[fetch_api_text] 尝试直连从 {u} 获取 API...", flush=True)
            request = urllib.request.Request(
                u,
                headers={
                    "User-Agent": "Mozilla/5.0 vpngate-openvpn-manager/2.0",
                    "Accept": "text/plain,*/*",
                },
            )
            if u.startswith("https://") and not use_ssl_verify:
                import ssl
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(request, timeout=12, context=ctx) as response:
                    return response.read().decode("utf-8", errors="replace")
            else:
                with urllib.request.urlopen(request, timeout=12) as response:
                    return response.read().decode("utf-8", errors="replace")
            last_err = ""
        except Exception as e:
            last_err = str(e)
            print(f"[fetch_api_text] 从 {u} 获取 API 失败: {e}", flush=True)
            log_to_json("WARNING", "Main", f"从 {u} 获取 API 失败: {e}")

    raise Exception(f"获取 VPNGate API 失败。所有镜像源及代理重试均已耗尽。最后一次错误: {last_err}")

def parse_vpngate_rows(text: str) -> list[dict[str, str]]:
    lines = text.strip().splitlines()
    if not lines or len(lines) < 2:
        return []
    
    csv_lines = []
    headers = []
    for line in lines:
        if line.startswith("*"):
            continue
        if not headers:
            headers = [h.strip() for h in line.split(",")]
            continue
        csv_lines.append(line)
        
    rows = []
    import csv
    reader = csv.reader(csv_lines)
    for row in reader:
        if len(row) < len(headers):
            continue
        rows.append(dict(zip(headers, row)))
    return rows

def decode_config(encoded: str) -> str:
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return ""

def row_to_node(row: dict[str, str], config_text: str) -> dict[str, Any]:
    ip = row.get("IP", "")
    country_short = row.get("CountryShort", "")
    remote_host, remote_port, proto = vpn_utils.parse_remote(config_text, ip)
    
    country = row.get("CountryLong", "")
    country = normalize_force_country(country)
    
    score = parse_int(row.get("Score"))
    ping = parse_int(row.get("Ping"))
    
    # Generate unique ID based on IP and Port
    node_id = f"node-{ip.replace('.', '-')}-{remote_port}"
    config_file = CONFIG_DIR / f"{node_id}.ovpn"
    
    return {
        "id": node_id,
        "ip": ip,
        "remote_host": remote_host,
        "remote_port": remote_port,
        "proto": proto,
        "country": country,
        "country_short": country_short,
        "score": score,
        "ping": ping,
        "config_file": str(config_file),
        "config_text": config_text,
        "probe_status": "not_checked",
        "probe_message": "Not probed yet",
        "probed_at": 0.0,
        "latency_ms": 0,
        "active": False,
        "owner": "",
        "asn": "",
        "as_name": "",
        "location": "",
        "ip_type": "",
        "quality": "",
    }

def fetch_candidates() -> list[dict[str, Any]]:
    text = fetch_api_text()
    rows = parse_vpngate_rows(text)
    
    blacklist = load_blacklist()
    candidates = []
    
    for row in rows:
        ip = row.get("IP")
        if not ip:
            continue
            
        config_encoded = row.get("OpenVPN_ConfigData_Base64", "")
        if not config_encoded:
            continue
            
        config_text = decode_config(config_encoded)
        if not config_text:
            continue
            
        node = row_to_node(row, config_text)
        node_id = node["id"]
        
        # Check if in blacklist
        if node_id in blacklist or ip in blacklist or node["remote_host"] in blacklist:
            continue
            
        candidates.append(node)
        
    return candidates

def cached_nodes() -> list[dict[str, Any]]:
    return read_json(NODES_FILE, [])

def get_openvpn_version() -> float:
    global _openvpn_version
    if _openvpn_version is not None:
        return _openvpn_version
    try:
        cmd = shlex.split(OPENVPN_CMD, posix=False) or ["openvpn"]
        res = subprocess.run([cmd[0], "--version"], capture_output=True, text=True, timeout=2)
        match = re.search(r"OpenVPN\s+(\d+\.\d+)", res.stdout or res.stderr)
        if match:
            _openvpn_version = float(match.group(1))
            return _openvpn_version
    except Exception:
        pass
    _openvpn_version = 2.4
    return _openvpn_version

def openvpn_command(config_file: str, route_nopull: bool, dev: str = "tun0") -> list[str]:
    command = shlex.split(OPENVPN_CMD, posix=False) or ["openvpn"]
    command.extend(
        [
            "--config",
            config_file,
            "--dev",
            dev,
            "--dev-type",
            "tun",
            "--pull-filter",
            "ignore",
            "route-ipv6",
            "--pull-filter",
            "ignore",
            "ifconfig-ipv6",
            "--route-delay",
            "2",
            "--connect-retry-max",
            "1",
            "--connect-timeout",
            "15",
            "--auth-user-pass",
        ]
    )

    version = get_openvpn_version()
    if version >= 2.5:
        command.extend(["--data-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"])
    else:
        command.extend(["--ncp-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"])

    command.extend(["--verb", "3"])

    try:
        content = Path(config_file).read_text(encoding="utf-8", errors="replace")
        if vpn_utils.is_config_tcp(content):
            ptype, host, port = vpn_utils.get_upstream_proxy()
            if ptype == "socks" and host and port:
                command.extend(["--socks-proxy", host, str(port)])
            elif ptype == "http" and host and port:
                command.extend(["--http-proxy", host, str(port)])
    except Exception:
        pass

    if route_nopull:
        command.append("--route-nopull")
    return command

def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()

def kill_existing_openvpn_processes() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        subprocess.run(["pkill", "-f", "openvpn.*tun0"], capture_output=True, timeout=2)
        subprocess.run(["pkill", "-f", "openvpn.*vpngate_data"], capture_output=True, timeout=2)
        print("[Cleanup] Terminated existing AimiliVPN OpenVPN processes.", flush=True)
    except Exception as e:
        print(f"[Cleanup Error] Failed to kill existing OpenVPN processes: {e}", flush=True)

def kill_existing_xray_processes() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        subprocess.run(["pkill", "-f", "xray.*xray_config.json"], capture_output=True, timeout=2)
        print("[Cleanup] Terminated existing AimiliVPN Xray processes.", flush=True)
    except Exception as e:
        print(f"[Cleanup Error] Failed to kill existing Xray processes: {e}", flush=True)

def update_handshake_status(line_lower: str) -> None:
    status_map = {
        "resolving": ("解析域名", "正在解析服务器域名与 IP 地址..."),
        "udp link local": ("物理连接", "已创建本地套接字，开始尝试发送数据包..."),
        "tcp link local": ("物理连接", "已创建本地套接字，开始尝试发送数据包..."),
        "tls: initial packet": ("证书握手", "已成功发送首包，正在与远程服务器建立 TLS 安全通道..."),
        "verify ok": ("证书校验", "服务器证书校验成功，正在进行身份验证..."),
        "peer connection initiated": ("协商加密", "控制通道已建立，已初始化与服务器的加密对等连接..."),
        "push_request": ("请求配置", "正在向服务器发送 PUSH_REQUEST 请求配置参数与 IP 分配..."),
        "push_reply": ("应用配置", "已接收服务器 PUSH_REPLY，获取到 IP 分配，正在准备配置网卡..."),
        "tun/tap device": ("创建网卡", "正在创建虚拟通道并打开 TUN 虚拟网卡设备..."),
        "do_ifconfig": ("网卡配置", "正在为虚拟网卡配置 IP 地址及相关网络属性..."),
    }
    for key, (short_status, detailed_desc) in status_map.items():
        if key in line_lower:
            set_state(active_node_latency=short_status, last_check_message=detailed_desc)
            break

def run_openvpn_until_ready(config_file: str, keep_alive: bool, route_nopull: bool, timeout: int | None = None, dev: str = "tun0") -> tuple[bool, str, subprocess.Popen[str] | None]:
    limit = timeout if timeout is not None else OPENVPN_TEST_TIMEOUT_SECONDS
    command = openvpn_command(config_file, route_nopull, dev)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT_DIR),
        )
        if process.stdin:
            process.stdin.write(f"{OPENVPN_AUTH_USER}\n{OPENVPN_AUTH_PASS}\n")
            process.stdin.flush()
    except FileNotFoundError:
        log_to_json("ERROR", "VPN", f"OpenVPN 启动失败: 未找到命令 {command[0] if command else OPENVPN_CMD}")
        return False, "[错误代码 2001] [ERR_OVPN_CMD_NOT_FOUND] 未找到 openvpn 命令。原因: 系统未安装 openvpn，或 PATH 环境变量不正确。", None
    except OSError as exc:
        log_to_json("ERROR", "VPN", f"OpenVPN 启动失败: {exc}")
        return False, f"[错误代码 2002] [ERR_OVPN_START_FAILED] openvpn 启动失败: {exc}。原因: 系统权限不足或配置冲突。", None

    lines: queue.Queue[str | None] = queue.Queue()
    startup_done = [False]
    openvpn_logs: list[str] = []

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_str = line.rstrip()
            if not startup_done[0]:
                openvpn_logs.append(line_str)
                lines.put(line_str)
            else:
                if keep_alive:
                    print(f"[OpenVPN] {line_str}", flush=True)
                    level = "ERROR" if any(token in line_str.lower() for token in ["error", "failed", "cannot", "fatal", "permission denied"]) else "INFO"
                    log_to_json(level, "VPN", f"[OpenVPN] {line_str}")
        if not startup_done[0]:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    started = time.time()
    tail: list[str] = []
    ok = False
    message = "OpenVPN did not complete initialization."
    while time.time() - started < limit:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if line is None:
            break
        if line:
            tail.append(line)
            tail = tail[-50:]
            if keep_alive:
                print(f"[OpenVPN] {line}", flush=True)
        lower = line.lower()
        if keep_alive:
            update_handshake_status(lower)
            # Parse dynamic device name allocated by OpenVPN
            match = re.search(r"tun/tap device (\w+) opened", lower) or re.search(r"opened tun device (\w+)", lower)
            if match:
                allocated_dev = match.group(1)
                vpn_utils.ACTIVE_TUN_DEVICE = allocated_dev
                print(f"[OpenVPN] 解析到分配的实际网卡: {allocated_dev}", flush=True)
        if "initialization sequence completed" in lower:
            ok = True
            message = f"OpenVPN connected in {int((time.time() - started) * 1000)} ms."
            break
        if "auth_failed" in lower or "authentication failed" in lower:
            message = "AUTH_FAILED"
            break
        if "cannot ioctl" in lower or "fatal error" in lower:
            message = line[-220:]
            break
    else:
        message = f"OpenVPN timeout after {limit}s."

    if not ok:
        err_code, diag_msg = vpn_utils.diagnose_openvpn_failure(tail)
        message = f"[错误代码 {err_code}] {diag_msg} (原始日志尾部: {tail[-1][-100:] if tail else '无'})"
        raw_tail = " | ".join(tail[-12:]) if tail else "无"
        log_to_json("ERROR", "VPN", f"OpenVPN 不可用诊断: {diag_msg}")
        log_to_json("ERROR", "VPN", f"OpenVPN 原始日志尾部: {raw_tail}")
    else:
        log_to_json("INFO", "VPN", f"OpenVPN 初始化完成，设备: {dev}")
    for line_str in openvpn_logs:
        level = "ERROR" if any(token in line_str.lower() for token in ["error", "failed", "cannot", "fatal", "permission denied"]) else "INFO"
        log_to_json(level, "VPN", f"[OpenVPN] {line_str}")
    startup_done[0] = True
    if not keep_alive or not ok:
        stop_process(process)
        process = None
    return ok, message, process

def setup_policy_routing(interface: str = "tun0") -> None:
    try:
        subprocess.run(["ip", "rule", "del", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.run(["ip", "route", "flush", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass

    success = False
    for attempt in range(1, 4):
        try:
            subprocess.run(["ip", "route", "add", "default", "dev", interface, "table", "100"], check=True, timeout=2)
            subprocess.run(["ip", "rule", "add", "oif", interface, "table", "100"], check=True, timeout=2)
            for proc_path in ["all", "default", interface]:
                try:
                    subprocess.run(["sysctl", "-w", f"net.ipv4.conf.{proc_path}.rp_filter=2"], capture_output=True, timeout=2)
                except Exception:
                    pass
            print(f"[policy_routing] Enabled policy routing for interface {interface} (attempt {attempt} success)", flush=True)
            success = True
            break
        except Exception as e:
            print(f"[policy_routing] Attempt {attempt} failed to enable policy routing: {e}", flush=True)
            time.sleep(1)

    if not success:
        print("[路由配置失败] [错误代码 3003] [ERR_ROUTE_TABLE_ADD_FAILED] 策略路由配置失败。原因: 无法向路由表 100 添加默认路由，这可能会导致通过 VPN 接口的出站路由无法正常解析。请检查系统是否支持策略路由、iproute2 工具是否完整，以及是否具有 root 权限。", flush=True)
        log_to_json("ERROR", "Routing", "[错误代码 3003] [ERR_ROUTE_TABLE_ADD_FAILED] 策略路由配置失败。原因: 无法向路由表 100 添加默认路由")

def cleanup_policy_routing() -> None:
    try:
        subprocess.run(["ip", "rule", "del", "table", "100"], capture_output=True, timeout=2)
        subprocess.run(["ip", "route", "flush", "table", "100"], capture_output=True, timeout=2)
        print("[policy_routing] Cleared policy routing table 100", flush=True)
    except Exception:
        pass

def stop_active_openvpn(clear_connecting: bool = True, stop_xray_service: bool = False) -> None:
    from backend.app.core.xray import stop_xray, active_xray_running
    save_session_traffic_to_total()
    if stop_xray_service and active_xray_running():
        stop_xray()
    cleanup_policy_routing()
    config_to_delete = None
    if state.active_openvpn_node_id:
        nodes = read_json(NODES_FILE, [])
        node = next((item for item in nodes if item.get("id") == state.active_openvpn_node_id), None)
        if node:
            config_to_delete = node.get("config_file")

    stop_process(state.active_openvpn_process)
    state.active_openvpn_process = None
    state.active_openvpn_node_id = ""
    vpn_utils.ACTIVE_TUN_DEVICE = "tun0"
    if clear_connecting:
        state.is_connecting = False
    kill_existing_openvpn_processes()

    if config_to_delete:
        try:
            path = Path(config_to_delete)
            if path.exists():
                path.unlink()
        except Exception:
            pass

def active_openvpn_running() -> bool:
    return state.active_openvpn_process is not None and state.active_openvpn_process.poll() is None

def sort_all_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    available_nodes = sorted(
        [n for n in nodes if n.get("probe_status") == "available" or n.get("active")],
        key=lambda n: (parse_int(n.get("latency_ms")) or 999999, -parse_int(n.get("score")))
    )
    untested_nodes = sorted(
        [n for n in nodes if n.get("probe_status") == "not_checked" and not n.get("active")],
        key=lambda n: (-parse_int(n.get("score")), parse_int(n.get("ping")))
    )
    unavailable_nodes = sorted(
        [n for n in nodes if n.get("probe_status") == "unavailable" and not n.get("active")],
        key=lambda n: (-parse_int(n.get("score")), -float(n.get("probed_at", 0)))
    )
    return available_nodes + untested_nodes + unavailable_nodes

def get_free_test_index() -> int:
    with test_indexes_lock:
        for idx in range(2, 100):
            if idx not in active_test_indexes:
                active_test_indexes.add(idx)
                return idx
        return 99

def release_test_index(idx: int) -> None:
    with test_indexes_lock:
        active_test_indexes.discard(idx)

def test_node_by_id(node_id: str) -> dict[str, Any]:
    with state.lock:
        nodes = read_json(NODES_FILE, [])
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        config_file = str(node["config_file"])
        config_text = node.get("config_text") or ""
        h = str(node.get("remote_host") or node.get("ip"))
        p = parse_int(node.get("remote_port"))
        fallback_ping = parse_int(node.get("ping"))

    import uuid
    temp_config_file = str(Path(config_file).with_name(f"{node_id}_test_{uuid.uuid4().hex[:8]}.ovpn"))
    temp_path = Path(temp_config_file)
    try:
        CONFIG_DIR.mkdir(exist_ok=True, parents=True)
        temp_path.write_text(config_text, encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to write temp config file: {e}")

    latency = vpn_utils.ping_latency_ms(h, p, fallback_ping)

    idx = get_free_test_index()
    try:
        ok, message, _ = run_openvpn_until_ready(temp_config_file, keep_alive=False, route_nopull=True, timeout=12, dev=f"tun{idx}")
    finally:
        release_test_index(idx)

    try:
        if temp_path.exists():
            temp_path.unlink()
    except Exception:
        pass

    temp_node = {
        "id": node_id,
        "ip": h,
        "remote_host": h,
        "remote_port": p,
        "owner": "",
        "asn": "",
        "as_name": "",
        "location": "",
        "ip_type": "",
        "quality": "",
    }
    if ok:
        vpn_utils.enrich_ip_info([temp_node])

    with state.lock:
        nodes = read_json(NODES_FILE, [])
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if node:
            node["latency_ms"] = latency
            node["probe_status"] = "available" if ok else "unavailable"
            node["probe_message"] = message
            node["probed_at"] = time.time()
            if ok:
                node["owner"] = temp_node["owner"]
                node["asn"] = temp_node["asn"]
                node["as_name"] = temp_node["as_name"]
                node["location"] = temp_node["location"]
                node["ip_type"] = temp_node["ip_type"]
                node["quality"] = temp_node["quality"]
            else:
                mark_blacklisted(node, f"节点手动检测失败: {message}")

            sorted_nodes = sort_all_nodes(nodes)
            write_json(NODES_FILE, sorted_nodes)
            res = next((item for item in sorted_nodes if item.get("id") == node_id), node)
            return res
        else:
            return {}

def test_multiple_nodes(node_ids: list[str]) -> list[dict[str, Any]]:
    with state.lock:
        nodes = read_json(NODES_FILE, [])
        to_test = [n for n in nodes if n.get("id") in node_ids]

    def test_worker(args: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        idx, n_info = args
        node_id = n_info["id"]
        config_file = n_info["config_file"]
        config_text = n_info.get("config_text") or ""
        h = str(n_info.get("remote_host") or n_info.get("ip"))
        p = parse_int(n_info.get("remote_port"))
        fallback_ping = parse_int(n_info.get("ping"))

        import uuid
        temp_config_file = str(Path(config_file).with_name(f"{node_id}_test_{uuid.uuid4().hex[:8]}.ovpn"))
        temp_path = Path(temp_config_file)
        try:
            CONFIG_DIR.mkdir(exist_ok=True, parents=True)
            temp_path.write_text(config_text, encoding="utf-8")
        except Exception:
            pass

        latency = vpn_utils.ping_latency_ms(h, p, fallback_ping)
        tun_idx = get_free_test_index()
        dev_name = f"tun{tun_idx}"
        try:
            ok, message, _ = run_openvpn_until_ready(temp_config_file, keep_alive=False, route_nopull=True, timeout=12, dev=dev_name)
        finally:
            release_test_index(tun_idx)

        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

        temp_node = {
            "id": node_id,
            "latency_ms": latency,
            "probe_status": "available" if ok else "unavailable",
            "probe_message": message,
            "probed_at": time.time(),
            "owner": "",
            "asn": "",
            "as_name": "",
            "location": "",
            "ip_type": "",
            "quality": "",
        }
        if ok:
            ip_to_enrich = {
                "ip": n_info.get("ip"),
                "remote_host": h,
                "owner": "",
                "asn": "",
                "as_name": "",
                "location": "",
                "ip_type": "",
                "quality": "",
            }
            vpn_utils.enrich_ip_info([ip_to_enrich])
            temp_node.update(ip_to_enrich)
        return temp_node

    updated_nodes_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(to_test))) as executor:
        futures = {executor.submit(test_worker, (idx, n)): n["id"] for idx, n in enumerate(to_test)}
        for future in concurrent.futures.as_completed(futures):
            nid = futures[future]
            try:
                res = future.result()
                updated_nodes_map[nid] = res
            except Exception as e:
                updated_nodes_map[nid] = {
                    "id": nid,
                    "probe_status": "unavailable",
                    "probe_message": f"Test exception: {e}",
                    "latency_ms": 0
                }

    with state.lock:
        current_nodes = read_json(NODES_FILE, [])
        for n in current_nodes:
            nid = n.get("id")
            if nid in updated_nodes_map:
                n.update(updated_nodes_map[nid])
                if n.get("probe_status") == "unavailable":
                    mark_blacklisted(n, f"节点批量检测失败: {n.get('probe_message', '未知原因')}")
        sorted_nodes = sort_all_nodes(current_nodes)
        write_json(NODES_FILE, sorted_nodes)

    return list(updated_nodes_map.values())

def auto_switch_node(attempt: int = 0) -> None:
    if not state.openvpn_enabled:
        set_state(
            openvpn_enabled=False,
            is_connecting=False,
            active_node_latency="OpenVPN 未启动",
            last_check_message="OpenVPN 已停止，等待在网页中手动启动。"
        )
        return

    if attempt >= 3:
        print("[自动切换] 连续切换失败已达 3 次，停止切换以防止主线程死锁，将在后台重新加载节点...", flush=True)
        return

    ui_cfg = load_ui_config()
    routing_mode = ui_cfg.get("routing_mode", "auto")
    target_country = ui_cfg.get("force_country", "")

    if routing_mode == "fixed_ip":
        print("[自动切换] 当前处于固定 IP 模式，不进行自动切换。", flush=True)
        if state.active_openvpn_node_id:
            if not active_openvpn_running():
                print(f"[自动切换] 固定 IP 模式检测到连接已断开，尝试重新连接原节点: {state.active_openvpn_node_id}", flush=True)
                def reconnect_bg():
                    try:
                        connect_node(state.active_openvpn_node_id)
                    except Exception as e:
                        print(f"[自动切换] 重新连接固定节点失败: {e}", flush=True)
                threading.Thread(target=reconnect_bg, daemon=True).start()
        return

    with state.lock:
        nodes = read_json(NODES_FILE, [])
        candidates = [
            n for n in nodes
            if n.get("probe_status") == "available"
            and not n.get("active")
        ]

        if routing_mode == "fixed_region" and target_country:
            candidates = [n for n in candidates if n.get("country") == target_country]

        candidates.sort(key=lambda n: (parse_int(n.get("latency_ms")) or 999999, -parse_int(n.get("score"))))

    if candidates:
        next_node = candidates[0]
        msg = f"当前连接已失效或代理连通性检测失败，正在自动切换至最佳备用节点: {next_node['id']}"
        print(f"[自动切换] {msg}", flush=True)
        log_to_json("INFO", "VPN", msg)
        try:
            connect_node(next_node["id"])
        except Exception as e:
            err_msg = f"切换到备用节点 {next_node['id']} 失败: {e}，将尝试下一个..."
            print(f"[自动切换] {err_msg}", flush=True)
            log_to_json("WARNING", "VPN", err_msg)
            auto_switch_node(attempt + 1)
    else:
        msg = "没有可用的备选节点，将自动断开并清理当前连接状态，同时在后台异步获取新节点..."
        if routing_mode == "fixed_region" and target_country:
            msg = f"没有可用的【{target_country}】备选节点，已断开连接，将在后台持续尝试获取新节点..."
        print(f"[自动切换] {msg}", flush=True)
        log_to_json("WARNING", "VPN", msg)
        stop_active_openvpn()
        with state.lock:
            nodes = read_json(NODES_FILE, [])
            for item in nodes:
                item["active"] = False
            write_json(NODES_FILE, nodes)
        set_state(active_openvpn_node_id="", last_check_message=msg)

        def bg_fetch_and_switch():
            try:
                maintain_valid_nodes(force=False)
                auto_switch_node()
            except Exception as e:
                print(f"[自动切换后台补齐] 获取并测试节点失败: {e}", flush=True)

        threading.Thread(target=bg_fetch_and_switch, daemon=True).start()

def connect_node(node_id: str) -> str:
    from backend.app.core.xray import start_xray, load_xray_cfg
    import backend.app.core.xray as xray_module
    with state.lock:
        if state.is_connecting:
            print("[连接] 正在建立其他连接中，跳过此请求", flush=True)
            return "Already connecting"
        state.openvpn_enabled = True
        state.is_connecting = True
        state.active_openvpn_node_id = node_id
        set_state(openvpn_enabled=True, active_openvpn_node_id=node_id, is_connecting=True, active_node_latency="正在连接", last_check_message="正在初始化连接配置...")

    try:
        log_to_json("INFO", "VPN", f"开始连接节点: {node_id}")
        nodes = read_json(NODES_FILE, [])
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        if not state.openvpn_enabled:
            raise RuntimeError("OpenVPN 已停止")

        set_state(active_node_latency="清理连接", last_check_message="正在关闭与清理旧的 VPN 连接及网卡...")
        stop_active_openvpn(clear_connecting=False)
        if not state.openvpn_enabled:
            raise RuntimeError("OpenVPN 已停止")

        set_state(active_node_latency="写入配置", last_check_message="正在写入 OpenVPN 节点配置文件...")
        config_path = Path(node["config_file"])
        try:
            CONFIG_DIR.mkdir(exist_ok=True, parents=True)
            config_path.write_text(node.get("config_text") or "", encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to write configuration: {e}")

        set_state(active_node_latency="启动核心", last_check_message="正在启动 OpenVPN Core 核心服务并建立连接...")
        ok, message, process = run_openvpn_until_ready(str(node["config_file"]), keep_alive=True, route_nopull=True)
        if process is not None and not state.openvpn_enabled:
            stop_process(process)
            raise RuntimeError("OpenVPN 已停止")
        if not ok or process is None:
            try:
                if config_path.exists():
                    config_path.unlink()
            except Exception:
                pass
            node["probe_status"] = "unavailable"
            node["probe_message"] = message
            for item in nodes:
                item["active"] = False
            write_json(NODES_FILE, nodes)
            mark_blacklisted(node, f"OpenVPN 启动或握手失败: {message}")
            log_to_json("ERROR", "VPN", f"连接节点 {node_id} 失败: {message}")
            print(f"[连接核心失败] 无法与 VPN 节点 {node_id} 建立隧道连接！详情: {message}", flush=True)
            set_state(active_openvpn_node_id="", is_connecting=False, active_node_latency="无活动连接", last_check_message=f"连接失败: {message}")
            with state.lock:
                state.active_openvpn_node_id = ""
            raise RuntimeError(message)

        state.active_openvpn_process = process
        state.active_openvpn_node_id = node_id
        if not state.openvpn_enabled:
            stop_active_openvpn(clear_connecting=False)
            raise RuntimeError("OpenVPN 已停止")

        set_state(active_node_latency="配置路由", last_check_message="正在配置策略路由规则与流量转发...")
        active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
        setup_policy_routing(active_dev)
        record_session_traffic_start(active_dev)

        state.last_active_ping_time = time.time()
        state.last_active_latency = 0

        set_state(active_node_latency="测试延迟", last_check_message="正在直连测试代理出口延迟与可用性...")
        try:
            ip = node.get("ip") or node.get("remote_host")
            port = parse_int(node.get("remote_port"))
            fallback = parse_int(node.get("ping"))
            latency = vpn_utils.ping_latency_ms(ip, port, fallback)
            if latency > 0:
                state.last_active_latency = latency
        except Exception:
            pass

        for item in nodes:
            item["active"] = item.get("id") == node_id
            if item["active"]:
                _ph = f"[{LOCAL_PROXY_HOST}]" if ":" in LOCAL_PROXY_HOST else LOCAL_PROXY_HOST
                item["probe_message"] = f"Active node. HTTP proxy: http://{_ph}:{LOCAL_PROXY_PORT}"
        write_json(NODES_FILE, nodes)

        set_state(last_check_message="正在测试本地代理出站联通性与出口 IP...")
        res = check_proxy_health()
        if res["ok"]:
            set_state(
                proxy_ok=True,
                proxy_ip=res["ip"],
                proxy_latency_ms=res["latency_ms"],
                proxy_error=""
            )
        else:
            set_state(
                proxy_ok=False,
                proxy_ip="-",
                proxy_latency_ms=0,
                proxy_error=res.get("error", "未知错误")
            )

        latency_str = f"{state.last_active_latency} ms" if state.last_active_latency > 0 else "检测超时"
        xray_cfg = load_xray_cfg()
        xray_msg = ""
        if xray_cfg.get("enabled", False):
            set_state(active_node_latency="启动 Xray", last_check_message="OpenVPN 已连接，正在启动 Xray 入站代理...")
            if start_xray():
                xray_msg = "，Xray 入站已启动"
            else:
                xray_msg = f"，但 Xray 启动失败: {state.xray_last_error or '未知错误'}"
                log_to_json("ERROR", "Xray", f"OpenVPN 已连接但 Xray 启动失败: {state.xray_last_error or '未知错误'}")

        set_state(active_openvpn_node_id=node_id, is_connecting=False, last_check_message=f"Connected {node_id}{xray_msg}", active_node_latency=latency_str)
        log_to_json("INFO", "VPN", f"节点 {node_id} 连接成功，出口网卡 tun0 已启用")
        return f"Connected {node_id}"
    finally:
        with state.lock:
            state.is_connecting = False

def maintain_valid_nodes(force: bool = False) -> str:
    ensure_dirs()
    flags = load_feature_flags()
    if not flags.get("vpngate_enabled", False):
        if active_openvpn_running() or state.active_openvpn_node_id:
            stop_active_openvpn()
            mark_all_vpngate_nodes_inactive()
        state.is_connecting = False
        set_state(
            openvpn_enabled=False,
            active_openvpn_node_id="",
            is_connecting=False,
            last_check_message="VPNGate 公益节点功能未开启，未加载节点资源。",
            active_node_latency="VPNGate 未开启",
            proxy_ok=False,
            proxy_ip="-",
            proxy_latency_ms=0,
            proxy_error="VPNGate 功能未开启",
        )
        return "VPNGate 公益节点功能未开启"
    vpn_allowed = state.openvpn_enabled
    state.is_connecting = bool(vpn_allowed)
    try:
        if vpn_allowed:
            if force:
                with state.lock:
                    stop_active_openvpn(clear_connecting=False)
            elif not active_openvpn_running():
                has_active_id = False
                with state.lock:
                    if state.active_openvpn_node_id:
                        has_active_id = True
                        stop_active_openvpn(clear_connecting=False)
                if has_active_id:
                    print("[维护线程] 检测到当前 OpenVPN 进程已意外退出，准备自动切换节点", flush=True)
                    state.is_connecting = False
                    auto_switch_node()
                    state.is_connecting = bool(state.openvpn_enabled)
        elif active_openvpn_running() or state.active_openvpn_node_id:
            stop_active_openvpn()
            with state.lock:
                nodes = read_json(NODES_FILE, [])
                for item in nodes:
                    item["active"] = False
                write_json(NODES_FILE, nodes)

        try:
            fetch_message = "正在拉取最新的免费 VPN 节点列表..." if vpn_allowed else "正在同步 VPNGate 节点，OpenVPN 保持手动停止。"
            set_state(is_connecting=bool(vpn_allowed), last_check_message=fetch_message)
            candidates = fetch_candidates()
        except Exception as exc:
            vpn_utils.check_and_fix_dns()
            diag_msg = str(exc)
            if not any(token in diag_msg for token in ["[ERR_", "错误代码"]):
                err_code, raw_diag = vpn_utils.diagnose_api_failure(API_URL)
                diag_msg = f"[错误代码 {err_code}] 获取节点失败: {exc} | 诊断结果: {raw_diag}"
            set_state(last_fetch_at=time.time(), last_fetch_status="error", last_fetch_message=diag_msg)
            candidates = []

        if not candidates:
            state.is_connecting = False
            set_state(is_connecting=False, last_check_message="没有拉取到新节点，OpenVPN 未启动。" if not state.openvpn_enabled else "没有拉取到新节点")
            return "没有拉取到新节点"

        with state.lock:
            active_node = None
            if state.active_openvpn_node_id:
                current_nodes = read_json(NODES_FILE, [])
                active_node = next((n for n in current_nodes if n.get("id") == state.active_openvpn_node_id), None)

            merged: list[dict[str, Any]] = []
            seen_ids: set[str] = set()

            if active_node:
                merged.append(active_node)
                seen_ids.add(active_node["id"])

            for cand in candidates:
                if cand["id"] not in seen_ids:
                    merged.append(cand)
                    seen_ids.add(cand["id"])

            if len(merged) > 1000:
                merged = merged[:1000]

            for n in merged:
                config_path = Path(n["config_file"])
                if not config_path.exists():
                    try:
                        config_path.write_text(n["config_text"], encoding="utf-8")
                    except Exception:
                        pass

            write_json(NODES_FILE, merged)

        with state.lock:
            current_nodes = read_json(NODES_FILE, [])
            ui_cfg = load_ui_config()
            routing_mode = ui_cfg.get("routing_mode", "auto")
            target_country = ui_cfg.get("force_country", "")

            if routing_mode == "fixed_region" and target_country:
                to_test = [n for n in current_nodes if not n.get("active") and n.get("country") == target_country][:10]
            else:
                to_test = [n for n in current_nodes if not n.get("active")][:10]

            to_test_ids = [n["id"] for n in to_test]

        print(f"[维护线程] 正在检测新获取列表的前 10 个节点: {to_test_ids}", flush=True)
        set_state(is_connecting=bool(vpn_allowed), last_check_message="正在并发检测筛选可用节点，这可能需要 5-30 秒...")
        test_multiple_nodes(to_test_ids)

        state.is_connecting = False
        should_auto_switch = False

        with state.lock:
            merged = read_json(NODES_FILE, [])
            if state.openvpn_enabled and not active_openvpn_running():
                ui_cfg = load_ui_config()
                routing_mode = ui_cfg.get("routing_mode", "auto")
                target_country = ui_cfg.get("force_country", "")

                if routing_mode == "fixed_ip":
                    if state.active_openvpn_node_id:
                        auto_switch_node()
                else:
                    available_candidates = [n for n in merged if n.get("probe_status") == "available"]
                    if routing_mode == "fixed_region" and target_country:
                        available_candidates = [n for n in available_candidates if n.get("country") == target_country]

                    if available_candidates:
                        should_auto_switch = True

        if should_auto_switch:
            auto_switch_node()

        valid_nodes_count = len([n for n in merged if n.get("probe_status") == "available"])
        message = f"Fetched {len(candidates)} nodes. Tested first 10 nodes."
        if not state.openvpn_enabled:
            message += " OpenVPN is stopped and waiting for manual start."
        set_state(
            last_check_at=time.time(),
            last_check_message=message,
            active_openvpn_node_id=state.active_openvpn_node_id,
            openvpn_enabled=state.openvpn_enabled,
            is_connecting=False,
            valid_nodes=valid_nodes_count,
        )
        return message
    except Exception as e:
        state.is_connecting = False
        raise e
    finally:
        state.is_connecting = False

def mark_all_vpngate_nodes_inactive() -> None:
    with state.lock:
        nodes = read_json(NODES_FILE, [])
        for item in nodes:
            item["active"] = False
        write_json(NODES_FILE, nodes)

def start_openvpn_service() -> str:
    if not load_feature_flags().get("vpngate_enabled", False):
        return "VPNGate 公益节点功能未开启，请先打开功能开关"
    with state.lock:
        state.openvpn_enabled = True
        state.is_connecting = True
    set_state(
        openvpn_enabled=True,
        is_connecting=True,
        last_check_message="OpenVPN 已启用，正在选择可用 VPNGate 节点...",
        active_node_latency="正在准备"
    )

    if active_openvpn_running():
        state.is_connecting = False
        set_state(is_connecting=False, last_check_message="OpenVPN 已在运行中。")
        return "OpenVPN 已在运行中"

    def bg_start() -> None:
        try:
            maintain_valid_nodes(force=False)
            if state.openvpn_enabled and not active_openvpn_running():
                auto_switch_node()
        except Exception as exc:
            state.is_connecting = False
            set_state(
                is_connecting=False,
                last_check_message=f"OpenVPN 启动失败: {exc}",
                active_node_latency="启动失败"
            )
            log_to_json("ERROR", "VPN", f"OpenVPN 手动启动失败: {exc}")

    threading.Thread(target=bg_start, daemon=True).start()
    return "OpenVPN 已启用，正在后台选择节点"

def stop_openvpn_service(message: str = "手动停止 OpenVPN") -> None:
    with state.lock:
        state.openvpn_enabled = False
        state.is_connecting = False
    stop_active_openvpn()
    mark_all_vpngate_nodes_inactive()
    state.last_active_ping_time = 0.0
    state.last_active_latency = 0
    set_state(
        openvpn_enabled=False,
        active_openvpn_node_id="",
        is_connecting=False,
        last_check_message=message,
        active_node_latency="OpenVPN 未启动",
        proxy_ok=False,
        proxy_ip="-",
        proxy_latency_ms=0,
        proxy_error="OpenVPN 未启动"
    )
    log_to_json("INFO", "VPN", message)

def ensure_xray_default_start_config() -> None:
    from backend.app.core.xray import load_xray_cfg, save_xray_cfg, xray_event
    cfg = load_xray_cfg()
    changed = False
    if not cfg.get("enabled", False):
        cfg["enabled"] = True
        changed = True
    if cfg.get("require_vpn", False):
        cfg["require_vpn"] = False
        changed = True
    if changed:
        saved, err = save_xray_cfg(cfg)
        if not saved:
            xray_event("ERROR", err or "写入 Xray 默认启动配置失败")

def check_proxy_health() -> dict[str, Any]:
    is_ipv6 = ":" in LOCAL_PROXY_HOST
    af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
    s = socket.socket(af, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        connect_host = LOCAL_PROXY_HOST
        if connect_host in ("::", "0.0.0.0", ""):
            connect_host = "::1" if is_ipv6 else "127.0.0.1"
        try:
            s.connect((connect_host, LOCAL_PROXY_PORT))
        except Exception as e:
            if connect_host == "::1":
                s.close()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                s.connect(("127.0.0.1", LOCAL_PROXY_PORT))
            else:
                raise e
    except Exception as e:
        diag = vpn_utils.diagnose_local_obstructions(LOCAL_PROXY_PORT, host=LOCAL_PROXY_HOST)
        diag_msg = diag[1] if diag else f"端口 {LOCAL_PROXY_PORT} 连接失败，原因: {e}"
        return {
            "ok": False,
            "error": f"代理服务未运行 ({diag_msg})"
        }
    finally:
        try:
            s.close()
        except Exception:
            pass

    active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
    tun_path = Path(f"/sys/class/net/{active_dev}")
    if sys.platform.startswith("linux") and not tun_path.exists():
        return {
            "ok": False,
            "error": f"[错误代码 3004] [ERR_ROUTE_DEV_NOT_FOUND] VPN 虚拟网卡 ({active_dev}) 未启用，请确保当前已成功连接 VPN 节点"
        }

    def _curl_check_ip(url: str) -> dict[str, Any] | None:
        proxy_hosts = []
        if LOCAL_PROXY_HOST == "::":
            proxy_hosts = ["[::1]", "127.0.0.1"]
        elif LOCAL_PROXY_HOST == "0.0.0.0":
            proxy_hosts = ["127.0.0.1"]
        elif ":" in LOCAL_PROXY_HOST:
            proxy_hosts = [f"[{LOCAL_PROXY_HOST}]", "127.0.0.1"]
        else:
            proxy_hosts = [LOCAL_PROXY_HOST]

        for p_host in proxy_hosts:
            proxy_url = f"socks5h://{p_host}:{LOCAL_PROXY_PORT}"
            cmd = [
                "curl", "-s",
                "-w", "\n%{time_total} %{http_code}",
                "-x", proxy_url,
                url,
                "--max-time", "5"
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                if res.returncode == 0:
                    lines = res.stdout.strip().splitlines()
                    if len(lines) >= 2:
                        ip = lines[0].strip()
                        time_info = lines[1].strip().split()
                        if len(time_info) == 2:
                            total_time_str, http_code = time_info
                            if http_code == "200" and ip:
                                latency_ms = int(float(total_time_str) * 1000)
                                return {"ok": True, "ip": ip, "latency_ms": latency_ms}
            except Exception:
                pass
        return None

    try:
        result = _curl_check_ip("http://ip.sb")
        if result:
            return result
        result = _curl_check_ip("http://api.ipify.org")
        if result:
            return result

        diag = vpn_utils.diagnose_local_obstructions(LOCAL_PROXY_PORT, host=LOCAL_PROXY_HOST)
        if diag:
            return {"ok": False, "error": f"出口连接测试失败 | 本机诊断结果: {diag[1]}"}

        return {"ok": False, "error": "出口连接测试失败 (ip.sb 和 api.ipify.org 均无法连通，可能是节点已失效或 VPS 防火墙限制了 UDP/TCP 出站端口)"}
    except Exception as e:
        return {"ok": False, "error": f"出口连接测试异常: {e}"}

def test_socks5_exit(dns_local: bool, test_https: bool = False) -> tuple[bool, str]:
    import ssl
    is_ipv6 = ":" in LOCAL_PROXY_HOST
    af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
    s = socket.socket(af, socket.SOCK_STREAM)
    s.settimeout(4.0)
    try:
        connect_host = LOCAL_PROXY_HOST
        if connect_host in ("::", "0.0.0.0", ""):
            connect_host = "::1" if is_ipv6 else "127.0.0.1"
        try:
            s.connect((connect_host, LOCAL_PROXY_PORT))
        except Exception:
            if connect_host == "::1":
                s.close()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(4.0)
                s.connect(("127.0.0.1", LOCAL_PROXY_PORT))
            else:
                raise
        
        # SOCKS5 Greeting
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        if len(resp) < 2 or resp[0] != 5 or resp[1] != 0:
            return False, "SOCKS5 handshake greeting failed"
        
        target_domain = "api.ipify.org"
        target_port = 443 if test_https else 80
        
        if dns_local:
            try:
                addr_info = socket.getaddrinfo(target_domain, target_port, socket.AF_INET, socket.SOCK_STREAM)
                ip = addr_info[0][4][0]
            except Exception as e:
                return False, f"Local DNS resolution failed: {e}"
            ip_bytes = socket.inet_aton(ip)
            req = b"\x05\x01\x00\x01" + ip_bytes + target_port.to_bytes(2, "big")
        else:
            domain_bytes = target_domain.encode('ascii')
            req = b"\x05\x01\x00\x03" + bytes([len(domain_bytes)]) + domain_bytes + target_port.to_bytes(2, "big")
            
        s.sendall(req)
        resp_header = s.recv(4)
        if len(resp_header) < 4 or resp_header[0] != 5 or resp_header[1] != 0:
            return False, f"SOCKS5 connection request failed (code: {resp_header[1] if len(resp_header) >= 2 else 'unknown'})"
        
        # Consume bind address
        atyp = resp_header[3]
        if atyp == 1:
            s.recv(6)
        elif atyp == 3:
            addr_len = s.recv(1)[0]
            s.recv(addr_len + 2)
        elif atyp == 4:
            s.recv(18)
        
        if test_https:
            try:
                ctx = ssl._create_unverified_context()
                ssl_sock = ctx.wrap_socket(s, server_hostname=target_domain)
                ssl_sock.close()
            except Exception as e:
                return False, f"TLS_INTERFERENCE: {e}"
        else:
            s.close()
            
        return True, "Success"
    except Exception as e:
        return False, str(e)

def check_layered_health() -> dict[str, Any]:
    # 1. API Connectivity (api_connectivity)
    api_ok = False
    api_details = "连接正常"
    api_err_code = 0
    try:
        parsed = urllib.parse.urlsplit(API_URL)
        domain = parsed.hostname or "www.vpngate.net"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        socket.getaddrinfo(domain, port, 0, socket.SOCK_STREAM)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.5)
        s.connect((domain, port))
        s.close()
        api_ok = True
    except Exception:
        err_code, diag = vpn_utils.diagnose_api_failure(API_URL)
        api_err_code = err_code
        api_details = diag
        
    # 2. Node Pool Ratio (node_pool)
    np_ok = False
    np_avail = 0
    np_total = 0
    np_ratio = 0.0
    np_details = ""
    try:
        nodes = read_json(NODES_FILE, [])
        np_total = len(nodes)
        np_avail = sum(1 for n in nodes if n.get("probe_status") == "available")
        if np_total > 0:
            np_ratio = np_avail / np_total
            if np_avail > 0:
                np_ok = True
                np_details = f"正常连通，可用率 {np_ratio:.1%} ({np_avail}/{np_total})"
            elif any((n.get("probe_status") or "not_checked") == "not_checked" for n in nodes):
                np_details = "节点池已有备选节点，但尚未完成可用性检测，请点击「检测」或「同步节点」刷新状态"
            else:
                recent_messages = [
                    str(n.get("probe_message") or "").strip()
                    for n in nodes[:8]
                    if str(n.get("probe_message") or "").strip()
                ]
                suffix = f" 最近原因：{recent_messages[0][:160]}" if recent_messages else ""
                np_details = f"节点池内所有备选节点检测为不可用，请同步节点或检查 OpenVPN/TUN 权限配置。{suffix}"
        else:
            np_details = "节点池为空，请点击主页的「同步节点」获取备选服务器"
    except Exception as e:
        np_details = f"读取节点池异常: {e}"

    # 3. OpenVPN Interface (openvpn_interface)
    ovpn_ok = False
    ovpn_details = "未启动"
    ovpn_err_type = ""
    active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
    is_linux = sys.platform.startswith("linux")
    
    if active_openvpn_running():
        if is_linux:
            if Path(f"/sys/class/net/{active_dev}").exists():
                ovpn_ok = True
                ovpn_details = f"网卡 {active_dev} 已启用且正常工作"
            else:
                if not Path("/dev/net/tun").exists():
                    ovpn_err_type = "TUN_DRIVER_MISSING"
                    ovpn_details = "TUN 驱动缺失。系统 /dev/net/tun 设备不存在，可能内核未加载 tun 模块或 Docker 容器缺少 --device=/dev/net/tun 设备挂载与 NET_ADMIN 权限。"
                else:
                    ovpn_err_type = "NOT_CONNECTED"
                    ovpn_details = f"虚拟网卡 {active_dev} 未就绪。OpenVPN 进程在运行，但未建立隧道连接。请检查日志确定是否账号认证失败或协商超时。"
        else:
            ovpn_ok = True
            ovpn_details = "非 Linux 系统，免检网卡，OpenVPN 进程运行正常"
    else:
        ovpn_err_type = "SERVICE_NOT_RUNNING"
        ovpn_details = "OpenVPN 连接未启动"

    # 4. Policy Routing Health (policy_routing)
    pr_ok = False
    pr_details = "正常"
    pr_err_type = ""
    if is_linux:
        if not active_openvpn_running():
            pr_ok = True
            pr_details = "OpenVPN 未连接，策略路由暂不需要检查；连接成功后系统会自动配置 table 100。"
        else:
            has_table_100 = False
            try:
                res = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and ("100" in res.stdout or "lookup 100" in res.stdout):
                    has_table_100 = True
            except Exception:
                pass
            
            rp_strict = False
            rp_val = "0"
            for p in ["/proc/sys/net/ipv4/conf/all/rp_filter", "/proc/sys/net/ipv4/conf/default/rp_filter"]:
                rp_path = Path(p)
                if rp_path.exists():
                    try:
                        val = rp_path.read_text(encoding="utf-8").strip()
                        if val == "1":
                            rp_strict = True
                            rp_val = val
                    except Exception:
                        pass
                    
            if not has_table_100:
                pr_err_type = "TABLE_100_MISSING"
                pr_details = "策略路由规则缺失。系统路由表中找不到 table 100 策略规则，流量无法分流至 VPN 网卡，请尝试重启服务以自动配置路由。"
            elif rp_strict:
                pr_err_type = "RP_FILTER_STRICT"
                pr_details = f"反向路径过滤 rp_filter 处于严格模式({rp_val})。这会导致通过 tun 网卡的回包被内核判定为非对称路由而被静默丢弃，请将 net.ipv4.conf.all.rp_filter 设为 2 或 0。"
            else:
                pr_ok = True
                pr_details = "策略路由表与反向过滤策略配置正确"
    else:
        pr_ok = True
        pr_details = "非 Linux 系统，无需配置策略路由"

    # 5. Local Proxy Connectivity (local_proxy)
    lp_ok = False
    lp_details = "未检测"
    lp_err_type = ""
    
    is_ipv6 = ":" in LOCAL_PROXY_HOST
    af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
    s = socket.socket(af, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        connect_host = LOCAL_PROXY_HOST
        if connect_host in ("::", "0.0.0.0", ""):
            connect_host = "::1" if is_ipv6 else "127.0.0.1"
        try:
            s.connect((connect_host, LOCAL_PROXY_PORT))
            connected = True
        except Exception:
            if connect_host == "::1":
                s.close()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                s.connect(("127.0.0.1", LOCAL_PROXY_PORT))
                connected = True
            else:
                raise
    except Exception:
        connected = False
    finally:
        try:
            s.close()
        except Exception:
            pass
            
    if not connected:
        s = socket.socket(af, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((LOCAL_PROXY_HOST, LOCAL_PROXY_PORT))
            occupied = False
        except OSError:
            occupied = True
        finally:
            try:
                s.close()
            except Exception:
                pass
        if occupied:
            lp_err_type = "PORT_COLLISION"
            lp_details = f"端口占用。本地代理端口 {LOCAL_PROXY_PORT} 已被其他程序强行占领，导致 Xray 网关服务无法监听该端口，请使用 lsof 释放端口。"
        else:
            lp_err_type = "XRAY_NOT_RUNNING"
            lp_details = f"代理服务未运行。Xray 监听端口 {LOCAL_PROXY_PORT} 未开启且未被占用，可能是服务被停止或异常崩溃。"
    else:
        s5h_ok, s5h_err = test_socks5_exit(dns_local=False, test_https=False)
        if s5h_ok:
            s5_ok, s5_err = test_socks5_exit(dns_local=True, test_https=False)
            tls_ok, tls_err = test_socks5_exit(dns_local=False, test_https=True)
            
            if not s5_ok:
                lp_err_type = "DNS_POLLUTION"
                lp_details = f"DNS 污染。本地 DNS 无法解析或返回了被污染的 IP ({s5_err})。但通过代理网关的远程 DNS 解析能正常访问。建议为系统配置干净的 DNS。"
            elif not tls_ok:
                lp_err_type = "TLS_INTERFERENCE"
                lp_details = f"TLS 干扰。代理网关建立 TCP 连接成功，但 TLS 安全握手被断开或超时 ({tls_err})，表明当前节点的 TLS 证书特征正遭到防火墙审查或干扰。"
            else:
                lp_ok = True
                res_check = check_proxy_health()
                if res_check["ok"]:
                    lp_details = f"连通性正常，延迟 {res_check.get('latency_ms', 0)} ms，出口 IP: {res_check.get('ip', '-')}"
                else:
                    lp_details = "连通性正常，出口测试完成"
        else:
            lp_err_type = "NODE_DOWN"
            lp_details = f"出口连通性失败。当前代理节点无法穿透或已失效。错误详情: {s5h_err}"

    return {
        "ok": api_ok and np_ok and ovpn_ok and pr_ok and lp_ok,
        "api_connectivity": {"ok": api_ok, "details": api_details, "error_code": api_err_code},
        "node_pool": {"ok": np_ok, "details": np_details, "avail": np_avail, "total": np_total, "ratio": np_ratio},
        "openvpn_interface": {"ok": ovpn_ok, "details": ovpn_details, "error_type": ovpn_err_type},
        "policy_routing": {"ok": pr_ok, "details": pr_details, "error_type": pr_err_type},
        "local_proxy": {"ok": lp_ok, "details": lp_details, "error_type": lp_err_type}
    }

def background_proxy_checker() -> None:
    from backend.app.core.xray import active_xray_running, query_xray_client_stats, update_and_accumulate_client_traffic, enforce_client_quotas, load_xray_cfg, start_xray
    time.sleep(30)
    while True:
        state.last_checker_heartbeat = time.time()
        try:
            stats_data = load_traffic_stats()
            active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
            tun_rx, tun_tx = get_tun_stats(active_dev)
            session_rx = max(0, tun_rx - state.session_rx_start) if active_openvpn_running() else 0
            session_tx = max(0, tun_tx - state.session_tx_start) if active_openvpn_running() else 0
            total_bytes = stats_data.get("accumulated_rx", 0) + stats_data.get("accumulated_tx", 0) + session_rx + session_tx
            record_hourly_traffic(total_bytes)
        except Exception as te:
            print(f"[错误] 记录流量趋势异常: {te}", flush=True)

        try:
            if active_xray_running():
                current_stats = query_xray_client_stats()
                traffic = update_and_accumulate_client_traffic(current_stats)
                enforce_client_quotas(traffic)
        except Exception as xe:
            print(f"[错误] Xray 流量统计与配额执行异常: {xe}", flush=True)

        try:
            if not state.openvpn_enabled and not active_openvpn_running():
                set_state(
                    proxy_ok=False,
                    proxy_ip="-",
                    proxy_latency_ms=0,
                    proxy_error="OpenVPN 未启动"
                )
                time.sleep(30)
                continue

            if state.is_connecting:
                time.sleep(5)
                continue

            res = check_proxy_health()
            if res["ok"]:
                set_state(
                    proxy_ok=True,
                    proxy_ip=res["ip"],
                    proxy_latency_ms=res["latency_ms"],
                    proxy_error=""
                )
                log_to_json("INFO", "Proxy", f"代理可用，IP: {res['ip']}, 延迟: {res['latency_ms']} ms")
                xray_cfg = load_xray_cfg()
                if xray_cfg.get("enabled", False) and not active_xray_running():
                    if start_xray():
                        log_to_json("INFO", "Xray", "检测到 VPN 出口可用，已自动恢复 Xray 入站服务")
            else:
                error_msg = res.get("error", "未知错误")
                if state.active_openvpn_node_id:
                    print(f"[警告] {LOCAL_PROXY_PORT} 端口本地代理当前不可用！原因: {error_msg}", flush=True)
                    log_to_json("WARNING", "Proxy", f"代理不可用: {error_msg}")
                set_state(
                    proxy_ok=False,
                    proxy_ip="-",
                    proxy_latency_ms=0,
                    proxy_error=error_msg
                )

                if state.openvpn_enabled and state.active_openvpn_node_id:
                    with state.lock:
                        nodes = read_json(NODES_FILE, [])
                        active_node = next((n for n in nodes if n.get("id") == state.active_openvpn_node_id), None)
                        if active_node:
                            mark_blacklisted(active_node, f"代理连通性检测失败: {error_msg}")
                            active_node["probe_status"] = "unavailable"
                            write_json(NODES_FILE, nodes)

                    auto_switch_node()
        except Exception as e:
            print(f"[错误] 代理后台检测发生异常: {e}", flush=True)
            log_to_json("ERROR", "Proxy", f"检测守护线程发生异常: {e}")
        time.sleep(30)

def active_node_pinger() -> None:
    while True:
        state.last_pinger_heartbeat = time.time()
        try:
            if active_openvpn_running() and state.active_openvpn_node_id:
                nodes = read_json(NODES_FILE, [])
                node = next((n for n in nodes if n.get("id") == state.active_openvpn_node_id), None)
                if node:
                    ip = node.get("ip") or node.get("remote_host")
                    port = parse_int(node.get("remote_port"))
                    fallback = parse_int(node.get("ping"))
                    if ip:
                        latency = vpn_utils.ping_latency_ms(ip, port, fallback)
                        if latency > 0:
                            set_state(active_node_latency=f"{latency} ms")
                        else:
                            set_state(active_node_latency="检测超时")
                    else:
                        set_state(active_node_latency="检测超时")
                else:
                    set_state(active_node_latency="检测超时")
            elif state.is_connecting:
                set_state(active_node_latency="测试中...")
            elif not state.openvpn_enabled:
                set_state(active_node_latency="OpenVPN 未启动")
            else:
                set_state(active_node_latency="无活动连接")
        except Exception as e:
            print(f"[ERROR] active_node_pinger error: {e}", flush=True)
        time.sleep(10)

def collector_loop() -> None:
    while True:
        state.last_collector_heartbeat = time.time()
        success = False
        try:
            if load_feature_flags().get("vpngate_enabled", False):
                res = maintain_valid_nodes(force=False)
                if "没有拉取到新节点" not in res:
                    success = True
            else:
                set_state(last_check_message="VPNGate 公益节点功能未开启，后台同步已暂停。")
        except Exception as exc:
            set_state(last_check_at=time.time(), last_check_message=f"check error: {exc}")

        if not active_openvpn_running() and not success:
            sleep_time = 30
        else:
            sleep_time = CHECK_INTERVAL_SECONDS

        time.sleep(sleep_time)
