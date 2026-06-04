"""
Signal Parser — parse signals trả về từ tester-agent và dev-agent.

Tách từ adapter_v2 + orchestrator_v2.
Tất cả xử lý signal string đi qua module này.

DEV signals (sinh ra bởi _gemini_dev trong agents/dev_agent.py):
  DEV_ESCALATE:<task_id>         — agent bó tay, cần human review
  DEV_CONTRACT_FAIL:<detail>     — contract invalid, retry ngay
  DEV_STATIC_FAIL:<detail>       — lỗi static analysis (mypy/eslint/...)
  DEV_SMOKE_FAIL:<detail>        — smoke test fail
  DEV_IMPORT_FAIL:<detail>       — import error
  DEV_SERIALIZATION_FAIL:<detail>— serialization error
  DEV_SKIP:<reason>              — skip task (won't do)
  DEV_DONE (hoặc bất kỳ string khác không match) — thành công

TEST signals (sinh ra bởi _gemini_tester trong agents/tester_agent.py):
  TEST_PASS:<task_id>            — tất cả tests pass
  TEST_FAIL:<task_id>:<perm>:<trans> — có test fail
"""

from __future__ import annotations

import contracts.parser as _parser  # local parser.py (không phải stdlib)


def parse_test_signal(raw: str) -> dict:
    """
    Parse chuỗi signal từ tester-agent.

    Returns dict:
        {
            "passed": bool,
            "task_id": str | None,
            "permanent_failures": int,
            "transient_failures": int,
            "raw": str,
        }
    """
    return _parser.parse_test_signal(raw)


def is_dev_fail_signal(signal: str) -> bool:
    """True nếu signal là một trong các DEV_*_FAIL cần retry."""
    return signal.startswith((
        "DEV_STATIC_FAIL",
        "DEV_SMOKE_FAIL",
        "DEV_IMPORT_FAIL",
        "DEV_SERIALIZATION_FAIL",
    ))


def parse_dev_fail(raw: str) -> tuple[str, str]:
    """
    Parse DEV_*_FAIL signal.

    Returns:
        (signal_name, detail)  vd: ("DEV_STATIC_FAIL", "mypy: ...")
    """
    parts = raw.split(":", 1)
    signal = parts[0]
    detail = parts[1] if len(parts) > 1 else ""
    return signal, detail


def is_dev_escalate(raw: str) -> bool:
    return raw.startswith("DEV_ESCALATE")


def is_dev_contract_fail(raw: str) -> bool:
    return raw.startswith("DEV_CONTRACT_FAIL")


def is_dev_skip(raw: str) -> bool:
    return raw.startswith("DEV_SKIP")