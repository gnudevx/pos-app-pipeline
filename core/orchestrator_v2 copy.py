"""
POS Pipeline Orchestrator - V2 (Cumulative Execution & Scaffold Safe Mode)
Luong day du: Phase1 -> Phase2 (Jira sprint that) -> Phase3 (code+test+push that)
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error
import base64
from state_manager import (
    set_task_state,
    can_execute,
    print_state_summary
)
from core.adapter_v2_draft import run_agent
from config import POS_APP_DIR
import core.contracts.parser as p
DOCS_DIR = "docs"
BUGS_DIR = os.path.join(DOCS_DIR, "bugs")
MAX_RETRY = 3
from core.infra.smart_scaffold import write_frontend_infra_once
from core.infra.git_ops import (
    init_repo_if_needed,
    make_branch_name,
    run,  # import thêm hàm run từ git_ops để điều phối nhánh cục bộ
    ensure_backbone,
    finalize_and_merge
)

# ── Jira helpers ─────────────────────────────────────────
def log_step(
    task_id,
    step,
    message=""
):
    ts = datetime.datetime.now().strftime(
        "%H:%M:%S"
    )

    print(
        f"\n[{ts}]"
        f" [{task_id}]"
        f" [{step}]"
        f" {message}"
    )
    
def _jira_cfg():
    try:
        with open(".mcp.json", encoding="utf-8") as f:
            cfg = json.load(f)["mcpServers"]["jira"]["env"]
        return cfg["ATLASSIAN_URL"], cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_TOKEN"]
    except Exception:
        return None, None, None


def _headers(email, token):
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def jira_get_project_key():
    url, email, token = _jira_cfg()
    if not url:
        return None
    req = urllib.request.Request(
        f"{url}/rest/api/3/project",
        headers=_headers(email, token)
    )
    with urllib.request.urlopen(req) as r:
        projects = json.loads(r.read())
    pos = next((p for p in projects if p["key"] == "PA"), projects[0])
    return pos["key"]


def jira_create_sprint(project_key, sprint_name, sprint_number):
    """Tao Sprint that tren Jira board."""
    url, email, token = _jira_cfg()
    if not url:
        return None

    req = urllib.request.Request(
        f"{url}/rest/agile/1.0/board?projectKeyOrId={project_key}",
        headers=_headers(email, token)
    )
    try:
        with urllib.request.urlopen(req) as r:
            boards = json.loads(r.read())
        if not boards.get("values"):
            return None
        board_id = boards["values"][0]["id"]

        payload = json.dumps({
            "name": f"Sprint {sprint_number} — {sprint_name}",
            "originBoardId": board_id,
            "goal": f"Complete {sprint_name} features for POS app"
        }).encode()
        req2 = urllib.request.Request(
            f"{url}/rest/agile/1.0/sprint",
            data=payload,
            headers=_headers(email, token),
            method="POST"
        )
        with urllib.request.urlopen(req2) as r:
            sprint = json.loads(r.read())
        return sprint["id"]
    except Exception as e:
        print(f"      Sprint creation skipped: {e}")
        return None


def jira_add_to_sprint(sprint_id, issue_key):
    url, email, token = _jira_cfg()
    if not url or not sprint_id:
        return
    payload = json.dumps({"issues": [issue_key]}).encode()
    req = urllib.request.Request(
        f"{url}/rest/agile/1.0/sprint/{sprint_id}/issue",
        data=payload,
        headers=_headers(email, token),
        method="POST"
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def jira_create_ticket(project_key, task):
    url, email, token = _jira_cfg()
    if not url:
        return None
    payload = json.dumps({
        "fields": {
            "project": {"key": project_key},
            "summary": f"[{task['id']}] {task['summary']}",
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": task["description"]}
                ]}]
            },
            "issuetype": {"name": "Task"},
            "priority": {
                "name": "High" if task["priority"] == "P0" else "Medium"
            },
        }
    }).encode()
    req = urllib.request.Request(
        f"{url}/rest/api/3/issue",
        data=payload,
        headers=_headers(email, token),
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["key"]


def jira_update_status(jira_key, transition_name):
    url, email, token = _jira_cfg()
    if not url or not jira_key or jira_key == "N/A":
        return
    req = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/transitions",
        headers=_headers(email, token)
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    transition = next(
        (t for t in data["transitions"]
         if transition_name.lower() in t["name"].lower()),
        None
    )
    if not transition:
        return
    payload = json.dumps({"transition": {"id": transition["id"]}}).encode()
    req2 = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/transitions",
        data=payload,
        headers=_headers(email, token),
        method="POST"
    )
    urllib.request.urlopen(req2)
    print(f"      Jira {jira_key} -> {transition_name}")


def jira_add_pr_link(jira_key, branch_name):
    url, email, token = _jira_cfg()
    if not url or not jira_key or jira_key == "N/A":
        return
    payload = json.dumps({
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": (
                    f"Branch: {branch_name}\n"
                    f"PR: feature -> main (pending review)\n"
                    f"Updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )}
            ]}]
        }
    }).encode()
    req = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/comment",
        data=payload,
        headers=_headers(email, token),
        method="POST"
    )
    try:
        urllib.request.urlopen(req)
        print(f"      Jira {jira_key} <- branch comment added")
    except Exception:
        pass


# ── Phase 1 ───────────────────────────────────────────────

def phase1_requirement(requirement):
    print("\n" + "=" * 60)
    print("PHASE 1 — Requirement & Planning")
    print("=" * 60)
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(BUGS_DIR, exist_ok=True)

    print("\n  [1/7] Requirement Agent...")
    result = run_agent("requirement-agent", requirement)
    if not result.startswith("REQUIREMENT_DONE"):
        raise RuntimeError(f"Requirement agent failed: {result}")
    print("  entities.json + stories.json + requirements.md created")

    print("\n  [2/7] Knowledge Graph...")
    result = run_agent("knowledge-graph", "Build knowledge graph from entities")
    if not result.startswith("KNOWLEDGE_GRAPH_DONE"):
        raise RuntimeError(f"Knowledge Graph failed: {result}")
    print("  knowledge_graph.json created")

    print("\n  [3/7] Architect Agent...")
    result = run_agent("architect-agent", "Read entities.json and requirements.md")
    if not result.startswith("ARCHITECT_DONE"):
        raise RuntimeError(f"Architect agent failed: {result}")
    print("  architecture.json created")

    print("\n  [4/7] Task Materializer...")
    result = run_agent("task-materializer", "Materialize tasks from architecture")
    if not result.startswith("TASK_MATERIALIZED"):
        raise RuntimeError(f"Task Materializer failed: {result}")
    print("  materialized_tasks.json created")

    print("\n  [5/7] Planner Agent...")
    result = run_agent("planner-agent", "Read stories.json and create tasks.json")
    if not result.startswith("PLANNER_DONE"):
        raise RuntimeError(f"Planner agent failed: {result}")
        
    print("\n  [6/7] Contract Compiler...")
    result = run_agent("contract-compiler", "Compile contracts from tasks.json")
    if not result.startswith("CONTRACT_COMPILED"):
        raise RuntimeError(f"Contract compiler failed: {result}")
    print("  docs/contracts/ populated")

    print("\n  [7/7] Structure Planner...")
    result = run_agent("structure-planner", "Generate scaffold structure from contracts and knowledge graph")
    if not result.startswith("STRUCTURE_PLANNED"):
        raise RuntimeError(f"Structure planner failed: {result}")
    print("  docs/structure_plan.json created")

    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)

    total   = sum(len(s["tasks"]) for s in tasks["sprints"])
    pts     = sum(t["story_points"] for s in tasks["sprints"] for t in s["tasks"])
    sprints = len(tasks["sprints"])
    print(f"  tasks.json: {total} tasks, {pts} pts, {sprints} sprints")
    return tasks


# ── Phase 2 ───────────────────────────────────────────────
def phase2_jira_sync(tasks):
    print("\n" + "=" * 60)
    print("PHASE 2 — Jira Sync (REAL): Tao Sprint + Tickets")
    print("=" * 60)

    ticket_map = {}
    sprint_map = {}

    try:
        project_key = jira_get_project_key()
        if not project_key:
            print("  Khong lay duoc project key")
            return ticket_map

        print(f"\n  Project: {project_key}")

        for sprint_data in tasks["sprints"]:
            sprint_num = sprint_data["number"]
            sprint_name = sprint_data["name"]

            print(f"\n  Tao Sprint {sprint_num}: {sprint_name}...")
            sprint_id = jira_create_sprint(project_key, sprint_name, sprint_num)
            sprint_map[sprint_num] = sprint_id

            for task in sprint_data["tasks"]:
                jira_key = jira_create_ticket(project_key, task)
                ticket_map[task["id"]] = jira_key

                if sprint_id and jira_key:
                    jira_add_to_sprint(sprint_id, jira_key)

                print(f"    {task['id']} -> {jira_key}: {task['summary'][:40]}")
                time.sleep(0.3)

        total = len(ticket_map)
        print(f"\n  Phase 2 done: {total} tickets, {len(sprint_map)} sprints")
    except Exception as e:
        print(f"\n  Jira sync failed: {e}")

    return ticket_map


# ── Phase 3 ───────────────────────────────────────────────
def _read_latest_bug_report(task_id):
    import glob
    pattern = f"{BUGS_DIR}/BUG-{task_id}-*.md"
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return f.read()[:1000]

def _validate_tasks_json(tasks: dict) -> None:
    all_task_ids = set()
    for sprint in tasks.get("sprints", []):
        for task in sprint.get("tasks", []):
            all_task_ids.add(task["id"])
 
    warnings = []
    for sprint in tasks.get("sprints", []):
        for task in sprint.get("tasks", []):
            task_id = task["id"]
            for dep in task.get("depends_on", []):
                if dep == task_id:
                    warnings.append(f"  [VALIDATE] WARNING: {task_id} has self-loop dep '{dep}' — will be SKIPPED")
                elif dep not in all_task_ids:
                    warnings.append(f"  [VALIDATE] WARNING: {task_id} depends on unknown task '{dep}'")
 
    if warnings:
        print("\n[VALIDATE] tasks.json dependency issues detected:")
        for w in warnings:
            print(w)
    else:
        print("[VALIDATE] tasks.json dependency graph: OK")


def phase3_sprint_execution(tasks, ticket_map):
    print("\n" + "=" * 60)
    print("PHASE 3 — Sprint Execution Loop (Cumulative Fix)")
    print("=" * 60)

    results = {"passed": [], "failed": [], "escalated": []}
    init_repo_if_needed(POS_APP_DIR)
    _validate_tasks_json(tasks)
    
    # ── [FIX] KHỞI TẠO NHÁNH XƯƠNG SỐNG TÍCH HỢP GỐI ĐẦU ──────────────────
    BACKBONE_BRANCH = "integration/current-sprint"
    ensure_backbone(POS_APP_DIR)
    run("checkout develop", POS_APP_DIR)
    write_frontend_infra_once(POS_APP_DIR)
    run("add -A", POS_APP_DIR)
    run('commit -m "chore: frontend infra scaffold" --allow-empty', POS_APP_DIR)
    run(f"checkout {BACKBONE_BRANCH}", POS_APP_DIR)
    run(f"merge develop", POS_APP_DIR) 
    print(f"  [git-fix] Đã tạo nhánh nền tảng tích hợp gối đầu: {BACKBONE_BRANCH}")
    # ───────────────────────────────────────────────────────────────────

    for sprint in tasks["sprints"]:
        print(f"\n  ========== Sprint {sprint['number']}: {sprint['name']} ==========")
        print_state_summary()

        for task in sprint["tasks"]:
            task_id = task["id"]

            if not can_execute(task):
                log_step(task_id, "SKIP", "dependency not passed")
                set_task_state(task_id, "blocked", "dependency failed")
                continue

            jira_key = ticket_map.get(task_id, "N/A")
            log_step(task_id, "START", task.get("summary", ""))
            set_task_state(task_id, "in_progress")

            MAX_DEV_RETRY = 3
            task_passed = False
            bug_context = None
            branch = make_branch_name(task_id, task["summary"])

            for dev_retry in range(MAX_DEV_RETRY + 1):
                log_step(task_id, "DEV", f"attempt {dev_retry + 1}")
                
                # ── [FIX] ÉP NHÁNH FEATURE PHẢI SINH RA TỪ ĐẦU MÚT MỚI NHẤT CỦA BACKBONE ──
                if dev_retry == 0:
                    log_step(task_id, "SCAFFOLD-PREP", f"Đồng bộ base từ {BACKBONE_BRANCH}...")
                    run(f"checkout {BACKBONE_BRANCH}", POS_APP_DIR)
                    # Tạo nhánh feature kế thừa 100% file App.tsx, package.json của task trước
                    run(f"checkout -B {branch}", POS_APP_DIR)
                # ─────────────────────────────────────────────────────────────────────

                try:
                    dev_result = run_agent("dev-agent", task_id, bug_context=bug_context, attempt=dev_retry + 1)
                except Exception as e:
                    log_step(task_id, "DEV_ERROR", str(e))
                    continue

                if dev_result.startswith("DEV_ESCALATE"):
                    print("    [DEV] ESCALATED")
                    results["escalated"].append(task_id)
                    jira_update_status(jira_key, "Blocked")
                    _write_escalation(task_id, "Dev agent could not implement")
                    set_task_state(task_id, "escalated", "dev escalated")
                    break

                if dev_result.startswith("DEV_CONTRACT_FAIL"):
                    print("    [DEV] Contract invalid — retrying immediately")
                    bug_context = dev_result.replace("DEV_CONTRACT_FAIL:", "")
                    time.sleep(1)
                    continue

                if dev_result.startswith(("DEV_STATIC_FAIL", "DEV_SMOKE_FAIL", "DEV_IMPORT_FAIL", "DEV_SERIALIZATION_FAIL")):
                    signal = dev_result.split(":")[0]
                    detail = dev_result[len(signal) + 1:]
                    print(f"    [DEV] {signal} — retrying")
                    bug_context = detail[:800]
                    time.sleep(2 ** (dev_retry + 1))
                    continue

                if dev_result.startswith("DEV_SKIP"):
                    results["failed"].append(task_id)
                    jira_update_status(jira_key, "Won't Do")
                    break

                if dev_retry == 0:
                    jira_add_pr_link(jira_key, branch)
                print(f"    [DEV] Done — branch: {branch}")

                print("\n    [TEST] Starting tester agent...")
                test_result = run_agent("tester-agent", task_id)
                sig = p.parse_test_signal(test_result)

                if sig["passed"]:
                    # ── [FIX] MERGE NGAY VÀO BACKBONE KHI PASS ĐỂ TASK SAU KẾ THỪA ──────
                    print(f"    [SYNC] Task {task_id} PASSED! Gộp ngay vào xương sống...")
                    run(f"checkout {BACKBONE_BRANCH}", POS_APP_DIR)
                    ok = finalize_and_merge(POS_APP_DIR, branch, task_id, task["summary"], task.get("component", "fullstack"))
                    
                    if not ok:
                        set_task_state(task_id, "failed", "backbone merge conflict")
                        break

                    # ── [FIX BUG-3] Cập nhật develop từ backbone sau mỗi task pass ──────
                    # Nếu không làm bước này, develop vẫn ở trạng thái cũ.
                    # Khi abort_to_backbone() checkout backbone rồi retry gọi
                    # "checkout develop", develop sẽ thiếu code của các task đã pass.
                    run("checkout develop", POS_APP_DIR)
                    run(f"merge {BACKBONE_BRANCH} --no-edit", POS_APP_DIR)
                    run(f"checkout {BACKBONE_BRANCH}", POS_APP_DIR)
                    # ──────────────────────────────────────────────────────────────────────

                    set_task_state(task_id, "passed")
                    log_step(task_id, "DONE", "integrated into backbone")
                    results["passed"].append(task_id)
                    task_passed = True
                    break

                bug_context = _read_latest_bug_report(task_id)
                # ... (Phần retry/sleep phía dưới giữ nguyên)

            if not task_passed and task_id not in results["escalated"]:
                results["failed"].append(task_id)

    return results


def _write_escalation(task_id, reason):
    os.makedirs(BUGS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    path = f"{BUGS_DIR}/ESCALATE-{task_id}-{ts}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"# Escalation: {task_id}\n"
            f"**Reason:** {reason}\n"
            f"**Time:** {ts}\n"
        )


# ── Main ──────────────────────────────────────────────────

def run_pipeline(requirement):
    from core.adapter_v2_draft import AGENT_BACKEND
    print(f"\n{'=' * 60}")
    print(f"POS PIPELINE V2  |  backend: {AGENT_BACKEND.upper()}")
    print(f"{'=' * 60}")
    print(f"Requirement: {requirement}")

    tasks = phase1_requirement(requirement)
    ticket_map = phase2_jira_sync(tasks)
    results = phase3_sprint_execution(tasks, ticket_map)

    total = sum(len(s["tasks"]) for s in tasks["sprints"])
    print(f"\n{'=' * 60}")
    print("PIPELINE COMPLETE")
    print(f"{'=' * 60}")
    print(f"Backend  : {AGENT_BACKEND.upper()}")
    print(f"Passed   : {len(results['passed'])}/{total}")
    if results["escalated"]:
        print(f"Review   : {results['escalated']}")
    print("\nJira: https://gnudevx.atlassian.net/jira/software/projects/PA/boards")


if __name__ == "__main__":
    req = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else \
        "Build a POS app with product catalog (name, price, stock), shopping cart with add/remove items, checkout that generates a receipt, and inventory stock management"
    run_pipeline(req)