from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


def load_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["dataset_lineage"] if "dataset_lineage" in payload else payload


def _bfs_downstream(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return transitive downstream nodes in BFS order, excluding start."""
    seen = {start}
    q: deque[str] = deque([start])
    out: list[str] = []
    while q:
        node = q.popleft()
        for child in graph.get(node, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                q.append(child)
    return out


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return transitive downstream assets in BFS order, excluding start."""
    return _bfs_downstream(graph, start)


def get_column_downstream(
    column_graph: dict[str, list[str]], start_column: str
) -> list[str]:
    """Return transitive downstream columns in BFS order, excluding start.

    Uses the same traversal as dataset-level lineage: `column_graph` maps a
    fully-qualified column (e.g. "stg_orders.amount_usd") to the columns
    that are directly derived from it.
    """
    return _bfs_downstream(column_graph, start_column)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Minimal dbt manifest parser.

    It maps each dbt node unique_id to the nodes that depend on it. Students may
    enrich names, exposures, owners, columns, or OpenLineage facets.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    graph: dict[str, list[str]] = {}
    child_map = manifest.get("child_map", {})
    for parent, children in child_map.items():
        graph[parent] = list(children)
    return graph
