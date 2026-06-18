import json
import time
import re
import datetime
from pathlib import Path
from typing import Any

from backend.app.state import lock
from backend.app.config import (
    DATA_DIR, CONFIG_DIR, AUTH_FILE, OPENVPN_AUTH_USER, OPENVPN_AUTH_PASS,
    BLACKLIST_FILE, INVALID_BACKOFF_SECONDS, SUBSCRIPTION_NODES_FILE,
    SUBSCRIPTION_LINKS_FILE, OUTBOUND_NODES_FILE, ROUTING_RULES_FILE,
    PANEL_MENUS, ALLOWED_OUTBOUND_TYPES, VPNGATE_ONLY_MODE, SERVICE_MODE,
    ALLOWED_SUBSCRIPTION_PROTOCOLS, FEATURE_FLAGS_FILE
)

# Global tracker for old logs cleanup throttle
_last_cleanup_time = 0.0
XRAY_TRAFFIC_FILE = DATA_DIR / "client_traffic.json"
DEFAULT_FEATURE_FLAGS = {
    "vpngate_enabled": False,
    "warp_enabled": False,
    "custom_enabled": True,
}

def write_json(path: Path, data: Any) -> None:
    with lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

def read_json(path: Path, default: Any) -> Any:
    with lock:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

def read_json_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]

def ensure_panel_framework_files() -> None:
    # We defer migrate_subscription_hierarchy imports to avoid circular import issues
    from backend.app.core.xray import migrate_subscription_hierarchy
    defaults: list[tuple[Path, Any]] = [
        (SUBSCRIPTION_NODES_FILE, []),
        (SUBSCRIPTION_LINKS_FILE, []),
        (OUTBOUND_NODES_FILE, []),
        (ROUTING_RULES_FILE, []),
    ]
    for path, default in defaults:
        if not path.exists():
            write_json(path, default)
    migrate_subscription_hierarchy()

def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    CONFIG_DIR.mkdir(exist_ok=True, parents=True)
    if not AUTH_FILE.exists():
        AUTH_FILE.write_text(f"{OPENVPN_AUTH_USER}\n{OPENVPN_AUTH_PASS}\n", encoding="utf-8")
        try:
            AUTH_FILE.chmod(0o600)
        except OSError:
            pass
    if not FEATURE_FLAGS_FILE.exists():
        write_json(FEATURE_FLAGS_FILE, DEFAULT_FEATURE_FLAGS.copy())
    ensure_panel_framework_files()

def load_feature_flags() -> dict[str, bool]:
    raw = read_json(FEATURE_FLAGS_FILE, {})
    flags = DEFAULT_FEATURE_FLAGS.copy()
    if isinstance(raw, dict):
        for key in flags:
            flags[key] = raw.get(key) is True
    flags["custom_enabled"] = True
    return flags

def save_feature_flags(flags: dict[str, Any]) -> dict[str, bool]:
    normalized = DEFAULT_FEATURE_FLAGS.copy()
    if isinstance(flags, dict):
        for key in normalized:
            normalized[key] = flags.get(key) is True
    normalized["custom_enabled"] = True
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    write_json(FEATURE_FLAGS_FILE, normalized)
    return normalized

def load_panel_framework_state() -> dict[str, Any]:
    subscription_nodes = read_json_list(SUBSCRIPTION_NODES_FILE)
    subscription_links = read_json_list(SUBSCRIPTION_LINKS_FILE)
    outbound_nodes = read_json_list(OUTBOUND_NODES_FILE)
    routing_rules = read_json_list(ROUTING_RULES_FILE)
    menus = PANEL_MENUS
    allowed_outbound_types = ALLOWED_OUTBOUND_TYPES
    if VPNGATE_ONLY_MODE:
        menus = [item for item in PANEL_MENUS if item["id"] in {"host", "nodes", "settings"}]
        allowed_outbound_types = [item for item in ALLOWED_OUTBOUND_TYPES if item["id"] == "vpngate-openvpn"]
    return {
        "ok": True,
        "status": "framework_ready",
        "message": "基础框架已预留，具体创建和路由功能将按开发计划逐步接入。",
        "service_mode": SERVICE_MODE,
        "feature_flags": load_feature_flags(),
        "menus": menus,
        "allowed_subscription_protocols": ALLOWED_SUBSCRIPTION_PROTOCOLS,
        "allowed_outbound_types": allowed_outbound_types,
        "files": {
            "subscription_nodes": str(SUBSCRIPTION_NODES_FILE),
            "subscription_links": str(SUBSCRIPTION_LINKS_FILE),
            "outbound_nodes": str(OUTBOUND_NODES_FILE),
            "routing_rules": str(ROUTING_RULES_FILE),
        },
    }

def generate_random_password() -> str:
    import string
    import random
    chars = string.ascii_letters + string.digits
    while True:
        pwd = "".join(random.choices(chars, k=12))
        has_lower = any(c.islower() for c in pwd)
        has_upper = any(c.isupper() for c in pwd)
        has_digit = any(c.isdigit() for c in pwd)
        if has_lower and has_upper and has_digit:
            return pwd

def generate_random_username() -> str:
    import string
    import random
    chars = string.ascii_letters + string.digits
    while True:
        uname = "".join(random.choices(chars, k=12))
        if uname[0].isalpha():
            has_lower = any(c.islower() for c in uname)
            has_upper = any(c.isupper() for c in uname)
            has_digit = any(c.isdigit() for c in uname)
            if has_lower and has_upper and has_digit:
                return uname

def current_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def load_ui_config() -> dict[str, Any]:
    import os
    from backend.app.config import UI_HOST, UI_PORT, LOCAL_PROXY_PORT
    with lock:
        auth_file = DATA_DIR / "ui_auth.json"
        config = {
            "username": os.environ.get("UI_USERNAME", ""),
            "secret_path": os.environ.get("SECRET_PATH") or "EJsW2EeBo9lY",
            "password": os.environ.get("UI_PASSWORD", ""),
            "host": UI_HOST,
            "port": UI_PORT,
            "proxy_port": LOCAL_PROXY_PORT,
            "routing_mode": "auto",
            "force_country": "",
            "domain": "",
            "tls_cert_file": "",
            "tls_key_file": "",
            "domain_certs": [],
        }
        updated = False
        if auth_file.exists():
            try:
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                for key, val in data.items():
                    config[key] = val
                for key in ["domain", "tls_cert_file", "tls_key_file", "domain_certs"]:
                    if key not in data:
                        updated = True
            except Exception:
                pass

        if not config.get("username"):
            config["username"] = generate_random_username()
            updated = True

        if not config.get("password"):
            config["password"] = generate_random_password()
            updated = True

        if not auth_file.exists() or updated:
            try:
                DATA_DIR.mkdir(exist_ok=True, parents=True)
                auth_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        return config


def load_client_traffic() -> dict:
    if XRAY_TRAFFIC_FILE.exists():
        try:
            with open(XRAY_TRAFFIC_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_client_traffic(data: dict) -> None:
    try:
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        with open(XRAY_TRAFFIC_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[错误] 写入 client_traffic.json 失败: {e}", flush=True)

def load_traffic_stats() -> dict[str, Any]:
    data = read_json(DATA_DIR / "traffic.json", {})
    this_month_start = datetime.datetime(datetime.date.today().year, datetime.date.today().month, 1).timestamp()
    stats = {
        "accumulated_rx": data.get("accumulated_rx", 0),
        "accumulated_tx": data.get("accumulated_tx", 0),
        "lifetime_rx": data.get("lifetime_rx", data.get("accumulated_rx", 0)),
        "lifetime_tx": data.get("lifetime_tx", data.get("accumulated_tx", 0)),
        "billing_cycle_start": data.get("billing_cycle_start", this_month_start)
    }
    try:
        if datetime.date.fromtimestamp(stats["billing_cycle_start"]).month != datetime.date.today().month:
            stats["accumulated_rx"] = 0
            stats["accumulated_tx"] = 0
            stats["billing_cycle_start"] = this_month_start
            write_json(DATA_DIR / "traffic.json", stats)
    except Exception:
        pass
    return stats

def save_traffic_stats(rx: int, tx: int) -> None:
    stats = load_traffic_stats()
    stats["accumulated_rx"] += rx
    stats["accumulated_tx"] += tx
    stats["lifetime_rx"] = stats.get("lifetime_rx", 0) + rx
    stats["lifetime_tx"] = stats.get("lifetime_tx", 0) + tx
    write_json(DATA_DIR / "traffic.json", stats)

def record_hourly_traffic(total_bytes: int) -> None:
    history_file = DATA_DIR / "traffic_history.json"
    history = read_json(history_file, [])
    if not isinstance(history, list):
        history = []
        
    now_dt = datetime.datetime.now()
    hour_str = now_dt.strftime("%H:00")
    
    should_record = True
    if history:
        last_item = history[-1]
        if last_item.get("date") == now_dt.strftime("%Y-%m-%d") and last_item.get("hour") == hour_str:
            should_record = False
            last_item["bytes"] = total_bytes
            write_json(history_file, history)
            
    if should_record:
        history.append({
            "date": now_dt.strftime("%Y-%m-%d"),
            "hour": hour_str,
            "bytes": total_bytes
        })
        if len(history) > 24:
            history = history[-24:]
        write_json(history_file, history)

def cleanup_old_logs(logs_dir: Path) -> None:
    global _last_cleanup_time
    now = time.time()
    with lock:
        if now - _last_cleanup_time < 3600:
            return
        _last_cleanup_time = now
    try:
        three_days_sec = 3 * 24 * 60 * 60
        for path in logs_dir.glob("*.json"):
            match = re.match(r"^(\d{4}-\d{2}-\d{2})\.json$", path.name)
            if match:
                date_str = match.group(1)
                try:
                    file_time = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
                    today_str = time.strftime("%Y-%m-%d", time.localtime())
                    today_time = time.mktime(time.strptime(today_str, "%Y-%m-%d"))
                    if today_time - file_time >= three_days_sec:
                        with lock:
                            path.unlink()
                        print(f"[清理] 已删除3天前的旧日志文件: {path.name}", flush=True)
                except Exception:
                    if now - path.stat().st_mtime > three_days_sec:
                        with lock:
                            path.unlink()
    except Exception as e:
        print(f"[清理错误] 清理旧日志失败: {e}", flush=True)

def log_to_json(level: str, module: str, message: str) -> None:
    try:
        logs_dir = DATA_DIR / "logs"
        logs_dir.mkdir(exist_ok=True, parents=True)
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        log_file = logs_dir / f"{date_str}.json"
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "level": level,
            "module": module,
            "message": message
        }
        with lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        cleanup_old_logs(logs_dir)
    except Exception as e:
        print(f"[Log Error] Failed to write JSON log: {e}", flush=True)

def load_blacklist() -> dict[str, dict[str, Any]]:
    now = time.time()
    raw = read_json(BLACKLIST_FILE, {})
    if not isinstance(raw, dict):
        return {}

    valid: dict[str, dict[str, Any]] = {}
    changed = False
    for key, value in raw.items():
        if not isinstance(value, dict):
            changed = True
            continue
        try:
            marked_at = float(value.get("marked_at") or value.get("time") or 0)
        except (TypeError, ValueError):
            marked_at = 0
        if marked_at <= 0 or now - marked_at >= INVALID_BACKOFF_SECONDS:
            changed = True
            continue
        valid[str(key)] = value

    if changed:
        write_json(BLACKLIST_FILE, valid)
    return valid

def mark_blacklisted(node: dict[str, Any], message: str) -> None:
    if not node:
        return

    identifiers = [
        str(node.get("id") or "").strip(),
        str(node.get("ip") or "").strip(),
        str(node.get("remote_host") or "").strip(),
    ]
    identifiers = [item for item in identifiers if item]
    if not identifiers:
        return

    entry = {
        "marked_at": time.time(),
        "message": message or "节点检测失败",
        "id": node.get("id", ""),
        "country": node.get("country", ""),
        "ip": node.get("ip", ""),
        "remote_host": node.get("remote_host", ""),
        "remote_port": node.get("remote_port", ""),
        "proto": node.get("proto", ""),
    }
    blacklist = load_blacklist()
    for identifier in identifiers:
        blacklist[identifier] = entry
    write_json(BLACKLIST_FILE, blacklist)
    log_to_json(
        "WARNING",
        "VPN",
        f"节点已加入临时退避名单 {node.get('id') or node.get('ip')}: {message}，{INVALID_BACKOFF_SECONDS // 60} 分钟内不再重复选择",
    )

def set_state(**updates: Any) -> None:
    current_state = get_state()
    current_state.update(updates)
    from backend.app.config import STATE_FILE
    write_json(STATE_FILE, current_state)

def get_state() -> dict[str, Any]:
    from backend.app import state
    from backend.app.core.vpn import active_openvpn_running
    from backend.app.config import (
        STATE_FILE, SERVICE_MODE, API_URL, TARGET_VALID_NODES,
        FETCH_INTERVAL_SECONDS, CHECK_INTERVAL_SECONDS,
        LOCAL_PROXY_HOST, LOCAL_PROXY_PORT
    )
    current_state = read_json(STATE_FILE, {})
    current_state["active_openvpn_node_id"] = state.active_openvpn_node_id
    current_state["openvpn_enabled"] = state.openvpn_enabled
    current_state["openvpn_running"] = active_openvpn_running()
    current_state["is_connecting"] = state.is_connecting
    current_state["service_mode"] = SERVICE_MODE
    current_state.setdefault("api_url", API_URL)
    current_state.setdefault("target_valid_nodes", TARGET_VALID_NODES)
    current_state.setdefault("fetch_interval_seconds", FETCH_INTERVAL_SECONDS)
    current_state.setdefault("check_interval_seconds", CHECK_INTERVAL_SECONDS)
    _proxy_display = f"[{LOCAL_PROXY_HOST}]" if ":" in LOCAL_PROXY_HOST else LOCAL_PROXY_HOST
    current_state.setdefault("local_proxy", f"http://{_proxy_display}:{LOCAL_PROXY_PORT}")
    current_state.setdefault("last_fetch_status", "not_started")
    current_state.setdefault("last_check_message", "")
    current_state.setdefault("blacklisted_nodes", 0)
    current_state["feature_flags"] = load_feature_flags()

    # Pre-populate settings inputs in UI
    ui_cfg = load_ui_config()
    current_state["username"] = ui_cfg.get("username", "admin")
    current_state["port"] = ui_cfg.get("port", 8787)
    current_state["proxy_port"] = ui_cfg.get("proxy_port", LOCAL_PROXY_PORT)
    current_state["secret_path"] = ui_cfg.get("secret_path", "EJsW2EeBo9lY")
    current_state["routing_mode"] = ui_cfg.get("routing_mode", "auto")
    current_state["force_country"] = ui_cfg.get("force_country", "")
    current_state["domain"] = ui_cfg.get("domain", "")
    current_state["tls_cert_file"] = ui_cfg.get("tls_cert_file", "")
    current_state["tls_key_file"] = ui_cfg.get("tls_key_file", "")
    current_state["domain_certs"] = ui_cfg.get("domain_certs", [])

    return current_state
