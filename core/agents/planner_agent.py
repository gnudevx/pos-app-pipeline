"""
Planner Agent  [FIXED v2]

Group materialized tasks vào sprints, sinh tasks.json.
Bao gồm helper: _assign_task_ids (dùng bởi cả planner và task_materializer).

FIXES:
  [FIX-P1] Sprint grouping dùng parallel_groups từ dep_graph thay vì naive i//5.
           Trước: chia đều theo index → vi phạm dependency (task A và dep của A
           cùng sprint → dev không biết build order).
           Sau: mỗi sprint = một "wave" từ parallel_groups.
           Wave = nhóm tasks không có dep lẫn nhau, chỉ dep vào wave trước.
  [FIX-P2] Validation mạnh hơn: kiểm tra mọi task phải có sprint lớn hơn sprint
           của tất cả dependencies của nó. Raise lỗi rõ ràng thay vì silently wrong.
  [FIX-P3] Xóa debug logs (planner-debug) khỏi production path.
"""
import os
import json
from contracts.dependency_graph import (
    save_graph as save_dep_graph,
)


def run(prompt: str) -> str:
    return _gemini_planner(prompt)


def _assign_task_ids(architecture: dict, execution_order: list) -> dict:
    """
    Gán task_id cho mỗi service dựa vào execution order.

    Nếu architect đã gán task_id (backward compat) → giữ nguyên.
    Nếu chưa có → gán TASK-01, TASK-02, ... theo thứ tự execution_order.

    depends_on: architect dùng service NAME → convert sang task_id.
    """
    services = architecture.get("services", [])

    # Build name → index map
    name_to_idx: dict[str, int] = {}
    for i, svc in enumerate(services):
        name_to_idx[svc.get("name", "")] = i

    # Nếu architect đã gán task_id → chỉ resolve depends_on names → ids
    all_have_ids = all("task_id" in svc for svc in services)
    if all_have_ids:
        id_map = {svc["name"]: svc["task_id"] for svc in services}
        for svc in services:
            svc["depends_on"] = [
                id_map.get(dep, dep)
                for dep in svc.get("depends_on", [])
            ]
        dep = architecture.get("deployment")
        if dep and "depends_on" in dep:
            dep["depends_on"] = [id_map.get(d, d) for d in dep["depends_on"]]
        return architecture

    # Gán task_id mới theo execution order
    ordered_names = execution_order if execution_order else [svc.get("name", "") for svc in services]

    name_to_id: dict[str, str] = {}
    counter = 1
    for name in ordered_names:
        if name and name not in name_to_id:
            name_to_id[name] = f"TASK-{counter:02d}"
            counter += 1

    for svc in services:
        name = svc.get("name", "")
        if name in name_to_id:
            svc["task_id"] = name_to_id[name]
        elif "task_id" not in svc:
            svc["task_id"] = f"TASK-{counter:02d}"
            counter += 1

    for svc in services:
        svc["depends_on"] = [
            name_to_id.get(dep, dep)
            for dep in svc.get("depends_on", [])
        ]

    dep = architecture.get("deployment")
    if dep:
        dep["task_id"] = "DEPLOY-01"
        dep["depends_on"] = [
            name_to_id.get(d, d)
            for d in dep.get("depends_on", [])
        ]

    return architecture


# ── Planner agent ──────────────────────────────────────────────────────────────

def _gemini_planner(prompt):
    mat_path = "docs/materialized_tasks.json"
    if not os.path.exists(mat_path):
        raise RuntimeError("materialized_tasks.json not found — run task-materializer first")

    with open(mat_path, encoding="utf-8") as f:
        materialized = json.load(f)

    raw_tasks = materialized.get("tasks", [])
    if not raw_tasks:
        raise RuntimeError("materialized_tasks.json has no tasks")

    # Load parallel_groups từ dep_graph — [FIX-P1] sprint = wave, không phải i//5
    dep_graph_path = "docs/dependency_graph.json"
    parallel_groups: list[list[str]] = []

    if os.path.exists(dep_graph_path):
        with open(dep_graph_path, encoding="utf-8") as f:
            dep_graph = json.load(f)
        parallel_groups = dep_graph.get("parallel_groups", [])
    
    if not parallel_groups:
        # Fallback: mỗi task là một group riêng (safe but slow)
        execution_order = materialized.get("execution_order", [t["id"] for t in raw_tasks])
        parallel_groups = [[tid] for tid in execution_order]
        print("      [planner] WARN: parallel_groups not found in dep_graph, falling back to sequential")

    # Load stories để lấy acceptance_criteria nếu có
    stories = []
    if os.path.exists("docs/stories.json"):
        with open("docs/stories.json", encoding="utf-8") as f:
            stories = json.load(f)

    story_map = {}
    for s in stories:
        ref = s.get("id") or s.get("story_id") or s.get("ref")
        if ref:
            story_map[ref] = s.get("acceptance_criteria", "")

    # Priority map theo component
    PRIORITY_MAP = {
        "backend":   "P0",
        "frontend":  "P1",
        "fullstack": "P0",
        "infra":     "P2",
        "service":   "P1",
    }

    # Story points theo component
    POINTS_MAP = {
        "backend":   5,
        "frontend":  5,
        "fullstack": 8,
        "infra":     3,
        "service":   5,
    }

    # [FIX-P1] Build sprints từ parallel_groups — mỗi wave = 1 sprint.
    # Điều này đảm bảo: sprint N chỉ chứa tasks mà TẤT CẢ deps của chúng
    # đều nằm ở sprint < N. Dev có thể execute từng sprint theo thứ tự an toàn.
    task_lookup: dict = {}
    for t in raw_tasks:
        if t.get("id"):
            task_lookup[t["id"]] = t
        if t.get("name"):
            task_lookup[t["name"]] = t

    # [FIX P-MISSING] Detect tasks có trong materialized nhưng không có trong
    # bất kỳ group nào của parallel_groups — sẽ bị planner bỏ qua hoàn toàn
    # → task count mismatch RuntimeError. Append chúng vào một group cuối.
    all_group_keys = {tid for group in parallel_groups for tid in group}
    orphan_task_ids = [t["id"] for t in raw_tasks if t.get("id") and t["id"] not in all_group_keys]
    if orphan_task_ids:
        print(f"      [planner] WARN [P-MISSING]: {len(orphan_task_ids)} task(s) not in any parallel_group — appending to last wave: {orphan_task_ids}")
        parallel_groups = list(parallel_groups) + [orphan_task_ids]

    # Warn keys in groups that don't exist in task_lookup
    all_group_keys_list = [tid for group in parallel_groups for tid in group]
    missing_keys = [tid for tid in all_group_keys_list if tid not in task_lookup]
    if missing_keys:
        print(f"      [planner] WARN: {len(missing_keys)} keys not in task_lookup: {missing_keys[:4]}")

    sprint_names = ["Foundation", "Core Features", "Integration", "Polish", "Deployment"]

    # [FIX P-EMPTY-SPRINT + P-SPRINT-RENUM]
    # Trước: mọi group đều tạo sprint, kể cả group rỗng (task không tồn tại) →
    #   - sprint với tasks=[] → total < len(raw_tasks) → RuntimeError
    #   - sprint numbers có gap (1, 2, 4 — sprint 3 rỗng)
    # Sau: chỉ tạo sprint khi có ít nhất 1 task hợp lệ.
    #      sprint_num được đếm theo số sprint thực sự được tạo (không dùng enumerate index).
    sprints = []
    sprint_num = 0  # incremented only when a non-empty sprint is appended

    for sprint_idx, group in enumerate(parallel_groups):
        sprint_tasks = []

        for task_id in group:
            t = task_lookup.get(task_id)
            if not t:
                print(f"      [planner] WARN: task_id {task_id} in parallel_groups but not in materialized tasks")
                continue
            component = t.get("component", "fullstack")
            story_ref = t.get("story_ref", "")
            ac = story_map.get(story_ref, f"Complete {t.get('name', task_id)}")

            # sprint number sẽ được gán sau khi biết sprint_num chính xác
            sprint_tasks.append({
                **t,
                "summary": t.get("summary") or t.get("name", task_id),
                "sprint": -1,  # placeholder — filled below
                "priority": PRIORITY_MAP.get(component, "P1"),
                "story_points": POINTS_MAP.get(component, 5),
                "status": "TODO",
                "acceptance_criteria": ac,
            })

        # [FIX P-EMPTY-SPRINT] skip group hoàn toàn rỗng
        if not sprint_tasks:
            print(f"      [planner] WARN [P-EMPTY-SPRINT]: group {sprint_idx} has no valid tasks — skipped")
            continue

        sprint_num += 1  # [FIX P-SPRINT-RENUM] đếm chỉ khi có task thật

        # Gán sprint number thật vào từng task
        for st in sprint_tasks:
            st["sprint"] = sprint_num

        name_idx = sprint_num - 1
        sprint_name = sprint_names[name_idx] if name_idx < len(sprint_names) else f"Sprint {sprint_num}"
        sprints.append({
            "number": sprint_num,
            "name": sprint_name,
            "tasks": sprint_tasks,
        })

    # [FIX-P2] Validation: mọi task phải có sprint > sprint của tất cả deps.
    # Nếu có vi phạm (do upstream bug còn sót), cố gắng tự sửa bằng cách
    # đẩy task vi phạm lên sprint sau dep của nó, thay vì crash ngay.
    task_sprint_map = {
        t["id"]: s["number"]
        for s in sprints
        for t in s["tasks"]
    }

    def _collect_violations(sprints_, tsm):
        v = []
        for s_ in sprints_:
            for t_ in s_["tasks"]:
                for dep_id_ in t_.get("depends_on", []):
                    dep_sp = tsm.get(dep_id_)
                    if dep_sp is not None and dep_sp >= t_["sprint"]:
                        v.append((t_["id"], t_["sprint"], dep_id_, dep_sp))
        return v

    violations_raw = _collect_violations(sprints, task_sprint_map)

    if violations_raw:
        print(f"      [planner] WARN [P2]: {len(violations_raw)} sprint ordering violation(s) detected — attempting auto-heal")
        # Auto-heal: reassign task sprint = max(dep_sprint) + 1
        # Build id → task object map for mutation
        id_to_task = {t["id"]: t for s in sprints for t in s["tasks"]}
        id_to_sprint_obj = {t["id"]: s for s in sprints for t in s["tasks"]}

        for _ in range(len(raw_tasks) + 2):
            violations = _collect_violations(sprints, task_sprint_map)
            if not violations:
                break
            for (tid, t_sprint, dep_id, dep_sprint) in violations:
                needed = dep_sprint + 1
                task_obj = id_to_task.get(tid)
                if not task_obj or task_obj["sprint"] >= needed:
                    continue
                old = task_obj["sprint"]
                task_obj["sprint"] = needed
                task_sprint_map[tid] = needed
                old_s = id_to_sprint_obj.get(tid)
                if old_s:
                    old_s["tasks"] = [t for t in old_s["tasks"] if t["id"] != tid]
                target = next((s for s in sprints if s["number"] == needed), None)
                if target:
                    target["tasks"].append(task_obj)
                    id_to_sprint_obj[tid] = target
                else:
                    ns = {"number": needed, "name": f"Sprint {needed}", "tasks": [task_obj]}
                    sprints.append(ns)
                    sprints.sort(key=lambda s_: s_["number"])
                    id_to_sprint_obj[tid] = ns
                print(f"      [planner] AUTO-HEAL: {tid} {old}→{needed}")
        else:
            remaining = _collect_violations(sprints, task_sprint_map)
            if remaining:
                raise RuntimeError(...)

        sprints = [s for s in sprints if s["tasks"]]
        for i, s in enumerate(sprints):
            new_num = i + 1
            for t in s["tasks"]:
                task_sprint_map[t["id"]] = new_num
                t["sprint"] = new_num
            s["number"] = new_num

        # Final check — if violations remain after heal, raise with full context
        violations_final = _collect_violations(sprints, task_sprint_map)
        if violations_final:
            raise RuntimeError(
                f"[planner] Sprint ordering violation (auto-heal failed) — "
                f"{len(violations_final)} dep(s) still in same or later sprint:\n"
                + "\n".join(
                    f"  - {tid}(sprint {ts}) depends on {did}(sprint {ds})"
                    for tid, ts, did, ds in violations_final
                )
                + "\n\nROOT CAUSE: architect-agent produced incorrect depends_on. "
                "Run with --fix-architect to re-run architect step."
            )
        print(f"      [planner] [P2] Auto-heal complete — {len(violations_raw)} violation(s) resolved")

    tasks_json = {
        "project": "POS App",
        "generated_from": "materialized_tasks.json",
        "dependency_graph": materialized.get("dependency_graph", {}),
        "sprints": sprints,
    }

    total = sum(len(s["tasks"]) for s in sprints)

    # Validation: số task phải khớp materialized
    if total != len(raw_tasks):
        raise RuntimeError(
            f"Planner task count mismatch: got {total}, expected {len(raw_tasks)}"
        )

    with open("docs/tasks.json", "w", encoding="utf-8") as f:
        json.dump(tasks_json, f, indent=2, ensure_ascii=False)

    print(f"      [planner] tasks.json ({total} tasks, {len(sprints)} sprints) — wave-based sprint grouping")
    return "PLANNER_DONE"


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT COMPILER  [v4: export artifact files]
#
#   Pipeline:
#     1. Đọc docs/tasks.json (planner output — raw)
#     2. normalize_tasks_to_contracts() → enforce schema cứng in-place
#     3. Ghi lại docs/tasks.json (backward compat)
#     4. [NEW] export_contracts_to_files() → docs/contracts/TASK-XX.contract.json
#
#   Sau bước này:
#     - docs/contracts/ là nguồn truth duy nhất cho DEV + TESTER
#     - tasks.json vẫn được update nhưng KHÔNG còn là nguồn truth của contract
# ══════════════════════════════════════════════════════════════════════════════