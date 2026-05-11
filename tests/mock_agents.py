"""
Mock Agents — thay the Claude API khi chua co API key.
Moi ham tra ve output dung format nhu agent that se tra ve.

Khi co API key: chi can sua adapter.py, khong sua file nay.
"""
import json
import os
import datetime
import random

DOCS_DIR = "docs"
BUGS_DIR = os.path.join(DOCS_DIR, "bugs")


def mock_requirement_agent(prompt):
    """Mock requirement agent: tao requirements.md va stories.json."""
    os.makedirs(DOCS_DIR, exist_ok=True)

    with open(f"{DOCS_DIR}/requirements.md", "w", encoding="utf-8") as f:
        f.write(
            "## Problem statement\n"
            "Small retail shops need a simple, reliable POS system\n"
            "that works on both web browsers and mobile devices.\n\n"
            "## Features (MVP)\n"
            "- Product catalog management (add, edit, search)\n"
            "- Shopping cart with quantity control\n"
            "- Payment processing (cash and card)\n"
            "- Receipt printing and email\n\n"
            "## Features (Phase 2)\n"
            "- Sales analytics dashboard\n"
            "- Inventory alerts\n"
            "- Multi-location support\n\n"
            "## Non-functional requirements\n"
            "- Performance: page load < 2s, API response < 500ms\n"
            "- Security: JWT auth, HTTPS only\n"
            "- Scalability: support 100 concurrent users\n"
        )

    stories = [
        {
            "id": "US-01",
            "priority": "P0",
            "role": "cashier",
            "action": "scan or search for a product",
            "benefit": "quickly add items to cart",
            "acceptance": [
                "product lookup under 500ms",
                "shows name, price, stock level",
                "add to cart with one tap"
            ]
        },
        {
            "id": "US-02",
            "priority": "P0",
            "role": "cashier",
            "action": "process a payment",
            "benefit": "complete the sale and print receipt",
            "acceptance": [
                "supports cash and card",
                "calculates change for cash",
                "receipt printed or emailed"
            ]
        },
        {
            "id": "US-03",
            "priority": "P1",
            "role": "store manager",
            "action": "manage the product catalog",
            "benefit": "keep prices and stock accurate",
            "acceptance": [
                "add/edit/delete products",
                "bulk import via CSV",
                "changes reflected immediately"
            ]
        }
    ]
    with open(f"{DOCS_DIR}/stories.json", "w", encoding="utf-8") as f:
        json.dump(stories, f, indent=2, ensure_ascii=False)

    print("      [mock] requirements.md created")
    print(f"      [mock] stories.json created ({len(stories)} stories)")
    return "REQUIREMENT_DONE"


def mock_planner_agent(prompt):
    """Mock planner agent: tao tasks.json tu stories."""
    os.makedirs(DOCS_DIR, exist_ok=True)

    tasks = {
        "project": "pos-app",
        "sprints": [
            {
                "number": 1,
                "name": "MVP",
                "tasks": [
                    {
                        "id": "TASK-01",
                        "story_ref": "US-01",
                        "summary": "Setup project skeleton (React + FastAPI)",
                        "description": (
                            "Initialize monorepo, install dependencies, "
                            "configure ESLint + Prettier, "
                            "connect frontend to backend health endpoint"
                        ),
                        "story_points": 2,
                        "priority": "P0",
                        "status": "TODO",
                        "component": "fullstack"
                    },
                    {
                        "id": "TASK-02",
                        "story_ref": "US-01",
                        "summary": "Implement Product model and CRUD API",
                        "description": (
                            "PostgreSQL Product table, FastAPI CRUD endpoints, "
                            "Pydantic schemas, unit tests"
                        ),
                        "story_points": 3,
                        "priority": "P0",
                        "status": "TODO",
                        "component": "backend"
                    },
                    {
                        "id": "TASK-03",
                        "story_ref": "US-01",
                        "summary": "Build product search UI",
                        "description": (
                            "Search bar component, product card grid, "
                            "add-to-cart button, loading states"
                        ),
                        "story_points": 3,
                        "priority": "P0",
                        "status": "TODO",
                        "component": "frontend"
                    },
                    {
                        "id": "TASK-04",
                        "story_ref": "US-02",
                        "summary": "Implement cart and checkout flow",
                        "description": (
                            "Cart state management, quantity controls, "
                            "total calculation, payment form, receipt generation"
                        ),
                        "story_points": 5,
                        "priority": "P0",
                        "status": "TODO",
                        "component": "fullstack"
                    }
                ]
            },
            {
                "number": 2,
                "name": "Advanced",
                "tasks": [
                    {
                        "id": "TASK-05",
                        "story_ref": "US-03",
                        "summary": "Product catalog management UI",
                        "description": (
                            "Admin page to add/edit/delete products, "
                            "bulk CSV import, image upload"
                        ),
                        "story_points": 5,
                        "priority": "P1",
                        "status": "TODO",
                        "component": "fullstack"
                    }
                ]
            }
        ]
    }

    with open(f"{DOCS_DIR}/tasks.json", "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    total_tasks = sum(len(s["tasks"]) for s in tasks["sprints"])
    total_points = sum(
        t["story_points"] for s in tasks["sprints"] for t in s["tasks"]
    )
    print(f"      [mock] tasks.json created ({total_tasks} tasks, {total_points} pts)")
    return "PLANNER_DONE"


def mock_dev_agent(task_id):
    """Mock dev agent: gia vo implement code va tao PR."""
    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        data = json.load(f)

    task = next(
        (t for s in data["sprints"] for t in s["tasks"] if t["id"] == task_id),
        None
    )
    if not task:
        print(f"      [mock] Task {task_id} not found")
        return f"DEV_ESCALATE:{task_id}"

    slug = task["summary"][:20].lower().replace(" ", "-")
    branch = f"feature/{task_id}-{slug}"

    for s in data["sprints"]:
        for t in s["tasks"]:
            if t["id"] == task_id:
                t["status"] = "DONE"
                t["branch"] = branch

    with open(f"{DOCS_DIR}/tasks.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"      [mock] Code implemented for {task_id}")
    print(f"      [mock] Branch: {branch}")
    print("      [mock] PR created (simulated)")
    return f"DEV_DONE:{task_id}"


def mock_tester_agent(task_id):
    """Mock tester agent: gia vo chay test va tra ve ket qua."""
    os.makedirs(BUGS_DIR, exist_ok=True)

    passed = random.random() > 0.2
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    result_line = "PASS" if passed else "FAIL"
    with open(f"{DOCS_DIR}/test-results.md", "w", encoding="utf-8") as f:
        f.write(
            f"# Test Results — {task_id}\n"
            f"Date: {datetime.datetime.now().isoformat()}\n\n"
            "## Frontend (Jest)\n"
            f"Tests: 12 | Passed: {'12' if passed else '10'} "
            f"| Failed: {'0' if passed else '2'}\n\n"
            "## Backend (Pytest)\n"
            "Tests: 8 | Passed: 8 | Failed: 0\n\n"
            f"## Result: {result_line}\n"
        )

    if passed:
        print("      [mock] Tests PASSED")
        return f"TEST_PASS:{task_id}"

    bug_path = f"{BUGS_DIR}/BUG-{task_id}-{ts}.md"
    with open(bug_path, "w", encoding="utf-8") as f:
        f.write(
            f"## Bug: {task_id}\n"
            f"**Date:** {ts}\n"
            "**Failure type:** PERMANENT\n\n"
            "### Failed tests\n"
            "| File | Test | Error |\n"
            "|------|------|-------|\n"
            "| Cart.test.tsx | should calculate total |"
            " TypeError: Cannot read 'price' |\n\n"
            "### Suggested fix\n"
            "Check for null product before accessing product.price.\n\n"
            "### Action\nRETRY\n"
        )
    print(f"      [mock] Tests FAILED — bug report: BUG-{task_id}-{ts}.md")
    return f"TEST_FAIL:{task_id}:2"
