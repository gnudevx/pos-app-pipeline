"""
Contract Compiler — deterministic transform, không gọi LLM.

Input:  docs/tasks.json
Output: docs/tasks.json (normalized in-place)
        docs/contracts/TASK-XX.contract.json  ← artifact files (v4)

Sau bước này:
  - docs/contracts/ là nguồn truth duy nhất cho DEV + TESTER
  - tasks.json vẫn được update nhưng KHÔNG còn là nguồn truth của contract
"""

import os
import json

from contracts.contract_normalizer import (
    normalize_tasks_to_contracts,
    export_contracts_to_files,
    load_contract,
    list_contracts,
)


def run_contract_compiler(prompt: str) -> str:
    """
    Entry point cho pipeline router.

    Args:
        prompt: không dùng (compat với run_agent interface)

    Returns:
        "CONTRACT_COMPILED" nếu thành công
        Raises RuntimeError nếu tasks.json chưa tồn tại
    """
    tasks_path = "docs/tasks.json"
    if not os.path.exists(tasks_path):
        raise RuntimeError("tasks.json not found — run planner-agent first")

    with open(tasks_path, encoding="utf-8") as f:
        tasks_json = json.load(f)

    # Bước 1: normalize in-place (cập nhật tasks.json)
    compiled = normalize_tasks_to_contracts(tasks_json)

    with open(tasks_path, "w", encoding="utf-8") as f:
        json.dump(compiled, f, indent=2, ensure_ascii=False)

    # [FIX BUG-C] Xóa contract files cũ trước khi ghi mới
    # Tránh tình huống pipeline cũ (3 tasks) để lại contract của pipeline mới (4 tasks)
    contracts_dir = "docs/contracts"
    if os.path.isdir(contracts_dir):
        stale = [f for f in os.listdir(contracts_dir) if f.endswith(".contract.json")]
        if stale:
            for fname in stale:
                os.remove(os.path.join(contracts_dir, fname))
            print(f"      [contract-compiler] Cleared {len(stale)} stale contract file(s)")

    # Bước 2: [NEW v4] Export contract artifacts
    written = export_contracts_to_files(compiled, contracts_dir=contracts_dir)

    total_routes = sum(
        len(t.get("api_contract", {}).get("routes", []))
        for s in compiled.get("sprints", [])
        for t in s.get("tasks", [])
    )

    print(
        f"      [contract-compiler] DONE — "
        f"{total_routes} routes normalized, "
        f"{len(written)} contract files → {contracts_dir}/"
    )
    return "CONTRACT_COMPILED"


def require_contract(task_id: str) -> dict:
    """
    Load contract file cho task_id.
    Raise RuntimeError nếu chưa compile (pipeline bị sai thứ tự).
    """
    contract = load_contract(task_id, contracts_dir="docs/contracts")
    if contract is None:
        available = list_contracts("docs/contracts")
        raise RuntimeError(
            f"Contract not found for {task_id}. "
            f"Run contract-compiler first. "
            f"Available: {available or 'none'}"
        )
    return contract