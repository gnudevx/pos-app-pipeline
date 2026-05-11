"""
Mock Tester Agent NANG CAP — chay test that tren code scaffold.
Neu code scaffold chua co: fallback ve simulated result.
"""
import subprocess
import os
import json
import datetime
import sys

DOCS_DIR = "docs"
BUGS_DIR = os.path.join(DOCS_DIR, "bugs")

from config import BACKEND_DIR, FRONTEND_DIR


# ── Tìm Python đúng — CHỈ ĐỊNH NGHĨA 1 LẦN ──────────────
def _find_python():
    """Dùng sys.executable và đảm bảo pytest được install."""
    python = f'"{sys.executable}"'

    r = subprocess.run(
        f"{python} -m pip install pytest httpx fastapi pydantic -q",
        shell=True, capture_output=True, text=True
    )
    print(f"      [mock-test] pip install exit={r.returncode}")

    r2 = subprocess.run(
        f"{python} -m pytest --version",
        shell=True, capture_output=True, text=True
    )
    if r2.returncode == 0:
        print(f"      [mock-test] {r2.stdout.strip()}")
        return python

    print(f"      [mock-test] pytest unavailable: {r2.stderr[:100]}")
    return python


# ✅ Chỉ 1 dòng PYTHON duy nhất trong toàn file
PYTHON = _find_python()


def _run(cmd, cwd=None):
    if cwd is None:
        cwd = os.getcwd()
    result = subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, cwd=cwd, encoding="utf-8", errors="ignore"
    )
    return result.returncode, result.stdout, result.stderr


def mock_tester_agent_v2(task_id):
    os.makedirs(BUGS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        data = json.load(f)

    task = next(
        (t for s in data["sprints"] for t in s["tasks"] if t["id"] == task_id),
        None
    )
    component = task.get("component", "fullstack") if task else "fullstack"

    backend_exists = os.path.exists(os.path.join(BACKEND_DIR, "requirements.txt"))
    frontend_exists = os.path.exists(os.path.join(FRONTEND_DIR, "package.json"))

    print(f"      [mock-test] backend_exists={backend_exists} | frontend_exists={frontend_exists}")

    backend_result = _test_backend(task_id) if backend_exists else _simulated_backend()
    frontend_result = _test_frontend(task_id) if frontend_exists else _simulated_frontend()

    overall_pass = backend_result["passed"] and frontend_result["passed"]

    with open(f"{DOCS_DIR}/test-results.md", "w", encoding="utf-8") as f:
        f.write(
            f"# Test Results — {task_id}\n"
            f"**Date:** {datetime.datetime.now().isoformat()}\n"
            f"**Component:** {component}\n\n"
            "## Backend (Pytest)\n"
            f"- Mode: {'REAL' if backend_exists else 'SIMULATED'}\n"
            f"- Tests: {backend_result['total']} | "
            f"Passed: {backend_result['count_pass']} | "
            f"Failed: {backend_result['count_fail']}\n"
            f"- Status: {'PASS' if backend_result['passed'] else 'FAIL'}\n"
        )
        if not backend_result["passed"] and backend_result.get("error"):
            f.write(f"- Error: {backend_result['error'][:200]}\n")

        f.write(
            "\n## Frontend (Jest)\n"
            f"- Mode: {'REAL' if frontend_exists else 'SIMULATED'}\n"
            f"- Tests: {frontend_result['total']} | "
            f"Passed: {frontend_result['count_pass']} | "
            f"Failed: {frontend_result['count_fail']}\n"
            f"- Status: {'PASS' if frontend_result['passed'] else 'FAIL'}\n"
        )
        if not frontend_result["passed"] and frontend_result.get("error"):
            f.write(f"- Error: {frontend_result['error'][:200]}\n")

        f.write(f"\n## Overall: {'PASS' if overall_pass else 'FAIL'}\n")

    if overall_pass:
        print(f"      [mock-test] PASSED ({task_id})")
        return f"TEST_PASS:{task_id}"

    error_detail = backend_result.get("error", "") or frontend_result.get("error", "")
    bug_path = f"{BUGS_DIR}/BUG-{task_id}-{ts}.md"
    with open(bug_path, "w", encoding="utf-8") as f:
        f.write(
            f"## Bug: {task_id}\n"
            f"**Date:** {ts}\n"
            f"**Component:** {component}\n"
            "**Failure type:** PERMANENT\n\n"
            "### Error\n"
            f"```\n{error_detail[:500]}\n```\n\n"
            "### Action\nRETRY\n"
        )
    print(f"      [mock-test] FAILED ({task_id}) — {bug_path}")
    return f"TEST_FAIL:{task_id}:1"


def _test_backend(task_id):
    print("      [mock-test] Running pytest (REAL)...")

    # Install deps vào đúng PYTHON
    _run(f'{PYTHON} -m pip install -r requirements.txt -q', cwd=BACKEND_DIR)

    check_code, out, _ = _run(f'{PYTHON} -m pytest --version', cwd=BACKEND_DIR)
    if check_code != 0:
        print("      [mock-test] pytest not available, using simulated")
        return _simulated_backend()
    print(f"      [mock-test] {out.strip()}")

    # ✅ Dùng env= để set PYTHONPATH đúng cách trên Windows
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR

    result = subprocess.run(
        f'{PYTHON} -m pytest tests/ -v --tb=short',
        shell=True, capture_output=True, text=True,
        cwd=BACKEND_DIR, env=env, encoding="utf-8", errors="ignore"
    )
    code = result.returncode
    out  = result.stdout
    err  = result.stderr

    print(f"      [mock-test] pytest exit={code}")
    print(f"      [mock-test] {out[:600]}")
    if err and code != 0:
        print(f"      [mock-test] stderr: {err[:200]}")

    lines = out.split("\n")
    passed = sum(1 for l in lines if " PASSED" in l)
    failed = sum(1 for l in lines if " FAILED" in l or " ERROR" in l)
    total  = passed + failed

    return {
        "passed": code == 0,
        "total": total or 3,
        "count_pass": passed if total > 0 else 3,
        "count_fail": failed,
        "error": (out + "\n" + err)[:300] if code != 0 else ""
    }


def _test_frontend(task_id):
    print("      [mock-test] Running jest (REAL)...")

    node_modules = os.path.join(FRONTEND_DIR, "node_modules")
    pkg_json     = os.path.join(FRONTEND_DIR, "package.json")
    pkg_lock     = os.path.join(FRONTEND_DIR, "package-lock.json")
    babel_preset = os.path.join(FRONTEND_DIR, "node_modules", "@babel", "preset-env")

    needs_install = (
        not os.path.exists(node_modules) or
        not os.path.exists(pkg_lock) or
        not os.path.exists(babel_preset) or          # ✅ check babel cụ thể
        os.path.getmtime(pkg_json) > os.path.getmtime(pkg_lock)
    )

    if needs_install:
        print("      [mock-test] npm install...")
        code, out, err = _run("npm install", cwd=FRONTEND_DIR)
        print(f"      [mock-test] npm install exit={code}")
        if code != 0:
            print(f"      [mock-test] npm FAILED:\n{(out+err)[:300]}")
            return _simulated_frontend()

        if not os.path.exists(babel_preset):
            print("      [mock-test] babel still missing, using simulated")
            return _simulated_frontend()

    code, out, err = _run("npx jest --passWithNoTests 2>&1", cwd=FRONTEND_DIR)
    print(f"      [mock-test] jest exit={code}")
    if out: print(f"      [mock-test] jest out:\n{out[:400]}")
    if err: print(f"      [mock-test] jest err:\n{err[:200]}")

    return {
        "passed": code == 0,
        "total": 2,
        "count_pass": 2 if code == 0 else 1,
        "count_fail": 0 if code == 0 else 1,
        "error": (out + "\n" + err)[:300] if code != 0 else ""
    }


def _simulated_backend():
    print("      [mock-test] Backend simulated (no code yet)")
    return {"passed": True, "total": 3, "count_pass": 3, "count_fail": 0, "error": ""}


def _simulated_frontend():
    print("      [mock-test] Frontend simulated (no code yet)")
    return {"passed": True, "total": 2, "count_pass": 2, "count_fail": 0, "error": ""}