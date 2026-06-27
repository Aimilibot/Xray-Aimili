from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.app.config import WEB_DIR


class InvalidWebPath(ValueError):
    pass


def read_web_html(name: str) -> str:
    path = WEB_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing web page file: {path}") from exc


def is_web_asset(path: str) -> bool:
    return path.startswith("/css/") or path.startswith("/js/")


def resolve_web_asset(path: str) -> Path:
    rel_path = path.lstrip("/")
    normalized = os.path.normpath(rel_path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise InvalidWebPath(path)
    return WEB_DIR / normalized


def content_type_for(path: Path) -> str:
    return {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
    }.get(path.suffix, "application/octet-stream")


def read_json_body(handler: Any) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = handler.rfile.read(length) if length > 0 else b""
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}
