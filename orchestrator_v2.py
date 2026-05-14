"""
POS Pipeline Orchestrator - V2
Luong day du: Phase1 -> Phase2 (Jira sprint that) -> Phase3 (code+test+push that)

Chay: python orchestrator.py
      python orchestrator.py "Build me a POS app with inventory"
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error
import base64

from adapter_v2 import run_agent
from config import POS_APP_DIR
import parser as p
DOCS_DIR = "docs"
BUGS_DIR = os.path.join(DOCS_DIR, "bugs")
MAX_RETRY = 3

from git_ops import (
    init_repo_if_needed,
    make_branch_name,
)
# ── Jira helpers ─────────────────────────────────────────

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

    # Lay board ID
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

        # Tao sprint
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
    """Them ticket vao sprint."""
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
    """Them comment vao Jira ticket voi thong tin branch/PR."""
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
    """Phase 1: Requirement Agent + Planner Agent."""
    print("\n" + "=" * 60)
    print("PHASE 1 — Requirement & Planning")
    print("=" * 60)
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(BUGS_DIR, exist_ok=True)

    print("\n  [1/2] Requirement Agent...")
    result = run_agent("requirement-agent", requirement)
    if not result.startswith("REQUIREMENT_DONE"):
        raise RuntimeError(f"Requirement agent failed: {result}")
    if result == "REQUIREMENT_DONE:FALLBACK":
        print("  [WARN] Using fallback stories — Gemini output may be low quality")
    print("  PRD + stories.json created")

    print("\n  [2/2] Planner Agent...")
    result = run_agent("planner-agent", "Read stories.json and create tasks.json")
    if not result.startswith("PLANNER_DONE"):
        raise RuntimeError(f"Planner agent failed: {result}")
    if result == "PLANNER_DONE:FALLBACK":
        print("  [WARN] Using fallback tasks — Gemini output may be low quality")
    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)
   
    total = sum(len(s["tasks"]) for s in tasks["sprints"])
    pts = sum(t["story_points"] for s in tasks["sprints"] for t in s["tasks"])
    sprints = len(tasks["sprints"])
    print(f"  tasks.json: {total} tasks, {pts} pts, {sprints} sprints")
    return tasks


# ── Phase 2 ───────────────────────────────────────────────
def phase2_jira_sync(tasks):
    """
    Phase 2: Tao Jira Sprint that + push tickets that.
    Moi sprint trong tasks.json = 1 Sprint that tren Jira.
    """
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

            # Tao Sprint that tren Jira
            print(f"\n  Tao Sprint {sprint_num}: {sprint_name}...")
            sprint_id = jira_create_sprint(project_key, sprint_name, sprint_num)
            sprint_map[sprint_num] = sprint_id
            if sprint_id:
                print(f"  Sprint created (id={sprint_id})")
            else:
                print("  Sprint creation skipped (board may not support it)")

            # Tao tickets va them vao sprint
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
TARGET_BRANCH = "develop"
def _merge_to_develop(branch, task_id):
    from config import POS_APP_DIR
    import subprocess

    def git(cmd):
        return subprocess.run(
            f"git {cmd}", shell=True,
            capture_output=True, text=True,
            cwd=POS_APP_DIR
        )

    print(f"    [MERGE] Merging {branch} -> {TARGET_BRANCH}...")

    # Tao develop neu chua co
    r = git(f"checkout {TARGET_BRANCH}")
    if r.returncode != 0:
        r = git(f"checkout -b {TARGET_BRANCH}")  # tao moi tu main
        if r.returncode != 0:
            print(f"    [MERGE] Cannot create {TARGET_BRANCH}: {r.stderr.strip()}")
            return

    # Merge --no-ff
    r = git(f'merge --no-ff {branch} -m "merge: {branch} -> {TARGET_BRANCH} [{task_id}]"')
    if r.returncode != 0:
        print(f"    [MERGE] Conflict: {r.stderr.strip()[:100]}")
        git("merge --abort")
        git("checkout main")  # quay ve main neu conflict
        return

    # Push
    r = git(f"push origin {TARGET_BRANCH}")
    if r.returncode == 0:
        print(f"    [MERGE] Done — {TARGET_BRANCH} updated")
    else:
        print(f"    [MERGE] Push skipped (no remote)")

    # Quay ve main sau khi merge xong
    git("checkout main")
def _read_latest_bug_report(task_id):
    """Đọc bug report mới nhất của task để truyền cho dev retry."""
    import glob
    pattern = f"{BUGS_DIR}/BUG-{task_id}-*.md"
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return f.read()[:1000]
    
def phase3_sprint_execution(tasks, ticket_map):
    print("\n" + "=" * 60)
    print("PHASE 3 — Sprint Execution Loop")
    print("=" * 60)

    results = {"passed": [], "failed": [], "escalated": []}

    # Khởi tạo repo nếu chưa có commit nào
    init_repo_if_needed(POS_APP_DIR)  # ← THÊM DÒNG NÀY

    for sprint in tasks["sprints"]:
        print(f"\n  ========== Sprint {sprint['number']}: {sprint['name']} ==========")

        for task in sprint["tasks"]:
            task_id = task["id"]
            jira_key = ticket_map.get(task_id, "N/A")

            print(f"\n  [{task_id}] {task['summary']}")
            print(f"  Jira: {jira_key} | Component: {task.get('component', '?')}")
            print(f"  Points: {task['story_points']} | Priority: {task['priority']}")

            jira_update_status(jira_key, "In Progress")

            MAX_DEV_RETRY = 3
            task_passed = False
            bug_context = None

            for dev_retry in range(MAX_DEV_RETRY + 1):
                print(f"\n    [DEV] attempt {dev_retry + 1}/{MAX_DEV_RETRY + 1}...")
                dev_result = run_agent("dev-agent", task_id, bug_context=bug_context)

                if dev_result.startswith("DEV_ESCALATE"):
                    print("    [DEV] ESCALATED")
                    results["escalated"].append(task_id)
                    jira_update_status(jira_key, "Blocked")
                    _write_escalation(task_id, "Dev agent could not implement")
                    break
                if dev_result.startswith("DEV_CONTRACT_FAIL"):
                    print("    [DEV] Contract invalid — retrying immediately")

                    bug_context = dev_result.replace(
                        "DEV_CONTRACT_FAIL:",
                        ""
                    )

                    time.sleep(1)
                    continue
                if dev_result.startswith("DEV_SKIP"):
                    results["failed"].append(task_id)
                    jira_update_status(jira_key, "Won't Do")
                    break

                # ← DÙNG make_branch_name thay vì đọc lại tasks.json
                branch = make_branch_name(task_id, task["summary"])

                if dev_retry == 0:
                    jira_add_pr_link(jira_key, branch)
                print(f"    [DEV] Done — branch: {branch}")

                print("\n    [TEST] Starting tester agent...")
                test_result = run_agent("tester-agent", task_id)
                sig = p.parse_test_signal(test_result)

                if sig["passed"]:
                    jira_update_status(jira_key, "Done")
                    results["passed"].append(task_id)
                    print("    [TEST] PASSED")
                    task_passed = True
                    _merge_to_develop(branch, task_id)
                    break
                bug_context = _read_latest_bug_report(task_id)
                print(f"    [TEST] FAIL (permanent={sig['permanent']}, transient={sig['transient']})")
                if dev_retry >= MAX_DEV_RETRY:
                    print(f"    [TEST] ESCALATED after {MAX_DEV_RETRY + 1} attempts")
                    results["escalated"].append(task_id)
                    jira_update_status(jira_key, "Blocked")
                    _write_escalation(task_id, f"Failed after {MAX_DEV_RETRY + 1} attempts")
                    break

                wait = 2 ** (dev_retry + 1)
                print(f"    [DEV] Retrying in {wait}s...")
                time.sleep(wait)

            if not task_passed and task_id not in results["escalated"]:
                results["failed"].append(task_id)

    # Dùng tasks gốc (tham số), không bị shadow nữa
    total = sum(len(s["tasks"]) for s in tasks["sprints"])
    print("\n  ========== Sprint Summary ==========")
    print(f"  Passed   : {len(results['passed'])}/{total}")
    print(f"  Failed   : {len(results['failed'])}/{total}")
    print(f"  Escalated: {len(results['escalated'])}/{total}")
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
    from adapter_v2 import AGENT_BACKEND
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
    print(
        "\nJira: "
        "https://gnudevx.atlassian.net/jira/software/projects/PA/boards"
    )
    print("\nDe chuyen sang Claude that:")
    print('  adapter.py -> AGENT_BACKEND = "claude"')


if __name__ == "__main__":
    req = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else \
        "Build me a POS app with product catalog, cart, payment, receipt"
    run_pipeline(req)