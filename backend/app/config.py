import os
import sys
from pathlib import Path

ROOT_DIR = Path(sys.executable).resolve().parent if globals().get("__compiled__") else Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = Path(os.environ["VPNGATE_DATA_DIR"]).resolve() if os.environ.get("VPNGATE_DATA_DIR") else ROOT_DIR / "vpngate_data"
CONFIG_DIR = DATA_DIR / "configs"

NODES_FILE = DATA_DIR / "nodes.json"
STATE_FILE = DATA_DIR / "state.json"
AUTH_FILE = DATA_DIR / "vpngate_auth.txt"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
FEATURE_FLAGS_FILE = DATA_DIR / "feature_flags.json"

XRAY_CFG_FILE = DATA_DIR / "xray_cfg.json"
XRAY_CONFIG_FILE = DATA_DIR / "xray_config.json"
SUBSCRIPTION_NODES_FILE = DATA_DIR / "subscription_nodes.json"
SUBSCRIPTION_LINKS_FILE = DATA_DIR / "subscription_links.json"
OUTBOUND_NODES_FILE = DATA_DIR / "outbound_nodes.json"
ROUTING_RULES_FILE = DATA_DIR / "routing_rules.json"

API_URL = "https://www.vpngate.net/api/iphone/"
FETCH_INTERVAL_SECONDS = int(os.environ.get("FETCH_INTERVAL_SECONDS", "960"))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "960"))
TARGET_VALID_NODES = int(os.environ.get("TARGET_VALID_NODES", "3"))
MAX_SCAN_ROWS = int(os.environ.get("MAX_SCAN_ROWS", "300"))
OPENVPN_TEST_TIMEOUT_SECONDS = int(os.environ.get("OPENVPN_TEST_TIMEOUT_SECONDS", "35"))
OPENVPN_CMD = os.environ.get("OPENVPN_CMD", "openvpn")
OPENVPN_AUTH_USER = os.environ.get("OPENVPN_AUTH_USER", "vpn")
OPENVPN_AUTH_PASS = os.environ.get("OPENVPN_AUTH_PASS", "vpn")
LOCAL_PROXY_HOST = os.environ.get("LOCAL_PROXY_HOST", os.environ.get("PROXY_HOST", "::"))
LOCAL_PROXY_PORT = int(os.environ.get("LOCAL_PROXY_PORT", os.environ.get("PROXY_PORT", "7928")))
UI_HOST = os.environ.get("UI_HOST", "::")
UI_PORT = int(os.environ.get("UI_PORT", "8787"))
INVALID_BACKOFF_SECONDS = int(os.environ.get("INVALID_BACKOFF_SECONDS", str(30 * 60)))
SERVICE_MODE = os.environ.get("AIMILI_SERVICE_MODE", "full").strip().lower() or "full"
VPNGATE_ONLY_MODE = SERVICE_MODE in {"vpngate", "vpngate-only", "vpn"}

PANEL_MENUS = [
    {"id": "host", "name": "控制台", "tab": "tab-host"},
    {"id": "xray", "name": "订阅节点", "tab": "tab-xray"},
    {"id": "nodes", "name": "落地代理", "tab": "tab-nodes"},
    {"id": "gateway", "name": "路由规则", "tab": "tab-gateway"},
    {"id": "settings", "name": "面板设置", "tab": "tab-settings"},
]
ALLOWED_SUBSCRIPTION_PROTOCOLS = [
    {"id": "vless-reality", "name": "VLESS-Reality"},
    {"id": "vmess-ws-tls", "name": "VMess + WS + TLS"},
    {"id": "socks5", "name": "SOCKS5"},
]
ALLOWED_OUTBOUND_TYPES = [
    {"id": "vpngate-openvpn", "name": "VPNGate OpenVPN"},
    {"id": "warp", "name": "Cloudflare WARP"},
]
