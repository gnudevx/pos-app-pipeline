"""
self_healing.py — Phase 4.5: Self-Healing Loop

Strategy (per integration_pipeline.py spec):
  - Chỉ trigger khi phase 4.3 (build) hoặc 4.4 (runtime) fail
  - FailureType.TRANSIENT  → retry phase đó (max TRANSIENT_RETRY_LIMIT lần)
  - FailureType.STRUCTURAL → gọi Gemini sinh patch → git commit+push → HEALED
  - FailureType.SEMANTIC   → escalate, fail pipeline (human review required)
  - FailureType.UNKNOWN    → treat as SEMANTIC (safe default)

Gemini patch flow:
  1. Classify error_log → FailureType
  2. If STRUCTURAL: prompt Gemini for unified diff patch (via ai_client)
  3. Apply patch via `git apply`
  4. git commit + push → CI re-triggers
  5. Return HealingReport(final_status="HEALED")

AI client:
  Dùng ai_client.call() với key rotation + exponential backoff tự động.
  Keys được load từ config.GEMINI_API_KEYS (list[str]).
  Fallback: env var GEMINI_API_KEYS (comma-separated) hoặc GEMINI_API_KEY.

Env vars (fallback nếu không có config.py):
  GEMINI_API_KEYS — comma-separated list of API keys
  GEMINI_API_KEY  — single key (fallback)
  REPO_DIR        — repo root for git operations (default: ".")
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from xml.parsers.expat import errors



# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def _load_api_keys() -> list[str]:
    """Load Gemini API keys: config.py → env GEMINI_API_KEYS → env GEMINI_API_KEY."""
    try:
        from core.config import GEMINI_API_KEYS  # type: ignore
        if isinstance(GEMINI_API_KEYS, list):
            keys = [k for k in GEMINI_API_KEYS if k]
            if keys:
                return keys
    except ImportError:
        pass

    # Fallback: env vars
    env_multi = os.getenv("GEMINI_API_KEYS", "")
    if env_multi:
        keys = [k.strip() for k in env_multi.split(",") if k.strip()]
        if keys:
            return keys

    single = os.getenv("GEMINI_API_KEY", "")
    return [single] if single else []


GEMINI_API_KEYS  = _load_api_keys()
MAX_HEAL_RETRIES = 3  # số lần thử patch nếu apply fail (API call đã có retry trong ai_client)


# ─────────────────────────────────────────────────────────────────────────────
# Enums & Data
# ─────────────────────────────────────────────────────────────────────────────

class FailureType(Enum):
    TRANSIENT  = "TRANSIENT"   # network flake, timeout, race condition
    STRUCTURAL = "STRUCTURAL"  # import error, tsc error, missing dep → patchable
    SEMANTIC   = "SEMANTIC"    # logic/design bug → human review required
    UNKNOWN    = "UNKNOWN"     # can't classify → treat as SEMANTIC


@dataclass
class HealAttempt:
    attempt_number: int
    failure_type: FailureType
    action: str          # "classify_only" | "gemini_patch" | "retry_signal"
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealingReport:
    ok: bool = False
    failure_type: FailureType = FailureType.UNKNOWN
    final_status: str = ""   # TRANSIENT_RETRY | HEALED | ESCALATED | MAX_RETRIES | NO_API_KEY
    attempts: list[HealAttempt] = field(default_factory=list)
    escalate_reason: str | None = None

    def print_summary(self) -> None:
        icon = "✓" if self.ok else "✗"
        print(f"\n  [self_healing] [{icon}] {self.final_status} "
              f"(type={self.failure_type.value}, attempts={len(self.attempts)})")
        if self.escalate_reason:
            print(f"  [self_healing]   escalate: {self.escalate_reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Classify failure
# ─────────────────────────────────────────────────────────────────────────────

# Keyword patterns → FailureType  (checked in order; first match wins)
_TRANSIENT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"connection (reset|refused|timed? out)",
        r"temporary failure",
        r"ETIMEDOUT|ECONNRESET|ECONNREFUSED",
        r"health check failed after \d+ attempts",
        r"docker compose up.*timeout",
        r"npm (ERR! code ENOTFOUND|ERR! network)",
        r"pip.*retrying.*attempt",
        r"socket hang up",
        r"read ECONNRESET",
    ]
]

_STRUCTURAL_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ModuleNotFoundError",
        r"ImportError",
        r"Cannot find module",
        r"TS\d+:",             # TypeScript compiler error
        r"error TS",
        r"SyntaxError",
        r"IndentationError",
        r"NameError",
        r"AttributeError",
        r"No matching distribution found",
        r"npm ERR! missing:",
        r"npm ERR! peer dep",
        r"requirement.*not found",
        r"failed to resolve",
        r"build failed",
        r"compilation error",
    ]
]

_SEMANTIC_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"AssertionError",
        r"ValueError.*expected",
        r"wrong (result|value|output)",
        r"test.*fail",
        r"FAIL.*test",
        r"smoke test failed",
        r"assertion.*failed",
        r"unexpected response",
        r"business logic",
    ]
]


def classify_failure(error_log: str) -> FailureType:
    """
    Rule-based classifier. Priority: TRANSIENT > STRUCTURAL > SEMANTIC > UNKNOWN.
    Falls through to UNKNOWN (safe default → treated as SEMANTIC by caller).
    """
    for pat in _TRANSIENT_PATTERNS:
        if pat.search(error_log):
            return FailureType.TRANSIENT

    for pat in _STRUCTURAL_PATTERNS:
        if pat.search(error_log):
            return FailureType.STRUCTURAL

    for pat in _SEMANTIC_PATTERNS:
        if pat.search(error_log):
            return FailureType.SEMANTIC

    return FailureType.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Gemini patch generation (via ai_client)
# ─────────────────────────────────────────────────────────────────────────────

_PATCH_SYSTEM_PROMPT = """You are an expert software engineer acting as an automated bug-fixer.

Given a build or runtime error log, generate a minimal unified diff patch (git diff format)
that fixes the root cause. The patch must:
- Use unified diff format (--- a/file +++ b/file @@ ... @@)
- Be as small as possible (only change what is necessary)
- Not break other functionality
- Target Python backend or TypeScript/React frontend code

Respond with ONLY the unified diff patch, starting with "---" and nothing else.
Do not include explanations, markdown fences, or commentary.
If the error cannot be fixed by a code patch (e.g. infrastructure/config issue),
respond with exactly: CANNOT_PATCH
"""

def extract_related_files(error_log: str) -> list[str]:
    matches = re.findall(r"src/[^\s(:]+", error_log)
    return sorted(set(matches))

def load_file_context(repo_dir: str, files: list[str]) -> str:
    chunks = []

    for file in files:
        path = Path(repo_dir) / file

        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                chunks.append(
                    f"\n=== FILE: {file} ===\n{content[:4000]}"
                )
            except Exception:
                pass

    return "\n".join(chunks)
def generate_patch(
    task_id: str,
    error_log: str,
    phase: str,
    repo_dir: str,
) -> str:
    """
    Gọi Gemini qua ai_client (key rotation + exponential backoff tự động).
    Returns unified diff string, hoặc "CANNOT_PATCH" nếu không thể fix.
    Raises RuntimeError nếu tất cả keys đều exhausted.
    """
    import core.ai_client as ai_client
    related_files = extract_related_files(error_log)
    context = load_file_context(repo_dir, related_files)
    user_prompt = (
        f"Task ID: {task_id}\n"
        f"Phase: {phase}\n\n"
        f"Error log:\n{error_log[:4000]}\n\n"
        f"Relevant files:\n{related_files}\n\n"
        f"Relevant file contents:\n{context}\n\n"
        f"Generate a valid unified diff patch.\n"
        f"Patch paths must be relative to repo root.\n"
        f"Respond ONLY with raw diff."
    )

    # ai_client.call() tự xử lý: 429 → rotate key, 5xx → exponential backoff
    return ai_client.call(
        api_keys=GEMINI_API_KEYS,
        system_prompt=_PATCH_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        agent_name="dev-agent",  # dùng model gemini-2.5-flash theo AGENT_MODELS
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Apply patch + git push
# ─────────────────────────────────────────────────────────────────────────────

def _git(cmd: str, cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        f"git {cmd}",
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"`git {cmd}` failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )
    return result


def apply_patch_and_push(
    patch_diff: str,
    task_id: str,
    repo_dir: str,
) -> None:
    """
    Write patch to temp file → git apply → git commit → git push.
    Raises RuntimeError on any failure (caller should catch and escalate).
    """
    patch_path = Path(repo_dir) / f".self_healing_{task_id}.patch"
    patch_path.write_text(patch_diff, encoding="utf-8")

    try:
        # Validate patch first (dry run)
        r = subprocess.run(
            f"git apply --check {patch_path}",
            shell=True,
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"Patch validation failed:\n{r.stderr.strip()}\n\nPatch:\n{patch_diff[:500]}"
            )

        # Apply
        _git(f"apply {patch_path}", cwd=repo_dir)

        # Commit
        _git("add -A", cwd=repo_dir)
        _git(
            f'commit -m "fix(self-healing): auto-patch for {task_id} [{int(time.time())}]"',
            cwd=repo_dir,
        )

        # Push — get current branch
        branch_r = _git("rev-parse --abbrev-ref HEAD", cwd=repo_dir)
        branch = branch_r.stdout.strip()
        _git(f"push origin {branch}", cwd=repo_dir)

        print(f"  [self_healing] ✓ Patch applied and pushed to {branch!r}")

    finally:
        patch_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry
# ─────────────────────────────────────────────────────────────────────────────

def run_self_healing_loop(
    task_id: str,
    error_log: str,
    phase: str,
    repo_dir: str,
) -> HealingReport:
    """
    Phase 4.5 Self-Healing Loop.

    Args:
        task_id:   Task ID being healed (e.g. "TASK-01")
        error_log: Raw error output from failed phase
        phase:     "build_verification" | "runtime_validation" | "contract_validation"

    Returns:
        HealingReport with final_status one of:
          TRANSIENT_RETRY  — caller should retry the failed phase
          HEALED           — patch pushed, CI will re-trigger
          ESCALATED        — human review required
          MAX_RETRIES      — Gemini tried but patch never applied cleanly
          NO_API_KEY       — GEMINI_API_KEY not configured
    """
    print(f"\n── 4.5 Self-Healing Loop ── task={task_id} phase={phase}")

    report = HealingReport()

    # ── Step 1: Classify ────────────────────────────────────────────────────
    failure_type = classify_failure(error_log)
    report.failure_type = failure_type
    print(f"  [self_healing] Classified as: {failure_type.value}")

    # ── TRANSIENT: signal caller to retry ───────────────────────────────────
    if failure_type == FailureType.TRANSIENT:
        report.attempts.append(HealAttempt(
            attempt_number=1,
            failure_type=failure_type,
            action="retry_signal",
            ok=True,
            details={"reason": "transient failure, caller will retry phase"},
        ))
        report.ok = True
        report.final_status = "TRANSIENT_RETRY"
        report.print_summary()
        return report

    # ── SEMANTIC / UNKNOWN: escalate immediately ─────────────────────────────
    if failure_type in (FailureType.SEMANTIC, FailureType.UNKNOWN):
        reason = (
            "Semantic or unknown failure — automated patching not safe. "
            "Human review required."
        )
        report.attempts.append(HealAttempt(
            attempt_number=1,
            failure_type=failure_type,
            action="classify_only",
            ok=False,
            details={"escalate_reason": reason},
        ))
        report.ok = False
        report.final_status = "ESCALATED"
        report.escalate_reason = reason
        report.print_summary()
        return report

    # ── STRUCTURAL: attempt Gemini patch ────────────────────────────────────
    if not GEMINI_API_KEYS:
        report.attempts.append(HealAttempt(
            attempt_number=1,
            failure_type=failure_type,
            action="gemini_patch",
            ok=False,
            details={"error": "GEMINI_API_KEY not set"},
        ))
        report.ok = False
        report.final_status = "NO_API_KEY"
        report.escalate_reason = (
            "STRUCTURAL failure detected but no GEMINI_API_KEYS configured. "
            "Set config.GEMINI_API_KEYS or env var GEMINI_API_KEYS to enable automated patching."
        )
        report.print_summary()
        return report
    missing_modules = re.findall(r"Cannot find module '([^']+)'", error_log)
    if missing_modules:
        print(f"  [self_healing] Detected missing files: {missing_modules}")
        all_generated = True

        for module_path in missing_modules:
            rel = module_path.lstrip('./')
            # Chỉ xử lý relative imports (./components/X), bỏ qua node_modules
            if not module_path.startswith('.'):
                continue

            full_path = Path(repo_dir) / "pos-app-test_v2/src/frontend/src" / (rel + ".tsx")
            print(f"  [self_healing] Generating missing file: {full_path}")

            try:
                component_name = Path(rel).name
                gen_prompt = (
                    f"Generate a minimal functional React TypeScript component: {component_name}\n"
                    f"Context: POS (Point of Sale) application.\n"
                    f"Return ONLY valid .tsx code, no markdown, no explanation."
                )
                # Dùng lại generate_patch infrastructure — gọi ai_client trực tiếp
                import core.ai_client as ai_client
                file_content = ai_client.call(
                    api_keys=GEMINI_API_KEYS,
                    system_prompt="You are an expert React TypeScript developer. Return only code.",
                    user_prompt=gen_prompt,
                    agent_name="dev-agent",
                )
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(file_content.strip(), encoding="utf-8")
                print(f"  [self_healing] ✓ Created: {full_path}")

            except Exception as e:
                print(f"  [self_healing] ✗ Failed to generate {module_path}: {e}")
                all_generated = False

        if all_generated:
            # Commit files mới tạo rồi push
            try:
                _git("add -A", cwd=repo_dir)
                _git(
                    f'commit -m "fix(self-healing): generate missing files for {task_id}"',
                    cwd=repo_dir,
                )
                branch = _git("rev-parse --abbrev-ref HEAD", cwd=repo_dir).stdout.strip()
                _git(f"push origin {branch}", cwd=repo_dir)
                report.ok = True
                report.final_status = "HEALED"
                report.print_summary()
                return report
            except RuntimeError as e:
                print(f"  [self_healing] ✗ Git push failed: {e}")
    for attempt_num in range(1, MAX_HEAL_RETRIES + 1):
        print(f"  [self_healing] Gemini patch attempt {attempt_num}/{MAX_HEAL_RETRIES}...")
        # 2a. Generate patch (ai_client xử lý retry/backoff nội bộ)
        try:
            patch_diff = generate_patch(
                task_id,
                error_log,
                phase,
                repo_dir,
            )
        except RuntimeError as e:
            report.attempts.append(HealAttempt(
                attempt_number=attempt_num,
                failure_type=failure_type,
                action="gemini_patch",
                ok=False,
                details={"error": str(e), "stage": "generate"},
            ))
            print(f"  [self_healing] ✗ Gemini API error (all keys exhausted): {e}")
            # Không sleep ở đây — ai_client đã backoff bên trong
            break  # Không retry generate nữa nếu ai_client đã exhausted

        if patch_diff.strip().upper() == "CANNOT_PATCH":
            report.attempts.append(HealAttempt(
                attempt_number=attempt_num,
                failure_type=failure_type,
                action="gemini_patch",
                ok=False,
                details={"error": "Gemini returned CANNOT_PATCH"},
            ))
            report.ok = False
            report.final_status = "ESCALATED"
            report.escalate_reason = (
                f"Gemini determined error in {phase} cannot be auto-patched. "
                "Human review required."
            )
            report.print_summary()
            return report

        print(f"  [self_healing] Patch received ({len(patch_diff)} chars)")

        # 2b. Apply patch
        try:
            apply_patch_and_push(
                patch_diff,
                task_id,
                repo_dir,
            )
            report.attempts.append(HealAttempt(
                attempt_number=attempt_num,
                failure_type=failure_type,
                action="gemini_patch",
                ok=True,
                details={"patch_size": len(patch_diff)},
            ))
            report.ok = True
            report.final_status = "HEALED"
            report.print_summary()
            return report

        except RuntimeError as e:
            report.attempts.append(HealAttempt(
                attempt_number=attempt_num,
                failure_type=failure_type,
                action="gemini_patch",
                ok=False,
                details={"error": str(e), "stage": "apply"},
            ))
            print(f"  [self_healing] ✗ Patch apply failed: {e}")
            # Enrich error_log with patch failure context for next attempt
            error_log = (
                f"{error_log}\n\n=== Patch Apply Error (attempt {attempt_num}) ===\n{e}"
            )
            time.sleep(2 ** attempt_num)

    # All attempts exhausted
    report.ok = False
    report.final_status = "MAX_RETRIES"
    report.escalate_reason = (
        f"Self-healing exhausted {MAX_HEAL_RETRIES} Gemini patch attempts "
        f"for {task_id} in phase {phase}. Human review required."
    )
    report.print_summary()
    return report