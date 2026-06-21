import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def js_sources():
    return "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "web" / "js").glob("*.js"))


class FrontendInlineActionsTest(unittest.TestCase):
    def test_main_frontend_scripts_are_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")

        scripts = [
            ROOT / "web" / "js" / "app.js",
            ROOT / "web" / "js" / "outbound.js",
            ROOT / "web" / "js" / "route.js",
            ROOT / "web" / "js" / "dashboard.js",
        ]
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(
                    [node, "--check", str(script)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual("", result.stderr, result.stderr)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_outbound_and_routing_inline_handlers_are_exported_to_window(self):
        source = js_sources()
        required_handlers = [
            "showTab",
            "toggleFeaturePower",
            "showOutboundNodeTab",
            "toggleWarpFeaturePower",
            "refreshWarpNode",
            "testWarpNode",
            "openOutboundNodeModal",
            "closeOutboundNodeModal",
            "fetchAndConvertOutbound",
            "testAllCustomOutboundNodes",
            "openOpenvpnRoutingModal",
            "closeOpenvpnRoutingModal",
            "openRoutingRuleModal",
            "closeRoutingRuleModal",
            "saveRoutingRule",
            "editRoutingRule",
            "deleteRoutingRule",
            "toggleRoutingRule",
            "startOpenvpnService",
            "disconnectNode",
            "startConnectionPolling",
            "testAllVpngateNodes",
        ]

        missing = [
            name
            for name in required_handlers
            if not re.search(rf"\bwindow\.{re.escape(name)}\s*=", source)
        ]

        self.assertEqual([], missing)

    def test_vpngate_panel_has_single_start_control(self):
        outbound = (ROOT / "web" / "js" / "outbound.js").read_text(encoding="utf-8")
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        vpngate_panel = index.split('id="outbound-vpngate-panel"', 1)[1].split('<!-- WARP -->', 1)[0]

        self.assertNotIn('onclick="startOpenvpnService()"', outbound)
        self.assertNotIn("启动 OpenVPN", outbound)
        self.assertEqual(1, vpngate_panel.count('data-feature-power="vpngate_enabled"'))
        self.assertIn("openOpenvpnRoutingModal()", vpngate_panel)
        self.assertIn("规则设置", vpngate_panel)

    def test_vpngate_panel_lists_all_nodes_with_filters(self):
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        vpngate_panel = index.split('id="outbound-vpngate-panel"', 1)[1].split('<!-- WARP -->', 1)[0]

        required_controls = [
            'id="search"',
            'id="country_filter"',
            'id="status_filter"',
            'id="ip_type_filter"',
            'id="sort_filter"',
            'id="btn_test_all_nodes"',
            'id="vpngate_count_summary"',
        ]
        for control in required_controls:
            with self.subTest(control=control):
                self.assertIn(control, vpngate_panel)

        self.assertIn("检测全部", vpngate_panel)
        self.assertNotIn("vpngate_pagination_region", vpngate_panel)
        self.assertNotIn("btn_next_page", vpngate_panel)

    def test_vpngate_rows_are_compact_and_proxy_status_does_not_mix_ip_latency(self):
        outbound = (ROOT / "web" / "js" / "outbound.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "css" / "theme.css").read_text(encoding="utf-8")

        self.assertIn("nodeEndpointText(activeNode)", outbound)
        self.assertIn("const isConnecting = state.is_connecting && !confirmedActiveNode", outbound)
        self.assertNotIn('pIpVal.textContent = state.active_node_latency', outbound)
        self.assertIn("vpngate-node-endpoint", outbound)
        self.assertIn("vpngate-node-latency", outbound)
        self.assertIn("grid-template-columns:minmax(180px, 1.25fr)", css)
        self.assertIn(".vpngate-node-list #rows { gap:8px !important; }", css)


if __name__ == "__main__":
    unittest.main()
