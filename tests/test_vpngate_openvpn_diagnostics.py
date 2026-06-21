from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core import vpn
from utils import vpn as vpn_utils


class VpnGateOpenVpnDiagnosticsTests(unittest.TestCase):
    def test_openvpn_auth_defaults_are_available_to_runner(self) -> None:
        self.assertEqual(vpn.OPENVPN_AUTH_USER, "vpn")
        self.assertEqual(vpn.OPENVPN_AUTH_PASS, "vpn")

    def test_openvpn_command_uses_auth_file_instead_of_interactive_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "vpngate_auth.txt"
            with patch.object(vpn, "AUTH_FILE", auth_file):
                vpn.ensure_openvpn_auth_file()
                command = vpn.openvpn_command(str(Path(tmp) / "node.ovpn"), route_nopull=True)

            self.assertEqual(auth_file.read_text(encoding="utf-8"), "vpn\nvpn\n")
            auth_index = command.index("--auth-user-pass")
            self.assertEqual(command[auth_index + 1], str(auth_file))
            self.assertIn("--auth-nocache", command)

    def test_openvpn_command_matches_vpngate_script_tls_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "node.ovpn"
            config.write_text("client\nproto udp\nremote 1.2.3.4 1194\n", encoding="utf-8")

            command = vpn.openvpn_command(str(config), route_nopull=True)

            self.assertIn("--route-delay", command)
            self.assertIn("--connect-retry-max", command)
            self.assertIn("--connect-timeout", command)
            if Path("/etc/ssl/certs").exists():
                self.assertIn("--capath", command)
                self.assertIn("/etc/ssl/certs", command)

    def test_openvpn_auth_prompt_failure_has_specific_diagnostic(self) -> None:
        code, message = vpn_utils.diagnose_openvpn_failure([
            "neither stdin nor stderr are a tty device and you have neither a controlling tty nor systemd - can't ask for 'Enter Auth Username:'",
            "Exiting due to fatal error",
        ])

        self.assertEqual(code, 2012)
        self.assertIn("ERR_OVPN_AUTH_PROMPT_UNAVAILABLE", message)

    def test_missing_openvpn_does_not_blacklist_batch_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            nodes_file = data_dir / "nodes.json"
            config_dir = data_dir / "configs"
            config_dir.mkdir(parents=True)
            nodes_file.write_text(
                """[
                  {
                    "id": "node-1-2-3-4-443",
                    "ip": "1.2.3.4",
                    "remote_host": "1.2.3.4",
                    "remote_port": 443,
                    "ping": 10,
                    "score": 100,
                    "config_file": "%s",
                    "config_text": "client\\ndev tun\\nproto tcp\\nremote 1.2.3.4 443\\n",
                    "probe_status": "not_checked",
                    "probe_message": "Not probed yet",
                    "active": false
                  }
                ]"""
                % str(config_dir / "node-1-2-3-4-443.ovpn").replace("\\", "\\\\"),
                encoding="utf-8",
            )

            with patch.object(vpn, "NODES_FILE", nodes_file), \
                 patch.object(vpn, "CONFIG_DIR", config_dir), \
                 patch.object(vpn.vpn_utils, "ping_latency_ms", return_value=10), \
                 patch.object(vpn.shutil, "which", return_value=None), \
                 patch.object(vpn, "mark_blacklisted") as mark_blacklisted:
                results = vpn.test_multiple_nodes(["node-1-2-3-4-443"])
                nodes = vpn.read_json(nodes_file, [])

            self.assertEqual(results[0]["probe_status"], "not_checked")
            self.assertIn("ERR_OVPN_CMD_NOT_FOUND", results[0]["probe_message"])
            self.assertEqual(nodes[0]["probe_status"], "not_checked")
            mark_blacklisted.assert_not_called()


if __name__ == "__main__":
    unittest.main()
