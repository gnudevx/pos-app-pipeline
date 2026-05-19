"""
fix_agent.py — CI Fix Agent dùng Gemini API

Đọc: contract + dev-agent md + ci-rules.md + error log
Gọi: Gemini API → sinh patch
Apply: patch vào src/
Update: tasks.json status

Usage:
  python .ci/fix_agent.py --task TASK-01 --error-log /tmp/ci_error.log
  python .ci/fix_agent.py --task TASK-01 --error-log /tmp/ci_error.log --dry-run

Env:
  GEMINI_API_KEY   — bắt buộc
  TASKS_JSON       — default: docs/tasks.json
  CONTRACTS_DIR    — default: docs/contracts
  AGENTS_DIR       — default: .claude/agents
  MAX_RETRIES      — default: 2
"""

import json, os, sys, argparse, subprocess
from pathlib import Path

TASKS_JSON    = os.environ.get("TASKS_JSON",    "docs/tasks.json")
CONTRACTS_DIR = os.environ.get("CONTRACTS_DIR", "docs/contracts")
AGENTS_DIR    = os.environ.get("AGENTS_DIR",    ".claude/agents")
CI_RULES_PATH = os.path.join(os.path.dirname(__file__), "ci-rules.md")
MAX_RETRIES   = int(os.environ.get("MAX_RETRIES", "2"))


# ── Helpers ────────────────────────────────────────────────────────────────

def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_file(path: str, max_chars: int = 8000) -> str:
    if not os.path.exists(path):
        return f"[file not found: {path}]"
    content = Path(path).read_text(encoding="utf-8")
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... [truncated at {max_chars} chars]"
    return content


def get_task(tasks_data: dict, task_id: str) -> dict | None:
    for sprint in tasks_data.get("sprints", []):
        for task in sprint.get("tasks", []):
            if task.get("id") == task_id:
                return task
    return None


def get_retry_count(tasks_data: dict, task_id: str) -> int:
    task = get_task(tasks_data, task_id)
    return task.get("ci_retry_count", 0) if task else 0


# ── Build prompt ────────────────────────────────────────────────────────────

def build_prompt(task_id: str, error_log: str, tasks_data: dict) -> str:
    task = get_task(tasks_data, task_id)
    if not task:
        print(f"[fix_agent] ERROR: {task_id} not found in tasks.json", file=sys.stderr)
        sys.exit(1)

    contract_path  = os.path.join(CONTRACTS_DIR, f"{task_id}.contract.json")
    agent_md_path  = os.path.join(AGENTS_DIR, f"dev-agent-{task_id.lower()}.md")

    contract_text  = read_file(contract_path)
    agent_md_text  = read_file(agent_md_path)
    ci_rules_text  = read_file(CI_RULES_PATH)
    artifacts      = task.get("artifacts", [])

    # Đọc source files liên quan (giới hạn 3 file đầu để tránh overflow token)
    source_context = ""
    for artifact in artifacts[:3]:
        if os.path.exists(artifact):
            source_context += f"\n\n### {artifact}\n```\n{read_file(artifact, max_chars=3000)}\n```"

    prompt = f"""You are a CI Fix Agent. A CI pipeline has failed for {task_id}.
Your job is to fix the code so CI passes.

## CI Rules (MUST follow)
{ci_rules_text}

## Contract (source of truth — DO NOT change)
```json
{contract_text}
```

## Task Agent Rules
{agent_md_text}

## CI Error Log
```
{error_log}
```

## Current Source Files
{source_context if source_context else "[No source files found yet]"}

## Task Artifacts (files you may modify)
{json.dumps(artifacts, indent=2)}

## Instructions
1. Analyze the error log carefully
2. Fix ONLY files listed in artifacts above
3. DO NOT change contract, method, path, or status_code
4. If fix requires changing contract → set escalate=true

Respond ONLY with valid JSON in this exact format (no markdown, no explanation):
{{
  "task_id": "{task_id}",
  "files_changed": [
    {{
      "path": "src/...",
      "action": "modify",
      "reason": "short reason",
      "content": "FULL file content after fix (not a diff)"
    }}
  ],
  "escalate": false,
  "escalate_reason": ""
}}
"""
    return prompt


# ── Gemini API call ─────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[fix_agent] ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    try:
        import urllib.request
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={api_key}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Strip markdown fences nếu có
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"[fix_agent] ERROR: Gemini returned invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[fix_agent] ERROR calling Gemini: {e}", file=sys.stderr)
        sys.exit(1)


# ── Apply patch ─────────────────────────────────────────────────────────────

def apply_fix(fix_result: dict, dry_run: bool = False) -> bool:
    if fix_result.get("escalate"):
        reason = fix_result.get("escalate_reason", "no reason given")
        print(f"[fix_agent] ESCALATE: {reason}")
        return False

    files_changed = fix_result.get("files_changed", [])
    if not files_changed:
        print("[fix_agent] WARNING: No files in fix result")
        return False

    for fc in files_changed:
        path    = fc.get("path", "")
        content = fc.get("content", "")
        reason  = fc.get("reason", "")

        if not path or not content:
            print(f"[fix_agent] WARNING: Skipping invalid entry (path={path})")
            continue

        print(f"[fix_agent] {'[DRY RUN] ' if dry_run else ''}Fix: {path} — {reason}")

        if not dry_run:
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            Path(path).write_text(content, encoding="utf-8")

    return True


def git_push(task_id: str, dry_run: bool = False) -> None:
    branch = f"feature/{task_id.lower()}"
    cmds = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"ci-fix: auto-fix for {task_id} [skip ci-fix]"],
        ["git", "push", "origin", f"HEAD:{branch}"],
    ]
    for cmd in cmds:
        print(f"[fix_agent] {'[DRY RUN] ' if dry_run else ''}$ {' '.join(cmd)}")
        if not dry_run:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[fix_agent] ERROR: {result.stderr}", file=sys.stderr)
                sys.exit(1)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="CI Fix Agent")
    p.add_argument("--task",      required=True, help="Task ID, e.g. TASK-01")
    p.add_argument("--error-log", required=True, help="Path to CI error log file")
    p.add_argument("--dry-run",   action="store_true", help="Print actions without writing files")
    args = p.parse_args()

    task_id   = args.task.upper()
    error_log = read_file(args.error_log, max_chars=4000)

    # Load tasks.json để check retry count
    tasks_data   = load_json(TASKS_JSON)
    retry_count  = get_retry_count(tasks_data, task_id)

    if retry_count >= MAX_RETRIES:
        print(f"[fix_agent] {task_id} has reached max retries ({MAX_RETRIES}). Escalating.")
        # Set CI_FAILED
        subprocess.run([
            sys.executable, os.path.join(os.path.dirname(__file__), "ci_state.py"),
            "--set-failed", task_id, "--retry", str(retry_count)
        ])
        sys.exit(1)

    print(f"[fix_agent] Fixing {task_id} (attempt {retry_count + 1}/{MAX_RETRIES}) ...")

    # Build prompt & call Gemini
    prompt     = build_prompt(task_id, error_log, tasks_data)
    fix_result = call_gemini(prompt)

    # Apply fix
    success = apply_fix(fix_result, dry_run=args.dry_run)

    if not success:
        # Escalate
        subprocess.run([
            sys.executable, os.path.join(os.path.dirname(__file__), "ci_state.py"),
            "--set-failed", task_id, "--retry", str(retry_count + 1)
        ])
        sys.exit(1)

    # Push lại → CI re-trigger tự động
    git_push(task_id, dry_run=args.dry_run)

    # Update retry count trong tasks.json
    task = get_task(tasks_data, task_id)
    if task:
        task["ci_retry_count"] = retry_count + 1
        with open(TASKS_JSON, "w", encoding="utf-8") as f:
            json.dump(tasks_data, f, indent=2, ensure_ascii=False)

    print(f"[fix_agent] Done. Pushed fix for {task_id}. CI will re-trigger.")


if __name__ == "__main__":
    main()
