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


if __name__ == "__main__":
    unittest.main()
