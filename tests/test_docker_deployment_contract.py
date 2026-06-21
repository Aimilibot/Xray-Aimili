from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerDeploymentContractTests(unittest.TestCase):
    def test_legacy_installer_delegates_to_docker_installer(self) -> None:
        install_sh = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("install-docker.sh", install_sh)
        self.assertIn("exec bash", install_sh)
        self.assertNotIn("aimilivpn.service <<", install_sh)
        self.assertNotIn("apt-get install -y openvpn", install_sh)

    def test_full_compose_keeps_gateway_inside_container_network(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertNotIn("network_mode: host", compose)
        self.assertIn("${UI_PORT:-8787}:${UI_PORT:-8787}", compose)
        self.assertIn("${LOCAL_PROXY_PORT:-7928}:${LOCAL_PROXY_PORT:-7928}", compose)
        self.assertIn("NET_ADMIN", compose)
        self.assertIn("/dev/net/tun:/dev/net/tun", compose)

    def test_menu_update_path_does_not_create_host_gateway_service(self) -> None:
        menu = (ROOT / "cli" / "menu.py").read_text(encoding="utf-8")
        update_body = menu.split("def update_panel():", 1)[1].split("def uninstall_panel():", 1)[0]

        self.assertIn("Docker Stack", update_body)
        self.assertIn("已停止宿主机服务更新路径", update_body)
        self.assertNotIn("ensure_host_service()", update_body)
