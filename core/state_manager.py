"""
Pipeline execution state manager

Lưu trạng thái task runtime:
- pending
- in_progress
- passed
- failed
- blocked
- escalated

File:
docs/execution_state.json
"""

import json
import os
import datetime

STATE_PATH = "docs/execution_state.json"


def _default():
    return {
        "updated_at": "",
        "tasks": {}
    }


def load_state():
    if not os.path.exists(STATE_PATH):
        return _default()

    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs("docs", exist_ok=True)

    state["updated_at"] = (
        datetime.datetime.now()
        .strftime("%Y-%m-%d %H:%M:%S")
    )

    with open(
        STATE_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False
        )


def set_task_state(
    task_id,
    status,
    message=""
):
    state = load_state()

    state["tasks"][task_id] = {
        "status": status,
        "message": message,
        "updated_at":
        datetime.datetime.now()
        .strftime("%Y-%m-%d %H:%M:%S")
    }

    save_state(state)


def get_task_state(task_id):

    state = load_state()

    return (
        state["tasks"]
        .get(task_id, {})
        .get("status")
    )


def can_execute(task):

    deps = task.get(
        "depends_on",
        []
    )

    state = load_state()

    for dep in deps:

        dep_status = (
            state["tasks"]
            .get(dep, {})
            .get("status")
        )

        if dep_status != "passed":
            return False

    return True


def print_state_summary():

    state = load_state()

    tasks = state["tasks"]

    passed = 0
    failed = 0
    blocked = 0
    running = 0

    for v in tasks.values():

        s = v["status"]

        if s == "passed":
            passed += 1
        elif s == "failed":
            failed += 1
        elif s == "blocked":
            blocked += 1
        elif s == "in_progress":
            running += 1

    print("\n[STATE]")
    print(
        f" passed={passed}"
        f" failed={failed}"
        f" blocked={blocked}"
        f" running={running}"
    )