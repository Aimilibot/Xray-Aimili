from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.api.routes_features import handle_feature_toggle


class FakeHandler:
    def __init__(self, payload):
        self.payload = payload
        self.responses = []

    def read_json_body(self):
        return self.payload

    def send_json(self, data, status=None):
        self.responses.append((data, status))


class FeatureRouteTests(unittest.TestCase):
    def test_unknown_feature_key_is_rejected(self) -> None:
        handler = FakeHandler({"key": "nope", "enabled": True})
        handle_feature_toggle(handler)

        self.assertEqual("未知功能开关", handler.responses[0][0]["error"])
        self.assertEqual(400, handler.responses[0][1])


if __name__ == "__main__":
    unittest.main()
