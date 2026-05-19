"""
build_verifier.py — Phase 4.3: Build Verification

Runs in repo checkout (not docker) for speed:
  Backend : pip install -r requirements.txt → python -c "import app.main"
  Frontend: npm ci → tsc --noEmit → npm run build

Returns BuildReport with per-target results.
CI does a lightweight version of this — here we want full detail.
"""

from __future__ import annotations

import subprocess
from sys import stdout
import time
from dataclasses import dataclass, field
from pathlib import Path


# ── Config ─────────────────────────────────────────────────────────────────

BACKEND_DIR = "pos-app-test_v2/src/backend"
FRONTEND_DIR = "pos-app-test_v2/src/frontend"


# ── Data ───────────────────────────────────────────────────────────────────

@dataclass
class TargetResult:
    name: str
    ok: bool
    duration_s: float
    stdout: str = ""
    stderr: str = ""

    def print_summary(self) -> None:
        icon = "✓" if self.ok else "✗"
        print(f"  [{icon}] {self.name} ({self.duration_s:.1f}s)")
        if not self.ok and self.stderr:
            # Show last 20 lines only
            lines = self.stderr.strip().splitlines()[-20:]
            print("      " + "\n      ".join(lines))


@dataclass
class BuildReport:
    ok: bool = True
    targets: list[TargetResult] = field(default_factory=list)

    def add(self, result: TargetResult) -> None:
        self.targets.append(result)
        if not result.ok:
            self.ok = False

    def print_summary(self) -> None:
        status = "✓ BUILD PASSED" if self.ok else "✗ BUILD FAILED"
        print(f"\n  [build] {status}")
        for t in self.targets:
            t.print_summary()


# ── Subprocess helper ──────────────────────────────────────────────────────

def _run(cmd: str, cwd: str = ".", timeout: int = 60):
    process = subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    try:
        stdout, _ = process.communicate(timeout=timeout)

        if stdout:
            print(stdout, end="")

        ok = process.returncode == 0

        return ok, stdout or "", ""

    except subprocess.TimeoutExpired:
        process.kill()

        try:
            stdout, _ = process.communicate(timeout=5)
        except Exception:
            stdout = ""

        return False, stdout or "", f"Timeout after {timeout}s"


def _timed_run(name: str, cmd: str, cwd: str, timeout: int = 300) -> TargetResult:
    print(f"  [build] Running: {cmd}")
    t0 = time.time()
    ok, stdout, stderr = _run(cmd, cwd, timeout)
    return TargetResult(
        name=name,
        ok=ok,
        duration_s=round(time.time() - t0, 2),
        stdout=stdout,
        stderr=stderr,
    )

def _needs_pip_install(backend_dir: str) -> bool:
    req = Path(backend_dir) / "requirements.txt"
    stamp = Path(backend_dir) / ".deps_installed"

    if not stamp.exists():
        return True

    return stamp.stat().st_mtime < req.stat().st_mtime


def _mark_pip_installed(backend_dir: str) -> None:
    stamp = Path(backend_dir) / ".deps_installed"
    stamp.touch()


def _needs_npm_install(frontend_dir: str) -> bool:
    node_modules = Path(frontend_dir) / "node_modules"
    package_lock = Path(frontend_dir) / "package-lock.json"
    stamp = Path(frontend_dir) / ".npm_installed"

    if not node_modules.exists():
        return True

    if not stamp.exists():
        return True

    return stamp.stat().st_mtime < package_lock.stat().st_mtime


def _mark_npm_installed(frontend_dir: str) -> None:
    stamp = Path(frontend_dir) / ".npm_installed"
    stamp.touch()
    
# ── Backend ────────────────────────────────────────────────────────────────
import platform

def _get_venv_python(repo_dir: str) -> Path:
    if platform.system() == "Windows":
        return Path(repo_dir) / ".venv" / "Scripts" / "python.exe"
    else:
        return Path(repo_dir) / ".venv" / "bin" / "python"
    
def verify_backend(repo_dir: str = "../pos-app-test_v2") -> list[TargetResult]:
    backend = str(Path(repo_dir) / "src" / "backend")
    venv_python = _get_venv_python(repo_dir)
    if not venv_python.exists():
        create_result = _timed_run(
            "backend:create-venv",
            "python -m venv .venv",
            repo_dir,
            timeout=60,
        )

        results = [create_result]

        if not create_result.ok:
            return results
    results = []

    if _needs_pip_install(backend):
        result = _timed_run(
            "backend:pip-install",
            f'"{venv_python}" -m pip install -r requirements.txt --disable-pip-version-check',
            backend,
            timeout=120,
        )

        results.append(result)

        if result.ok:
            _mark_pip_installed(backend)
        else:
            return results

    else:
        print("  [build] backend deps cache hit ✓")

    results.append(
        _timed_run(
            "backend:import-check",
            f'"{venv_python}" -c "import app.main; print(\'import OK\')"',
            backend,
            timeout=30,
        )
    )

    return results


# ── Frontend ───────────────────────────────────────────────────────────────

def verify_frontend(repo_dir: str = "../pos-app-test_v2") -> list[TargetResult]:
    frontend = str(Path(repo_dir) / "src" / "frontend")

    results = []

    if _needs_npm_install(frontend):
        result = _timed_run(
            "frontend:npm-install",
            "npm install --prefer-offline 2>&1",
            frontend,
            timeout=180,
        )

        results.append(result)

        if result.ok:
            _mark_npm_installed(frontend)
        else:
            return results

    else:
        print("  [build] npm cache hit ✓")

    results.append(
        _timed_run(
            "frontend:tsc",
            "npx tsc --noEmit 2>&1",
            frontend,
            timeout=60,
        )
    )

    results.append(
        _timed_run(
            "frontend:build",
            "npm run build 2>&1",
            frontend,
            timeout=120,
        )
    )

    return results

# ── Main Entry ─────────────────────────────────────────────────────────────

def run_build_verification(repo_dir: str = "../pos-app-test_v2") -> BuildReport:
    print("\n── 4.3 Build Verification ─────────────────────────────")

    report = BuildReport()

    for result in verify_backend(repo_dir):
        report.add(result)
        # If pip install fails, skip import check — but we've already collected both
        # Future: could short-circuit here

    for result in verify_frontend(repo_dir):
        report.add(result)

    report.print_summary()
    return report