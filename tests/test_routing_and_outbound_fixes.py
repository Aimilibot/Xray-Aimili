from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core import vpn, xray


class RoutingAndOutboundFixTests(unittest.TestCase):
    def xray_storage(self, tmp: str):
        data_dir = Path(tmp)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patcher in [
            patch.object(xray, "DATA_DIR", data_dir),
            patch.object(xray, "SUBSCRIPTION_LINKS_FILE", data_dir / "subscription_links.json"),
            patch.object(xray, "SUBSCRIPTION_NODES_FILE", data_dir / "subscription_nodes.json"),
            patch.object(xray, "OUTBOUND_NODES_FILE", data_dir / "outbound_nodes.json"),
            patch.object(xray, "ROUTING_RULES_FILE", data_dir / "routing_rules.json"),
            patch.object(xray, "XRAY_CFG_FILE", data_dir / "xray_cfg.json"),
            patch.object(xray, "XRAY_CONFIG_FILE", data_dir / "xray_config.json"),
        ]:
            stack.enter_context(patcher)
        return stack

    def test_base64_subscription_text_imports_first_shadowrocket_node(self) -> None:
        raw = "ss://YWVzLTI1Ni1nY206cGFzcw@example.com:8388#HK-01\n"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

        proto, name, config = xray.parse_share_link(encoded)
        outbound = json.loads(config)

        self.assertEqual(proto, "shadowsocks")
        self.assertEqual(name, "HK-01")
        self.assertEqual(outbound["settings"]["servers"][0]["address"], "example.com")

    def test_save_imported_subscription_node_keeps_type_and_can_build_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(parents=True, exist_ok=True)
            with self.xray_storage(tmp):
                node, error = xray.save_outbound_node({
                    "name": "sub-import",
                    "type": "subscription",
                    "subscription_url": "https://example.com/sub",
                    "json_config": json.dumps({
                        "protocol": "freedom",
                        "settings": {}
                    }),
                })

                self.assertEqual(error, "")
                self.assertEqual(node["type"], "subscription")
                self.assertTrue(xray.write_xray_config(xray.default_xray_cfg()))
                generated = json.loads(xray.XRAY_CONFIG_FILE.read_text(encoding="utf-8"))
                self.assertTrue(any(item.get("tag") == node["id"] for item in generated["outbounds"]))

    def test_create_routing_rule_draft_does_not_sync_xray(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(parents=True, exist_ok=True)
            with self.xray_storage(tmp), patch.object(xray, "sync_panel_subscription_nodes_to_xray", lambda restart_service=True: calls.append(restart_service)):
                rule, error = xray.save_routing_rule({
                    "name": "draft-rule",
                    "inbound_node_ids": ["subnode-1"],
                    "outbound_node_ids": ["warp"],
                    "match_conditions": [{"type": "domain", "value": "example.com"}],
                    "apply_immediately": False,
                })

                self.assertEqual(error, "")
                self.assertEqual(calls, [])
                self.assertIn("尚未应用", rule["status_text"])

    def test_delete_warp_node_removes_warp_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(parents=True, exist_ok=True)
            with self.xray_storage(tmp), patch.object(xray, "sync_panel_subscription_nodes_to_xray", lambda restart_service=True: None):
                xray.write_json(xray.OUTBOUND_NODES_FILE, [
                    {"id": "warp", "type": "warp"},
                    {"id": "custom-1", "type": "json-config"},
                ])

                self.assertTrue(xray.delete_warp_node())
                nodes = xray.read_json_list(xray.OUTBOUND_NODES_FILE)
                self.assertEqual([item["id"] for item in nodes], ["custom-1"])

    def test_policy_routing_is_not_error_when_openvpn_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nodes_file = Path(tmp) / "nodes.json"
            nodes_file.write_text("[]", encoding="utf-8")
            with patch.object(vpn, "NODES_FILE", nodes_file), \
                 patch.object(vpn, "active_openvpn_running", lambda: False), \
                 patch.object(vpn.sys, "platform", "linux"), \
                 patch.object(vpn.socket, "getaddrinfo", lambda *args, **kwargs: []), \
                 patch.object(vpn.socket, "socket") as socket_cls:
                socket_cls.return_value.settimeout.return_value = None
                socket_cls.return_value.connect.return_value = None
                socket_cls.return_value.close.return_value = None

                health = vpn.check_layered_health()

                self.assertTrue(health["policy_routing"]["ok"])
                self.assertIn("OpenVPN 未连接", health["policy_routing"]["details"])

    def test_policy_routing_requires_rule_and_default_route_for_active_tun(self) -> None:
        def fake_run(cmd, *args, **kwargs):
            class Result:
                returncode = 0
                stdout = ""

            result = Result()
            if cmd[:3] == ["ip", "rule", "show"]:
                result.stdout = "1000: from all oif tun7 lookup 100\n"
            elif cmd[:4] == ["ip", "route", "show", "table"]:
                result.stdout = "default dev tun7 scope link\n"
            return result

        with patch.object(vpn.subprocess, "run", side_effect=fake_run):
            self.assertTrue(vpn.policy_routing_is_configured("tun7"))
            self.assertFalse(vpn.policy_routing_is_configured("tun0"))

    def test_domain_rules_can_bind_same_inbound_to_different_outbounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(parents=True, exist_ok=True)
            with self.xray_storage(tmp):
                xray.write_json(xray.SUBSCRIPTION_LINKS_FILE, [{
                    "id": "sub-36060",
                    "name": "入口 36060",
                    "token": "tok",
                    "port": 36060,
                    "protocol": "vless-reality",
                    "enabled": True,
                    "created_at": "1",
                    "updated_at": "1",
                }])
                xray.write_json(xray.SUBSCRIPTION_NODES_FILE, [{
                    "id": "node-36060",
                    "subscription_id": "sub-36060",
                    "name": "端口 36060",
                    "protocol": "vless-reality",
                    "port": 36060,
                    "uuid": "11111111-1111-4111-8111-111111111111",
                    "enabled": True,
                }])
                xray.write_json(xray.OUTBOUND_NODES_FILE, [{
                    "id": "custom-site-a",
                    "name": "自定义节点 A",
                    "type": "json-config",
                    "enabled": True,
                    "json_config": json.dumps({"protocol": "freedom", "settings": {}}),
                }])
                xray.write_json(xray.ROUTING_RULES_FILE, [
                    {
                        "id": "rule-warp",
                        "name": "站点 A 走 WARP",
                        "inbound_node_ids": ["sub-36060"],
                        "outbound_node_ids": ["warp"],
                        "match_conditions": [{"type": "domain", "value": "site-a.example"}],
                        "enabled": True,
                        "priority": 10,
                    },
                    {
                        "id": "rule-custom",
                        "name": "站点 B 走自定义",
                        "inbound_node_ids": ["sub-36060"],
                        "outbound_node_ids": ["custom-site-a"],
                        "match_conditions": [{"type": "domain", "value": "site-b.example"}],
                        "enabled": True,
                        "priority": 20,
                    },
                ])
                valid_warp = {
                    "id": "warp",
                    "type": "warp",
                    "enabled": True,
                    "private_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "peer_public_key": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    "reserved": [1, 2, 3],
                    "addresses": ["172.16.0.2/32"],
                    "endpoint": "engage.cloudflareclient.com:2408",
                }
                nodes = xray.read_json_list(xray.OUTBOUND_NODES_FILE)
                xray.write_json(xray.OUTBOUND_NODES_FILE, nodes + [valid_warp])

                self.assertTrue(xray.write_xray_config(xray.default_xray_cfg()))
                generated = json.loads(xray.XRAY_CONFIG_FILE.read_text(encoding="utf-8"))
                rules = generated["routing"]["rules"]

                self.assertTrue(any(rule.get("domain") == ["site-a.example"] and rule.get("outboundTag") == "warp" for rule in rules))
                self.assertTrue(any(rule.get("domain") == ["site-b.example"] and rule.get("outboundTag") == "custom-site-a" for rule in rules))

    def test_vpngate_outbound_uses_active_openvpn_interface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sys_net = Path(tmp) / "sys" / "class" / "net"
            (sys_net / "tun7").mkdir(parents=True)
            with self.xray_storage(tmp), \
                 patch.object(xray.sys, "platform", "linux"), \
                 patch.object(xray, "SYS_CLASS_NET", sys_net), \
                 patch.object(xray.vpn_utils, "ACTIVE_TUN_DEVICE", "tun7"):
                cfg = xray.default_xray_cfg()
                cfg["outbound_interface"] = "tun0"

                self.assertTrue(xray.write_xray_config(cfg))
                generated = json.loads(xray.XRAY_CONFIG_FILE.read_text(encoding="utf-8"))
                outbound = next(item for item in generated["outbounds"] if item.get("tag") == "vpngate-openvpn-active")

                self.assertEqual(outbound["streamSettings"]["sockopt"]["interface"], "tun7")


if __name__ == "__main__":
    unittest.main()
