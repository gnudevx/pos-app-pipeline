"""
Jira Sync — toàn bộ Jira REST API helpers.

Tách từ orchestrator_v2 (tất cả hàm jira_* và _jira_cfg / _headers).
"""

from __future__ import annotations

import json
import base64
import datetime
import time
import urllib.request
import urllib.error


# ── Config / Auth ─────────────────────────────────────────────────────────────

def _jira_cfg() -> tuple[str, str, str] | None:
    """Đọc Jira credentials từ .mcp.json. Trả None nếu thiếu config."""
    try:
        with open(".mcp.json", encoding="utf-8") as f:
            cfg = json.load(f)["mcpServers"]["jira"]["env"]
        return cfg["ATLASSIAN_URL"], cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_TOKEN"]
    except Exception:
        return None


def _headers(email: str, token: str) -> dict:
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ── Project ───────────────────────────────────────────────────────────────────

def jira_get_project_key() -> str | None:
    cfg = _jira_cfg()
    if cfg is None:
        return None
    url, email, token = cfg
    req = urllib.request.Request(
        f"{url}/rest/api/3/project",
        headers=_headers(email, token),
    )
    with urllib.request.urlopen(req) as r:
        projects = json.loads(r.read())
    pos = next((p for p in projects if p["key"] == "PA"), projects[0])
    return pos["key"]


# ── Sprint ────────────────────────────────────────────────────────────────────

def jira_create_sprint(project_key: str, sprint_name: str, sprint_number: int) -> int | None:
    """Tạo Sprint mới trên Jira board."""
    cfg = _jira_cfg()
    if cfg is None:
        return None
    url, email, token = cfg

    req = urllib.request.Request(
        f"{url}/rest/agile/1.0/board?projectKeyOrId={project_key}",
        headers=_headers(email, token),
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
            "goal": f"Complete {sprint_name} features for POS app",
        }).encode()
        req2 = urllib.request.Request(
            f"{url}/rest/agile/1.0/sprint",
            data=payload,
            headers=_headers(email, token),
            method="POST",
        )
        with urllib.request.urlopen(req2) as r:
            sprint = json.loads(r.read())
        return sprint["id"]
    except Exception as e:
        print(f"      Sprint creation skipped: {e}")
        return None


def jira_add_to_sprint(sprint_id: int, issue_key: str) -> None:
    cfg = _jira_cfg()
    if cfg is None or not sprint_id:
        return
    url, email, token = cfg
    payload = json.dumps({"issues": [issue_key]}).encode()
    req = urllib.request.Request(
        f"{url}/rest/agile/1.0/sprint/{sprint_id}/issue",
        data=payload,
        headers=_headers(email, token),
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


# ── Ticket ────────────────────────────────────────────────────────────────────

def jira_create_ticket(project_key: str, task: dict) -> str | None:
    cfg = _jira_cfg()
    if cfg is None:
        return None
    url, email, token = cfg
    payload = json.dumps({
        "fields": {
            "project": {"key": project_key},
            "summary": f"[{task['id']}] {task['summary']}",
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": task["description"]}
                ]}],
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
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["key"]


# ── Status / Comments ─────────────────────────────────────────────────────────

def jira_update_status(jira_key: str, transition_name: str) -> None:
    cfg = _jira_cfg()
    if cfg is None or not jira_key or jira_key == "N/A":
        return
    url, email, token = cfg
    req = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/transitions",
        headers=_headers(email, token),
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    transition = next(
        (t for t in data["transitions"]
         if transition_name.lower() in t["name"].lower()),
        None,
    )
    if not transition:
        return
    payload = json.dumps({"transition": {"id": transition["id"]}}).encode()
    req2 = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/transitions",
        data=payload,
        headers=_headers(email, token),
        method="POST",
    )
    urllib.request.urlopen(req2)
    print(f"      Jira {jira_key} -> {transition_name}")


def jira_add_pr_link(jira_key: str, branch_name: str) -> None:
    cfg = _jira_cfg()
    if cfg is None or not jira_key or jira_key == "N/A":
        return
    url, email, token = cfg
    payload = json.dumps({
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": (
                    f"Branch: {branch_name}\n"
                    f"PR: feature -> main (pending review)\n"
                    f"Updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )}
            ]}],
        }
    }).encode()
    req = urllib.request.Request(
        f"{url}/rest/api/3/issue/{jira_key}/comment",
        data=payload,
        headers=_headers(email, token),
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        print(f"      Jira {jira_key} <- branch comment added")
    except Exception:
        pass


# ── Phase 2 bulk sync ─────────────────────────────────────────────────────────

def sync_tasks_to_jira(tasks: dict) -> dict:
    """
    Phase 2: tạo toàn bộ Sprints + Tickets từ tasks.json.

    Returns ticket_map: {task_id -> jira_key}
    """
    ticket_map: dict = {}
    sprint_map: dict = {}

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