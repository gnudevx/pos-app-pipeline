"""
integration_pipeline.py — Phase 4 Orchestrator (fixed)

Pipeline:
    4.1 Merge Coordinator      — topo sort + merge feature branches
    4.2 Contract Validation    — route graph consistency check
    4.3 Build Verification     — backend pip + frontend npm/tsc/build
    4.4 Runtime Validation     — docker compose up + health checks + smoke tests
    4.5 Self-Healing Loop      — classify failure → Gemini patch → retry
    4.6 Release Snapshot       — save integration_manifest.json

Self-Healing Strategy:
    - Chỉ trigger khi phase 4.3 hoặc 4.4 fail
    - FailureType.TRANSIENT  → retry phase đó (max TRANSIENT_RETRY_LIMIT lần)
    - FailureType.STRUCTURAL → gọi Gemini sinh patch → push → return HEALED
    - FailureType.SEMANTIC   → escalate, fail pipeline (human review required)
    - FailureType.UNKNOWN    → treat as SEMANTIC (safe default)

GitHub Actions:
    python scripts/phase4/integration_pipeline.py

Fixes vs original:
    - [BUG1] Xóa import thừa trong while loop (build healing section)
    - [BUG2] Phase 4.6 Release Snapshot được reach đúng — restructure flow
              dùng _HealResult enum thay vì bool để caller không cần đọc manifest
    - [BUG3] _attempt_healing trả về _HealResult (HEALED/TRANSIENT/FAILED)
              thay vì bool — loại bỏ fragile manifest re-read pattern
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import sys
import traceback
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────────────────────

from ci.merge.merge_coordinator       import run_merge_coordinator
from ci.runtime.validate_contracts    import run_contract_validation
from ci.runtime.build_verifier        import run_build_verification
from ci.repair.self_healing           import (
    run_self_healing_loop,
    HealingReport,
    FailureType,
)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

TASKS_JSON             = "docs/tasks.json"
REPO_DIR               = "../pos-app-test_v2"
COMPOSE_FILE           = "docker-compose.yml"
HEALTH_CHECK_URL       = "http://localhost:8000/health"
HEALTH_CHECK_RETRIES   = 10
HEALTH_CHECK_SLEEP_S   = 3
TRANSIENT_RETRY_LIMIT  = 2     # số lần retry phase khi gặp TRANSIENT failure


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineState:
    phase: str
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrationManifest:
    integration_run_id: str
    passed_task_ids: list[str]
    merge_order: list[str] = field(default_factory=list)
    phases: list[PipelineState] = field(default_factory=list)

    def add_phase(
        self,
        phase: str,
        ok: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.phases.append(PipelineState(
            phase=phase, ok=ok, details=details or {},
        ))

    def save(self, path: str = "docs/integration_manifest.json") -> None:
        data = {
            "integration_run_id": self.integration_run_id,
            "passed_task_ids":    self.passed_task_ids,
            "merge_order":        self.merge_order,
            "phases": [
                {"phase": p.phase, "ok": p.ok, "details": p.details}
                for p in self.phases
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# [FIX-BUG3] HealResult enum — thay thế bool trả về từ _attempt_healing
#   bool(True) không phân biệt HEALED vs TRANSIENT_RETRY được
# ─────────────────────────────────────────────────────────────────────────────

class _HealResult(Enum):
    HEALED          = auto()   # patch đã push, CI sẽ re-trigger — kết thúc run
    TRANSIENT_RETRY = auto()   # lỗi tạm thời, caller nên retry phase
    FAILED          = auto()   # escalate / max retries — abort pipeline


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_passed_tasks(tasks_json: str = TASKS_JSON) -> list[str]:
    path = Path(tasks_json)
    if not path.exists():
        raise FileNotFoundError(f"tasks.json not found: {tasks_json}")
    data = json.loads(path.read_text(encoding="utf-8"))
    passed: list[str] = []
    for sprint in data.get("sprints", []):
        for task in sprint.get("tasks", []):
            if task.get("status") == "PASSED":
                task_id = task.get("id")
                if isinstance(task_id, str):
                    passed.append(task_id)
    return passed


def _collect_error_log_from_build(build_report: Any) -> str:
    """Gom tất cả stderr từ failed build targets thành một error log."""
    lines: list[str] = []
    for target in build_report.targets:
        if not target.ok:
            lines.append(f"=== {target.name} ===")
            if target.stderr:
                lines.append(target.stderr)
            if target.stdout:
                lines.append(target.stdout)
    return "\n".join(lines)


def _collect_error_log_from_contract(contract_report: Any) -> str:
    lines: list[str] = ["=== Contract Validation Errors ==="]
    lines.extend(contract_report.errors)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4.4: Runtime Validation (Docker + Health Check + Smoke Tests)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuntimeReport:
    ok: bool = True
    error_log: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _run_cmd(cmd: str, cwd: str = ".", timeout: int = 60) -> tuple[bool, str, str]:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Timeout after {timeout}s: {cmd}"
    except Exception as e:
        return False, "", str(e)


def run_runtime_validation(
    repo_dir: str = REPO_DIR,
    compose_file: str = COMPOSE_FILE,
    health_url: str = HEALTH_CHECK_URL,
) -> RuntimeReport:
    print("\n── 4.4 Runtime Validation ─────────────────────────────")
    report = RuntimeReport()
    error_lines: list[str] = []

    # ── docker compose up ──────────────────────────────────────────────────
    print("  [runtime] docker compose up -d ...")
    ok, stdout, stderr = _run_cmd(
        f"docker compose -f {compose_file} up -d --build",
        cwd=repo_dir,
        timeout=120,
    )
    if not ok:
        error_lines.append("=== docker compose up failed ===")
        error_lines.append(stderr)
        report.ok = False
        report.error_log = "\n".join(error_lines)
        report.details["docker"] = {"ok": False, "stderr": stderr[:500]}
        return report

    report.details["docker"] = {"ok": True}

    # ── Health check với retry ─────────────────────────────────────────────
    print(f"  [runtime] Health check: {health_url}")
    health_ok = False
    for i in range(1, HEALTH_CHECK_RETRIES + 1):
        time.sleep(HEALTH_CHECK_SLEEP_S)
        ok, stdout, stderr = _run_cmd(
            f'curl -sf {health_url} -o /dev/null -w "%{{http_code}}"',
            timeout=10,
        )
        if ok and "200" in stdout:
            print(f"  [runtime] ✓ Health check passed (attempt {i})")
            health_ok = True
            break
        print(f"  [runtime] Waiting... ({i}/{HEALTH_CHECK_RETRIES}) — {stdout or stderr}")

    if not health_ok:
        error_lines.append(f"=== Health check failed after {HEALTH_CHECK_RETRIES} attempts ===")
        error_lines.append(f"URL: {health_url}")
        error_lines.append(f"Last response: {stdout or stderr}")
        _, dlogs, _ = _run_cmd(
            f"docker compose -f {compose_file} logs --tail=50",
            cwd=repo_dir,
        )
        error_lines.append("=== docker logs ===")
        error_lines.append(dlogs)
        report.ok = False
        report.error_log = "\n".join(error_lines)
        report.details["health_check"] = {"ok": False, "url": health_url}
        return report

    report.details["health_check"] = {"ok": True, "url": health_url}

    # ── Smoke tests (GET /products, GET /categories, ...) ─────────────────
    smoke_routes = ["/products", "/categories"]
    smoke_results: dict[str, bool] = {}
    base_url = health_url.rsplit("/health", 1)[0]

    for route in smoke_routes:
        ok, stdout, _ = _run_cmd(
            f'curl -sf {base_url}{route} -o /dev/null -w "%{{http_code}}"',
            timeout=10,
        )
        passed = ok and stdout.startswith("2")
        smoke_results[route] = passed
        icon = "✓" if passed else "✗"
        print(f"  [runtime] [{icon}] smoke {route} → {stdout}")

    failed_smokes = [r for r, ok in smoke_results.items() if not ok]
    if failed_smokes:
        error_lines.append(f"=== Smoke test failed: {failed_smokes} ===")
        report.ok = False
        report.error_log = "\n".join(error_lines)
        report.details["smoke_tests"] = {"ok": False, "failed": failed_smokes}
        return report

    report.details["smoke_tests"] = {"ok": True, "routes": smoke_routes}
    print("  [runtime] ✓ Runtime validation passed")
    return report


def _docker_down(repo_dir: str, compose_file: str) -> None:
    _run_cmd(f"docker compose -f {compose_file} down -v", cwd=repo_dir, timeout=30)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4.5 — Self-Healing wrapper
# [FIX-BUG3] Trả về _HealResult thay vì bool
#   Caller không cần re-read manifest để biết HEALED vs TRANSIENT
# ─────────────────────────────────────────────────────────────────────────────

def _attempt_healing(
    task_id: str,
    error_log: str,
    phase: str,
    manifest: IntegrationManifest,
) -> _HealResult:
    """
    Gọi self-healing loop cho một task.

    Returns:
        _HealResult.HEALED          — patch pushed, CI sẽ re-trigger
        _HealResult.TRANSIENT_RETRY — transient lỗi, caller nên retry phase
        _HealResult.FAILED          — escalate / không thể tự fix
    """
    healing = run_self_healing_loop(
        task_id=task_id,
        error_log=error_log,
        phase=phase,
        repo_dir= REPO_DIR,
    )

    manifest.add_phase(
        phase=f"self_healing_{phase}_{task_id}",
        ok=healing.ok,
        details={
            "failure_type":    healing.failure_type.value,
            "final_status":    healing.final_status,
            "attempts":        len(healing.attempts),
            "escalate_reason": healing.escalate_reason,
        },
    )

    if healing.final_status == "TRANSIENT_RETRY":
        print(f"  [pipeline] Transient failure for {task_id} — will retry phase")
        return _HealResult.TRANSIENT_RETRY

    if healing.final_status == "HEALED":
        print(f"  [pipeline] ✓ {task_id} healed — CI will re-trigger on push")
        return _HealResult.HEALED

    # ESCALATED / MAX_RETRIES / NO_API_KEY
    print(f"  [pipeline] ✗ {task_id} healing failed: {healing.final_status}")
    if healing.escalate_reason:
        print(f"  [pipeline]   escalate_reason: {healing.escalate_reason}")
    return _HealResult.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# [FIX-BUG2] _run_phase_with_healing — helper tách logic retry ra khỏi main
#   Tránh dead code sau while loop, phase 4.6 luôn được reach
# ─────────────────────────────────────────────────────────────────────────────

def _run_build_with_healing(
    passed_task_ids: list[str],
    manifest: IntegrationManifest,
) -> tuple[bool, bool]:
    """
    Chạy 4.3 Build Verification + 4.5 Self-Healing loop.

    Returns:
        (pipeline_ok, healed_and_pushed)
        - (True,  False) → build passed bình thường, tiếp tục pipeline
        - (False, True)  → healed + pushed → kết thúc run (CI re-trigger)
        - (False, False) → failed, abort pipeline
    """
    transient_retries = 0

    while True:
        try:
            build_report = run_build_verification(REPO_DIR)
            manifest.add_phase(
                phase="build_verification",
                ok=build_report.ok,
                details={
                    "targets": [
                        {"name": t.name, "ok": t.ok, "duration_s": t.duration_s}
                        for t in build_report.targets
                    ]
                },
            )
        except Exception as e:
            traceback.print_exc()
            manifest.add_phase("build_verification", ok=False, details={"error": str(e)})
            return False, False

        if build_report.ok:
            return True, False  # build passed, tiếp tục

        # Build failed → self-healing
        error_log = _collect_error_log_from_build(build_report)
        task_id = passed_task_ids[0] if passed_task_ids else "UNKNOWN"
        result  = _attempt_healing(task_id, error_log, "build_verification", manifest)

        if result == _HealResult.FAILED:
            return False, False

        if result == _HealResult.HEALED:
            return False, True  # healed, CI re-trigger — kết thúc run này

        # TRANSIENT_RETRY
        transient_retries += 1
        if transient_retries > TRANSIENT_RETRY_LIMIT:
            print(f"  [pipeline] Build transient retry limit reached ({TRANSIENT_RETRY_LIMIT})")
            return False, False
        print(f"  [pipeline] Build transient retry {transient_retries}/{TRANSIENT_RETRY_LIMIT}...")
        time.sleep(5)


def _run_runtime_with_healing(
    passed_task_ids: list[str],
    manifest: IntegrationManifest,
) -> tuple[bool, bool]:
    """
    Chạy 4.4 Runtime Validation + 4.5 Self-Healing loop.

    Returns: same convention as _run_build_with_healing
    """
    transient_retries = 0

    while True:
        runtime_report = run_runtime_validation(
            repo_dir=REPO_DIR,
            compose_file=COMPOSE_FILE,
            health_url=HEALTH_CHECK_URL,
        )
        manifest.add_phase(
            phase="runtime_validation",
            ok=runtime_report.ok,
            details=runtime_report.details,
        )

        if runtime_report.ok:
            _docker_down(REPO_DIR, COMPOSE_FILE)
            return True, False  # passed, tiếp tục

        # Runtime failed → teardown trước khi heal
        _docker_down(REPO_DIR, COMPOSE_FILE)

        task_id = passed_task_ids[0] if passed_task_ids else "UNKNOWN"
        result  = _attempt_healing(task_id, runtime_report.error_log, "runtime_validation", manifest)

        if result == _HealResult.FAILED:
            return False, False

        if result == _HealResult.HEALED:
            return False, True

        # TRANSIENT_RETRY
        transient_retries += 1
        if transient_retries > TRANSIENT_RETRY_LIMIT:
            print(f"  [pipeline] Runtime transient retry limit reached ({TRANSIENT_RETRY_LIMIT})")
            return False, False
        print(f"  [pipeline] Runtime transient retry {transient_retries}/{TRANSIENT_RETRY_LIMIT}...")
        time.sleep(10)


# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_phase4_pipeline() -> int:
    print("\n============================================================")
    print("PHASE 4 — Integration Validation Pipeline")
    print("============================================================")

    passed_task_ids = load_passed_tasks()
    manifest = IntegrationManifest(
        integration_run_id=os.environ.get("GITHUB_RUN_ID", "integration-run-local"),
        passed_task_ids=passed_task_ids,
    )
    print(f"\n[phase4] Passed tasks: {passed_task_ids}")

    # ─────────────────────────────────────────────────────────────────────
    # 4.1 Merge Coordinator
    # ─────────────────────────────────────────────────────────────────────

    try:
        merge_result = run_merge_coordinator(
            passed_task_ids=passed_task_ids,
            repo_dir=REPO_DIR,
            tasks_json=TASKS_JSON,
        )
        merged_tasks       = merge_result["tasks"]
        integration_branch = merge_result["branch"]
        merge_order        = [t["id"] for t in merged_tasks]

        manifest.merge_order = merge_order
        manifest.add_phase(
            phase="merge_coordinator",
            ok=True,
            details={
                "merged_tasks":       merge_order,
                "integration_branch": integration_branch,
            },
        )
    except Exception as e:
        traceback.print_exc()
        manifest.add_phase("merge_coordinator", ok=False, details={"error": str(e)})
        manifest.save()
        return 1

    # ─────────────────────────────────────────────────────────────────────
    # 4.2 Contract Validation
    # (Không auto-patch contracts — lỗi contract → escalate)
    # ─────────────────────────────────────────────────────────────────────

    try:
        contract_report = run_contract_validation()
        manifest.add_phase(
            phase="contract_validation",
            ok=contract_report.ok,
            details={
                "errors":      contract_report.errors,
                "warnings":    contract_report.warnings,
                "merge_order": contract_report.merge_order,
            },
        )

        if not contract_report.ok:
            error_log = _collect_error_log_from_contract(contract_report)
            for task_id in passed_task_ids:
                _attempt_healing(task_id, error_log, "contract_validation", manifest)
            manifest.save()
            return 1

    except Exception as e:
        traceback.print_exc()
        manifest.add_phase("contract_validation", ok=False, details={"error": str(e)})
        manifest.save()
        return 1

    # ─────────────────────────────────────────────────────────────────────
    # 4.3 Build Verification  +  4.5 Self-Healing (build)
    # [FIX-BUG1] Import thừa trong loop đã bị xóa
    # [FIX-BUG2] Logic tách vào helper, main flow tuyến tính
    # ─────────────────────────────────────────────────────────────────────

    build_ok, build_healed = _run_build_with_healing(passed_task_ids, manifest)

    if build_healed:
        # Patch đã push, CI sẽ re-trigger — đây là success cho run này
        manifest.save()
        return 0

    if not build_ok:
        manifest.save()
        return 1

    # ─────────────────────────────────────────────────────────────────────
    # 4.4 Runtime Validation  +  4.5 Self-Healing (runtime)
    # ─────────────────────────────────────────────────────────────────────

    runtime_ok, runtime_healed = _run_runtime_with_healing(passed_task_ids, manifest)

    if runtime_healed:
        manifest.save()
        return 0

    if not runtime_ok:
        manifest.save()
        return 1

    # ─────────────────────────────────────────────────────────────────────
    # 4.6 Release Snapshot
    # [FIX-BUG2] Phase này bây giờ luôn được reach khi 4.3 + 4.4 đều pass
    # ─────────────────────────────────────────────────────────────────────

    manifest.add_phase(
        phase="integration_pipeline",
        ok=True,
        details={"status": "READY_FOR_RELEASE"},
    )
    manifest.save()

    print("\n============================================================")
    print("PHASE 4 PASSED — Ready for Release")
    print("============================================================")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(run_phase4_pipeline())