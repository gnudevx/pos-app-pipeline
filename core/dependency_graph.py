"""
Dependency Graph Builder

Nhận architecture.json (services + depends_on) →
  - Build DiGraph thật bằng networkx
  - Phát hiện vòng lặp (circular dependency)
  - Topo sort → execution order
  - Group parallel tasks (tasks có thể chạy song song)

Được gọi bởi adapter_v2.py sau architect-agent, trước task-materializer.
"""

import json
import os
from typing import Optional


# ── networkx optional import ──────────────────────────────────────────────────
try:
    import networkx as nx
    _HAS_NX = True
except ImportError:
    nx = None
    _HAS_NX = False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def build_dependency_graph(architecture: dict) -> dict:
    """
    Input:  architecture dict (từ architecture.json)
    Output: dependency_graph dict — ghi vào docs/dependency_graph.json

    Output schema:
    {
      "nodes": ["TASK-01", "TASK-02", ...],
      "edges": [{"from": "TASK-02", "to": "TASK-01"}],   # TASK-02 depends on TASK-01
      "execution_order": ["TASK-01", "TASK-02", ...],    # topo sort
      "parallel_groups": [["TASK-01"], ["TASK-02", "TASK-03"], ["TASK-04"]],
      "has_cycle": false,
      "cycle_detail": null
    }
    """
    services = architecture.get("services", [])

    # Build node list và edge list
    nodes = [svc["task_id"] for svc in services if "task_id" in svc]
    edges = []
    for svc in services:
        task_id = svc.get("task_id")
        if not task_id:
            continue
        for dep in svc.get("depends_on", []):
            if dep in nodes:
                edges.append({"from": task_id, "to": dep})

    # Detect cycle + topo sort
    if _HAS_NX:
        result = _build_with_networkx(nodes, edges)
    else:
        result = _build_without_networkx(nodes, edges)

    result["nodes"] = nodes
    result["edges"] = edges
    return result


def validate_no_cycles(graph: dict) -> tuple[bool, Optional[str]]:
    """
    Trả về (ok, error_message).
    ok=True  → không có cycle, pipeline có thể chạy
    ok=False → có cycle, pipeline phải dừng
    """
    if graph.get("has_cycle"):
        return False, f"Circular dependency detected: {graph.get('cycle_detail')}"
    return True, None


def get_execution_order(graph: dict) -> list[str]:
    """Trả về task_ids theo thứ tự có thể execute (topo sort)."""
    return graph.get("execution_order", graph.get("nodes", []))


def get_parallel_groups(graph: dict) -> list[list[str]]:
    """
    Trả về các nhóm task có thể chạy song song.
    Group i chạy sau khi tất cả group i-1 PASSED.

    Ví dụ:
      [["TASK-01"], ["TASK-02", "TASK-03"], ["TASK-04"]]
      → TASK-01 trước, rồi TASK-02 và TASK-03 song song, rồi TASK-04
    """
    return graph.get("parallel_groups", [[t] for t in get_execution_order(graph)])


def save_graph(graph: dict, path: str = "docs/dependency_graph.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
    print(
        f"      [dep-graph] {len(graph['nodes'])} nodes, "
        f"{len(graph['edges'])} edges, "
        f"{'CYCLE DETECTED' if graph.get('has_cycle') else 'no cycles'}"
    )


def load_graph(path: str = "docs/dependency_graph.json") -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL — networkx path
# ══════════════════════════════════════════════════════════════════════════════

def _build_with_networkx(nodes: list, edges: list) -> dict:
    if nx is None:
        raise RuntimeError(
            "networkx unavailable"
        )
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    for e in edges:
        # edge "from depends on to" → trong DiGraph: to → from
        # (to phải complete trước from)
        G.add_edge(e["to"], e["from"])

    has_cycle = not nx.is_directed_acyclic_graph(G)
    cycle_detail = None
    execution_order = []
    parallel_groups = []

    if has_cycle:
        try:
            cycle = nx.find_cycle(G)
            cycle_detail = " → ".join(f"{u}→{v}" for u, v in cycle)
        except Exception:
            cycle_detail = "unknown cycle"
    else:
        execution_order = list(nx.topological_sort(G))
        parallel_groups = _compute_parallel_groups(G, execution_order)

    return {
        "has_cycle": has_cycle,
        "cycle_detail": cycle_detail,
        "execution_order": execution_order,
        "parallel_groups": parallel_groups,
    }


def _compute_parallel_groups(G, topo_order: list) -> list[list]:
    """
    Gom các node cùng "depth" vào một group.
    Depth của node = max(depth của predecessors) + 1.
    """
    depth: dict[str, int] = {}
    for node in topo_order:
        preds = list(G.predecessors(node))
        if not preds:
            depth[node] = 0
        else:
            depth[node] = max(depth.get(p, 0) for p in preds) + 1

    max_depth = max(depth.values(), default=0)
    groups = []
    for d in range(max_depth + 1):
        group = [n for n in topo_order if depth.get(n, 0) == d]
        if group:
            groups.append(group)
    return groups


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL — fallback (không có networkx)
# ══════════════════════════════════════════════════════════════════════════════

def _build_without_networkx(nodes: list, edges: list) -> dict:
    """
    Kahn's algorithm cho topo sort + cycle detection.
    Không cần networkx.
    """
    # Build adjacency: dep → [dependents]
    adj: dict[str, list] = {n: [] for n in nodes}
    in_degree: dict[str, int] = {n: 0 for n in nodes}

    for e in edges:
        src, dst = e["from"], e["to"]   # src depends on dst
        if src in adj and dst in adj:
            adj[dst].append(src)        # dst phải xong trước src
            in_degree[src] += 1

    # Kahn's
    queue = [n for n in nodes if in_degree[n] == 0]
    order = []
    depth: dict[str, int] = {n: 0 for n in queue}

    while queue:
        # Lấy node có in_degree = 0
        node = queue.pop(0)
        order.append(node)
        for neighbor in adj.get(node, []):
            in_degree[neighbor] -= 1
            depth[neighbor] = max(depth.get(neighbor, 0), depth.get(node, 0) + 1)
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    has_cycle = len(order) != len(nodes)
    cycle_detail = None
    if has_cycle:
        remaining = [n for n in nodes if n not in order]
        cycle_detail = f"Nodes in cycle: {remaining}"

    # Parallel groups
    parallel_groups = []
    if not has_cycle:
        max_depth = max(depth.values(), default=0)
        for d in range(max_depth + 1):
            group = [n for n in order if depth.get(n, 0) == d]
            if group:
                parallel_groups.append(group)

    return {
        "has_cycle": has_cycle,
        "cycle_detail": cycle_detail,
        "execution_order": order if not has_cycle else [],
        "parallel_groups": parallel_groups,
    }