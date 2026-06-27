from __future__ import annotations

import base64
from http import HTTPStatus
from typing import Any, Callable

from backend.app.config import SUBSCRIPTION_NODES_FILE
from backend.app.db import ensure_panel_framework_files, read_json_list
from backend.app.core.xray import (
    delete_routing_rule, delete_subscription_link, delete_subscription_node,
    generate_panel_node_share_link, get_public_ip_or_domain,
    save_routing_rule, save_subscription_link, save_subscription_node,
    set_routing_rule_enabled, set_subscription_link_enabled,
    set_subscription_node_enabled,
)


HandlerLike = Any


def handle_panel_post(handler: HandlerLike, path: str) -> bool:
    routes: dict[str, Callable[[HandlerLike], None]] = {
        "/api/panel/subscription-links": save_link,
        "/api/panel/subscription-links/delete": delete_link,
        "/api/panel/subscription-links/toggle": toggle_link,
        "/api/panel/subscription-nodes": save_node,
        "/api/panel/subscription-nodes/delete": delete_node,
        "/api/panel/subscription-nodes/toggle": toggle_node,
        "/api/panel/subscription-nodes/share-link": share_node_link,
        "/api/panel/routing-rules": save_rule,
        "/api/panel/routing-rules/delete": delete_rule,
        "/api/panel/routing-rules/toggle": toggle_rule,
    }
    route = routes.get(path)
    if not route:
        return False

    try:
        ensure_panel_framework_files()
        route(handler)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
    return True


def save_link(handler: HandlerLike) -> None:
    subscription, error = save_subscription_link(handler.read_json_body())
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
        return
    handler.send_json({"ok": True, "subscription": subscription, "message": "订阅链接已保存。"})


def delete_link(handler: HandlerLike) -> None:
    link_id = str(handler.read_json_body().get("id") or "").strip()
    if not link_id:
        handler.send_json({"ok": False, "error": "缺少订阅链接 ID"}, HTTPStatus.BAD_REQUEST)
        return
    deleted, deleted_nodes = delete_subscription_link(link_id)
    if not deleted:
        handler.send_json({"ok": False, "error": "订阅链接不存在"}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "message": f"订阅链接已删除，{deleted_nodes} 个包含的节点链接已同步删除。"})


def toggle_link(handler: HandlerLike) -> None:
    payload = handler.read_json_body()
    subscription, error = set_subscription_link_enabled(
        str(payload.get("id") or "").strip(),
        bool(payload.get("enabled", False)),
    )
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "subscription": subscription, "message": "订阅链接状态已更新。"})


def save_node(handler: HandlerLike) -> None:
    node, error = save_subscription_node(handler.read_json_body())
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
        return
    handler.send_json({"ok": True, "node": node, "message": "订阅节点已保存。"})


def delete_node(handler: HandlerLike) -> None:
    node_id = str(handler.read_json_body().get("id") or "").strip()
    if not node_id:
        handler.send_json({"ok": False, "error": "缺少订阅节点 ID"}, HTTPStatus.BAD_REQUEST)
        return
    if not delete_subscription_node(node_id):
        handler.send_json({"ok": False, "error": "订阅节点不存在"}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "message": "订阅节点已删除。"})


def toggle_node(handler: HandlerLike) -> None:
    payload = handler.read_json_body()
    node, error = set_subscription_node_enabled(
        str(payload.get("id") or "").strip(),
        bool(payload.get("enabled", False)),
    )
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "node": node, "message": "订阅节点状态已更新。"})


def share_node_link(handler: HandlerLike) -> None:
    node_id = str(handler.read_json_body().get("id") or "").strip()
    if not node_id:
        handler.send_json({"ok": False, "error": "缺少订阅节点 ID"}, HTTPStatus.BAD_REQUEST)
        return
    node = next((n for n in read_json_list(SUBSCRIPTION_NODES_FILE) if n.get("id") == node_id), None)
    if not node:
        handler.send_json({"ok": False, "error": "订阅节点不存在"}, HTTPStatus.NOT_FOUND)
        return
    link = generate_panel_node_share_link(node, get_public_ip_or_domain())
    if link and link.startswith(("vless://", "socks://", "ss://", "trojan://")):
        link = base64.b64encode(link.encode("utf-8")).decode("utf-8")
    handler.send_json({"ok": True, "node": {"name": node.get("name", ""), "link": link}})


def save_rule(handler: HandlerLike) -> None:
    payload = handler.read_json_body()
    rule, error = save_routing_rule(payload)
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
        return
    message = "路由规则已保存并应用。" if payload.get("apply_immediately") is not False else "路由规则已创建为草稿，尚未应用到 Xray。"
    handler.send_json({"ok": True, "rule": rule, "message": message})


def delete_rule(handler: HandlerLike) -> None:
    rule_id = str(handler.read_json_body().get("id") or "").strip()
    if not rule_id:
        handler.send_json({"ok": False, "error": "缺少路由规则 ID"}, HTTPStatus.BAD_REQUEST)
        return
    if not delete_routing_rule(rule_id):
        handler.send_json({"ok": False, "error": "路由规则不存在"}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "message": "路由规则已删除。"})


def toggle_rule(handler: HandlerLike) -> None:
    payload = handler.read_json_body()
    rule, error = set_routing_rule_enabled(
        str(payload.get("id") or "").strip(),
        bool(payload.get("enabled", False)),
    )
    if error:
        handler.send_json({"ok": False, "error": error}, HTTPStatus.NOT_FOUND)
        return
    handler.send_json({"ok": True, "rule": rule, "message": "路由规则状态已更新。"})
