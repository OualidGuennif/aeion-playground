"""Bounded ParcelDesk context and strict model proposals; no model tools needed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PATCH_PROTOCOL = "patch/v1"
MAX_PATCH_BYTES = 24_000
CONTEXT_FILES = ("app/pricing.py", "tests/test_pricing.py")


def source_context(root: Path) -> str:
    files = []
    for name in CONTEXT_FILES:
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Context symlink refused: {name}")
        with path.open("rb") as source:
            data = source.read(16_001)
        if len(data) > 16_000 or b"\0" in data:
            raise ValueError(f"Context file exceeds the text bound: {name}")
        files.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "text": data.decode("utf-8")})
    return json.dumps(files, ensure_ascii=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def response_object(text: str, keys: set[str]) -> dict:
    if len(text.encode("utf-8")) > MAX_PATCH_BYTES * 2:
        raise ValueError("Model response exceeds the byte bound")
    value = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Model response has an unexpected JSON shape")
    if any(not isinstance(item, str) or not item.strip() or "\0" in item for item in value.values()):
        raise ValueError("Model response requires nonempty text fields without NUL")
    return value


def parse_patch(text: str) -> tuple[str, str]:
    value = response_object(text, {"patch", "summary"})
    patch, summary = value["patch"], value["summary"]
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES or not patch.startswith("diff --git "):
        raise ValueError("Expected a bounded Git unified diff")
    if len(summary) > 1000:
        raise ValueError("Patch summary exceeds 1000 characters")
    return patch, summary
