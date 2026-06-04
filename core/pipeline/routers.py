"""
Pipeline Router — dispatcher duy nhất cho toàn bộ pipeline.

orchestrator_v2.py chỉ gọi run_agent() từ đây.
Không còn phụ thuộc vào adapter_v2.
"""
from __future__ import annotations

AGENT_BACKEND: str = "gemini"  # "mock" | "gemini"


def run_agent(agent_name: str, prompt: str, bug_context=None, attempt: int = 1) -> str:
    print(f"    [{AGENT_BACKEND.upper()}] {agent_name}: {str(prompt)[:60]}")
    if AGENT_BACKEND == "mock":
        return _run_mock(agent_name, prompt)
    elif AGENT_BACKEND == "gemini":
        return _run_gemini(agent_name, prompt, bug_context=bug_context, attempt=attempt)
    raise ValueError(f"Unknown backend: {AGENT_BACKEND}")


# ── Mock backend ──────────────────────────────────────────────────────────────

def _run_mock(agent_name: str, prompt: str) -> str:
    from tests.mock_agents_v2 import (
        mock_requirement_agent,
        mock_planner_agent,
        mock_dev_agent,
        mock_tester_agent,
    )
    dispatch = {
        "requirement-agent": mock_requirement_agent,
        "planner-agent":     mock_planner_agent,
        "dev-agent":         mock_dev_agent,
        "tester-agent":      mock_tester_agent,
    }
    if agent_name not in dispatch:
        raise ValueError(f"Unknown agent for mock backend: {agent_name}")
    return dispatch[agent_name](prompt)


# ── Gemini backend ────────────────────────────────────────────────────────────
# Tất cả import đều lazy (bên trong hàm) để tránh circular import và
# để mỗi agent file chỉ được load khi thực sự cần.

def _run_gemini(agent_name: str, prompt: str, bug_context=None, attempt: int = 1) -> str:
    if agent_name == "requirement-agent":
        from agents.requirement_agent import run
        return run(prompt)

    elif agent_name == "knowledge-graph":
        from agents.knowledge_graph_agent import run
        return run(prompt)

    elif agent_name == "architect-agent":
        from agents.architect_agent import run
        return run(prompt)

    elif agent_name == "task-materializer":
        from agents.dev_agent import run_task_materializer
        return run_task_materializer(prompt)

    elif agent_name == "planner-agent":
        from agents.planner_agent import run
        return run(prompt)

    elif agent_name == "contract-compiler":
        from planning.contract_compiler import run_contract_compiler
        return run_contract_compiler(prompt)

    elif agent_name == "structure-planner":
        from planning.structure_planner import run_structure_planner
        return run_structure_planner(prompt)

    elif agent_name == "dev-agent":
        from agents.dev_agent import run
        return run(task_id=prompt, bug_context=bug_context, attempt=attempt)

    elif agent_name == "tester-agent":
        from agents.tester_agent import run
        return run(prompt)

    raise ValueError(f"Unknown agent for gemini backend: {agent_name}")