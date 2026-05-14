"""
Parser - doc file agent instruction, strip markdown fences, parse JSON an toan.
Khong phu thuoc vao Gemini hay Git — co the unit test doc lap.
"""
import os
import json


# ── Agent instruction ─────────────────────────────────────

def load_agent_instruction(agent_name, backend=None):
    """
    Doc .claude/agents/{agent_name}.md lam system prompt.
    If backend provided, try {agent_name}-{backend}.md first.
    Tu dong strip YAML frontmatter (phan --- ... ---).
    """
    # Try backend-specific file first if backend specified
    if backend:
        path = os.path.join(".claude", "agents", f"{agent_name}-{backend}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    content = content[end + 3:].strip()
            return content
    
    # Fallback to generic file
    path = os.path.join(".claude", "agents", f"{agent_name}.md")
    if not os.path.exists(path):
        return f"You are {agent_name}. Complete the task precisely."

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()

    return content


def load_claude_md():
    """Doc CLAUDE.md lam shared project context."""
    for path in ["CLAUDE.md", "context/claude.md"]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    return ""


# ── Markdown fence stripping ──────────────────────────────

def strip_fences(text, lang=""):
    """
    Bo markdown code fences khoi response cua Gemini.
    Thu strip ```{lang} truoc, fallback sang ``` bat ky.
    """
    marker = f"```{lang}" if lang else "```"
    if marker in text:
        start = text.find(marker) + len(marker)
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    if "```" in text:
        start = text.find("```") + 3
        start = text.find("\n", start) + 1
        end = text.rfind("```")
        if end > start:
            return text[start:end].strip()

    return text.strip()


# ── JSON extraction ───────────────────────────────────────

def extract_json_array(text):
    """
    Tim va parse JSON array [...] dau tien trong text.
    Tra ve (list, None) neu ok, hoac (None, error_msg) neu fail.
    """
    if "[" not in text or "]" not in text:
        return None, "No [ ] found in response"

    start = text.find("[")
    end = text.rfind("]") + 1
    raw = text[start:end]

    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return None, f"Parsed JSON is {type(data).__name__}, expected list"
        return data, None
    except json.JSONDecodeError as e:
        return None, str(e)


def extract_json_object(text):
    """
    Tim va parse JSON object {...} dau tien trong text.
    Strip fences truoc, sau do tim { }.
    Tra ve (dict, None) neu ok, hoac (None, error_msg) neu fail.
    """
    raw = strip_fences(text, "json")

    if "{" not in raw or "}" not in raw:
        return None, "No { } found in response"

    start = raw.find("{")
    end = raw.rfind("}") + 1

    try:
        data = json.loads(raw[start:end])
        if not isinstance(data, dict):
            return None, f"Parsed JSON is {type(data).__name__}, expected dict"
        return data, None
    except json.JSONDecodeError as e:
        return None, str(e)


def split_prd_and_stories(response):
    """
    Tach response cua requirement-agent thanh:
      - prd_text: phan markdown truoc JSON array
      - stories:  JSON array da parse
    Tra ve (prd_text, stories, error_msg).
    """
    stories, err = extract_json_array(response)
    if err:
        return response, None, err

    bracket_pos = response.find("[")
    prd_text = response[:bracket_pos].strip()
    return prd_text, stories, None


# ── Signal parsing (orchestrator dung) ───────────────────

def parse_test_signal(signal):
    """
    Parse 'TEST_PASS:TASK-01' hoac 'TEST_FAIL:TASK-01:2:0'.
    Tra ve dict: { passed, task_id, permanent, transient }
    """
    parts = signal.split(":")
    if len(parts) < 2:
        return {"passed": False, "task_id": "UNKNOWN", "permanent": 0, "transient": 0}

    passed = parts[0] == "TEST_PASS"
    task_id = parts[1]
    permanent = int(parts[2]) if len(parts) > 2 else 0
    transient = int(parts[3]) if len(parts) > 3 else 0

    return {
        "passed": passed,
        "task_id": task_id,
        "permanent": permanent,
        "transient": transient,
    }


def is_fallback(signal):
    """Kiem tra requirement/planner agent co dung default data khong."""
    return signal.endswith(":FALLBACK")


# ── File block parsing (dev-agent output) ────────────────

def parse_file_blocks(response):
    """
    Parse output cua dev-agent theo format:

        FILE: src/backend/main.py
        ```python
        [code]
        ```

    Tra ve dict { filepath: code_string }.
    Bo qua cac block rong hoac chi co placeholder.
    """
    import re
    result = {}

    # Match: FILE: <path>\n```<lang>\n<code>\n```
    pattern = re.compile(
        r"FILE:\s*(\S+)\s*\n```[a-zA-Z]*\n(.*?)```",
        re.DOTALL
    )

    for match in pattern.finditer(response):
        filepath = match.group(1).strip()
        code = match.group(2).strip()

        # Bo qua placeholder rong
        if not code or len(code) < 10:
            continue
        if "[complete file content here]" in code:
            continue

        result[filepath] = code

    if not result:
        # Fallback: Gemini doi khi khong theo format FILE:
        # Thu parse tat ca code blocks va gan ten theo thu tu
        fallback_pattern = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
        blocks = [m.group(1).strip() for m in fallback_pattern.finditer(response)
                  if len(m.group(1).strip()) > 20]
        if blocks:
            print(f"      [WARN] parse_file_blocks: no FILE: markers found, "
                  f"got {len(blocks)} raw blocks — cannot map to filenames")

    return result