"""
Parser - doc file agent instruction, strip markdown fences, parse JSON an toan.
Khong phu thuoc vao Gemini hay Git — co the unit test doc lap.
"""
import os
import json
import os
import re

AGENTS_DIR = ".claude/agents"

# ── Agent instruction ─────────────────────────────────────
 
# Map task_id → file chứa spec của task đó
TASK_FILE_MAP = {
    "TASK-01": "dev-agent-task01.md",
    "TASK-02": "dev-agent-task02.md",
    "TASK-03": "dev-agent-task03.md",
}
 
# File luôn được load bất kể task nào
CORE_FILE = "dev-agent-core.md"
 
 
def _read_agent_file(path: str) -> str:
    """Đọc file và strip frontmatter YAML (--- ... ---)."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
    return content.strip()
 

def load_agent_instruction(agent_name: str, backend: str = "gemini", task_id: str = "") -> str:
    """
    Load system prompt cho agent.
 
    Chế độ hoạt động:
      - agent khác dev-agent  → đọc file đơn như cũ (backward compat)
      - dev-agent + task_id   → core + task file tương ứng (SMART MODE)
      - dev-agent + no task_id → core + ALL task files (fallback)
 
    Token so sánh (ước tính):
      Cũ  : core + task01 + task02 + task03 ~ 4,000 tokens mỗi lần gọi
      Mới : core + task01 only              ~ 1,500 tokens  (-62%)
    """
    base_dir = AGENTS_DIR
 
    # ── Non-dev agents: đọc file đơn như cũ ──────────────────────────────
    if agent_name != "dev-agent":
        filename = f"{agent_name}-{backend}.md" if backend else f"{agent_name}.md"
        filepath = os.path.join(base_dir, filename)
        if not os.path.exists(filepath):
            filepath = os.path.join(base_dir, f"{agent_name}.md")
        if not os.path.exists(filepath):
            return ""
        return _read_agent_file(filepath)
 
    # ── Dev agent: SMART MODE ─────────────────────────────────────────────
    core_path = os.path.join(base_dir, CORE_FILE)
    if not os.path.exists(core_path):
        raise RuntimeError(f"Core file not found: {core_path}")
 
    parts = [_read_agent_file(core_path)]
 
    if task_id and task_id in TASK_FILE_MAP:
        # Chỉ load đúng 1 file task
        task_filename = TASK_FILE_MAP[task_id]
        task_path = os.path.join(base_dir, task_filename)
        if not os.path.exists(task_path):
            raise RuntimeError(f"Task file not found: {task_path} (for {task_id})")
        parts.append(_read_agent_file(task_path))
        print(f"      [parser] dev-agent: core + {task_filename}")
    else:
        # Fallback: load tất cả (không biết task_id)
        for fname in TASK_FILE_MAP.values():
            task_path = os.path.join(base_dir, fname)
            if os.path.exists(task_path):
                parts.append(_read_agent_file(task_path))
        print(f"      [parser] dev-agent: core + ALL tasks (fallback — no task_id)")
 
    return "\n\n---\n\n".join(parts)
 


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