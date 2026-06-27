from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.api.routes_panel import handle_panel_post


class FakeHandler:
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.responses = []

    def read_json_body(self):
        return self.payload

    def send_json(self, data, status=None):
        self.responses.append((data, status))


class PanelRouteTests(unittest.TestCase):
    def test_unknown_path_is_not_handled(self) -> None:
        self.assertFalse(handle_panel_post(FakeHandler(), "/api/other"))

    def test_delete_subscription_link_requires_id(self) -> None:
        handler = FakeHandler({})
        with patch("backend.app.api.routes_panel.ensure_panel_framework_files"):
            self.assertTrue(handle_panel_post(handler, "/api/panel/subscription-links/delete"))

        self.assertEqual("缺少订阅链接 ID", handler.responses[0][0]["error"])
        self.assertEqual(400, handler.responses[0][1])


if __name__ == "__main__":
    unittest.main()
