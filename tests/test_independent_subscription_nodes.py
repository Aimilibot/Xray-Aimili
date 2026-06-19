from __future__ import annotations

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

from backend.app.core import xray


class IndependentSubscriptionNodeTests(unittest.TestCase):
    def patched_storage(self, tmp: str):
        data_dir = Path(tmp)
        patches = [
            patch.object(xray, "DATA_DIR", data_dir),
            patch.object(xray, "SUBSCRIPTION_LINKS_FILE", data_dir / "subscription_links.json"),
            patch.object(xray, "SUBSCRIPTION_NODES_FILE", data_dir / "subscription_nodes.json"),
            patch.object(xray, "OUTBOUND_NODES_FILE", data_dir / "outbound_nodes.json"),
            patch.object(xray, "ROUTING_RULES_FILE", data_dir / "routing_rules.json"),
            patch.object(xray, "XRAY_CFG_FILE", data_dir / "xray_cfg.json"),
            patch.object(xray, "XRAY_CONFIG_FILE", data_dir / "xray_config.json"),
            patch.object(xray, "sync_panel_subscription_nodes_to_xray", lambda restart_service=True: None),
            patch.object(xray, "load_client_traffic", lambda: {}),
        ]
        return patches

    def test_independent_socks_node_is_saved_without_subscription_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(exist_ok=True)
            with self.enter_patches(tmp):
                node, error = xray.save_subscription_node({
                    "add_to_subscription": False,
                    "name": "independent-socks",
                    "protocol": "socks5",
                    "port": 12080,
                    "socks_username": "user_demo",
                    "socks_password": "password_demo",
                })

                self.assertEqual(error, "")
                self.assertIsNotNone(node)
                self.assertEqual(node["subscription_id"], "")
                self.assertEqual(node["protocol"], "socks5")
                self.assertEqual(node["port"], 12080)

    def test_independent_node_rejects_subscription_port_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.enter_patches(tmp):
                xray.write_json(xray.SUBSCRIPTION_LINKS_FILE, [{
                    "id": "sublink-1",
                    "name": "main",
                    "token": "token-demo",
                    "port": 12080,
                    "protocol": "socks5",
                    "enabled": True,
                }])

                node, error = xray.save_subscription_node({
                    "add_to_subscription": False,
                    "name": "independent-socks",
                    "protocol": "socks5",
                    "port": 12080,
                })

                self.assertIsNone(node)
                self.assertIn("已被订阅链接占用", error)

    def test_subscription_link_rejects_independent_node_port_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.enter_patches(tmp):
                xray.write_json(xray.SUBSCRIPTION_NODES_FILE, [{
                    "id": "subnode-1",
                    "subscription_id": "",
                    "name": "independent-socks",
                    "protocol": "socks5",
                    "port": 12080,
                    "enabled": True,
                }])

                link, error = xray.save_subscription_link({
                    "name": "main",
                    "token": "token-demo",
                    "port": 12080,
                    "protocol": "socks5",
                })

                self.assertIsNone(link)
                self.assertIn("已被独立节点占用", error)

    def test_default_subscription_link_has_runnable_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.enter_patches(tmp):
                link = xray.ensure_default_subscription_link()

                self.assertEqual(link["protocol"], "vless-reality")
                self.assertEqual(link["port"], 10086)
                self.assertEqual(link["camouflage_host"], "www.microsoft.com")

    def test_write_xray_config_adds_independent_socks_inbound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.enter_patches(tmp):
                xray.write_json(xray.SUBSCRIPTION_NODES_FILE, [{
                    "id": "subnode-independent",
                    "subscription_id": "",
                    "name": "independent-socks",
                    "protocol": "socks5",
                    "port": 12080,
                    "enabled": True,
                    "socks_username": "user_demo",
                    "socks_password": "password_demo",
                }])

                self.assertTrue(xray.write_xray_config(xray.default_xray_cfg()))
                generated = json.loads(xray.XRAY_CONFIG_FILE.read_text(encoding="utf-8"))
                inbound = next(item for item in generated["inbounds"] if item.get("tag") == "subnode-independent")

                self.assertEqual(inbound["protocol"], "socks")
                self.assertEqual(inbound["port"], 12080)
                self.assertEqual(inbound["settings"]["auth"], "password")

    def enter_patches(self, tmp: str):
        stack = ExitStack()
        self.addCleanup(stack.close)
        Path(tmp).mkdir(exist_ok=True, parents=True)
        for patcher in self.patched_storage(tmp):
            stack.enter_context(patcher)
        return stack


if __name__ == "__main__":
    unittest.main()
