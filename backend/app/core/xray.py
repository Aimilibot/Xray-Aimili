from __future__ import annotations
import os
import sys
import time
import uuid
import shutil
import json
import subprocess
import threading
import shlex
import base64
import re
import socket
import urllib.request
import urllib.parse
import urllib.error
import ipaddress
from pathlib import Path
from typing import Any
from http import HTTPStatus

from backend.app import state
from backend.app.config import (
    ROOT_DIR, DATA_DIR, CONFIG_DIR, XRAY_CFG_FILE, XRAY_CONFIG_FILE,
    SUBSCRIPTION_NODES_FILE, SUBSCRIPTION_LINKS_FILE, OUTBOUND_NODES_FILE,
    ROUTING_RULES_FILE, ALLOWED_SUBSCRIPTION_PROTOCOLS, ALLOWED_OUTBOUND_TYPES,
    VPNGATE_ONLY_MODE, SERVICE_MODE, PANEL_MENUS
)
from backend.app.db import (
    read_json, write_json, read_json_list, log_to_json,
    load_client_traffic, save_client_traffic, load_ui_config,
    ensure_panel_framework_files, current_timestamp
)

def check_xray_installed() -> bool:
    if shutil.which("xray") is not None:
        return True
    for p in [
        "/usr/local/bin/xray",
        "/usr/bin/xray",
        "/bin/xray",
        "/usr/local/sbin/xray",
        "/usr/sbin/xray",
        "/sbin/xray",
    ]:
        if os.path.exists(p):
            return True
    return False

def xray_binary_path() -> str | None:
    path = shutil.which("xray")
    if path:
        return path
    for p in [
        "/usr/local/bin/xray",
        "/usr/bin/xray",
        "/bin/xray",
        "/usr/local/sbin/xray",
        "/usr/sbin/xray",
        "/sbin/xray",
    ]:
        if os.path.exists(p):
            return p
    return None

def xray_event(level: str, message: str) -> None:
    print(f"[Xray] {message}", flush=True)
    try:
        log_to_json(level, "Xray", message)
    except Exception:
        pass

def active_xray_running() -> bool:
    return state.active_xray_process is not None and state.active_xray_process.poll() is None

def default_xray_cfg() -> dict[str, Any]:
    return {
        "enabled": not VPNGATE_ONLY_MODE,
        "require_vpn": False,
        "outbound_interface": "tun0",
        "loglevel": "warning",
        "inbounds": [
            {
                "id": "inbound-vless-1",
                "protocol": "vless",
                "port": 10086,
                "listen": "0.0.0.0",
                "uuid": str(uuid.uuid4()),
                "password": "",
                "network": "tcp",
                "encryption": "none",
                "ws_path": "/",
                "remark": "默认 VLESS 入站"
            }
        ]
    }

def normalize_xray_cfg(raw_cfg: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    cfg = default_xray_cfg()
    if isinstance(raw_cfg, dict):
        cfg.update({k: v for k, v in raw_cfg.items() if k != "inbounds"})

    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["require_vpn"] = bool(cfg.get("require_vpn", True))
    cfg["outbound_interface"] = str(cfg.get("outbound_interface") or "tun0").strip() or "tun0"
    cfg["loglevel"] = str(cfg.get("loglevel") or "warning").strip().lower()
    if cfg["loglevel"] not in ("debug", "info", "warning", "error", "none"):
        cfg["loglevel"] = "warning"

    raw_inbounds = raw_cfg.get("inbounds") if isinstance(raw_cfg, dict) else None
    if not isinstance(raw_inbounds, list) or not raw_inbounds:
        raw_inbounds = cfg["inbounds"]

    normalized: list[dict[str, Any]] = []
    used_ports: set[int] = set()
    allowed_protocols = {"vless", "vmess", "trojan", "shadowsocks"}
    allowed_networks = {"tcp", "ws"}
    for idx, item in enumerate(raw_inbounds[:10], start=1):
        if not isinstance(item, dict):
            return cfg, f"第 {idx} 个 Xray 入站配置格式无效"
        protocol = str(item.get("protocol") or "vless").lower().strip()
        if protocol not in allowed_protocols:
            return cfg, f"第 {idx} 个入站协议不支持: {protocol}"
        try:
            port = int(item.get("port", 10086))
        except (TypeError, ValueError):
            return cfg, f"第 {idx} 个入站端口不是有效数字"
        if not (1 <= port <= 65535):
            return cfg, f"第 {idx} 个入站端口必须在 1-65535 之间"
        if port in used_ports:
            return cfg, f"Xray 入站端口 {port} 重复，请改成不同端口"
        used_ports.add(port)

        network = str(item.get("network") or "tcp").lower().strip()
        if network not in allowed_networks:
            return cfg, f"第 {idx} 个入站传输类型暂只支持 tcp/ws"

        inbound_id = str(item.get("id") or f"inbound-{protocol}-{idx}").strip()
        listen = str(item.get("listen") or "0.0.0.0").strip() or "0.0.0.0"
        remark = str(item.get("remark") or f"{protocol.upper()} 入站 {idx}").strip()
        encryption = str(item.get("encryption") or ("none" if protocol == "vless" else "aes-256-gcm")).strip()
        ws_path = str(item.get("ws_path") or "/").strip()
        if not ws_path.startswith("/"):
            ws_path = "/" + ws_path

        clients = item.get("clients", [])
        if not isinstance(clients, list):
            clients = []

        if not clients:
            secret = str(item.get("uuid") or item.get("password") or item.get("secret") or "").strip()
            if protocol in ("vless", "vmess"):
                if not secret:
                    secret = str(uuid.uuid4())
                try:
                    secret = str(uuid.UUID(secret))
                except ValueError:
                    secret = str(uuid.uuid4())
                clients = [{"name": "client-01", "uuid": secret, "status": "active"}]
            else:
                if not secret:
                    secret = str(uuid.uuid4()).replace("-", "")
                clients = [{"name": "client-01", "password": secret, "status": "active"}]

        traffic = load_client_traffic()
        normalized_clients = []
        for c_idx, client in enumerate(clients, start=1):
            if not isinstance(client, dict):
                continue
            name = str(client.get("name") or f"client-{c_idx:02d}").strip()
            status = str(client.get("status") or "active").strip().lower()
            if status not in ("active", "disabled"):
                status = "active"

            c_secret = str(client.get("uuid") or client.get("password") or client.get("secret") or "").strip()
            if protocol in ("vless", "vmess"):
                if not c_secret:
                    c_secret = str(uuid.uuid4())
                try:
                    c_secret = str(uuid.UUID(c_secret))
                except ValueError:
                    c_secret = str(uuid.uuid4())
                c_uuid = c_secret
                c_pwd = ""
            else:
                if not c_secret:
                    c_secret = str(uuid.uuid4()).replace("-", "")
                c_uuid = ""
                c_pwd = c_secret

            try:
                quota_gb = float(client.get("quota_gb", 0.0))
            except (TypeError, ValueError):
                quota_gb = 0.0

            try:
                expiry_time = int(client.get("expiry_time", 0))
            except (TypeError, ValueError):
                expiry_time = 0

            client_stats = traffic.get(name, {})
            uploaded = client_stats.get("uploaded", 0)
            downloaded = client_stats.get("downloaded", 0)

            normalized_clients.append({
                "name": name,
                "uuid": c_uuid,
                "password": c_pwd,
                "status": status,
                "quota_gb": quota_gb,
                "expiry_time": expiry_time,
                "uploaded": uploaded,
                "downloaded": downloaded
            })

        first_client = normalized_clients[0] if normalized_clients else {"uuid": "", "password": ""}
        legacy_uuid = first_client.get("uuid", "")
        legacy_password = first_client.get("password", "")

        normalized.append({
            "id": inbound_id,
            "protocol": protocol,
            "port": port,
            "listen": listen,
            "uuid": legacy_uuid,
            "password": legacy_password,
            "clients": normalized_clients,
            "network": network,
            "encryption": encryption,
            "ws_path": ws_path,
            "remark": remark
        })

    cfg["inbounds"] = normalized
    return cfg, ""

def load_xray_cfg() -> dict:
    data = None
    if XRAY_CFG_FILE.exists():
        try:
            with open(XRAY_CFG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    cfg, err = normalize_xray_cfg(data)
    if err:
        fallback = default_xray_cfg()
        fallback["enabled"] = False
        return fallback
    return cfg

def save_xray_cfg(cfg: dict) -> tuple[bool, str]:
    normalized, err = normalize_xray_cfg(cfg)
    if err:
        return False, err
    try:
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        with open(XRAY_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        return True, ""
    except Exception as exc:
        return False, f"写入 Xray 配置缓存失败: {exc}"

def diagnose_xray_failure(tail: list[str], fallback: str = "") -> str:
    raw = "\n".join(tail[-20:]) or fallback
    text = raw.lower()
    if "address already in use" in text or "failed to listen" in text or "bind:" in text:
        return "[错误代码 2103] [ERR_XRAY_PORT_IN_USE] Xray 入站端口被占用。原因: 端口已被其他程序监听，请更换入站端口或停止占用该端口的服务。"
    if "permission denied" in text or "operation not permitted" in text:
        return "[错误代码 2104] [ERR_XRAY_PERMISSION] Xray 权限不足。原因: 当前用户没有监听端口或绑定出站接口的权限，请使用 root/systemd 启动服务。"
    if "invalid" in text and ("uuid" in text or "id" in text):
        return "[错误代码 2105] [ERR_XRAY_UUID_INVALID] Xray 用户 ID 配置无效。原因: VLESS/VMess 必须使用合法 UUID。"
    if "failed to read config" in text or "failed to parse" in text or "json" in text:
        return "[错误代码 2106] [ERR_XRAY_CONFIG_INVALID] Xray 配置文件解析失败。原因: 入站协议、端口或传输参数不符合 Xray 配置规范。"
    if "no such file" in text or "not found" in text:
        return "[错误代码 2107] [ERR_XRAY_FILE_NOT_FOUND] Xray 启动文件或配置文件不存在。原因: Xray Core 未完整安装或配置路径丢失。"
    if "unknown command" in text or "flag provided but not defined" in text:
        return "[错误代码 2108] [ERR_XRAY_COMMAND_UNSUPPORTED] Xray 命令参数不兼容。原因: 当前 Xray 版本不支持本程序使用的启动参数。"
    if "tun0" in text or "interface" in text or "no such device" in text:
        return "[错误代码 2109] [ERR_XRAY_OUTBOUND_INTERFACE] Xray 无法绑定 VPN 出口网卡。原因: OpenVPN 未连接成功，或 tun0 虚拟网卡不存在。"
    if fallback:
        return f"[错误代码 2199] [ERR_XRAY_UNKNOWN] Xray 启动失败。原因: {fallback}"
    return "[错误代码 2199] [ERR_XRAY_UNKNOWN] Xray 启动失败。原因: 进程退出但没有输出明确错误，请查看运行日志。"

def xray_config_port_error(cfg: dict[str, Any]) -> str:
    seen: set[int] = set()
    for ib in cfg.get("inbounds", []):
        port = int(ib.get("port", 0))
        if port in seen:
            return f"Xray 入站端口 {port} 重复"
        seen.add(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
        except OSError as exc:
            return f"端口 {port} 当前不可用: {exc}"
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return ""

def write_xray_config(cfg: dict) -> bool:
    try:
        cfg, err = normalize_xray_cfg(cfg)
        if err:
            xray_event("ERROR", f"配置校验失败: {err}")
            return False
        xray_inbounds = []
        for ib in cfg.get("inbounds", []):
            protocol = ib.get("protocol", "vless").lower()
            port = int(ib.get("port", 10086))
            network = ib.get("network", "tcp").lower()

            clients = ib.get("clients", [])
            xray_clients = []
            for c in clients:
                if c.get("status") == "disabled":
                    continue
                c_secret = c.get("uuid") or c.get("password") or ""
                c_name = c.get("name") or "client-01"
                if protocol in ("vless", "vmess"):
                    xray_clients.append({"id": c_secret, "level": 0, "email": c_name})
                elif protocol == "trojan":
                    xray_clients.append({"password": c_secret, "level": 0, "email": c_name})

            if not xray_clients:
                uuid_or_pwd = ib.get("uuid") or ib.get("password") or ""
                if protocol in ("vless", "vmess"):
                    xray_clients = [{"id": uuid_or_pwd, "level": 0, "email": "client-01"}]
                elif protocol == "trojan":
                    xray_clients = [{"password": uuid_or_pwd, "level": 0, "email": "client-01"}]

            settings = {}
            if protocol == "vless":
                settings = {
                    "clients": xray_clients,
                    "decryption": "none"
                }
            elif protocol == "vmess":
                settings = {
                    "clients": [{"id": c["id"], "alterId": 0, "level": 0, "email": c.get("email", "client-01")} for c in xray_clients]
                }
            elif protocol == "trojan":
                settings = {
                    "clients": xray_clients
                }
            elif protocol == "shadowsocks":
                cipher = ib.get("encryption", "aes-256-gcm")
                uuid_or_pwd = ib.get("password") or ib.get("uuid") or ""
                settings = {
                    "method": cipher,
                    "password": uuid_or_pwd,
                    "network": "tcp,udp"
                }

            inbound_entry = {
                "listen": ib.get("listen", "0.0.0.0"),
                "port": port,
                "protocol": protocol,
                "settings": settings,
                "streamSettings": {
                    "network": network
                }
            }
            if network == "ws":
                inbound_entry["streamSettings"]["wsSettings"] = {
                    "path": ib.get("ws_path", "/")
                }
            xray_inbounds.append(inbound_entry)

        try:
            sub_links = read_json_list(SUBSCRIPTION_LINKS_FILE)
            sub_nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
        except Exception:
            sub_links = []
            sub_nodes = []

        for link in sub_links:
            if not link.get("enabled"):
                continue

            protocol = str(link.get("protocol") or "").strip().lower()
            port = int(link.get("port") or 0)
            if not port:
                continue

            camouflage_host = clean_hostname(link.get("camouflage_host"))
            child_nodes = [node for node in sub_nodes if node.get("subscription_id") == link.get("id") and node.get("enabled")]
            if not child_nodes:
                continue

            name = str(link.get("name") or f"{protocol}-{port}").strip()

            if protocol == "vless-reality":
                private_key = str(link.get("reality_private_key") or "").strip()
                short_id = str(link.get("reality_short_id") or "").strip()
                mldsa_seed = str(link.get("reality_mldsa65_seed") or "").strip()
                spider_x = link.get("reality_spider_x")
                
                changed = False
                if not private_key:
                    priv, pub = generate_reality_keys()
                    if priv and pub:
                        link["reality_private_key"] = priv
                        link["reality_public_key"] = pub
                        private_key = priv
                        changed = True

                if not mldsa_seed:
                    seed, verify = generate_mldsa65_keys()
                    if seed and verify:
                        link["reality_mldsa65_seed"] = seed
                        link["reality_mldsa65_verify"] = verify
                        mldsa_seed = seed
                        changed = True

                if spider_x is None:
                    import random
                    rand_hex = "".join(random.choice("0123456789abcdef") for _ in range(random.randint(8, 16)))
                    link["reality_spider_x"] = f"/{rand_hex}"
                    spider_x = f"/{rand_hex}"
                    changed = True

                if changed:
                    write_json(SUBSCRIPTION_LINKS_FILE, sub_links)

                if not private_key:
                    xray_event("WARNING", f"订阅 {name} 缺少 Reality 私钥，已跳过")
                    continue

                reality_settings = {
                    "show": False,
                    "dest": f"{camouflage_host}:443" if camouflage_host else "www.microsoft.com:443",
                    "xver": 0,
                    "serverNames": [
                        camouflage_host or "www.microsoft.com"
                    ],
                    "privateKey": private_key,
                    "shortIds": [
                        short_id
                    ] if short_id else [],
                    "spiderX": spider_x or "/"
                }
                if mldsa_seed:
                    reality_settings["mldsa65Seed"] = mldsa_seed

                xray_clients = []
                for node in child_nodes:
                    uuid_value = str(node.get("uuid") or "").strip()
                    if uuid_value:
                        xray_clients.append({
                            "id": uuid_value,
                            "flow": "xtls-rprx-vision",
                            "level": 0,
                            "email": node.get("name") or "client"
                        })
                if not xray_clients:
                    continue

                inbound_entry = {
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "vless",
                    "tag": link["id"],
                    "settings": {
                        "clients": xray_clients,
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": reality_settings
                    }
                }
                xray_inbounds.append(inbound_entry)

            elif protocol == "vmess-ws-tls":
                ui_cfg = load_ui_config()
                cert_file = ""
                key_file = ""

                if camouflage_host == ui_cfg.get("domain", "").strip():
                    cert_file = ui_cfg.get("tls_cert_file", "").strip()
                    key_file = ui_cfg.get("tls_key_file", "").strip()

                if not cert_file or not key_file:
                    for item in ui_cfg.get("domain_certs", []):
                        if camouflage_host == item.get("domain", "").strip():
                            cert_file = item.get("tls_cert_file", "").strip()
                            key_file = item.get("tls_key_file", "").strip()
                            break

                if not cert_file or not key_file or not os.path.exists(cert_file) or not os.path.exists(key_file):
                    xray_event("WARNING", f"订阅 {name} 的 TLS 证书文件不存在，已跳过以防止 Xray 启动失败")
                    continue

                xray_clients = []
                for node in child_nodes:
                    uuid_value = str(node.get("uuid") or "").strip()
                    if uuid_value:
                        xray_clients.append({
                            "id": uuid_value,
                            "level": 0,
                            "alterId": 0,
                            "email": node.get("name") or "client"
                        })
                if not xray_clients:
                    continue

                inbound_entry = {
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "vmess",
                    "tag": link["id"],
                    "settings": {
                        "clients": xray_clients
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "tls",
                        "wsSettings": {
                            "path": str(link.get("ws_path") or "/")
                        },
                        "tlsSettings": {
                            "certificates": [
                                {
                                    "certificateFile": cert_file,
                                    "keyFile": key_file
                                }
                            ]
                        }
                    }
                }
                xray_inbounds.append(inbound_entry)

            elif protocol == "socks5":
                accounts = []
                for node in child_nodes:
                    socks_username = str(node.get("socks_username") or node.get("username") or "").strip()
                    socks_password = str(node.get("socks_password") or node.get("password") or "").strip()
                    if socks_username and socks_password:
                        accounts.append({"user": socks_username, "pass": socks_password})

                inbound_entry = {
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "socks",
                    "tag": link["id"],
                    "settings": {
                        "auth": "password" if accounts else "noauth",
                        "accounts": accounts,
                        "udp": True
                    }
                }
                xray_inbounds.append(inbound_entry)

        independent_nodes_changed = False
        for node in [item for item in sub_nodes if item.get("enabled") and not item.get("subscription_id")]:
            protocol = str(node.get("protocol") or "").strip().lower()
            port = int(node.get("port") or 0)
            if not port:
                xray_event("WARNING", f"独立节点 {node.get('name') or node.get('id')} 缺少端口，已跳过")
                continue
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            name = str(node.get("name") or node_id).strip()
            camouflage_host = clean_hostname(node.get("camouflage_host"))

            if protocol == "vless-reality":
                uuid_value = str(node.get("uuid") or "").strip()
                if not uuid_value:
                    continue

                private_key = str(node.get("reality_private_key") or "").strip()
                public_key = str(node.get("reality_public_key") or "").strip()
                short_id = str(node.get("reality_short_id") or "").strip()
                mldsa_seed = str(node.get("reality_mldsa65_seed") or "").strip()
                spider_x = str(node.get("reality_spider_x") or "").strip()

                if not private_key or not public_key:
                    priv, pub = generate_reality_keys()
                    if priv and pub:
                        node["reality_private_key"] = priv
                        node["reality_public_key"] = pub
                        private_key = priv
                        public_key = pub
                        independent_nodes_changed = True
                if not short_id:
                    import secrets
                    short_id = secrets.token_hex(8)
                    node["reality_short_id"] = short_id
                    independent_nodes_changed = True
                if not spider_x:
                    import random
                    rand_hex = "".join(random.choice("0123456789abcdef") for _ in range(random.randint(8, 16)))
                    spider_x = f"/{rand_hex}"
                    node["reality_spider_x"] = spider_x
                    independent_nodes_changed = True
                if not private_key:
                    xray_event("WARNING", f"独立节点 {name} 缺少 Reality 私钥，已跳过")
                    continue

                reality_settings = {
                    "show": False,
                    "dest": f"{camouflage_host}:443" if camouflage_host else "www.microsoft.com:443",
                    "xver": 0,
                    "serverNames": [camouflage_host or "www.microsoft.com"],
                    "privateKey": private_key,
                    "shortIds": [short_id] if short_id else [],
                    "spiderX": spider_x or "/"
                }
                if mldsa_seed:
                    reality_settings["mldsa65Seed"] = mldsa_seed

                xray_inbounds.append({
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "vless",
                    "tag": node_id,
                    "settings": {
                        "clients": [{
                            "id": uuid_value,
                            "flow": "xtls-rprx-vision",
                            "level": 0,
                            "email": name
                        }],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": reality_settings
                    }
                })

            elif protocol == "vmess-ws-tls":
                ui_cfg = load_ui_config()
                cert_file = ""
                key_file = ""
                if camouflage_host == str(ui_cfg.get("domain", "")).strip():
                    cert_file = str(ui_cfg.get("tls_cert_file", "")).strip()
                    key_file = str(ui_cfg.get("tls_key_file", "")).strip()
                if not cert_file or not key_file:
                    for item in ui_cfg.get("domain_certs", []):
                        if camouflage_host == str(item.get("domain", "")).strip():
                            cert_file = str(item.get("tls_cert_file", "")).strip()
                            key_file = str(item.get("tls_key_file", "")).strip()
                            break
                if not cert_file or not key_file or not os.path.exists(cert_file) or not os.path.exists(key_file):
                    xray_event("WARNING", f"独立节点 {name} 的 TLS 证书文件不存在，已跳过以防止 Xray 启动失败")
                    continue
                uuid_value = str(node.get("uuid") or "").strip()
                if not uuid_value:
                    continue
                ws_path = str(node.get("ws_path") or "/").strip()
                if not ws_path.startswith("/"):
                    ws_path = "/" + ws_path
                xray_inbounds.append({
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "vmess",
                    "tag": node_id,
                    "settings": {
                        "clients": [{
                            "id": uuid_value,
                            "level": 0,
                            "alterId": 0,
                            "email": name
                        }]
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "tls",
                        "wsSettings": {"path": ws_path},
                        "tlsSettings": {
                            "certificates": [{
                                "certificateFile": cert_file,
                                "keyFile": key_file
                            }]
                        }
                    }
                })

            elif protocol == "socks5":
                socks_username = str(node.get("socks_username") or node.get("username") or "").strip()
                socks_password = str(node.get("socks_password") or node.get("password") or "").strip()
                accounts = [{"user": socks_username, "pass": socks_password}] if socks_username and socks_password else []
                xray_inbounds.append({
                    "listen": "0.0.0.0",
                    "port": port,
                    "protocol": "socks",
                    "tag": node_id,
                    "settings": {
                        "auth": "password" if accounts else "noauth",
                        "accounts": accounts,
                        "udp": True
                    }
                })

        if independent_nodes_changed:
            write_json(SUBSCRIPTION_NODES_FILE, sub_nodes)

        xray_inbounds.append({
            "listen": "127.0.0.1",
            "port": 10085,
            "protocol": "dokodemo-door",
            "settings": {
                "address": "127.0.0.1"
            },
            "tag": "api"
        })

        outbound_nodes = []
        try:
            outbound_nodes = read_json_list(OUTBOUND_NODES_FILE)
        except Exception:
            pass

        warp_enabled = False
        warp_node = None
        for n in outbound_nodes:
            if n.get("type") == "warp" and n.get("enabled") and is_valid_warp_node(n):
                warp_enabled = True
                warp_node = n
                break

        if warp_enabled:
            xray_inbounds.append({
                "listen": "127.0.0.1",
                "port": 10088,
                "protocol": "http",
                "settings": {
                    "timeout": 10
                },
                "tag": "local-warp-test-http"
            })

        outbound_interface = str(cfg.get("outbound_interface") or "tun0")
        primary_outbound = {
            "tag": "vpn-out",
            "protocol": "freedom",
            "settings": {}
        }
        if cfg.get("require_vpn", False):
            inject_outbound_sockopt(primary_outbound, outbound_interface)

        vpngate_active_outbound = {
            "tag": "vpngate-openvpn-active",
            "protocol": "freedom",
            "settings": {}
        }
        inject_outbound_sockopt(vpngate_active_outbound, outbound_interface)

        xray_outbounds = [
            primary_outbound,
            vpngate_active_outbound
        ]

        for node in outbound_nodes:
            if not node.get("enabled"):
                continue
            
            node_id = node.get("id")
            if not node_id:
                continue

            if node.get("type") == "json-config":
                try:
                    outbound_obj = json.loads(node["json_config"])
                    outbound_obj["tag"] = node_id
                    if cfg.get("require_vpn", False):
                        inject_outbound_sockopt(outbound_obj, outbound_interface)
                    xray_outbounds.append(outbound_obj)
                except Exception as e:
                    xray_event("WARNING", f"出站节点 {node.get('name')} 的 JSON 配置解析失败: {e}")
            
            elif node.get("type") == "warp":
                if not is_valid_warp_node(node):
                    xray_event("WARNING", f"WARP 出站节点配置无效或不完整，已跳过")
                else:
                    xray_outbounds.append(build_warp_outbound(node, node_id))

        xray_routing_rules = [
            {
                "inboundTag": [
                    "api"
                ],
                "outboundTag": "api",
                "type": "field"
            }
        ]

        if warp_enabled:
            xray_routing_rules.append({
                "inboundTag": ["local-warp-test-http"],
                "outboundTag": "warp",
                "type": "field"
            })

        try:
            custom_rules = read_json_list(ROUTING_RULES_FILE)
            custom_rules = [item for item in custom_rules if item.get("enabled")]
            custom_rules.sort(key=lambda x: int(x.get("priority") or 100))
        except Exception:
            custom_rules = []

        rules_changed = False
        available_inbound_tags = {str(item.get("tag") or "").strip() for item in xray_inbounds if item.get("tag")}
        available_outbound_tags = {str(item.get("tag") or "").strip() for item in xray_outbounds if item.get("tag")}

        for rule in custom_rules:
            inbound_ids = normalize_id_list(rule.get("inbound_node_ids"), rule.get("inbound_node_id"))
            outbound_ids = normalize_id_list(rule.get("outbound_node_ids"), rule.get("outbound_node_id"))
            
            resolved_inbound_ids = []
            for ib_id in inbound_ids:
                if ib_id in available_inbound_tags:
                    resolved_inbound_ids.append(ib_id)
                else:
                    node = next((n for n in sub_nodes if n.get("id") == ib_id), None)
                    if node:
                        sub_id = node.get("subscription_id")
                        if sub_id in available_inbound_tags:
                            resolved_inbound_ids.append(sub_id)
            
            valid_inbound_ids = list(set(resolved_inbound_ids))
            if not valid_inbound_ids:
                rule["status"] = "error"
                rule["status_text"] = "入站节点配置已失效，规则已自动禁用"
                rules_changed = True
                continue

            valid_outbound_ids = [val for val in outbound_ids if val in available_outbound_tags]
            if not valid_outbound_ids:
                rule["status"] = "error"
                rule["status_text"] = "出站配置已失效，规则已自动禁用"
                rules_changed = True
                continue

            rule["status"] = "active"
            rule["status_text"] = "规则已被 Xray 加载运行"
            rules_changed = True

            match_conditions = rule.get("match_conditions") or [{
                "type": rule.get("match_type", "all"),
                "value": rule.get("match_value", ""),
            }]

            for condition in match_conditions:
                rule_entry = {
                    "inboundTag": valid_inbound_ids,
                    "outboundTag": valid_outbound_ids[0],
                    "type": "field"
                }
                c_type = condition.get("type", "all")
                c_val = condition.get("value", "")

                if c_type == "domain":
                    rule_entry["domain"] = [c_val]
                elif c_type == "ip":
                    rule_entry["ip"] = [c_val]
                elif c_type == "port":
                    rule_entry["port"] = c_val

                xray_routing_rules.append(rule_entry)

        if rules_changed:
            write_json(ROUTING_RULES_FILE, custom_rules)

        xray_routing_rules.append({
            "inboundTag": [
                "inbound-vless-1"
            ],
            "outboundTag": "vpn-out",
            "type": "field"
        })

        xray_config = {
            "log": {
                "loglevel": cfg.get("loglevel", "warning")
            },
            "api": {
                "tag": "api",
                "services": [
                    "HandlerService",
                    "StatsService"
                ]
            },
            "stats": {},
            "policy": {
                "levels": {
                    "0": {
                        "statsUserUplink": True,
                        "statsUserDownlink": True
                    }
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True
                }
            },
            "inbounds": xray_inbounds,
            "outbounds": xray_outbounds,
            "routing": {
                "domainStrategy": "AsIs",
                "rules": xray_routing_rules
            }
        }
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        with open(XRAY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(xray_config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        xray_event("ERROR", f"编译 Xray 配置发生致命错误: {exc}")
        return False

def test_xray_config_file() -> tuple[bool, str]:
    binary_path = xray_binary_path()
    if not binary_path:
        return False, "未检测到 xray 二进制程序，无法验证配置"
    command = [binary_path, "run", "-config", str(XRAY_CONFIG_FILE), "-test"]
    try:
        res = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        output = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        if res.returncode == 0:
            return True, output
        return False, diagnose_xray_failure(output.splitlines(), output or f"xray test exited with {res.returncode}")
    except Exception as exc:
        return False, f"验证 Xray 配置失败: {exc}"

def start_xray() -> bool:
    from backend.app.core.vpn import active_openvpn_running
    if active_xray_running():
        return True

    state.xray_last_error = ""
    state.xray_log_tail = []
    cfg = load_xray_cfg()
    if not cfg.get("enabled", False):
        state.xray_last_error = "Xray 入站未启用"
        return False

    binary_path = xray_binary_path()
    if not binary_path:
        state.xray_last_error = "[错误代码 2101] [ERR_XRAY_CMD_NOT_FOUND] 未检测到 xray 二进制程序。原因: Xray Core 未安装，或 /usr/local/bin/xray 不存在。"
        xray_event("ERROR", state.xray_last_error)
        return False

    outbound_interface = str(cfg.get("outbound_interface") or "tun0")
    if cfg.get("require_vpn", True) and sys.platform.startswith("linux"):
        if not active_openvpn_running() or not Path(f"/sys/class/net/{outbound_interface}").exists():
            state.xray_last_error = f"[错误代码 2102] [ERR_XRAY_NEEDS_VPN] Xray 暂不能启动。原因: 当前配置要求 Xray 出站经由 {outbound_interface}，但 OpenVPN 未连接成功或该网卡不存在。"
            xray_event("ERROR", state.xray_last_error)
            return False

    port_error = xray_config_port_error(cfg)
    if port_error:
        state.xray_last_error = f"[错误代码 2103] [ERR_XRAY_PORT_IN_USE] {port_error}。原因: Xray 入站端口不能与其他服务冲突。"
        xray_event("ERROR", state.xray_last_error)
        return False

    if not write_xray_config(cfg):
        state.xray_last_error = "[错误代码 2106] [ERR_XRAY_CONFIG_INVALID] Xray 配置生成失败，请检查入站协议、端口和用户凭据。"
        return False

    command = [binary_path, "run", "-config", str(XRAY_CONFIG_FILE)]
    state.xray_last_command = command
    try:
        xray_event("INFO", f"正在启动 Xray Core: {' '.join(shlex.quote(part) for part in command)}")
        state.active_xray_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT_DIR),
        )

        def xray_reader(proc: subprocess.Popen[str]) -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line_str = line.rstrip()
                    if not line_str:
                        continue
                    state.xray_log_tail.append(line_str)
                    state.xray_log_tail = state.xray_log_tail[-80:]
                    level = "ERROR" if any(token in line_str.lower() for token in ["error", "failed", "fatal", "panic", "permission denied"]) else "INFO"
                    xray_event(level, line_str)
            except Exception as exc:
                xray_event("WARNING", f"读取 Xray 输出日志失败: {exc}")

        threading.Thread(target=xray_reader, args=(state.active_xray_process,), daemon=True).start()
        time.sleep(1.0)
        if active_xray_running():
            outbound_label = outbound_interface if cfg.get("require_vpn", False) else "系统默认出口"
            xray_event("INFO", f"服务启动成功，入站端口: {', '.join(str(ib.get('port')) for ib in cfg.get('inbounds', []))}，出站网卡: {outbound_label}")
            return True
        else:
            exit_code = state.active_xray_process.poll() if state.active_xray_process else "unknown"
            state.xray_last_error = diagnose_xray_failure(state.xray_log_tail, f"进程退出码: {exit_code}")
            xray_event("ERROR", state.xray_last_error)
            state.active_xray_process = None
            return False
    except Exception as e:
        state.xray_last_error = diagnose_xray_failure(state.xray_log_tail, str(e))
        xray_event("ERROR", state.xray_last_error)
        return False

def stop_xray():
    if state.active_xray_process is not None:
        xray_event("INFO", "正在停止 Xray 服务...")
        try:
            state.active_xray_process.terminate()
            state.active_xray_process.wait(timeout=3)
        except Exception:
            try:
                state.active_xray_process.kill()
            except Exception:
                pass
        state.active_xray_process = None
        xray_event("INFO", "服务已停止")

def query_xray_client_stats() -> dict[str, dict[str, int]]:
    binary = xray_binary_path()
    if not binary or not active_xray_running():
        return {}
    cmd = [binary, "api", "statsquery", "--server=127.0.0.1:10085"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode != 0:
            return {}
        data = json.loads(res.stdout)
        stats = {}
        for item in data.get("stat", []):
            name = item.get("name", "")
            val = item.get("value", 0)
            try:
                val = int(val)
            except (ValueError, TypeError):
                val = 0
            if name.startswith("user>>>"):
                parts = name.split(">>>")
                if len(parts) >= 4:
                    email = parts[1]
                    direction = parts[3]
                    if email not in stats:
                        stats[email] = {"uplink": 0, "downlink": 0}
                    if direction in ("uplink", "downlink"):
                        stats[email][direction] = val
        return stats
    except Exception:
        return {}

def update_and_accumulate_client_traffic(current_stats: dict) -> dict:
    traffic = load_client_traffic()
    changed = False
    for email, stats in current_stats.items():
        curr_up = stats.get("uplink", 0)
        curr_down = stats.get("downlink", 0)
        client_data = traffic.get(email)
        if not client_data:
            client_data = {
                "uploaded": 0,
                "downloaded": 0,
                "last_seen_uplink": 0,
                "last_seen_downlink": 0
            }
            traffic[email] = client_data
        last_up = client_data.get("last_seen_uplink", 0)
        last_down = client_data.get("last_seen_downlink", 0)
        delta_up = curr_up - last_up if curr_up >= last_up else curr_up
        delta_down = curr_down - last_down if curr_down >= last_down else curr_down
        if delta_up > 0 or delta_down > 0 or client_data.get("last_seen_uplink") != curr_up or client_data.get("last_seen_downlink") != curr_down:
            client_data["uploaded"] = client_data.get("uploaded", 0) + delta_up
            client_data["downloaded"] = client_data.get("downloaded", 0) + delta_down
            client_data["last_seen_uplink"] = curr_up
            client_data["last_seen_downlink"] = curr_down
            changed = True
    for email, client_data in traffic.items():
        if email not in current_stats:
            if client_data.get("last_seen_uplink", 0) != 0 or client_data.get("last_seen_downlink", 0) != 0:
                client_data["last_seen_uplink"] = 0
                client_data["last_seen_downlink"] = 0
                changed = True
    if changed:
        save_client_traffic(traffic)
    return traffic

def enforce_client_quotas(traffic: dict) -> bool:
    cfg = load_xray_cfg()
    changed = False
    now = time.time()
    for inbound in cfg.get("inbounds", []):
        for client in inbound.get("clients", []):
            if client.get("status") != "disabled":
                expiry_time = client.get("expiry_time", 0)
                if expiry_time > 0 and now > expiry_time:
                    client["status"] = "disabled"
                    changed = True
                    xray_event("WARNING", f"用户 {client.get('name')} 的授权已过期 (到期时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry_time))})，已自动停用")
                    continue

                quota_gb = client.get("quota_gb", 0)
                if quota_gb > 0:
                    email = client.get("name")
                    client_stats = traffic.get(email, {})
                    uploaded = client_stats.get("uploaded", 0)
                    downloaded = client_stats.get("downloaded", 0)
                    total_used = uploaded + downloaded
                    quota_bytes = int(quota_gb * 1024 * 1024 * 1024)
                    if total_used >= quota_bytes:
                        client["status"] = "disabled"
                        changed = True
                        xray_event("WARNING", f"用户 {email} 流量已超限 (已用: {total_used / (1024**3):.2f} GB / 额度: {quota_gb} GB)，已自动停用")
    if changed:
        save_xray_cfg(cfg)
        if active_xray_running():
            xray_event("INFO", "检测到用户超额或过期被禁用，正在重载 Xray 实例...")
            stop_xray()
            start_xray()
        return True
    return False

def _run_best_effort(command: list[str], timeout: int = 15) -> None:
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception:
        pass

def _package_installed(command: list[str], timeout: int = 5) -> bool:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).returncode == 0
    except Exception:
        return False

def disable_xray_system_service() -> None:
    if shutil.which("systemctl"):
        for action in ("stop", "disable", "reset-failed"):
            _run_best_effort(["systemctl", action, "xray.service", "xray@*.service"])
        _run_best_effort(["systemctl", "daemon-reload"])
    if shutil.which("rc-service"):
        _run_best_effort(["rc-service", "xray", "stop"])
    if shutil.which("rc-update"):
        _run_best_effort(["rc-update", "del", "xray", "default"])

def cleanup_existing_xray_installation() -> None:
    xray_event("INFO", "正在全面清理旧 Xray Core、服务与残留配置...")
    stop_xray()
    disable_xray_system_service()

    if shutil.which("pkill"):
        _run_best_effort(["pkill", "-TERM", "-x", "xray"], timeout=5)
        time.sleep(1)
        _run_best_effort(["pkill", "-KILL", "-x", "xray"], timeout=5)

    if shutil.which("dpkg") and shutil.which("apt-get"):
        if _package_installed(["dpkg", "-s", "xray"]):
            _run_best_effort(["apt-get", "purge", "-y", "xray"], timeout=90)
        if _package_installed(["dpkg", "-s", "xray-core"]):
            _run_best_effort(["apt-get", "purge", "-y", "xray-core"], timeout=90)
    elif shutil.which("rpm"):
        pkg_mgr = shutil.which("dnf") or shutil.which("yum")
        if pkg_mgr:
            if _package_installed(["rpm", "-q", "xray"]):
                _run_best_effort([pkg_mgr, "remove", "-y", "xray"], timeout=90)
            if _package_installed(["rpm", "-q", "xray-core"]):
                _run_best_effort([pkg_mgr, "remove", "-y", "xray-core"], timeout=90)
    elif shutil.which("apk"):
        if _package_installed(["apk", "info", "-e", "xray"]):
            _run_best_effort(["apk", "del", "xray"], timeout=90)
        if _package_installed(["apk", "info", "-e", "xray-core"]):
            _run_best_effort(["apk", "del", "xray-core"], timeout=90)

    remove_paths = [
        "/usr/local/bin/xray",
        "/usr/bin/xray",
        "/bin/xray",
        "/etc/systemd/system/xray.service",
        "/etc/systemd/system/xray@.service",
        "/lib/systemd/system/xray.service",
        "/lib/systemd/system/xray@.service",
        "/usr/lib/systemd/system/xray.service",
        "/usr/lib/systemd/system/xray@.service",
        "/etc/init.d/xray",
        "/etc/systemd/system/multi-user.target.wants/xray.service",
        "/usr/local/etc/xray",
        "/etc/xray",
        "/var/log/xray",
        "/usr/local/share/xray",
    ]
    for item in remove_paths:
        try:
            path = Path(item)
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists() or path.is_symlink():
                path.unlink()
        except Exception as exc:
            xray_event("WARNING", f"清理旧 Xray 路径失败 {item}: {exc}")

    disable_xray_system_service()
    xray_event("INFO", "旧 Xray 清理完成，准备重新安装。")

def bg_install_xray():
    with state.xray_install_lock:
        if state.xray_install_status["status"] == "installing":
            return
        state.xray_install_status["status"] = "installing"
        state.xray_install_status["message"] = "正在请求官方安装脚本..."
        state.xray_install_status["progress"] = 10

    try:
        script_urls = [
            "https://github.com/XTLS/Xray-install/raw/main/install-release.sh",
            "https://fastly.jsdelivr.net/gh/XTLS/Xray-install@main/install-release.sh",
            "https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh"
        ]
        script_content = ""
        last_download_err = ""
        for url in script_urls:
            try:
                xray_event("INFO", f"正在尝试从 {url} 下载 Xray 安装脚本...")
                req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    script_content = r.read().decode("utf-8")
                if script_content.strip():
                    xray_event("INFO", f"成功从 {url} 下载安装脚本。")
                    break
            except Exception as e:
                last_download_err = str(e)
                xray_event("WARNING", f"从 {url} 下载安装脚本失败: {e}")
                
        if not script_content:
            raise Exception(f"所有镜像源下载安装脚本均失败。最后一次错误: {last_download_err}")
        
        with state.xray_install_lock:
            state.xray_install_status["message"] = "正在清理旧 Xray Core 与残留服务..."
            state.xray_install_status["progress"] = 30

        cleanup_existing_xray_installation()

        with state.xray_install_lock:
            state.xray_install_status["message"] = "正在执行全新安装脚本..."
            state.xray_install_status["progress"] = 50
            
        script_path = DATA_DIR / "install-xray.sh"
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        script_path.write_text(script_content, encoding="utf-8")
        
        res = subprocess.run(["bash", str(script_path), "install"], capture_output=True, text=True, timeout=180)
        disable_xray_system_service()
        
        try: script_path.unlink()
        except Exception: pass
        
        if res.returncode == 0:
            with state.xray_install_lock:
                state.xray_install_status["status"] = "success"
                state.xray_install_status["message"] = "Xray Core 已清理旧版本并全新安装成功！"
                state.xray_install_status["progress"] = 100
            print("[Xray Install] Installation succeeded.", flush=True)
        else:
            err_msg = res.stderr or res.stdout or "未知错误"
            with state.xray_install_lock:
                state.xray_install_status["status"] = "failed"
                state.xray_install_status["message"] = f"安装脚本执行失败: {err_msg[:200]}"
                state.xray_install_status["progress"] = 0
            print(f"[Xray Install] Installation failed: {err_msg}", flush=True)
            
    except Exception as e:
        with state.xray_install_lock:
            state.xray_install_status["status"] = "failed"
            state.xray_install_status["message"] = f"安装异常: {str(e)}"
            state.xray_install_status["progress"] = 0
        print(f"[Xray Install] Exception during installation: {e}", flush=True)

def clean_hostname(value: Any) -> str:
    host = str(value or "").strip().lower()
    host = re.sub(r"^https?://", "", host)
    host = host.split("/", 1)[0].split(":", 1)[0].strip()
    return host

def clean_subscription_token(value: Any) -> str:
    token = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]", "", token)

def clean_proxy_credential(value: Any) -> str:
    credential = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.-]", "", credential)

def reality_key_b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def wireguard_key_b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")

def clamp_x25519_private_key(private_key: bytes) -> bytes:
    scalar = bytearray(private_key)
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    return bytes(scalar)

def x25519_public_key(private_key: bytes) -> bytes:
    scalar = bytearray(clamp_x25519_private_key(private_key))
    k = int.from_bytes(scalar, "little")
    p = 2**255 - 19
    x1 = 9
    x2 = 1
    z2 = 0
    x3 = x1
    z3 = 1
    swap = 0

    for t in range(254, -1, -1):
        k_t = (k >> t) & 1
        swap ^= k_t
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = k_t

        a = (x2 + z2) % p
        aa = (a * a) % p
        b = (x2 - z2) % p
        bb = (b * b) % p
        e = (aa - bb) % p
        c = (x3 + z3) % p
        d = (x3 - z3) % p
        da = (d * a) % p
        cb = (c * b) % p
        x3 = ((da + cb) ** 2) % p
        z3 = (x1 * ((da - cb) ** 2)) % p
        x2 = (aa * bb) % p
        z2 = (e * (aa + 121665 * e)) % p

    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2

    public = (x2 * pow(z2, p - 2, p)) % p
    return public.to_bytes(32, "little")

def generate_wireguard_keys() -> tuple[str, str]:
    private_raw = clamp_x25519_private_key(os.urandom(32))
    public_raw = x25519_public_key(private_raw)
    return wireguard_key_b64(private_raw), wireguard_key_b64(public_raw)

def generate_mldsa65_keys() -> tuple[str, str]:
    try:
        binary_path = xray_binary_path()
        if binary_path:
            res = subprocess.run([binary_path, "mldsa65"], capture_output=True, text=True, timeout=5)
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            lines = output.splitlines()
            seed = ""
            verify = ""
            for line in lines:
                if "Seed:" in line:
                    seed = line.split(":", 1)[1].strip()
                elif "Verify:" in line:
                    verify = line.split(":", 1)[1].strip()
            if seed and verify:
                return seed, verify
    except Exception:
        pass
    return "", ""

def generate_reality_keys() -> tuple[str, str]:
    try:
        binary_path = xray_binary_path()
        if binary_path:
            res = subprocess.run([binary_path, "x25519"], capture_output=True, text=True, timeout=5)
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            lines = output.splitlines()
            priv = ""
            pub = ""
            for line in lines:
                if "Private key:" in line or "PrivateKey:" in line:
                    priv = line.split(":", 1)[1].strip()
                elif "Public key:" in line or "PublicKey:" in line:
                    pub = line.split(":", 1)[1].strip()
            if priv and pub:
                return priv, pub
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import x25519

        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key()
        priv_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return reality_key_b64(priv_raw), reality_key_b64(pub_raw)
    except Exception:
        pass
    try:
        priv_raw = os.urandom(32)
        pub_raw = x25519_public_key(priv_raw)
        return reality_key_b64(priv_raw), reality_key_b64(pub_raw)
    except Exception:
        pass
    return "", ""

def sync_panel_subscription_nodes_to_xray(restart_service: bool = True) -> None:
    cfg = load_xray_cfg()
    if not write_xray_config(cfg):
        return
    if restart_service and active_xray_running():
        ok, error = test_xray_config_file()
        if not ok:
            state.xray_last_error = error
            xray_event("ERROR", f"新 Xray 配置验证失败，已保留当前运行实例: {error}")
            return
        stop_xray()
        start_xray()

def migrate_subscription_hierarchy() -> None:
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    
    changed_links = False
    changed_nodes = False
    
    if not links and nodes:
        default_link = {
            "id": f"sublink-{uuid.uuid4().hex[:12]}",
            "name": "默认订阅",
            "token": f"sub_{uuid.uuid4().hex}",
            "remark": "由旧订阅节点自动归档",
            "enabled": True,
            "status": "draft",
            "status_text": "订阅链接已创建，节点启用后会进入订阅内容",
            "created_at": current_timestamp(),
            "updated_at": current_timestamp(),
        }
        links.append(default_link)
        changed_links = True

    if links and nodes:
        fallback_link_id = str(links[0].get("id") or "")
        for item in nodes:
            if "subscription_id" not in item:
                item["subscription_id"] = fallback_link_id
                item["updated_at"] = current_timestamp()
                changed_nodes = True

        for link in links:
            link_id = link.get("id")
            # Find the first active or first available node under this link to inherit settings
            first_node = next((n for n in nodes if str(n.get("subscription_id")) == str(link_id)), None)
            if first_node:
                if "port" not in link or not link.get("port"):
                    link["port"] = first_node.get("port") or 10086
                    changed_links = True
                if "protocol" not in link or not link.get("protocol"):
                    link["protocol"] = first_node.get("protocol") or "vless-reality"
                    changed_links = True
                
                fields_to_copy = [
                    "camouflage_host", "ws_path", 
                    "reality_private_key", "reality_public_key", 
                    "reality_short_id", "reality_mldsa65_seed", 
                    "reality_mldsa65_verify", "reality_spider_x"
                ]
                for field in fields_to_copy:
                    if field in first_node and (field not in link or not link.get(field)):
                        link[field] = first_node[field]
                        changed_links = True

    if changed_links:
        write_json(SUBSCRIPTION_LINKS_FILE, links)
    if changed_nodes:
        write_json(SUBSCRIPTION_NODES_FILE, nodes)

def enrich_subscription_links(links: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for link in links:
        link_id = str(link.get("id") or "")
        child_nodes = [node for node in nodes if str(node.get("subscription_id") or "") == link_id]
        protocol_ids = {str(node.get("protocol") or "") for node in child_nodes if node.get("protocol")}
        item = dict(link)
        item["node_count"] = len(child_nodes)
        item["enabled_node_count"] = len([node for node in child_nodes if node.get("enabled") is True])
        item["protocol_count"] = len(protocol_ids)
        enriched.append(item)
    return enriched

def validate_subscription_link_payload(payload: dict[str, Any], existing_links: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    name = str(payload.get("name") or "").strip()
    token = clean_subscription_token(payload.get("token"))
    remark = str(payload.get("remark") or "").strip()
    protocol = str(payload.get("protocol") or "vless-reality").strip().lower()

    if not name:
        return None, "订阅名称不能为空"
    if not token:
        token = f"sub_{uuid.uuid4().hex}"
    if not (8 <= len(token) <= 96):
        return None, "订阅 Token 长度必须在 8 至 96 位之间"

    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError):
        return None, "端口必须是数字"
    if not (1 <= port <= 65535):
        return None, "端口必须在 1 至 65535 之间"

    current_id = str(payload.get("id") or "").strip()
    for item in existing_links:
        item_id = str(item.get("id") or "")
        if item_id != current_id and clean_subscription_token(item.get("token")) == token:
            return None, "订阅 Token 已存在"
        if item_id != current_id and int(item.get("port") or 0) == port:
            return None, f"端口 {port} 已被其他订阅占用"

    for item in read_json_list(SUBSCRIPTION_NODES_FILE):
        if not item.get("subscription_id") and int(item.get("port") or 0) == port:
            return None, f"端口 {port} 已被独立节点占用"

    if protocol not in {item["id"] for item in ALLOWED_SUBSCRIPTION_PROTOCOLS}:
        return None, f"协议类型不支持: {protocol}"

    camouflage_host = clean_hostname(payload.get("camouflage_host"))
    ws_path = str(payload.get("ws_path") or "/").strip()
    if not ws_path.startswith("/"):
        ws_path = "/" + ws_path

    reality_private_key = str(payload.get("reality_private_key") or "").strip()
    reality_public_key = str(payload.get("reality_public_key") or "").strip()
    reality_short_id = str(payload.get("reality_short_id") or "").strip()
    reality_mldsa65_seed = str(payload.get("reality_mldsa65_seed") or "").strip()
    reality_mldsa65_verify = str(payload.get("reality_mldsa65_verify") or "").strip()
    reality_spider_x = str(payload.get("reality_spider_x") or "").strip()

    now = current_timestamp()
    link_id = current_id or f"sublink-{uuid.uuid4().hex[:12]}"
    
    if current_id:
        existing = next((l for l in existing_links if l.get("id") == current_id), None)
        if existing:
            if not reality_private_key: reality_private_key = existing.get("reality_private_key", "")
            if not reality_public_key: reality_public_key = existing.get("reality_public_key", "")
            if not reality_short_id: reality_short_id = existing.get("reality_short_id", "")
            if not reality_mldsa65_seed: reality_mldsa65_seed = existing.get("reality_mldsa65_seed", "")
            if not reality_mldsa65_verify: reality_mldsa65_verify = existing.get("reality_mldsa65_verify", "")
            if not reality_spider_x: reality_spider_x = existing.get("reality_spider_x", "")

    if protocol == "vless-reality":
        if not camouflage_host:
            return None, "伪装网址不能为空"
        if not re.match(r"^[a-z0-9.-]+$", camouflage_host):
            return None, "伪装网址只允许填写域名"

        if not reality_private_key or not reality_public_key:
            priv, pub = generate_reality_keys()
            if priv and pub:
                reality_private_key = priv
                reality_public_key = pub
            else:
                return None, "生成 Reality 密钥失败，请确保系统已安装 Xray 并具有执行权限。"
        if not reality_short_id:
            import secrets
            reality_short_id = secrets.token_hex(8)
        if not reality_spider_x:
            import random
            rand_hex = "".join(random.choice("0123456789abcdef") for _ in range(random.randint(8, 16)))
            reality_spider_x = f"/{rand_hex}"

    return {
        "id": link_id,
        "name": name,
        "token": token,
        "remark": remark,
        "port": port,
        "protocol": protocol,
        "camouflage_host": camouflage_host,
        "ws_path": ws_path,
        "reality_private_key": reality_private_key,
        "reality_public_key": reality_public_key,
        "reality_short_id": reality_short_id,
        "reality_mldsa65_seed": reality_mldsa65_seed,
        "reality_mldsa65_verify": reality_mldsa65_verify,
        "reality_spider_x": reality_spider_x,
        "enabled": bool(payload.get("enabled", True)),
        "status": "draft",
        "status_text": "已配置入站服务",
        "created_at": str(payload.get("created_at") or now),
        "updated_at": now,
    }, ""

def save_subscription_link(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    link, error = validate_subscription_link_payload(payload, links)
    if error or link is None:
        return None, error
    
    is_new = not any(item.get("id") == link["id"] for item in links)
    
    updated = False
    for idx, item in enumerate(links):
        if item.get("id") == link["id"]:
            links[idx] = link
            updated = True
            break
    if not updated:
        links.append(link)
    write_json(SUBSCRIPTION_LINKS_FILE, links)
    
    if is_new:
        nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
        existing_nodes = [n for n in nodes if n.get("subscription_id") == link["id"]]
        if not existing_nodes:
            protocol = link["protocol"]
            uuid_value = ""
            socks_username = ""
            socks_password = ""
            if protocol == "socks5":
                socks_username = f"user_{uuid.uuid4().hex[:12]}"
                socks_password = f"pwd_{uuid.uuid4().hex[:12]}"
            else:
                uuid_value = str(uuid.uuid4())
                
            default_node = {
                "id": f"subnode-{uuid.uuid4().hex[:12]}",
                "subscription_id": link["id"],
                "name": f"默认节点",
                "protocol": protocol,
                "port": link["port"],
                "uuid": uuid_value,
                "socks_username": socks_username,
                "socks_password": socks_password,
                "enabled": True,
                "outbound_node_id": "",
                "status": "draft",
                "status_text": "已生成账号",
                "created_at": current_timestamp(),
                "updated_at": current_timestamp(),
            }
            nodes.append(default_node)
            write_json(SUBSCRIPTION_NODES_FILE, nodes)
            
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing after subscription link save failed: {e}", flush=True)
        
    return link, ""

def ensure_default_subscription_link() -> dict[str, Any]:
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    if links:
        return links[0]

    now = current_timestamp()
    link = {
        "id": f"sublink-{uuid.uuid4().hex[:12]}",
        "name": "默认订阅",
        "token": f"sub_{uuid.uuid4().hex}",
        "remark": "自动创建的默认订阅链接",
        "port": 10086,
        "protocol": "vless-reality",
        "camouflage_host": "www.microsoft.com",
        "ws_path": "/",
        "enabled": True,
        "status": "draft",
        "status_text": "订阅链接已创建，节点启用后会进入订阅内容",
        "created_at": now,
        "updated_at": now,
    }
    write_json(SUBSCRIPTION_LINKS_FILE, [link])
    return link

def delete_subscription_link(link_id: str) -> tuple[bool, int]:
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    next_links = [item for item in links if item.get("id") != link_id]
    if len(next_links) == len(links):
        return False, 0

    nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    deleted_node_ids = {str(item.get("id") or "") for item in nodes if item.get("subscription_id") == link_id}
    next_nodes = [item for item in nodes if item.get("subscription_id") != link_id]

    write_json(SUBSCRIPTION_LINKS_FILE, next_links)
    if deleted_node_ids:
        write_json(SUBSCRIPTION_NODES_FILE, next_nodes)
        routing_rules = read_json_list(ROUTING_RULES_FILE)
        next_rules = []
        rules_changed = False
        for item in routing_rules:
            inbound_ids = item.get("inbound_node_ids")
            if isinstance(inbound_ids, list):
                remaining_ids = [str(value) for value in inbound_ids if str(value) not in deleted_node_ids]
                if not remaining_ids:
                    rules_changed = True
                    continue
                if len(remaining_ids) != len(inbound_ids):
                    rules_changed = True
                item["inbound_node_ids"] = remaining_ids
                item["inbound_node_id"] = remaining_ids[0]
                next_rules.append(item)
            elif str(item.get("inbound_node_id") or "") not in deleted_node_ids:
                next_rules.append(item)
            else:
                rules_changed = True
        if rules_changed:
            write_json(ROUTING_RULES_FILE, next_rules)
        try:
            sync_panel_subscription_nodes_to_xray(True)
        except Exception as e:
            print(f"[ERROR] Syncing after subscription link deletion failed: {e}", flush=True)
    return True, len(deleted_node_ids)

def set_subscription_link_enabled(link_id: str, enabled: bool) -> tuple[dict[str, Any] | None, str]:
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    for item in links:
        if item.get("id") == link_id:
            item["enabled"] = bool(enabled)
            item["updated_at"] = current_timestamp()
            write_json(SUBSCRIPTION_LINKS_FILE, links)
            return item, ""
    return None, "订阅链接不存在"

def validate_subscription_node_payload(payload: dict[str, Any], existing_nodes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    subscription_id = str(payload.get("subscription_id") or "").strip()
    add_to_subscription = payload.get("add_to_subscription") is not False
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    if add_to_subscription and not subscription_id:
        subscription_id = str(ensure_default_subscription_link().get("id") or "")
        links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    
    name = str(payload.get("name") or "").strip()
    if not name:
        return None, "节点名称不能为空"

    link = next((item for item in links if item.get("id") == subscription_id), None) if add_to_subscription else None
    if add_to_subscription and not link:
        return None, "订阅链接不存在"

    if link:
        protocol = str(link.get("protocol") or "vless-reality").strip().lower()
        port = int(link.get("port") or 10086)
        camouflage_host = clean_hostname(link.get("camouflage_host"))
    else:
        protocol = str(payload.get("protocol") or "vless-reality").strip().lower()
        if protocol not in {item["id"] for item in ALLOWED_SUBSCRIPTION_PROTOCOLS}:
            return None, f"协议类型不支持: {protocol}"
        try:
            port = int(payload.get("port"))
        except (TypeError, ValueError):
            return None, "独立节点端口必须是数字"
        if not (1 <= port <= 65535):
            return None, "独立节点端口必须在 1 至 65535 之间"
        current_id = str(payload.get("id") or "").strip()
        for item in links:
            if int(item.get("port") or 0) == port:
                return None, f"端口 {port} 已被订阅链接占用"
        for item in existing_nodes:
            if str(item.get("id") or "") != current_id and not item.get("subscription_id") and int(item.get("port") or 0) == port:
                return None, f"端口 {port} 已被其他独立节点占用"
        camouflage_host = clean_hostname(payload.get("camouflage_host"))

    uuid_value = str(payload.get("uuid") or "").strip()
    socks_username = clean_proxy_credential(payload.get("socks_username") or payload.get("username"))
    socks_password = clean_proxy_credential(payload.get("socks_password") or payload.get("password"))

    if protocol == "socks5":
        uuid_value = ""
        if not socks_username:
            socks_username = f"user_{uuid.uuid4().hex[:12]}"
        if not socks_password:
            socks_password = f"pwd_{uuid.uuid4().hex[:12]}"
        if not (3 <= len(socks_username) <= 64):
            return None, "SOCKS5 用户名长度必须在 3 至 64 位之间"
        if not (6 <= len(socks_password) <= 96):
            return None, "SOCKS5 密码长度必须在 6 至 96 位之间"
    else:
        socks_username = ""
        socks_password = ""
        if not uuid_value:
            uuid_value = str(uuid.uuid4())
        try:
            uuid_value = str(uuid.UUID(uuid_value))
        except ValueError:
            return None, "UUID 格式不正确"
        if protocol == "vless-reality":
            if not camouflage_host:
                return None, "独立 VLESS-Reality 节点的伪装网址不能为空"
            if not re.match(r"^[a-z0-9.-]+$", camouflage_host):
                return None, "伪装网址只允许填写域名"

    now = current_timestamp()
    node_id = str(payload.get("id") or "") or f"subnode-{uuid.uuid4().hex[:12]}"
    existing = next((item for item in existing_nodes if str(item.get("id") or "") == node_id), {})
    reality_private_key = str(payload.get("reality_private_key") or existing.get("reality_private_key") or "").strip()
    reality_public_key = str(payload.get("reality_public_key") or existing.get("reality_public_key") or "").strip()
    reality_short_id = str(payload.get("reality_short_id") or existing.get("reality_short_id") or "").strip()
    reality_mldsa65_seed = str(payload.get("reality_mldsa65_seed") or existing.get("reality_mldsa65_seed") or "").strip()
    reality_mldsa65_verify = str(payload.get("reality_mldsa65_verify") or existing.get("reality_mldsa65_verify") or "").strip()
    reality_spider_x = str(payload.get("reality_spider_x") or existing.get("reality_spider_x") or "").strip()
    if not link and protocol == "vless-reality":
        if not reality_private_key or not reality_public_key:
            priv, pub = generate_reality_keys()
            if priv and pub:
                reality_private_key = priv
                reality_public_key = pub
        if not reality_short_id:
            import secrets
            reality_short_id = secrets.token_hex(8)
        if not reality_spider_x:
            import random
            rand_hex = "".join(random.choice("0123456789abcdef") for _ in range(random.randint(8, 16)))
            reality_spider_x = f"/{rand_hex}"

    return {
        "id": node_id,
        "subscription_id": subscription_id if link else "",
        "name": name,
        "protocol": protocol,
        "port": port,
        "uuid": uuid_value,
        "camouflage_host": camouflage_host,
        "ws_path": str(payload.get("ws_path") or existing.get("ws_path") or "/"),
        "reality_private_key": reality_private_key,
        "reality_public_key": reality_public_key,
        "reality_short_id": reality_short_id,
        "reality_mldsa65_seed": reality_mldsa65_seed,
        "reality_mldsa65_verify": reality_mldsa65_verify,
        "reality_spider_x": reality_spider_x,
        "socks_username": socks_username,
        "socks_password": socks_password,
        "enabled": bool(payload.get("enabled", True)),
        "outbound_node_id": str(payload.get("outbound_node_id") or ""),
        "status": "draft",
        "status_text": "未接入 Xray",
        "created_at": str(payload.get("created_at") or now),
        "updated_at": now,
    }, ""

def save_subscription_node(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    node, error = validate_subscription_node_payload(payload, nodes)
    if error or node is None:
        return None, error
    updated = False
    for idx, item in enumerate(nodes):
        if item.get("id") == node["id"]:
            nodes[idx] = node
            updated = True
            break
    if not updated:
        nodes.append(node)
    write_json(SUBSCRIPTION_NODES_FILE, nodes)
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing subscription nodes to Xray failed: {e}", flush=True)
    return node, ""

def delete_subscription_node(node_id: str) -> bool:
    nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    next_nodes = [item for item in nodes if item.get("id") != node_id]
    if len(next_nodes) == len(nodes):
        return False
    write_json(SUBSCRIPTION_NODES_FILE, next_nodes)
    routing_rules = read_json_list(ROUTING_RULES_FILE)
    next_rules = []
    rules_changed = False
    for item in routing_rules:
        inbound_ids = item.get("inbound_node_ids")
        if isinstance(inbound_ids, list):
            remaining_ids = [str(value) for value in inbound_ids if str(value) != node_id]
            if not remaining_ids:
                rules_changed = True
                continue
            if len(remaining_ids) != len(inbound_ids):
                rules_changed = True
            item["inbound_node_ids"] = remaining_ids
            item["inbound_node_id"] = remaining_ids[0]
            next_rules.append(item)
        elif str(item.get("inbound_node_id") or "") != node_id:
            next_rules.append(item)
        else:
            rules_changed = True
    if rules_changed:
        write_json(ROUTING_RULES_FILE, next_rules)
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing subscription nodes to Xray failed: {e}", flush=True)
    return True

def normalize_id_list(value: Any, fallback: Any = "") -> list[str]:
    raw_values = value if isinstance(value, list) else []
    if not raw_values and fallback:
        raw_values = [fallback]
    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw_values:
        item_id = str(item or "").strip()
        if item_id and item_id not in seen:
            normalized.append(item_id)
            seen.add(item_id)
    return normalized

def set_subscription_node_enabled(node_id: str, enabled: bool) -> tuple[dict[str, Any] | None, str]:
    nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    for item in nodes:
        if item.get("id") == node_id:
            item["enabled"] = bool(enabled)
            item["updated_at"] = current_timestamp()
            write_json(SUBSCRIPTION_NODES_FILE, nodes)
            try:
                sync_panel_subscription_nodes_to_xray(True)
            except Exception as e:
                print(f"[ERROR] Syncing subscription nodes to Xray failed: {e}", flush=True)
            return item, ""
    return None, "订阅节点不存在"

def inject_outbound_sockopt(outbound: dict, interface: str) -> None:
    if not interface:
        return
    import sys
    if sys.platform.startswith("linux"):
        from pathlib import Path
        if not Path(f"/sys/class/net/{interface}").exists():
            return
    stream_settings = outbound.setdefault("streamSettings", {})
    sockopt = stream_settings.setdefault("sockopt", {})
    sockopt["interface"] = interface

def valid_wireguard_key(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        raw = base64.b64decode(value.strip(), validate=True)
        return len(raw) == 32
    except Exception:
        return False

def valid_warp_address(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        ipaddress.ip_interface(value.strip())
        return True
    except Exception:
        return False

def is_valid_warp_node(node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict) or node.get("type") != "warp":
        return False
    endpoint = str(node.get("endpoint") or "").strip()
    addresses = node.get("addresses") or []
    return (
        valid_wireguard_key(node.get("private_key"))
        and valid_wireguard_key(node.get("peer_public_key"))
        and isinstance(addresses, list)
        and any(valid_warp_address(item) for item in addresses)
        and ":" in endpoint
    )

def build_warp_outbound(node: dict[str, Any], tag: str) -> dict[str, Any]:
    endpoint = str(node.get("endpoint") or "engage.cloudflareclient.com:2408").strip()
    reserved = node.get("reserved") or [0, 0, 0]
    try:
        reserved = [int(x) for x in reserved[:3]]
    except Exception:
        reserved = [0, 0, 0]
    if len(reserved) < 3:
        reserved = reserved + [0] * (3 - len(reserved))

    return {
        "protocol": "wireguard",
        "settings": {
            "secretKey": str(node.get("private_key") or "").strip(),
            "address": [str(item).strip() for item in (node.get("addresses") or []) if valid_warp_address(item)],
            "peers": [
                {
                    "publicKey": str(node.get("peer_public_key") or "").strip(),
                    "endpoint": endpoint,
                    "allowedIPs": ["0.0.0.0/0", "::/0"],
                    "keepAlive": 25
                }
            ],
            "reserved": reserved[:3],
            "mtu": 1280,
            "noKernelTun": True,
            "domainStrategy": "ForceIPv4"
        },
        "tag": tag
    }

def validate_outbound_node_payload(payload: dict[str, Any], existing_nodes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    name = str(payload.get("name") or "").strip()
    json_config = str(payload.get("json_config") or "").strip()
    if not name:
        return None, "节点名称不能为空"
    if not json_config:
        return None, "JSON 配置不能为空"
    try:
        json_data = json.loads(json_config)
        if not isinstance(json_data, dict):
            return None, "JSON 配置根对象必须是字典 (Object)"
    except Exception as e:
        return None, f"JSON 格式不正确: {e}"
    
    current_id = str(payload.get("id") or "").strip()
    node_id = current_id or f"outbound-{uuid.uuid4().hex[:12]}"
    now = current_timestamp()
    
    return {
        "id": node_id,
        "name": name,
        "type": "json-config",
        "json_config": json_config,
        "enabled": payload.get("enabled", True) is not False,
        "created_at": str(payload.get("created_at") or now),
        "updated_at": now
    }, ""

def save_outbound_node(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    nodes = read_json_list(OUTBOUND_NODES_FILE)
    node, error = validate_outbound_node_payload(payload, nodes)
    if error or node is None:
        return None, error
    updated = False
    for idx, item in enumerate(nodes):
        if item.get("id") == node["id"]:
            nodes[idx] = node
            updated = True
            break
    if not updated:
        nodes.append(node)
    write_json(OUTBOUND_NODES_FILE, nodes)
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing outbound nodes to Xray failed: {e}", flush=True)
    return node, ""

def delete_outbound_node(node_id: str) -> bool:
    nodes = read_json_list(OUTBOUND_NODES_FILE)
    next_nodes = [item for item in nodes if item.get("id") != node_id]
    if len(next_nodes) == len(nodes):
        return False
    write_json(OUTBOUND_NODES_FILE, next_nodes)
    
    routing_rules = read_json_list(ROUTING_RULES_FILE)
    rules_changed = False
    for rule in routing_rules:
        if rule.get("outbound_node_id") == node_id:
            rule["outbound_node_id"] = "vpn-out"
            rules_changed = True
        ids = rule.get("outbound_node_ids", [])
        if node_id in ids:
            rule["outbound_node_ids"] = [x for x in ids if x != node_id] or ["vpn-out"]
            rules_changed = True
    if rules_changed:
        write_json(ROUTING_RULES_FILE, routing_rules)
        
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing after outbound node deletion failed: {e}", flush=True)
    return True

def set_outbound_node_enabled(node_id: str, enabled: bool) -> tuple[dict[str, Any] | None, str]:
    nodes = read_json_list(OUTBOUND_NODES_FILE)
    for item in nodes:
        if item.get("id") == node_id:
            item["enabled"] = bool(enabled)
            item["updated_at"] = current_timestamp()
            write_json(OUTBOUND_NODES_FILE, nodes)
            try:
                sync_panel_subscription_nodes_to_xray(True)
            except Exception as e:
                print(f"[ERROR] Syncing outbound node status failed: {e}", flush=True)
            return item, ""
    return None, "出站节点不存在"

def safe_b64decode(s: str) -> str:
    s = s.strip()
    s = s.replace("-", "+").replace("_", "/")
    padding = len(s) % 4
    if padding == 2:
        s += "=="
    elif padding == 3:
        s += "="
    try:
        return base64.b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def parse_share_link(link: str) -> tuple[str, str, str]:
    link = link.strip()
    if not link:
        raise ValueError("链接不能为空")

    known_prefixes = ("vless://", "vmess://", "ss://", "socks://", "socks5://", "trojan://", "http://", "https://")

    # 1. If the entire link is Base64 encoded, decode it and parse recursively
    if not any(link.startswith(prefix) for prefix in known_prefixes):
        decoded = safe_b64decode(link)
        if decoded and any(decoded.startswith(prefix) for prefix in known_prefixes):
            return parse_share_link(decoded)

    name = "Imported Node"
    proto = ""
    
    hash_part = ""
    if "#" in link:
        link, hash_part = link.split("#", 1)
        name = urllib.parse.unquote(hash_part).strip()

    # 2. Support prefix + Base64 format (e.g. vless://[BASE64])
    for prefix in ("vless://", "trojan://", "socks://", "socks5://", "ss://"):
        if link.startswith(prefix) and "@" not in link:
            content = link[len(prefix):].strip()
            decoded = safe_b64decode(content)
            if decoded:
                if any(decoded.startswith(p) for p in known_prefixes):
                    suffix = f"#{hash_part}" if hash_part else ""
                    return parse_share_link(f"{decoded}{suffix}")
                elif "@" in decoded:
                    suffix = f"#{hash_part}" if hash_part else ""
                    return parse_share_link(f"{prefix}{decoded}{suffix}")
        
    if link.startswith("vmess://"):
        proto = "vmess"
        b64_data = link[8:].strip()
        decoded = safe_b64decode(b64_data)
        if not decoded:
            raise ValueError("VMess base64 解码失败")
        try:
            data = json.loads(decoded)
        except Exception as e:
            raise ValueError(f"VMess JSON 解析失败: {e}")
            
        add = data.get("add") or ""
        port = data.get("port") or 443
        uuid_val = data.get("id") or ""
        aid = data.get("aid") or 0
        net = data.get("net") or "tcp"
        tls = data.get("tls") or "none"
        host = data.get("host") or ""
        path = data.get("path") or ""
        sni = data.get("sni") or ""
        ps = data.get("ps") or ""
        if ps:
            name = ps
            
        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": add,
                    "port": int(port),
                    "users": [{
                        "id": uuid_val,
                        "alterId": int(aid),
                        "security": "auto"
                    }]
                }]
            },
            "streamSettings": {
                "network": net,
                "security": tls
            }
        }
        if net == "ws":
            ws_settings = {"path": path or "/"}
            if host:
                ws_settings["headers"] = {"Host": host}
            outbound["streamSettings"]["wsSettings"] = ws_settings
            
        if tls == "tls":
            tls_settings = {}
            if sni or host:
                tls_settings["serverName"] = sni or host
            outbound["streamSettings"]["tlsSettings"] = tls_settings
            
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    elif link.startswith("vless://"):
        proto = "vless"
        content = link[8:].strip()
        if "@" not in content:
            raise ValueError("VLESS 链接格式错误：缺少 @ 符号")
        uuid_val, rest = content.split("@", 1)
        
        params = {}
        address_port = rest
        if "?" in rest:
            address_port, query = rest.split("?", 1)
            params = urllib.parse.parse_qs(query)
            params = {k: v[0] for k, v in params.items() if v}
            
        if ":" not in address_port:
            raise ValueError("VLESS 链接格式错误：端口缺失")
        address, port_str = address_port.rsplit(":", 1)
        if address.startswith("[") and address.endswith("]"):
            address = address[1:-1]
            
        port = int(port_str)
        security = params.get("security", "none")
        network = params.get("type", "tcp")
        flow = params.get("flow", "")
        sni = params.get("sni", "")
        pbk = params.get("pbk", "")
        sid = params.get("sid", "")
        fp = params.get("fp", "")
        path = params.get("path", "")
        host = params.get("host", "")
        
        user_entry = {
            "id": uuid_val,
            "encryption": "none"
        }
        if flow:
            user_entry["flow"] = flow

        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": address,
                    "port": port,
                    "users": [user_entry]
                }]
            },
            "streamSettings": {
                "network": network,
                "security": security
            }
        }
        if security == "reality":
            reality_settings = {
                "publicKey": pbk,
                "shortId": sid,
                "serverName": sni or address,
                "fingerprint": fp or "chrome"
            }
            spx = params.get("spx", "")
            pqv = params.get("pqv", "")
            if spx:
                reality_settings["spiderX"] = spx
            if pqv:
                reality_settings["mldsa65Verify"] = pqv
            outbound["streamSettings"]["realitySettings"] = reality_settings
        elif security == "tls":
            tls_settings = {}
            if sni:
                tls_settings["serverName"] = sni
            outbound["streamSettings"]["tlsSettings"] = tls_settings
            
        if network == "ws":
            ws_settings = {"path": path or "/"}
            if host:
                ws_settings["headers"] = {"Host": host}
            outbound["streamSettings"]["wsSettings"] = ws_settings
            
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    elif link.startswith("ss://"):
        proto = "shadowsocks"
        content = link[5:].strip()
        method, password, address, port = "", "", "", 443
        if "@" in content:
            userinfo_b64, rest = content.split("@", 1)
            userinfo = safe_b64decode(userinfo_b64)
            if not userinfo:
                userinfo = userinfo_b64
            if ":" in userinfo:
                method, password = userinfo.split(":", 1)
            else:
                method = "aes-256-gcm"
                password = userinfo
                
            if "?" in rest:
                rest = rest.split("?", 1)[0]
            if ":" in rest:
                address, port_str = rest.rsplit(":", 1)
                port = int(port_str)
            else:
                address = rest
        else:
            decoded = safe_b64decode(content)
            if decoded and "@" in decoded:
                userinfo, rest = decoded.split("@", 1)
                if ":" in userinfo:
                    method, password = userinfo.split(":", 1)
                if ":" in rest:
                    address, port_str = rest.rsplit(":", 1)
                    port = int(port_str)
                else:
                    address = rest
            else:
                raise ValueError("Shadowsocks 链接解析失败：无法解析配置信息")
                
        outbound = {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": address,
                    "port": port,
                    "method": method,
                    "password": password
                }]
            }
        }
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    elif link.startswith("trojan://"):
        proto = "trojan"
        content = link[9:].strip()
        if "@" not in content:
            raise ValueError("Trojan 链接格式错误：缺少 @ 符号")
        password, rest = content.split("@", 1)
        params = {}
        address_port = rest
        if "?" in rest:
            address_port, query = rest.split("?", 1)
            params = urllib.parse.parse_qs(query)
            params = {k: v[0] for k, v in params.items() if v}
            
        if ":" not in address_port:
            raise ValueError("Trojan 链接格式错误：端口缺失")
            
        address, port_str = address_port.rsplit(":", 1)
        port = int(port_str)
        sni = params.get("sni", "")
        host = params.get("host", "")
        network = params.get("type", "tcp")
        path = params.get("path", "")
        
        outbound = {
            "protocol": "trojan",
            "settings": {
                "servers": [{
                    "address": address,
                    "port": port,
                    "password": password
                }]
            },
            "streamSettings": {
                "network": network,
                "security": "tls",
                "tlsSettings": {
                    "serverName": sni or host or address,
                    "allowInsecure": False
                }
            }
        }
        if network == "ws":
            ws_settings = {"path": path or "/"}
            if host:
                ws_settings["headers"] = {"Host": host}
            outbound["streamSettings"]["wsSettings"] = ws_settings
            
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    elif link.startswith("socks://") or link.startswith("socks5://"):
        proto = "socks"
        prefix_len = 8 if link.startswith("socks://") else 9
        content = link[prefix_len:].strip()
        username, password, address, port = "", "", "", 1080
        if "@" in content:
            userinfo, rest = content.split("@", 1)
            decoded = safe_b64decode(userinfo)
            if decoded and ":" in decoded:
                username, password = decoded.split(":", 1)
            elif ":" in userinfo:
                username, password = userinfo.split(":", 1)
            else:
                username = userinfo
                
            if ":" in rest:
                address, port_str = rest.rsplit(":", 1)
                port = int(port_str)
            else:
                address = rest
        else:
            if ":" in content:
                address, port_str = content.rsplit(":", 1)
                port = int(port_str)
            else:
                address = content
                
        outbound = {
            "protocol": "socks",
            "settings": {
                "servers": [{
                    "address": address,
                    "port": port,
                    "users": [{
                        "user": username,
                        "pass": password
                    }] if username else []
                }]
            }
        }
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    elif link.startswith("http://") or link.startswith("https://"):
        parsed = urllib.parse.urlparse(link)
        proto = "http"
        address = parsed.hostname or ""
        if not address:
            raise ValueError("HTTP 代理链接格式错误：缺少服务器地址")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        user = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        server: dict[str, Any] = {
            "address": address,
            "port": int(port)
        }
        if user:
            server["users"] = [{
                "user": user,
                "pass": password
            }]

        outbound = {
            "protocol": "http",
            "settings": {
                "servers": [server]
            }
        }
        if parsed.scheme == "https":
            outbound["streamSettings"] = {
                "security": "tls",
                "tlsSettings": {
                    "serverName": address
                }
            }
        return proto, name, json.dumps(outbound, ensure_ascii=False, indent=2)

    else:
        raise ValueError("不支持的分享链接协议类型")

def register_warp_account() -> dict[str, Any]:
    private_key, public_key = generate_wireguard_keys()
    if not private_key or not public_key:
        raise ValueError("无法生成 WireGuard x25519 密钥")
        
    install_id = str(uuid.uuid4())
    payload = {
        "key": public_key,
        "install_id": install_id,
        "fcm_token": "",
        "referrer": "",
        "warp_enabled": False,
        "tos": "2021-09-03T00:00:00.000+02:00",
        "type": "Android",
        "locale": "en_US"
    }
    
    urls = [
        "https://api.cloudflareclient.com/v0a4005/reg",
        "https://api.cloudflareclient.com/v0a2158/reg",
        "https://api.cloudflareclient.com/v0a1922/reg",
    ]
    last_error = ""
    resp_data = None
    for url in urls:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "User-Agent": "okhttp/3.12.1",
                "Content-Type": "application/json; charset=utf-8"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                resp_data = json.loads(res.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = f"{url}: HTTP {e.code} {e.reason} {body[:300]}".strip()
        except Exception as e:
            last_error = f"{url}: {e}"
    if not isinstance(resp_data, dict):
        raise ValueError(f"向 Cloudflare 注册设备失败: {last_error or '未知错误'}")
        
    device_id = resp_data.get("id")
    token = resp_data.get("token")
    config_data = resp_data.get("config", {})
    if not device_id or not token or not config_data:
        raise ValueError("注册返回数据缺失核心属性")
        
    interface = config_data.get("interface", {})
    addresses = interface.get("addresses", {})
    
    v4_addr = addresses.get("v4")
    v6_addr = addresses.get("v6")
    
    addr_list = []
    if v4_addr:
        addr_list.append(v4_addr if "/" in v4_addr else f"{v4_addr}/32")
    if v6_addr:
        addr_list.append(v6_addr if "/" in v6_addr else f"{v6_addr}/128")
        
    peers = config_data.get("peers", [])
    peer_pub = ""
    endpoint = "engage.cloudflareclient.com:2408"
    if peers:
        peer = peers[0]
        peer_pub = peer.get("public_key")
        peer_endpoint = peer.get("endpoint", {})
        if isinstance(peer_endpoint, dict):
            endpoint = peer_endpoint.get("host") or endpoint
        elif isinstance(peer_endpoint, str) and peer_endpoint:
            endpoint = peer_endpoint
            
    reserved = [0, 0, 0]
    client_id_val = None
    if isinstance(config_data, dict):
        client_id_val = config_data.get("client", {}).get("client_id")
        if not client_id_val:
            client_id_val = config_data.get("client_id")
    if not client_id_val and isinstance(resp_data, dict):
        client_id_val = resp_data.get("client_id")

    if client_id_val:
        if isinstance(client_id_val, list):
            try:
                reserved = [int(x) for x in client_id_val[:3]]
            except Exception:
                pass
        elif isinstance(client_id_val, str):
            client_id_val = client_id_val.strip()
            try:
                missing_padding = len(client_id_val) % 4
                b64_str = client_id_val
                if missing_padding:
                    b64_str += '=' * (4 - missing_padding)
                decoded = base64.b64decode(b64_str)
                if len(decoded) >= 3:
                    reserved = list(decoded[:3])
                elif len(decoded) > 0:
                    reserved = list(decoded) + [0] * (3 - len(decoded))
            except Exception:
                try:
                    if client_id_val.startswith("0x") or client_id_val.startswith("0X"):
                        val = int(client_id_val, 16)
                        reserved = list(val.to_bytes(3, byteorder="big"))
                    elif len(client_id_val) == 6 and all(c in "0123456789abcdefABCDEF" for c in client_id_val):
                        val = int(client_id_val, 16)
                        reserved = list(val.to_bytes(3, byteorder="big"))
                    else:
                        parts = client_id_val.replace("[", "").replace("]", "").split(",")
                        if len(parts) >= 3:
                            reserved = [int(p.strip()) for p in parts[:3]]
                except Exception:
                    pass
        
    return {
        "id": "warp",
        "name": "Cloudflare WARP",
        "type": "warp",
        "addresses": addr_list,
        "endpoint": endpoint,
        "account_id": device_id,
        "private_key": private_key,
        "public_key": public_key,
        "peer_public_key": peer_pub,
        "token": token,
        "reserved": reserved,
        "enabled": True,
        "created_at": current_timestamp(),
        "updated_at": current_timestamp()
    }

def test_warp_via_proxy() -> dict[str, Any]:
    nodes = read_json_list(OUTBOUND_NODES_FILE)
    node = next((item for item in nodes if item.get("type") == "warp"), None)
    if not node:
        return {"ok": False, "error": "WARP 配置不存在，请先注册设备"}
    if not is_valid_warp_node(node):
        return {"ok": False, "error": "WARP 配置无效，请重新启动生成真实配置"}

    binary_path = xray_binary_path()
    if not binary_path:
        return {"ok": False, "error": "未检测到 Xray Core，无法进行测试"}

    warp_outbound = build_warp_outbound(node, "test-warp")

    port = free_local_port()
    config_path = DATA_DIR / f"xray_warp_test_{uuid.uuid4().hex[:8]}.json"
    test_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "http",
                "settings": {"timeout": 10},
                "tag": "test-http"
            }
        ],
        "outbounds": [warp_outbound],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["test-http"],
                    "outboundTag": "test-warp"
                }
            ]
        }
    }

    proc: subprocess.Popen[str] | None = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(test_config, f, ensure_ascii=False, indent=2)
        proc = subprocess.Popen(
            [binary_path, "run", "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT_DIR),
        )
        time.sleep(1.2)
        if proc.poll() is not None:
            output = ""
            try:
                output = (proc.stdout.read() if proc.stdout else "")[-1200:]
            except Exception:
                pass
            return {"ok": False, "error": diagnose_xray_failure(output.splitlines(), "测试 Xray 进程启动失败")}

        return test_ip_lookup_via_http_proxy(f"http://127.0.0.1:{port}")
    except Exception as e:
        return {
            "ok": False,
            "error": f"请求超时或失败: {e}"
        }
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            config_path.unlink(missing_ok=True)
        except Exception:
            pass

def test_ip_lookup_via_http_proxy(proxy_url: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    checks = [
        ("https://api.ipify.org?format=json", lambda data: {"ip": data.get("ip", ""), "location": ""}),
        ("https://ipinfo.io/json", lambda data: {"ip": data.get("ip", ""), "location": " ".join(str(data.get(k) or "") for k in ("country", "city")).strip()}),
        ("http://ip-api.com/json", lambda data: {"ip": data.get("query", ""), "location": f"{data.get('country', '')} {data.get('city', '')}".strip()}),
    ]
    errors: list[str] = []
    for url, parser in checks:
        started = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=12) as response:
                latency = int((time.time() - started) * 1000)
                data = json.loads(response.read().decode("utf-8"))
            parsed = parser(data)
            if parsed.get("ip"):
                return {
                    "ok": True,
                    "ip": parsed.get("ip", ""),
                    "location": parsed.get("location", ""),
                    "latency_ms": latency
                }
            errors.append(f"{url}: 未返回出口 IP")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    return {"ok": False, "error": "出口 IP 查询失败: " + " | ".join(errors[-3:])}

def free_local_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        try:
            sock.close()
        except Exception:
            pass

def test_outbound_node_via_temp_xray(node_id: str) -> dict[str, Any]:
    binary_path = xray_binary_path()
    if not binary_path:
        return {"ok": False, "error": "未检测到 Xray Core，无法测试自定义出站节点"}

    nodes = read_json_list(OUTBOUND_NODES_FILE)
    node = next((item for item in nodes if str(item.get("id") or "") == node_id), None)
    if not node:
        return {"ok": False, "error": "出站节点不存在"}
    if node.get("type") != "json-config":
        return {"ok": False, "error": "当前仅支持测试自定义 JSON 出站节点"}

    try:
        outbound = json.loads(str(node.get("json_config") or "{}"))
        if not isinstance(outbound, dict):
            return {"ok": False, "error": "出站节点 JSON 根对象必须是 Object"}
    except Exception as exc:
        return {"ok": False, "error": f"出站节点 JSON 解析失败: {exc}"}

    test_tag = "test-outbound"
    outbound["tag"] = test_tag
    port = free_local_port()
    config_path = DATA_DIR / f"xray_outbound_test_{uuid.uuid4().hex[:8]}.json"
    test_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "http",
                "settings": {"timeout": 10},
                "tag": "test-http"
            }
        ],
        "outbounds": [outbound],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["test-http"],
                    "outboundTag": test_tag
                }
            ]
        }
    }

    proc: subprocess.Popen[str] | None = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(test_config, f, ensure_ascii=False, indent=2)
        proc = subprocess.Popen(
            [binary_path, "run", "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT_DIR),
        )
        time.sleep(1.0)
        if proc.poll() is not None:
            output = ""
            try:
                output = (proc.stdout.read() if proc.stdout else "")[-1200:]
            except Exception:
                pass
            return {"ok": False, "error": diagnose_xray_failure(output.splitlines(), "测试 Xray 进程启动失败")}

        return test_ip_lookup_via_http_proxy(f"http://127.0.0.1:{port}")
    except Exception as exc:
        return {"ok": False, "error": f"测试请求失败: {exc}"}
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            config_path.unlink(missing_ok=True)
        except Exception:
            pass

def validate_routing_rule_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    name = str(payload.get("name") or "").strip()
    inbound_node_ids = normalize_id_list(payload.get("inbound_node_ids"), payload.get("inbound_node_id"))
    outbound_node_ids = normalize_id_list(payload.get("outbound_node_ids"), payload.get("outbound_node_id"))
    raw_conditions = payload.get("match_conditions")
    match_conditions: list[dict[str, str]] = []
    if isinstance(raw_conditions, list):
        for item in raw_conditions:
            if not isinstance(item, dict):
                continue
            cond_type = str(item.get("type") or "all").strip().lower()
            cond_value = str(item.get("value") or "").strip()
            if cond_type in ("all", "domain", "ip", "port"):
                match_conditions.append({"type": cond_type, "value": cond_value})
    if not match_conditions:
        match_conditions = [{
            "type": str(payload.get("match_type") or "all").strip().lower(),
            "value": str(payload.get("match_value") or "").strip(),
        }]

    if not name:
        return None, "规则名称不能为空"
    if not inbound_node_ids:
        return None, "请至少选择一个入站节点"
    if not outbound_node_ids:
        return None, "请至少选择一个出站节点"
    for condition in match_conditions:
        if condition["type"] not in ("all", "domain", "ip", "port"):
            return None, "匹配方式不支持"
        if condition["type"] != "all" and not condition["value"]:
            return None, "匹配内容不能为空"

    now = current_timestamp()
    rule_id = str(payload.get("id") or "").strip() or f"rule-{uuid.uuid4().hex[:12]}"
    primary_condition = match_conditions[0]
    return {
        "id": rule_id,
        "name": name,
        "inbound_node_id": inbound_node_ids[0],
        "inbound_node_ids": inbound_node_ids,
        "outbound_node_id": outbound_node_ids[0],
        "outbound_node_ids": outbound_node_ids,
        "match_type": primary_condition["type"],
        "match_value": primary_condition["value"],
        "match_conditions": match_conditions,
        "enabled": bool(payload.get("enabled", True)),
        "priority": int(payload.get("priority") or 100),
        "status": "draft",
        "status_text": "未写入 Xray",
        "created_at": str(payload.get("created_at") or now),
        "updated_at": now,
    }, ""

def save_routing_rule(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    rules = read_json_list(ROUTING_RULES_FILE)
    rule, error = validate_routing_rule_payload(payload)
    if error or rule is None:
        return None, error
    updated = False
    for idx, item in enumerate(rules):
        if item.get("id") == rule["id"]:
            rules[idx] = rule
            updated = True
            break
    if not updated:
        rules.append(rule)
    write_json(ROUTING_RULES_FILE, rules)
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing routing rules to Xray failed: {e}", flush=True)
    
    # Reload rule to return the updated status_text
    rules = read_json_list(ROUTING_RULES_FILE)
    updated_rule = next((r for r in rules if r.get("id") == rule["id"]), rule)
    return updated_rule, ""

def delete_routing_rule(rule_id: str) -> bool:
    rules = read_json_list(ROUTING_RULES_FILE)
    next_rules = [item for item in rules if item.get("id") != rule_id]
    if len(next_rules) == len(rules):
        return False
    write_json(ROUTING_RULES_FILE, next_rules)
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as e:
        print(f"[ERROR] Syncing after routing rule deletion failed: {e}", flush=True)
    return True

def set_routing_rule_enabled(rule_id: str, enabled: bool) -> tuple[dict[str, Any] | None, str]:
    rules = read_json_list(ROUTING_RULES_FILE)
    for item in rules:
        if item.get("id") == rule_id:
            item["enabled"] = bool(enabled)
            item["updated_at"] = current_timestamp()
            write_json(ROUTING_RULES_FILE, rules)
            try:
                sync_panel_subscription_nodes_to_xray(True)
            except Exception as e:
                print(f"[ERROR] Syncing routing rule status failed: {e}", flush=True)
            # Reload rules to return updated status/status_text
            rules = read_json_list(ROUTING_RULES_FILE)
            updated_rule = next((r for r in rules if r.get("id") == rule_id), item)
            return updated_rule, ""
    return None, "路由规则不存在"

def get_public_ip_or_domain() -> str:
    now = time.time()
    if state.cached_public_ip and (now - state.cached_public_ip_time < 600):
        return state.cached_public_ip

    try:
        ui_cfg = load_ui_config()
        domain = ui_cfg.get("domain", "").strip()
        if domain:
            state.cached_public_ip = domain
            state.cached_public_ip_time = now
            return domain
    except Exception:
        pass

    for url in ["https://api.ipify.org", "http://ip.sb", "https://ipinfo.io/ip"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.81.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                ip = response.read().decode("utf-8").strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                    state.cached_public_ip = ip
                    state.cached_public_ip_time = now
                    return ip
        except Exception:
            pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_xray_share_link(inbound: dict, client: dict, host: str) -> str:
    protocol = inbound.get("protocol", "vless").lower()
    port = inbound.get("port", 10086)
    network = inbound.get("network", "tcp").lower()
    ws_path = inbound.get("ws_path", "/")
    client_name = client.get("name", "client")
    remark = f"{client_name}@{protocol.upper()}_{port}"

    if protocol == "vless":
        uuid_val = client.get("uuid", "")
        link = f"vless://{uuid_val}@{host}:{port}?type={network}&security=none"
        if network == "ws":
            link += f"&path={urllib.parse.quote(ws_path)}"
        link += f"#{urllib.parse.quote(remark)}"
        return link

    elif protocol == "vmess":
        uuid_val = client.get("uuid", "")
        vmess_json = {
            "v": "2",
            "ps": remark,
            "add": host,
            "port": str(port),
            "id": uuid_val,
            "aid": "0",
            "scy": "auto",
            "net": network,
            "type": "none",
            "host": "",
            "path": ws_path if network == "ws" else "",
            "tls": "none"
        }
        json_str = json.dumps(vmess_json, ensure_ascii=False)
        b64_str = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
        return f"vmess://{b64_str}"

    elif protocol == "trojan":
        pwd_val = client.get("password", "")
        link = f"trojan://{pwd_val}@{host}:{port}?type={network}&security=none"
        if network == "ws":
            link += f"&path={urllib.parse.quote(ws_path)}"
        link += f"#{urllib.parse.quote(remark)}"
        return link

    elif protocol == "shadowsocks":
        cipher = inbound.get("encryption", "aes-256-gcm")
        pwd_val = client.get("password") or inbound.get("password", "")
        credentials = f"{cipher}:{pwd_val}"
        b64_cred = base64.b64encode(credentials.encode("utf-8")).decode("utf-8").rstrip("=")
        link = f"ss://{b64_cred}@{host}:{port}#{urllib.parse.quote(remark)}"
        return link

    return ""

def generate_panel_node_share_link(node: dict[str, Any], host: str) -> str:
    sub_id = node.get("subscription_id")
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    link = next((l for l in links if l.get("id") == sub_id), None)
    if not link:
        return ""
        
    protocol = str(link.get("protocol") or node.get("protocol") or "").lower()
    port = int(link.get("port") or node.get("port") or 0)
    uuid_value = str(node.get("uuid") or "")
    camouflage_host = clean_hostname(link.get("camouflage_host") or node.get("camouflage_host"))
    remark = str(node.get("name") or f"{protocol}-{port}")

    if not protocol or not port:
        return ""

    if protocol == "vless-reality":
        if not uuid_value:
            return ""
        params = {
            "encryption": "none",
            "flow": "xtls-rprx-vision",
            "security": "reality",
            "type": "tcp",
            "headerType": "none",
            "sni": camouflage_host or "www.microsoft.com",
            "fp": "chrome",
        }
        public_key = str(link.get("reality_public_key") or node.get("reality_public_key") or node.get("public_key") or "").strip()
        short_id = str(link.get("reality_short_id") or node.get("reality_short_id") or node.get("short_id") or "").strip()
        mldsa_verify = str(link.get("reality_mldsa65_verify") or node.get("reality_mldsa65_verify") or "").strip()
        spider_x = str(link.get("reality_spider_x") or node.get("reality_spider_x") or "/").strip()
        
        if public_key:
            params["pbk"] = public_key
        if short_id:
            params["sid"] = short_id
        if spider_x:
            params["spx"] = spider_x
        if mldsa_verify:
            params["pqv"] = mldsa_verify
            
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        return f"vless://{uuid_value}@{host}:{port}?{query}#{urllib.parse.quote(remark)}"

    if protocol == "vmess-ws-tls":
        if not uuid_value:
            return ""
        vmess_json = {
            "v": "2",
            "ps": remark,
            "add": host,
            "port": str(port),
            "id": uuid_value,
            "aid": "0",
            "scy": "auto",
            "net": "ws",
            "type": "none",
            "host": camouflage_host,
            "path": str(link.get("ws_path") or node.get("ws_path") or "/"),
            "tls": "tls",
            "sni": camouflage_host,
        }
        json_str = json.dumps(vmess_json, ensure_ascii=False)
        b64_str = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
        return f"vmess://{b64_str}"

    if protocol == "socks5":
        username = str(node.get("socks_username") or node.get("username") or uuid_value or "").strip()
        password = str(node.get("socks_password") or node.get("password") or "").strip()
        if username and password:
            credentials = f"{urllib.parse.quote(username)}:{urllib.parse.quote(password)}@"
        elif username:
            credentials = f"{urllib.parse.quote(username)}@"
        else:
            credentials = ""
        return f"socks://{credentials}{host}:{port}#{urllib.parse.quote(remark)}"

    return ""

def build_panel_subscription_content(token: str) -> tuple[bool, bytes, HTTPStatus]:
    ensure_panel_framework_files()
    cleaned_token = clean_subscription_token(token)
    links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    link = next((item for item in links if clean_subscription_token(item.get("token")) == cleaned_token), None)
    if not link:
        return False, b"", HTTPStatus.NOT_FOUND
    if link.get("enabled") is False:
        return True, "Invalid or inactive subscription token".encode("utf-8"), HTTPStatus.NOT_FOUND

    host = get_public_ip_or_domain()
    nodes = [
        item for item in read_json_list(SUBSCRIPTION_NODES_FILE)
        if item.get("subscription_id") == link.get("id") and item.get("enabled") is True
    ]
    links_text = [item for item in (generate_panel_node_share_link(node, host) for node in nodes) if item]
    sub_content = "\n".join(links_text) + ("\n" if links_text else "")
    encoded_sub = base64.b64encode(sub_content.encode("utf-8"))
    return True, encoded_sub, HTTPStatus.OK
