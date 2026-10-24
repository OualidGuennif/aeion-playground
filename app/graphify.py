"""Real Graphify projection.

The graph is produced by the vendored Graphify CLI:

    .venv/bin/graphify extract . --code-only --out runtime/graphify --no-cluster

It is an AST graph (tree-sitter), not an LLM-generated diagram. The UI shows a
readable subgraph and links to Graphify's own interactive HTML export.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

PALETTE = [
    "#22d3ee",
    "#a3e635",
    "#f472b6",
    "#fb923c",
    "#818cf8",
    "#facc15",
    "#34d399",
    "#f87171",
]


def _monogram(label: str) -> str:
    cleaned = "".join(ch for ch in label if ch.isalnum() or ch in "._-")
    if not cleaned:
        return "??"
    parts = [p for p in cleaned.replace("-", "_").replace(".", "_").split("_") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return cleaned[:2].upper()


def _kind(node: dict[str, Any]) -> str:
    file_type = str(node.get("file_type") or "code").lower()
    if file_type in {"rationale", "note"}:
        return "note"
    label = str(node.get("label") or "")
    if label.endswith((".md", ".txt")):
        return "doc"
    if label.endswith((".json", ".yaml", ".yml", ".toml")):
        return "config"
    return "code"


def _short_path(path: str) -> str:
    return path.replace("\\", "/")


class GraphProjection:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.graph_path
        self._cache: tuple[float, dict[str, Any]] | None = None

    def exists(self) -> bool:
        return self.path.is_file()

    def _load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        mtime = self.path.stat().st_mtime
        if self._cache and self._cache[0] == mtime:
            return self._cache[1]
        data = json.loads(self.path.read_text())
        self._cache = (mtime, data)
        return data

    def stats(self) -> dict[str, Any]:
        data = self._load()
        if not data:
            return {
                "available": False,
                "provider": "graphify",
                "mode": "not-generated",
                "note": "Run `.venv/bin/graphify extract . --code-only --out runtime/graphify --no-cluster`.",
                "files": 0,
                "symbols": 0,
                "edges": 0,
                "digest": "—",
                "source": str(self.path),
            }
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        files = {n.get("source_file") for n in nodes if n.get("source_file")}
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()[:12]
        generated = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
        return {
            "available": True,
            "provider": "graphify 0.9.94 · tree-sitter AST",
            "mode": "extracted",
            "note": "Real local AST graph. No LLM extraction, no cloud call.",
            "files": len(files),
            "symbols": sum(1 for n in nodes if _kind(n) == "code"),
            "edges": len(edges),
            "nodes": len(nodes),
            "digest": digest,
            "source": str(self.path.relative_to(settings.poc_dir))
            if self.path.is_relative_to(settings.poc_dir)
            else str(self.path),
            "generated_at": generated,
        }

    def view(self, limit: int = 26) -> dict[str, Any]:
        data = self._load()
        if not data:
            return {
                "provider": "graphify",
                "mode": "not-generated",
                "note": "Graphify has not been run yet.",
                "files": 0,
                "symbols": 0,
                "edges": 0,
                "digest": "—",
                "nodes": [],
                "links": [],
                "hot_path": None,
                "memory_items": 0,
                "memory_used": 0,
            }
        nodes: list[dict[str, Any]] = data.get("nodes", [])
        edges: list[dict[str, Any]] = data.get("edges", [])
        by_id = {str(n.get("id")): n for n in nodes}
        degree: dict[str, int] = {str(n.get("id")): 0 for n in nodes}
        for edge in edges:
            source, target = str(edge.get("source")), str(edge.get("target"))
            if source in degree:
                degree[source] += 1
            if target in degree:
                degree[target] += 1
        ranked = sorted(nodes, key=lambda n: (-degree.get(str(n.get("id")), 0), str(n.get("label"))))
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for node in ranked:
            if len(selected) >= limit:
                break
            selected.append(node)
            selected_ids.add(str(node.get("id")))
        # Keep one hop around the selection so the subgraph is not a star-less
        # collection of isolated hubs.
        for node in ranked:
            if len(selected) >= limit + 10:
                break
            identifier = str(node.get("id"))
            if identifier in selected_ids:
                continue
            if any(
                str(edge.get("source")) in selected_ids or str(edge.get("target")) in selected_ids
                for edge in edges
                if str(edge.get("source")) == identifier or str(edge.get("target")) == identifier
            ):
                selected.append(node)
                selected_ids.add(identifier)

        positions = self._layout(selected, edges)
        file_index: dict[str, int] = {}
        for node in selected:
            source_file = _short_path(str(node.get("source_file") or ""))
            if source_file and source_file not in file_index:
                file_index[source_file] = len(file_index)
        ui_nodes = []
        for node in selected:
            identifier = str(node.get("id"))
            source_file = _short_path(str(node.get("source_file") or ""))
            position = positions.get(identifier, (320.0, 180.0))
            degree_value = degree.get(identifier, 0)
            kind = _kind(node)
            ui_nodes.append(
                {
                    "id": identifier,
                    "label": str(node.get("label") or identifier),
                    "kind": kind,
                    "x": round(position[0], 1),
                    "y": round(position[1], 1),
                    "color": PALETTE[file_index.get(source_file, 0) % len(PALETTE)],
                    "status": "hot" if degree_value >= max(4, int(len(edges) / max(len(nodes), 1))) else "verified",
                    "meta": f"{source_file} · {node.get('source_location') or 'AST'}",
                    "detail": (
                        f"AST node extracted from {source_file or 'the workspace'}. "
                        f"{degree_value} edge(s) in the full graph. "
                        "This is evidence of structure, not a grant of authority."
                    ),
                    "monogram": _monogram(str(node.get("label") or identifier)),
                    "degree": degree_value,
                }
            )

        links = []
        for edge in edges:
            source, target = str(edge.get("source")), str(edge.get("target"))
            if source not in selected_ids or target not in selected_ids:
                continue
            relation = str(edge.get("relation") or "related")
            confidence = str(edge.get("confidence") or "EXTRACTED")
            links.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "confidence": confidence,
                    "advisory": confidence != "EXTRACTED" or relation in {"recalls", "constrains"},
                }
            )
        hot = max(ui_nodes, key=lambda n: n["degree"], default=None)
        stats = self.stats()
        return {
            "provider": stats["provider"],
            "mode": stats["mode"],
            "note": stats["note"],
            "files": stats["files"],
            "symbols": stats["symbols"],
            "edges": stats["edges"],
            "nodes_total": stats.get("nodes", len(nodes)),
            "digest": stats["digest"],
            "generated_at": stats.get("generated_at"),
            "hot_path": hot["meta"].split(" · ")[0] if hot else None,
            "nodes": ui_nodes,
            "links": links,
            "memory_items": 0,
            "memory_used": 0,
            "full_graph_url": "/static/graphify/graph.html",
        }

    def _layout(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
        identifiers = [str(n.get("id")) for n in nodes]
        if not identifiers:
            return {}
        try:
            import networkx as nx

            graph = nx.Graph()
            graph.add_nodes_from(identifiers)
            for edge in edges:
                source, target = str(edge.get("source")), str(edge.get("target"))
                if source in graph and target in graph:
                    graph.add_edge(source, target)
            raw = nx.spring_layout(graph, seed=11, k=1.25 / math.sqrt(max(len(identifiers), 1)), iterations=120)
            xs = [point[0] for point in raw.values()]
            ys = [point[1] for point in raw.values()]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            width, height = 700.0, 330.0
            margin = 42.0
            return {
                identifier: (
                    margin + (point[0] - min_x) / (max_x - min_x or 1) * (width - 2 * margin),
                    margin + (point[1] - min_y) / (max_y - min_y or 1) * (height - 2 * margin),
                )
                for identifier, point in raw.items()
            }
        except Exception:
            # Deterministic circle if networkx is unavailable.
            output = {}
            for index, identifier in enumerate(identifiers):
                angle = 2 * math.pi * index / len(identifiers)
                output[identifier] = (350 + 280 * math.cos(angle), 165 + 135 * math.sin(angle))
            return output


graph_projection = GraphProjection()
