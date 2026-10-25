"""Bounded ParcelDesk context and strict model proposals; no model tools needed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PATCH_PROTOCOL = "patch/v1"
MAX_PATCH_BYTES = 24_000
CONTEXT_FILES = ("app/pricing.py", "tests/test_pricing.py")
MAX_CONTEXT_FILES = 8
MAX_CONTEXT_BYTES = 128_000
PRODUCT_SUFFIXES = {".java", ".md", ".ts", ".tsx", ".css", ".js", ".json", ".yaml", ".yml"}


def source_context(root: Path, files: tuple[str, ...] | None = None) -> str:
    names = tuple(files or CONTEXT_FILES)
    if not 1 <= len(names) <= MAX_CONTEXT_FILES:
        raise ValueError("Context requires between 1 and 8 files")
    selected = []
    total = 0
    for name in names:
        if not isinstance(name, str) or "\\" in name or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"Context path refused: {name!r}")
        if name.startswith("product/"):
            if Path(name).suffix not in PRODUCT_SUFFIXES:
                raise ValueError(f"Product context file has an unsupported type: {name}")
        elif not (name.startswith("app/") or name.startswith("tests/")) or not name.endswith(".py"):
            raise ValueError(f"Context path must be a Python file under app/ or tests/, or a product source file: {name}")
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Context symlink refused: {name}")
        if not path.is_file():
            raise ValueError(f"Context file is missing: {name}")
        with path.open("rb") as source:
            data = source.read(16_001)
        if len(data) > 16_000 or b"\0" in data:
            raise ValueError(f"Context file exceeds the text bound: {name}")
        total += len(data)
        if total > MAX_CONTEXT_BYTES:
            raise ValueError("Context exceeds the total text bound")
        selected.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "text": data.decode("utf-8")})
    return json.dumps(selected, ensure_ascii=False)


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
