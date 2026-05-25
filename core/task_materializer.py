"""
Task Materializer

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
"""

import json
import os
from typing import Optional

from dependency_graph import (
    build_dependency_graph,
    validate_no_cycles,
    get_execution_order,
    save_graph,
)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════
def _strip_self_deps(tasks: list) -> list:
    """
    [FIX BUG-B1] Loại bỏ self-references trong depends_on.
 
    Planner LLM đôi khi sinh:
        TASK-11 depends_on: ["TASK-07", "TASK-05", "TASK-11"]
    
    Self-loop khiến can_execute() luôn trả False → task bị SKIP vĩnh viễn.
    Strip ngay tại materializer — trước khi tasks.json được ghi.
 
    Gọi sau khi build danh sách tasks:
        tasks = _strip_self_deps(tasks)
    """
    fixed = 0
    for task in tasks:
        task_id = task.get("id", "")
        deps = task.get("depends_on", [])
        if task_id in deps:
            task["depends_on"] = [d for d in deps if d != task_id]
            fixed += 1
            print(f"      [task-materializer] FIX: removed self-dep from {task_id}")
    if fixed:
        print(f"      [task-materializer] Stripped {fixed} self-loop(s)")
    return tasks
def materialize(
    architecture: dict,
    stories: Optional[list] = None,
    execution_order: Optional[list] = None,  # [FIX] nhận từ ngoài, không rebuild dep graph
) -> dict:
    """
    Main entry point.

    Input:
      architecture     — dict từ docs/architecture.json (phải đã có task_id trên mỗi service)
      stories          — list từ docs/stories.json (optional, để map story_ref)
      execution_order  — list task_ids theo topo sort, tính sẵn bởi caller.
                         Nếu None → tự build dep graph (backward compat, nhưng dễ gây
                         double-numbering nếu caller đã gọi build_dependency_graph trước).

    Output:
      dict ghi vào docs/materialized_tasks.json

    [FIX BUG-C] Không gọi build_dependency_graph bên trong nữa khi execution_order
    đã được truyền vào. Trước đây, mỗi lần materialize() chạy nó build lại dep graph
    và save_graph() — overwrite graph file với nodes mới, khiến execution_order trả về
    IDs bị lệch (TASK-13~24 thay vì TASK-01~12).
    """
    # 1. Build dep graph chỉ khi caller không truyền execution_order
    if execution_order is None:
        graph = build_dependency_graph(architecture)
        save_graph(graph)
        ok, err = validate_no_cycles(graph)
        if not ok:
            raise RuntimeError(f"[task-materializer] {err}")
        execution_order = get_execution_order(graph)
        print(f"      [dep-graph] {len(graph.get('nodes', {}))} nodes, {len(graph.get('edges', []))} edges, no cycles")

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
            tasks.append(_materialize_service(svc, entity_to_story))
    tasks = _strip_self_deps(tasks)
    result = {
        "schema_version": "1",
        "generated_from": "architecture.json",
        "task_count": len(tasks),
        "execution_order": execution_order,
        "tasks": tasks,
    }
    return result


def save_materialized(data: dict, path: str = "docs/materialized_tasks.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(
        f"      [task-materializer] {data['task_count']} tasks → {path}"
    )


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
    arch_path = "docs/architecture.json"
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