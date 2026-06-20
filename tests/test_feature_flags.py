from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app import db


class FeatureFlagsTests(unittest.TestCase):
    def test_custom_feature_is_always_enabled_even_if_legacy_file_disabled_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            feature_file = data_dir / "feature_flags.json"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "FEATURE_FLAGS_FILE", feature_file):
                saved = db.save_feature_flags({
                    "vpngate_enabled": True,
                    "warp_enabled": False,
                    "custom_enabled": False,
                })
                self.assertEqual(saved, {
                    "vpngate_enabled": True,
                    "warp_enabled": False,
                    "custom_enabled": False,
                })
                self.assertEqual(db.load_feature_flags()["custom_enabled"], True)

    def test_missing_feature_flags_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            feature_file = data_dir / "feature_flags.json"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "FEATURE_FLAGS_FILE", feature_file):
                self.assertEqual(db.load_feature_flags(), db.DEFAULT_FEATURE_FLAGS)


if __name__ == "__main__":
    unittest.main()
