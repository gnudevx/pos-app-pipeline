"""
merge_coordinator.py — Phase 4.1: Merge Coordinator

Responsibilities:
  - Đọc tasks.json, lấy danh sách task PASSED từ Phase 3
  - Topo sort theo task-to-task dependency
  - Merge feature branches vào integration/run-xxx theo đúng thứ tự
  - Push integration branch → trigger GitHub CI chạy integration_pipeline.py
  - Detect conflict sớm, fail fast thay vì merge rác

Expected tasks.json shape:
  {
    "sprints": [{
      "tasks": [{
        "id": "TASK-01",
        "branch": "feature/task-01-product-list",
        "status": "PASSED",
        "depends_on_tasks": ["TASK-02"],   ← task-level deps (optional)
        "artifacts": ["src/..."]
      }]
    }]
  }

Standalone usage (pre-CI step):
  python scripts/phase4/merge_coordinator.py
  python scripts/phase4/merge_coordinator.py --dry-run
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict, deque
import os
from pathlib import Path
from typing import Any
from core.infra.git_ops import current_branch, run


# ─────────────────────────────────────────────────────────────────────────────
# Config  (dùng khi chạy standalone qua __main__)
# ─────────────────────────────────────────────────────────────────────────────

TASKS_JSON = "docs/tasks.json"
REPO_DIR   = "../pos-app-test_v2"


# ── Types ──────────────────────────────────────────────────────────────────

TaskDict = dict[str, Any]


# ── Helpers ────────────────────────────────────────────────────────────────

def create_integration_branch(
    repo_dir: str,
    run_id: str,
) -> str:
    branch = f"integration/{run_id}"

    run("checkout develop", repo_dir)
    run("pull origin develop", repo_dir)

    ok, out = run(f"checkout -B {branch}", repo_dir)
    if not ok:
        raise RuntimeError(out)

    ok, out = run(f"push -u origin {branch} --force", repo_dir)
    if not ok:
        raise RuntimeError(out)

    return branch


def finalize_integration_branch(
    branch: str,
    repo_dir: str,
) -> None:
    """Merge integration branch → develop sau khi pipeline PASS."""
    ok, out = run("checkout develop", repo_dir)
    if not ok:
        raise RuntimeError(out)

    ok, out = run(f"merge --no-ff {branch}", repo_dir)
    if not ok:
        raise RuntimeError(out)

    ok, out = run("push origin develop", repo_dir)
    if not ok:
        raise RuntimeError(out)

    print(f"  [merge] ✓ {branch} → develop pushed")


def verify_merged_artifacts(
    repo_dir: str,
    tasks: list[TaskDict],
) -> tuple[bool, list[str]]:
    """
    [FIX BUG-M1] Post-merge verification: ensure task artifacts actually in backbone.
    
    Prevents silent merge failures where:
      - git merge succeeded (no conflicts)
      - BUT: task files not actually in develop (lost in rebase/stash)
      - Task marked "PASSED" but code isn't there
    
    Returns: (all_verified, error_list)
    """
    errors: list[str] = []
    
    # Checkout develop to verify
    ok, _ = run("checkout develop", repo_dir)
    if not ok:
        return False, ["Failed to checkout develop for verification"]
    
    for task in tasks:
        task_id = task.get("id", "UNKNOWN")
        artifacts = task.get("artifacts", [])
        
        if not artifacts:
            # No artifacts specified → skip
            continue
        
        for artifact in artifacts:
            # Use `git ls-tree` to check if file exists in HEAD
            ok, out = run(f"ls-tree -r HEAD -- {artifact!r}", repo_dir)
            if not ok or not out.strip():
                errors.append(
                    f"[VERIFY FAIL] {task_id}: artifact missing in develop: {artifact}"
                )
                continue
            
            # File exists → check size sanity (not just a stub)
            size_match = out.split()
            if len(size_match) >= 4:
                try:
                    size = int(size_match[3])
                    if size < 10:  # Files should be at least 10 bytes (sanity check)
                        errors.append(
                            f"[VERIFY WARN] {task_id}: artifact suspiciously small ({size} bytes): {artifact}"
                        )
                except (ValueError, IndexError):
                    pass  # Size parse failed, assume OK
    
    return len(errors) == 0, errors


def cleanup_integration_branch(
    branch: str,
    repo_dir: str,
) -> None:
    run("checkout develop", repo_dir)
    run(f"branch -D {branch}", repo_dir)


def validate_dependencies(
    tasks: list[TaskDict],
    passed_set: set[str],
) -> None:
    for task in tasks:
        for dep in task.get("depends_on_tasks", []):
            if dep not in passed_set:
                raise RuntimeError(
                    f"{task['id']} depends on non-passed task {dep}"
                )


def detect_artifact_conflicts(tasks: list[TaskDict]) -> None:
    artifact_map: dict[str, list[str]] = defaultdict(list)

    for task in tasks:
        for art in task.get("artifacts", []):
            artifact_map[art].append(task["id"])

    conflicts = {
        art: ids
        for art, ids in artifact_map.items()
        if len(ids) > 1
    }

    if conflicts:
        lines = [f"{art}: {ids}" for art, ids in conflicts.items()]
        raise RuntimeError(
            "Artifact conflicts detected:\n" + "\n".join(lines)
        )


def load_all_tasks(tasks_json_path: str = TASKS_JSON) -> list[TaskDict]:
    """Flatten all sprints → flat task list."""
    data = json.loads(Path(tasks_json_path).read_text(encoding="utf-8"))
    tasks: list[TaskDict] = []
    for sprint in data.get("sprints", []):
        tasks.extend(sprint.get("tasks", []))
    return tasks


# ── Topological Sort ───────────────────────────────────────────────────────

def topo_sort(tasks: list[TaskDict]) -> list[TaskDict]:
    """
    Kahn's algorithm trên task-to-task dependency.
    Raises RuntimeError nếu có circular dependency.
    """
    task_map = {t["id"]: t for t in tasks}
    in_degree: dict[str, int] = defaultdict(int)
    graph: dict[str, list[str]] = defaultdict(list)

    for task in tasks:
        task_id = task["id"]
        in_degree.setdefault(task_id, 0)
        for dep in task.get("depends_on_tasks", []):
            if dep not in task_map:
                raise RuntimeError(
                    f"{task_id} depends on unknown task {dep!r}"
                )
            graph[dep].append(task_id)
            in_degree[task_id] += 1

    queue: deque[str] = deque(
        tid for tid in task_map if in_degree[tid] == 0
    )
    sorted_ids: list[str] = []

    while queue:
        tid = queue.popleft()
        sorted_ids.append(tid)
        for dependent in graph[tid]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(sorted_ids) != len(tasks):
        remaining = [tid for tid in task_map if tid not in sorted_ids]
        raise RuntimeError(
            f"Circular dependency detected among tasks: {remaining}"
        )

    return [task_map[tid] for tid in sorted_ids]


# ── Branch Merge ───────────────────────────────────────────────────────────

def fetch_all_branches(repo_dir: str | Path) -> None:
    ok, out = run("fetch --all --prune", repo_dir)
    if not ok:
        raise RuntimeError(out)


def branch_exists_remote(branch: str, repo_dir: str | Path) -> bool:
    print("REPO =", os.path.abspath(repo_dir))
    ok, out = run(f"ls-remote --heads origin {branch}", repo_dir)
    print("OK =", ok)
    print("OUT =", out)
    return ok and bool(out.strip())


def merge_one_branch(
    branch: str,
    task_id: str,
    repo_dir: str | Path,
) -> None:
    current = current_branch(repo_dir)
    print(f"  [merge] {branch} → {current}")

    if not branch_exists_remote(branch, repo_dir):
        raise RuntimeError(
            f"Branch {branch!r} does not exist on remote (task {task_id})"
        )

    ok, out = run(
        f'merge --no-ff origin/{branch} '
        f'-m "ci-merge: {task_id} ({branch})"',
        repo_dir,
    )

    if not ok:
        run("merge --abort", repo_dir)
        raise RuntimeError(f"Merge conflict on {branch}:\n{out}")


# ── Main Entry ─────────────────────────────────────────────────────────────
def check_shared_frontend_files(tasks_list: list) -> list[str]:
    """
    [FIX BUG-A3] Phát hiện shared frontend files trước khi merge.
    
    Những files như App.tsx, package.json được nhiều task modify nhưng
    không nằm trong task["artifacts"] → detect_artifact_conflicts() bỏ sót.
 
    Trả về list warnings (không raise — pipeline vẫn chạy, chỉ warn).
    
    Thêm vào run_merge_coordinator() sau detect_artifact_conflicts():
        warnings = check_shared_frontend_files(candidate_tasks)
        for w in warnings:
            print(w)
    """
    KNOWN_SHARED = {"App.tsx", "package.json", "tsconfig.json", "vite.config.ts"}
    
    # Tìm tất cả frontend tasks
    frontend_tasks = [t for t in tasks_list if t.get("component") == "frontend"]
    
    if len(frontend_tasks) <= 1:
        return []
 
    warnings = []
    for shared_file in KNOWN_SHARED:
        tasks_touching = [
            t["id"] for t in frontend_tasks
            if any(shared_file in str(art) for art in t.get("artifacts", []))
        ]
        if len(tasks_touching) > 1:
            warnings.append(
                f"  [WARN] Shared file '{shared_file}' appears in multiple tasks: {tasks_touching}\n"
                f"         This will cause merge conflict. Ensure write_frontend_infra_once() was called."
            )
        elif len(frontend_tasks) > 1:
            # File không trong artifacts nhưng scaffold có thể đã viết
            warnings.append(
                f"  [INFO] {len(frontend_tasks)} frontend tasks detected. "
                f"Verify '{shared_file}' was only written to develop branch, not feature branches."
            )
            break  # Chỉ warn 1 lần
 
    return warnings
def run_merge_coordinator(
    passed_task_ids: list[str],
    repo_dir: str = REPO_DIR,
    tasks_json: str = TASKS_JSON,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Merge feature branches → integration/run-xxx → push.
    GitHub CI sẽ trigger integration_pipeline.py khi thấy push tới integration/**.

    Returns dict với keys: branch, tasks.
    Raises on any failure — caller handles state update.
    """
    print("\n── 4.1 Merge Coordinator ──────────────────────────────")

    all_tasks = load_all_tasks(tasks_json)

    passed_set = set(passed_task_ids)
    candidate_tasks = [t for t in all_tasks if t["id"] in passed_set]

    validate_dependencies(candidate_tasks, passed_set)
    detect_artifact_conflicts(candidate_tasks)
    warnings = check_shared_frontend_files(candidate_tasks)
    for w in warnings:
        print(w)
    if not candidate_tasks:
        print("  [merge] No passed tasks to merge.")
        return {"branch": None, "tasks": []}

    print(f"  [merge] Passed tasks: {[t['id'] for t in candidate_tasks]}")

    ordered = topo_sort(candidate_tasks)
    print(f"  [merge] Merge order (topo): {[t['id'] for t in ordered]}")

    if dry_run:
        print("  [merge] DRY RUN — skipping actual git operations")
        return {"branch": None, "tasks": ordered}

    fetch_all_branches(repo_dir)

    integration_branch = create_integration_branch(
        repo_dir,
        run_id=f"run-{int(time.time())}",
    )

    merged: list[TaskDict] = []
    for task in ordered:
        branch = task.get("branch", f"feature/{task['id'].lower()}")
        merge_one_branch(branch, task["id"], repo_dir)
        merged.append(task)

    # Push → trigger GitHub CI on integration/**
    ok, out = run(f"push origin {integration_branch}", repo_dir)
    if not ok:
        cleanup_integration_branch(integration_branch, repo_dir)
        raise RuntimeError(out)

    print(
        f"  [merge] ✓ Merged {len(merged)} branches → {integration_branch} (pushed)"
    )
    print(
        f"  [merge] ✓ GitHub CI sẽ trigger integration_pipeline.py trên {integration_branch}"
    )

    return {
        "branch": integration_branch,
        "tasks": merged,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entrypoint
# Dùng khi chạy trực tiếp trước CI (không phải trong integration_pipeline)
#
# Usage:
#   python scripts/phase4/merge_coordinator.py
#   python scripts/phase4/merge_coordinator.py --dry-run
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv

    # Đọc passed tasks từ tasks.json (không cần truyền vào — standalone tự load)
    all_tasks = load_all_tasks(TASKS_JSON)
    passed_task_ids = [
        t["id"]
        for t in all_tasks
        if t.get("status") == "PASSED" and isinstance(t.get("id"), str)
    ]

    if not passed_task_ids:
        print("[merge_coordinator] No PASSED tasks found — nothing to merge.")
        sys.exit(0)

    print(f"[merge_coordinator] Standalone run | tasks: {passed_task_ids}")

    try:
        result = run_merge_coordinator(
            passed_task_ids=passed_task_ids,
            repo_dir=REPO_DIR,
            tasks_json=TASKS_JSON,
            dry_run=dry_run,
        )
        branch = result.get("branch")
        if branch:
            print(f"\n[merge_coordinator] ✓ Done — branch: {branch}")
            print(f"[merge_coordinator]   Waiting for GitHub CI to pick up {branch}...")
        else:
            print("\n[merge_coordinator] ✓ Done (no branch created — dry run or no tasks)")
        sys.exit(0)
    except Exception as exc:
        print(f"\n[merge_coordinator] ✗ FAILED: {exc}")
        sys.exit(1)