from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.vpn import get_upstream_proxy


PROXY_ENV_KEYS = [
    "OPENVPN_UPSTREAM_SOCKS",
    "OPENVPN_UPSTREAM_HTTP",
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
]


class GetUpstreamProxyTests(unittest.TestCase):
    def proxy_env(self, **values: str):
        clean_env = {key: value for key, value in os.environ.items() if key not in PROXY_ENV_KEYS}
        clean_env.update(values)
        return patch.dict(os.environ, clean_env, clear=True)

    def test_socks_url_without_port_uses_protocol_default(self) -> None:
        with self.proxy_env(OPENVPN_UPSTREAM_SOCKS="socks5://127.0.0.1"):
            self.assertEqual(get_upstream_proxy(), ("socks", "127.0.0.1", 1080))

    def test_http_url_without_port_uses_protocol_default(self) -> None:
        with self.proxy_env(http_proxy="http://127.0.0.1"):
            self.assertEqual(get_upstream_proxy(), ("http", "127.0.0.1", 80))

    def test_https_url_without_port_uses_https_default(self) -> None:
        with self.proxy_env(HTTPS_PROXY="https://proxy.example.com"):
            self.assertEqual(get_upstream_proxy(), ("http", "proxy.example.com", 443))

    def test_explicit_port_is_preserved(self) -> None:
        with self.proxy_env(OPENVPN_UPSTREAM_HTTP="http://127.0.0.1:8080"):
            self.assertEqual(get_upstream_proxy(), ("http", "127.0.0.1", 8080))

    def test_ipv6_host_port_is_supported(self) -> None:
        with self.proxy_env(OPENVPN_UPSTREAM_SOCKS="[::1]:1081"):
            self.assertEqual(get_upstream_proxy(), ("socks", "::1", 1081))

    def test_invalid_explicit_port_is_ignored(self) -> None:
        with self.proxy_env(OPENVPN_UPSTREAM_HTTP="http://127.0.0.1:not-a-port"):
            self.assertEqual(get_upstream_proxy(), (None, None, None))


if __name__ == "__main__":
    unittest.main()
