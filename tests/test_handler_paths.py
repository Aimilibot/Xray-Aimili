from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import MethodType

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.api.handler import Handler
from backend.app.api.http_utils import InvalidWebPath, content_type_for, resolve_web_asset


class HandlerPathValidationTests(unittest.TestCase):
    def make_handler(self, path: str, secret_path: str = "secret") -> Handler:
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.get_secret_path = MethodType(lambda self: secret_path, handler)
        return handler

    def test_secret_prefixed_api_path_ignores_query_string_for_routing(self) -> None:
        handler = self.make_handler("/secret/api/xray/subscribe?token=abc")
        self.assertEqual(handler.validate_path(), "/api/xray/subscribe")

    def test_unprefixed_subscription_path_ignores_query_string_for_routing(self) -> None:
        handler = self.make_handler("/api/xray/subscribe?token=abc")
        self.assertEqual(handler.validate_path(), "/api/xray/subscribe")

    def test_secret_prefixed_static_path_ignores_query_string_for_routing(self) -> None:
        handler = self.make_handler("/secret/js/app.js?v=1")
        self.assertEqual(handler.validate_path(), "/js/app.js")

    def test_web_asset_path_rejects_directory_traversal(self) -> None:
        with self.assertRaises(InvalidWebPath):
            resolve_web_asset("/js/../../ui_auth.json")

    def test_web_asset_content_type_uses_suffix(self) -> None:
        self.assertEqual(content_type_for(resolve_web_asset("/js/app.js")), "application/javascript; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
