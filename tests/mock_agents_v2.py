"""
Mock Agents V2.

Thay doi so voi v1:
  - Dev agent: tao code scaffold that + Git ops that
  - Tester agent: chay pytest/jest that neu co code
  - Requirement agent: sinh data dong tu prompt (khong hardcode)
  - Planner agent: sinh tasks phu hop voi requirement
"""
import json
import os
import datetime
import random
import re

DOCS_DIR = "docs"
BUGS_DIR = os.path.join(DOCS_DIR, "bugs")


# ── Requirement Agent ─────────────────────────────────────

def mock_requirement_agent(prompt):
    """
    Sinh PRD va stories DONG tu prompt.
    Khong hardcode — parse keyword tu prompt de tao dung noi dung.
    """
    os.makedirs(DOCS_DIR, exist_ok=True)

    # Parse keywords tu prompt
    prompt_lower = prompt.lower()
    features = []
    if any(w in prompt_lower for w in ["product", "catalog", "san pham"]):
        features.append("Product catalog management (add, edit, search, barcode)")
    if any(w in prompt_lower for w in ["cart", "gio hang", "basket"]):
        features.append("Shopping cart with quantity control")
    if any(w in prompt_lower for w in ["payment", "thanh toan", "checkout"]):
        features.append("Payment processing (cash and card)")
    if any(w in prompt_lower for w in ["receipt", "hoa don", "invoice"]):
        features.append("Receipt printing and email")
    if any(w in prompt_lower for w in ["inventory", "ton kho", "stock"]):
        features.append("Inventory management and alerts")
    if any(w in prompt_lower for w in ["report", "analytics", "bao cao"]):
        features.append("Sales analytics dashboard")

    # Default neu khong parse duoc gi
    if not features:
        features = [
            "Product catalog management",
            "Shopping cart",
            "Payment processing",
            "Receipt printing"
        ]

    app_name = "POS System"
    for word in ["pos", "point of sale", "shop", "store", "retail"]:
        if word in prompt_lower:
            app_name = "POS System"
            break

    with open(f"{DOCS_DIR}/requirements.md", "w", encoding="utf-8") as f:
        f.write(
            f"# {app_name} — Product Requirements Document\n\n"
            f"**Generated from:** {prompt}\n"
            f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
            "## Problem Statement\n"
            "Small retail shops need a simple, reliable POS system "
            "that works on both web browsers and mobile devices.\n\n"
            "## Features (MVP — Sprint 1)\n"
            + "".join(f"- {f}\n" for f in features[:4])
            + "\n## Features (Advanced — Sprint 2)\n"
            + "".join(f"- {f}\n" for f in features[4:])
            + "\n## Non-Functional Requirements\n"
            "- Performance: page load < 2s, API response < 500ms\n"
            "- Security: JWT auth, HTTPS, input validation\n"
            "- Scalability: 100 concurrent users\n"
            "- Compatibility: Chrome, Firefox, Safari, Mobile\n"
        )

    # Sinh stories tu features
    stories = []
    story_templates = [
        ("cashier", "scan or search for a product",
         "quickly add items to cart without typing",
         ["product lookup < 500ms", "show name + price + stock", "1-tap add to cart"]),
        ("cashier", "process a payment and print receipt",
         "complete the sale efficiently",
         ["support cash and card", "calculate change", "print/email receipt"]),
        ("store manager", "manage the product catalog",
         "keep prices and stock accurate",
         ["add/edit/delete products", "bulk CSV import", "instant update"]),
        ("store manager", "view sales analytics",
         "make data-driven decisions",
         ["daily/weekly/monthly reports", "top products", "revenue chart"]),
        ("cashier", "manage inventory alerts",
         "never run out of stock unexpectedly",
         ["low stock alerts", "reorder suggestions", "stock history"]),
    ]

    priorities = ["P0", "P0", "P1", "P1", "P2"]
    for i, feat in enumerate(features):
        if i < len(story_templates):
            role, action, benefit, acceptance = story_templates[i]
        else:
            role, action = "user", feat.lower()
            benefit, acceptance = "improve workflow", [f"feature works correctly"]

        stories.append({
            "id": f"US-{i+1:02d}",
            "priority": priorities[min(i, len(priorities)-1)],
            "role": role,
            "action": action,
            "benefit": benefit,
            "acceptance": acceptance
        })

    with open(f"{DOCS_DIR}/stories.json", "w", encoding="utf-8") as f:
        json.dump(stories, f, indent=2, ensure_ascii=False)

    print(f"      [mock] requirements.md created ({len(features)} features)")
    print(f"      [mock] stories.json created ({len(stories)} stories)")
    return "REQUIREMENT_DONE"


# ── Planner Agent ─────────────────────────────────────────

def mock_planner_agent(prompt):
    """
    Sinh tasks.json DONG tu stories.json.
    Khong hardcode tasks — doc stories va tao tasks phu hop.
    """
    os.makedirs(DOCS_DIR, exist_ok=True)

    with open(f"{DOCS_DIR}/stories.json", encoding="utf-8") as f:
        stories = json.load(f)

    # Map story -> tasks
    task_map = {
        "scan or search": [
            ("Setup project skeleton (React + FastAPI)",
             "Initialize monorepo, configure ESLint, Prettier, "
             "connect frontend to backend health endpoint",
             2, "fullstack"),
            ("Implement Product model and CRUD API",
             "PostgreSQL Product table, FastAPI CRUD endpoints, "
             "Pydantic schemas, unit tests",
             3, "backend"),
            ("Build product search UI",
             "Search bar, product card grid, add-to-cart button, loading states",
             3, "frontend"),
        ],
        "payment": [
            ("Implement cart and checkout flow",
             "Cart state, quantity controls, total calculation, "
             "payment form, receipt generation",
             5, "fullstack"),
        ],
        "catalog": [
            ("Product catalog management UI",
             "Admin page to add/edit/delete products, "
             "bulk CSV import, image upload",
             5, "fullstack"),
        ],
        "analytics": [
            ("Sales analytics dashboard",
             "Revenue chart, top products, date filter, export CSV",
             5, "frontend"),
        ],
        "inventory": [
            ("Inventory alert system",
             "Low stock detection, alert UI, reorder suggestion API",
             3, "backend"),
        ],
    }

    sprint1_tasks = []
    sprint2_tasks = []
    task_counter = 1

    for story in stories:
        action = story.get("action", "").lower()
        priority = story.get("priority", "P1")

        matched = False
        for keyword, task_defs in task_map.items():
            if keyword in action:
                for summary, desc, pts, comp in task_defs:
                    task = {
                        "id": f"TASK-{task_counter:02d}",
                        "story_ref": story["id"],
                        "summary": summary,
                        "description": desc,
                        "story_points": pts,
                        "priority": priority,
                        "status": "TODO",
                        "component": comp
                    }
                    if priority in ("P0",):
                        sprint1_tasks.append(task)
                    else:
                        sprint2_tasks.append(task)
                    task_counter += 1
                matched = True
                break

        if not matched:
            task = {
                "id": f"TASK-{task_counter:02d}",
                "story_ref": story["id"],
                "summary": f"Implement: {story['action'][:50]}",
                "description": (
                    f"Implementation for story {story['id']}: "
                    f"{story['action']}"
                ),
                "story_points": 3,
                "priority": priority,
                "status": "TODO",
                "component": "fullstack"
            }
            if priority == "P0":
                sprint1_tasks.append(task)
            else:
                sprint2_tasks.append(task)
            task_counter += 1

    tasks = {
        "project": "pos-app",
        "generated_from": "stories.json",
        "generated_at": datetime.datetime.now().isoformat(),
        "sprints": [
            {"number": 1, "name": "MVP", "tasks": sprint1_tasks},
            {"number": 2, "name": "Advanced", "tasks": sprint2_tasks}
        ]
    }

    # Loai sprint trong
    tasks["sprints"] = [s for s in tasks["sprints"] if s["tasks"]]

    with open(f"{DOCS_DIR}/tasks.json", "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    total = sum(len(s["tasks"]) for s in tasks["sprints"])
    pts = sum(t["story_points"] for s in tasks["sprints"] for t in s["tasks"])
    print(f"      [mock] tasks.json created ({total} tasks, {pts} pts)")
    return "PLANNER_DONE"


# ── Dev Agent (goi sang v2 ) ───────────────────

def mock_dev_agent(task_id):
    """Dev agent — su dung v2 voi Git ops that."""
    try:
        from tests.mock_dev_agent_v2 import mock_dev_agent_v2
        return mock_dev_agent_v2(task_id)
    except Exception as e:
        print(f"      [mock-dev] v2 failed ({e}), using fallback")
        return _mock_dev_fallback(task_id)


def _mock_dev_fallback(task_id):
    """Fallback don gian neu Git ops that bai."""
    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        data = json.load(f)

    task = next(
        (t for s in data["sprints"] for t in s["tasks"] if t["id"] == task_id),
        None
    )
    if not task:
        return f"DEV_ESCALATE:{task_id}"

    for s in data["sprints"]:
        for t in s["tasks"]:
            if t["id"] == task_id:
                t["status"] = "DONE"

    with open(f"{DOCS_DIR}/tasks.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"      [mock-dev] fallback: {task_id} marked DONE")
    return f"DEV_DONE:{task_id}"


# ── Tester Agent (goi sang v2) ────────────────
def mock_tester_agent(task_id):
    """Tester agent — su dung v2 voi real test neu co code."""
    try:
        from tests.mock_tester_agent_v2 import mock_tester_agent_v2
        return mock_tester_agent_v2(task_id)
    except Exception as e:
        print(f"      [mock-test] v2 failed ({e}), using fallback")
        return _mock_tester_fallback(task_id)


def _mock_tester_fallback(task_id):
    """Fallback: simulated test result."""
    os.makedirs(BUGS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    passed = random.random() > 0.15

    with open(f"{DOCS_DIR}/test-results.md", "w", encoding="utf-8") as f:
        f.write(
            f"# Test Results — {task_id}\n"
            f"Date: {datetime.datetime.now().isoformat()}\n"
            f"Mode: SIMULATED\n\n"
            f"## Result: {'PASS' if passed else 'FAIL'}\n"
        )

    if passed:
        print(f"      [mock-test] fallback PASSED ({task_id})")
        return f"TEST_PASS:{task_id}"

    with open(f"{BUGS_DIR}/BUG-{task_id}-{ts}.md", "w", encoding="utf-8") as f:
        f.write(
            f"## Bug: {task_id}\n"
            "**Failure type:** PERMANENT\n"
            "### Action\nRETRY\n"
        )
    print(f"      [mock-test] fallback FAILED ({task_id})")
    return f"TEST_FAIL:{task_id}:1"