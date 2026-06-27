from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any

from backend.app.config import OUTBOUND_NODES_FILE
from backend.app.core.vpn import maintain_valid_nodes, stop_openvpn_service
from backend.app.core.xray import sync_panel_subscription_nodes_to_xray, xray_event
from backend.app.db import load_feature_flags, read_json_list, save_feature_flags, write_json


VALID_FEATURE_KEYS = {"vpngate_enabled", "warp_enabled", "custom_enabled"}


def handle_feature_toggle(handler: Any) -> None:
    try:
        payload = handler.read_json_body()
        key = str(payload.get("key") or "").strip()
        enabled = payload.get("enabled") is True
        if key not in VALID_FEATURE_KEYS:
            handler.send_json({"ok": False, "error": "未知功能开关"}, HTTPStatus.BAD_REQUEST)
            return

        flags = load_feature_flags()
        flags[key] = enabled
        flags = save_feature_flags(flags)

        if key == "vpngate_enabled":
            message = toggle_vpngate(enabled)
        elif key == "warp_enabled":
            message = toggle_warp(enabled)
        else:
            message = toggle_custom(enabled)

        handler.send_json({"ok": True, "features": flags, "message": message})
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def toggle_vpngate(enabled: bool) -> str:
    if enabled:
        threading.Thread(target=maintain_valid_nodes, args=(False,), daemon=True).start()
        return "VPNGate 公益节点已开启，正在后台加载节点资源。"
    stop_openvpn_service("VPNGate 功能已关闭")
    return "VPNGate 公益节点已关闭，OpenVPN 已停止。"


def toggle_warp(enabled: bool) -> str:
    if not enabled:
        nodes = read_json_list(OUTBOUND_NODES_FILE)
        if any(node.get("type") == "warp" for node in nodes):
            write_json(OUTBOUND_NODES_FILE, [node for node in nodes if node.get("type") != "warp"])
            sync_panel_best_effort("WARP 关闭后同步 Xray 失败")
    return "Cloudflare WARP 已开启。" if enabled else "Cloudflare WARP 已关闭，出站配置已删除。"


def toggle_custom(enabled: bool) -> str:
    if not enabled:
        nodes = read_json_list(OUTBOUND_NODES_FILE)
        changed = False
        for node in nodes:
            if node.get("type") in ("custom-node", "subscription", "json-config") and node.get("enabled") is not False:
                node["enabled"] = False
                changed = True
        if changed:
            write_json(OUTBOUND_NODES_FILE, nodes)
            sync_panel_best_effort("自定义节点关闭后同步 Xray 失败")
    return "自定义节点已开启。" if enabled else "自定义节点已关闭。"


def sync_panel_best_effort(message: str) -> None:
    try:
        sync_panel_subscription_nodes_to_xray(True)
    except Exception as exc:
        xray_event("WARNING", f"{message}: {exc}")
