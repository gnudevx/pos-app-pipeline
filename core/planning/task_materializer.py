"""
Task Materializer  [FIXED v2]

Nhận architecture.json + dependency_graph.json →
  Xuất docs/materialized_tasks.json

Việc của Task Materializer:
  - Đọc services từ architect (không thêm, không bỏ)
  - Gán task_id theo topo sort order từ dependency graph
  - Chuẩn hoá từng task thành ExecutableTask schema
  - KHÔNG quyết định sprint, priority, story points — đó là việc của Planner

ExecutableTask schema:
{
  "id":          "TASK-01",          ← từ architect task_id
  "name":        "Auth Service",
  "component":   "backend",
  "description": "...",
  "file_structure": [...],
  "api_routes":  [...],              ← locked từ architect
  "depends_on":  ["TASK-XX"],
  "entity_refs": ["ENT-01"]
}

Planner sau đó chỉ nhận materialized_tasks.json và làm MỘT việc:
  sprint grouping + priority + story points.

FIXES:
  [FIX-M1] execution_order giờ lấy từ dep_graph.execution_order (topo sort thật),
           không còn giữ nguyên thứ tự sequential TASK-01..12.
           Trước: materialized dùng thứ tự services trong arch → sai khi arch không
           sorted theo topo (ví dụ TASK-04 trước TASK-05 dù TASK-05 phải chạy trước).
  [FIX-M2] Warn khi backend phụ thuộc frontend (cross-layer) — data smell từ architect.
  [FIX-M3] _strip_self_deps() vẫn giữ nhưng move lên trước khi ghi để rõ intent.
"""

import json
import os
from typing import Optional

from contracts.dependency_graph import (
    build_dependency_graph,
    validate_no_cycles,
    get_execution_order,
    get_parallel_groups,
    save_graph,
)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def _strip_self_deps(tasks: list) -> list:
    """
    [FIX BUG-B1 / BUG P-2] Loại bỏ self-references trong depends_on.

    Planner LLM hoặc Architect LLM đôi khi sinh:
        TASK-09 depends_on: ["TASK-09"]   ← self-loop thuần túy (BUG A-2)
        TASK-11 depends_on: ["TASK-07", "TASK-05", "TASK-11"]  ← mixed

    Self-loop khiến can_execute() luôn trả False → task bị SKIP vĩnh viễn.
    Hàm này CHỈ xoá entry bằng task_id của chính nó — KHÔNG xoá deps khác.

    [FIX P-2 guard] Sau khi strip, validate rằng task không mất dep hợp lệ:
    nếu depends_on đã là rỗng trước khi strip, không có gì để lo.
    Nếu sau strip vẫn còn dep → OK.
    """
    fixed = 0
    for task in tasks:
        task_id = task.get("id", "")
        deps = task.get("depends_on", [])
        if task_id in deps:
            new_deps = [d for d in deps if d != task_id]
            task["depends_on"] = new_deps
            fixed += 1
            remaining = len(new_deps)
            print(
                f"      [task-materializer] FIX P-2: removed self-dep from {task_id} "
                f"({remaining} valid dep(s) kept)"
            )
    if fixed:
        print(f"      [task-materializer] Stripped {fixed} self-loop(s)")
    return tasks


def _warn_cross_layer_deps(tasks: list) -> None:
    """
    [FIX-M2] Warn khi backend phụ thuộc vào frontend.
    Đây là data smell — architect đã sinh ra dep sai logic.
    Materializer không tự sửa vì đó là quyết định của architect agent,
    nhưng cần warn rõ để human review.
    """
    comp_map = {t["id"]: t.get("component", "") for t in tasks}
    for t in tasks:
        if t.get("component") != "backend":
            continue
        for dep_id in t.get("depends_on", []):
            dep_comp = comp_map.get(dep_id, "")
            if dep_comp == "frontend":
                print(
                    f"      [task-materializer] WARN: {t['id']}(backend) depends on "
                    f"{dep_id}(frontend) — architect should fix this cross-layer dep"
                )


def materialize(
    architecture: dict,
    stories: Optional[list] = None,
    execution_order: Optional[list] = None,
    parallel_groups: Optional[list] = None,
) -> dict:
    """
    Main entry point.

    Input:
      architecture     — dict từ docs/architecture.json (phải đã có task_id trên mỗi service)
      stories          — list từ docs/stories.json (optional, để map story_ref)
      execution_order  — list task_ids theo topo sort, tính sẵn bởi caller.
                         Nếu None → tự build dep graph (backward compat).

    Output:
      dict ghi vào docs/materialized_tasks.json

    [FIX-M1] execution_order phải lấy từ dep_graph (topo sort thật), không phải
    từ thứ tự services trong architecture.json. Thứ tự trong arch là thứ tự architect
    viết ra, không đảm bảo topo-sorted.
    """
    # 1. Build dep graph chỉ khi caller không truyền execution_order
    if execution_order is None:
        graph = build_dependency_graph(architecture)
        save_graph(graph)
        ok, err = validate_no_cycles(graph)
        if not ok:
            raise RuntimeError(f"[task-materializer] {err}")
        # [FIX-M1] Lấy từ dep_graph, không tự sort lại
        execution_order = get_execution_order(graph)
        parallel_groups  = get_parallel_groups(graph)
        print(
            f"      [dep-graph] {len(graph.get('nodes', {}))} nodes, "
            f"{len(graph.get('edges', []))} edges, no cycles"
        )
        print(f"      [dep-graph] execution_order: {execution_order}")
    elif parallel_groups is None:
        # [FIX BUG M-1] Caller truyền execution_order nhưng KHÔNG truyền parallel_groups.
        # Trước đây: fallback "parallel_groups or []" → materialized_tasks.json có
        # parallel_groups = [] → planner mất wave grouping hoàn toàn.
        # Fix: load từ dep_graph.json đã có sẵn (được ghi bởi dep-graph step trước đó).
        import os as _os
        _dep_graph_path = "docs/dependency_graph.json"
        if _os.path.exists(_dep_graph_path):
            import json as _json
            with open(_dep_graph_path, encoding="utf-8") as _f:
                _saved_graph = _json.load(_f)
            parallel_groups = _saved_graph.get("parallel_groups", [])
            if parallel_groups:
                print(f"      [task-materializer] [FIX M-1] parallel_groups loaded from dep_graph ({len(parallel_groups)} waves)")
            else:
                print("      [task-materializer] WARN [M-1]: dep_graph.json has empty parallel_groups")
        else:
            print("      [task-materializer] WARN [M-1]: dep_graph.json not found, parallel_groups will be empty")

    # 2. Build story lookup (entity_refs → story_id)
    entity_to_story: dict[str, str] = {}
    if stories:
        for story in stories:
            for ent in story.get("entities", []):
                entity_to_story[ent] = story.get("id", "")

    # 3. Convert services → ExecutableTask list (theo execution order)
    service_map = {
        svc["task_id"]: svc
        for svc in architecture.get("services", [])
        if "task_id" in svc
    }

    tasks = []
    seen = set()
    for task_id in execution_order:
        svc = service_map.get(task_id)
        if not svc:
            continue
        task = _materialize_service(svc, entity_to_story)
        tasks.append(task)
        seen.add(task_id)

    # Tasks có task_id nhưng không trong execution_order (edge case) — thêm vào cuối
    for task_id, svc in service_map.items():
        if task_id not in seen:
            print(f"      [task-materializer] WARN: {task_id} not in execution_order, appending at end")
            tasks.append(_materialize_service(svc, entity_to_story))

    # [FIX-M3] Strip self-deps + cross-layer warnings
    tasks = _strip_self_deps(tasks)
    _warn_cross_layer_deps(tasks)

    result = {
        "schema_version": "1",
        "generated_from": "architecture.json",
        "task_count": len(tasks),
        "execution_order": execution_order,  # [FIX-M1] topo-sorted từ dep_graph
        "parallel_groups": parallel_groups or [], # từ dep_graph hoặc fallback []
        "tasks": tasks,
    }
    return result


def save_materialized(data: dict, path: str = "docs/materialized_tasks.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"      [task-materializer] {data['task_count']} tasks → {path}")


def load_materialized(path: str = "docs/materialized_tasks.json") -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ══════════════════════════════════════════════════════════════════════════════

def _materialize_service(svc: dict, entity_to_story: dict) -> dict:
    """
    Convert một architect service dict → ExecutableTask.

    Quan trọng:
      - Giữ nguyên task_id, api_routes, file_structure từ architect
      - KHÔNG thêm sprint, priority, story_points — Planner làm việc đó
      - KHÔNG invent thêm routes
    """
    task_id    = svc.get("task_id", "")
    entity_refs = svc.get("entity_refs", [])

    # Map entity_refs → story_ref
    story_ref = ""
    for ent in entity_refs:
        if ent in entity_to_story:
            story_ref = entity_to_story[ent]
            break

    return {
        "id":             task_id,
        "name":           svc.get("name", ""),
        "component":      svc.get("component", "fullstack"),
        "description":    svc.get("description", ""),
        "entity_refs":    entity_refs,
        "story_ref":      story_ref,
        "file_structure": svc.get("file_structure", []),
        "api_routes":     svc.get("api_routes", []),     # locked từ architect
        "shared_types":   svc.get("shared_types", []),
        "depends_on":     svc.get("depends_on", []),
        # Planner sẽ điền các field này:
        # "sprint", "priority", "story_points", "status", "acceptance_criteria"
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI helper (chạy standalone để debug)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    arch_path  = "docs/architecture.json"
    story_path = "docs/stories.json"

    if not os.path.exists(arch_path):
        print(f"ERROR: {arch_path} not found")
        exit(1)

    with open(arch_path, encoding="utf-8") as f:
        arch = json.load(f)

    stories = []
    if os.path.exists(story_path):
        with open(story_path, encoding="utf-8") as f:
            stories = json.load(f)

    result = materialize(arch, stories)
    save_materialized(result)

    print(f"\nExecution order: {result['execution_order']}")
    print(f"Tasks: {[t['id'] + ' (' + t['component'] + ')' for t in result['tasks']]}")