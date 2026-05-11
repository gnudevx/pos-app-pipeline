"""
Agent Adapter - Router duy nhat giua mock va Claude API that.

DE CHUYEN SANG REAL API:
  Buoc 1: Lay API key tu console.anthropic.com
  Buoc 2: Sua dong duy nhat: AGENT_BACKEND = "mock" -> "claude"
  Buoc 3: Khong can sua gi them
"""

# ================================================================
# CHI SUA DONG NAY KHI CO API KEY
AGENT_BACKEND = "mock"
# ================================================================


def run_agent(agent_name, prompt):
    """Route sang mock hoac Claude that tuy AGENT_BACKEND."""
    if AGENT_BACKEND == "mock":
        return _run_mock(agent_name, prompt)
    elif AGENT_BACKEND == "claude":
        return _run_claude(agent_name, prompt)
    else:
        raise ValueError(f"Unknown backend: {AGENT_BACKEND}")


def _run_mock(agent_name, prompt):
    """Chay mock agent v2 — co code that + Git ops that."""
    from tests.mock_agents_v2 import (
        mock_requirement_agent,
        mock_planner_agent,
        mock_dev_agent,
        mock_tester_agent,
    )
    dispatch = {
        "requirement-agent": mock_requirement_agent,
        "planner-agent": mock_planner_agent,
        "dev-agent": mock_dev_agent,
        "tester-agent": mock_tester_agent,
    }
    if agent_name not in dispatch:
        raise ValueError(f"Unknown agent: {agent_name}")
    return dispatch[agent_name](prompt)


def _run_claude(agent_name, prompt):
    """
    Goi Claude Code CLI that.
    Claude tu doc file .claude/agents/{agent_name}.md lam instruction.
    CLAUDE.md duoc load tu dong boi Claude Code CLI.
    """
    import subprocess
    result = subprocess.run(
        ["claude", "--agent", agent_name, "--print", prompt],
        capture_output=True, text=True, cwd="."
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Claude agent '{agent_name}' failed:\n{result.stderr}"
        )
    return result.stdout.strip()