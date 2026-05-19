"""
validate_contracts.py — Phase 4.2: Contract Graph Validation

Improvements vs v1:
  - Topo sort on route-level dependency graph (detect circular)
  - Duplicate route detection
  - Response schema field consistency check
  - Rich error reporting (all errors collected, not fail-fast)
  - Returns structured ValidationReport instead of raise/print

Contract schema expected:
  {
    "task_id": "TASK-01",
    "routes": [
      {
        "path": "/products",
        "method": "GET",
        "status_code": 200,
        "response_fields": ["id", "name", "price"]
      }
    ],
    "depends_on": ["/categories"]   ← routes this task's code calls
  }
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
CONTRACT_DIR = BASE_DIR / "docs" / "contracts"


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class RouteInfo:
    task_id: str
    path: str
    method: str
    status_code: int
    response_fields: list[str]


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    merge_order: list[str] = field(default_factory=list)  # task_id topo order

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def print_summary(self) -> None:
        status = "✓ PASSED" if self.ok else "✗ FAILED"
        print(f"\n  [contract] {status}")
        for e in self.errors:
            print(f"    ERROR: {e}")
        for w in self.warnings:
            print(f"    WARN:  {w}")
        if self.merge_order:
            print(f"    Merge order: {self.merge_order}")


# ── Loaders ────────────────────────────────────────────────────────────────

def load_contracts(contract_dir: Path = CONTRACT_DIR) -> list[dict]:
    contracts = []
    for f in sorted(contract_dir.glob("*.contract.json")):
        contracts.append(json.loads(f.read_text()))
    return contracts


# ── Validation steps ───────────────────────────────────────────────────────

def check_duplicate_routes(
    contracts: list[dict],
    report: ValidationReport,
) -> dict[str, RouteInfo]:
    """
    Build route registry. Flag any path+method defined in multiple tasks.
    Returns registry (even on error, for downstream checks).
    """
    registry: dict[str, RouteInfo] = {}  # "METHOD /path" → RouteInfo

    for c in contracts:
        task_id = c.get("task_id", "UNKNOWN")
        for r in c.get("routes", []):
            key = f"{r['method'].upper()} {r['path']}"
            if key in registry:
                report.add_error(
                    f"Duplicate route {key!r}: defined in "
                    f"{registry[key].task_id} AND {task_id}"
                )
            else:
                registry[key] = RouteInfo(
                    task_id=task_id,
                    path=r["path"],
                    method=r["method"].upper(),
                    status_code=r.get("status_code", 200),
                    response_fields=r.get("response_fields", []),
                )

    return registry


def check_dependency_resolution(
    contracts: list[dict],
    registry: dict[str, RouteInfo],
    report: ValidationReport,
) -> None:
    """
    Each item in contract.depends_on must exist as a registered route.
    Only checks path (method-agnostic) to allow GET deps declared without method.
    """
    path_set = {info.path for info in registry.values()}

    for c in contracts:
        task_id = c.get("task_id", "UNKNOWN")
        for dep_path in c.get("depends_on", []):
            if dep_path not in path_set:
                report.add_error(
                    f"{task_id} depends_on missing route path: {dep_path!r}"
                )
def require_task_id(contract: dict) -> str:
    tid = contract.get("task_id")

    if not isinstance(tid, str) or not tid.strip():
        raise ValueError(f"Invalid task_id in contract: {contract}")

    return tid

def topo_sort_tasks(
    contracts: list[dict],
    registry: dict[str, RouteInfo],
    report: ValidationReport,
) -> list[str]:
    """
    Build task-level dependency graph from route-level depends_on.
    Route dep /products → task TASK-01 means:
    the depending task must come after TASK-01.
    Returns topo-sorted task_id list.
    """

    path_to_task = {
        info.path: info.task_id
        for info in registry.values()
    }

    task_ids: list[str] = [
        require_task_id(c)
        for c in contracts
    ]

    in_degree: dict[str, int] = {
        tid: 0 for tid in task_ids
    }

    graph: dict[str, list[str]] = defaultdict(list)

    for c in contracts:
        consumer = require_task_id(c)

        for dep_path in c.get("depends_on", []):
            provider = path_to_task.get(dep_path)

            if provider and provider != consumer:
                graph[provider].append(consumer)
                in_degree[consumer] += 1

    queue = deque(
        tid for tid in task_ids
        if in_degree[tid] == 0
    )

    sorted_ids: list[str] = []

    while queue:
        tid = queue.popleft()
        sorted_ids.append(tid)

        for dep in graph[tid]:
            in_degree[dep] -= 1

            if in_degree[dep] == 0:
                queue.append(dep)

    if len(sorted_ids) != len(task_ids):
        circular = [
            t for t in task_ids
            if t not in sorted_ids
        ]
        report.add_error(
            f"Circular route dependency detected: {circular}"
        )

    return sorted_ids


def check_schema_consistency(
    contracts: list[dict],
    report: ValidationReport,
) -> None:
    """
    Warn if two tasks expose the same path but with different response_fields.
    (Usually a bug — one contract is stale.)
    """
    path_fields: dict[str, tuple[str, list[str]]] = {}

    for c in contracts:
        task_id = c.get("task_id", "UNKNOWN")
        for r in c.get("routes", []):
            path = r["path"]
            fields = sorted(r.get("response_fields", []))
            if path in path_fields:
                prev_task, prev_fields = path_fields[path]
                if prev_fields != fields:
                    report.add_warning(
                        f"Schema mismatch for {path!r}: "
                        f"{prev_task} declares {prev_fields}, "
                        f"{task_id} declares {fields}"
                    )
            else:
                path_fields[path] = (task_id, fields)


# ── Main Entry ─────────────────────────────────────────────────────────────

def run_contract_validation(
    tasks: list[dict] | None = None,
    contract_dir: Path = CONTRACT_DIR,
) -> ValidationReport:
    print("\n── 4.2 Contract Graph Validation ──────────────────────")
    print("CONTRACT_DIR =", contract_dir)
    print("EXISTS =", Path(contract_dir).exists())
    report = ValidationReport()
    contracts = load_contracts(contract_dir)

    if not contracts:
        report.add_error(f"No contracts found in {contract_dir}")
        report.print_summary()
        return report

    print(f"  [contract] Loaded {len(contracts)} contracts")

    # Step 1: Duplicate routes
    registry = check_duplicate_routes(contracts, report)

    # Step 2: Dependency resolution
    check_dependency_resolution(contracts, registry, report)

    # Step 3: Topo sort (task order for merging)
    merge_order = topo_sort_tasks(contracts, registry, report)
    report.merge_order = merge_order

    # Step 4: Schema consistency warnings
    check_schema_consistency(contracts, report)

    report.print_summary()
    return report


# Backward-compat alias used by runtime_supervisor v1
def validate_all_contracts(tasks: list | None = None) -> None:
    result = run_contract_validation(tasks)
    if not result.ok:
        raise RuntimeError(
            "Contract validation failed:\n" + "\n".join(result.errors)
        )