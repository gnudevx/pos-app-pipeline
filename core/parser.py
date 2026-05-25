"""
Parser — đọc agent instruction files, strip markdown fences, parse JSON an toàn.
Không phụ thuộc vào Gemini hay Git — có thể unit test độc lập.

THAY ĐỔI từ v1:
  [v1] TASK_FILE_MAP hardcode TASK-01/02/03 → dead code, gây nhầm lẫn
       dev-agent-gemini.md đã là generic (không còn task-specific md)
  [v2] Xoá TASK_FILE_MAP + smart-mode cho dev-agent
       load_agent_instruction: đọc đúng 1 file duy nhất theo agent_name + backend
       Nếu cần inject task context → caller tự inject vào user prompt (không phải system prompt)

  [v1] split_prd_and_stories có side effect ẩn: ghi entities.json ra disk
       → trách nhiệm của adapter_v2._gemini_requirement, không phải parser
  [v2] split_prd_and_stories chỉ trả về (prd_text, entities, stories, error)
       Không còn ghi file. Caller quyết định ghi ở đâu.
"""
import os
import json
import re


AGENTS_DIR = ".claude/agents"
CORE_FILE = "dev-agent-core.md"


# ── Agent instruction ─────────────────────────────────────────────────────────

def _read_agent_file(path: str) -> str:
    """Đọc file và strip frontmatter YAML (--- ... ---)."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
    return content.strip()


def load_agent_instruction(agent_name: str, backend: str = "gemini", task_id: str = "") -> str:
    """
    Load system prompt cho agent từ .claude/agents/.

    Quy tắc tìm file (theo thứ tự ưu tiên):
      1. {agent_name}-{backend}.md   (vd: dev-agent-gemini.md)
      2. {agent_name}.md             (fallback không có backend suffix)

    Dev-agent:
      - Luôn load dev-agent-core.md làm base
      - Sau đó append dev-agent-{backend}.md nếu tồn tại
      - task_id KHÔNG dùng để chọn file nữa (không còn task-specific md)
        Caller inject task context vào user prompt, không phải system prompt

    Tất cả agent khác: đọc đúng 1 file.
    """
    base_dir = AGENTS_DIR

    if agent_name == "dev-agent":
        # Core luôn required
        core_path = os.path.join(base_dir, CORE_FILE)
        if not os.path.exists(core_path):
            raise RuntimeError(f"Core file not found: {core_path}")
        parts = [_read_agent_file(core_path)]

        # Backend-specific addendum (execution approach, model config, v.v.)
        addendum_path = os.path.join(base_dir, f"dev-agent-{backend}.md")
        if os.path.exists(addendum_path):
            parts.append(_read_agent_file(addendum_path))
            print(f"      [parser] dev-agent: core + dev-agent-{backend}.md")
        else:
            print(f"      [parser] dev-agent: core only (no dev-agent-{backend}.md)")

        return "\n\n---\n\n".join(parts)

    # Tất cả agent còn lại
    candidates = []
    if backend:
        candidates.append(os.path.join(base_dir, f"{agent_name}-{backend}.md"))
    candidates.append(os.path.join(base_dir, f"{agent_name}.md"))

    for filepath in candidates:
        if os.path.exists(filepath):
            print(f"      [parser] {agent_name}: {os.path.basename(filepath)}")
            return _read_agent_file(filepath)

    print(f"      [parser] WARNING: no instruction file found for '{agent_name}' (tried: {candidates})")
    return ""


def load_claude_md() -> str:
    """Đọc CLAUDE.md làm shared project context."""
    for path in ["CLAUDE.md", "context/claude.md"]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    return ""


# ── Markdown fence stripping ──────────────────────────────────────────────────

def strip_fences(text: str, lang: str = "") -> str:
    """
    Bỏ markdown code fences khỏi response của Gemini.
    Thử strip ```{lang} trước, fallback sang ``` bất kỳ.
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


# ── JSON extraction ───────────────────────────────────────────────────────────

def extract_json_array(text: str):
    """
    Tìm và parse JSON array [...] đầu tiên trong text.
    Trả về (list, None) nếu ok, hoặc (None, error_msg) nếu fail.
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


def extract_json_object(text: str):
    """
    Tìm và parse JSON object {...} đầu tiên trong text.
    Strip fences trước, sau đó tìm { }.
    Trả về (dict, None) nếu ok, hoặc (None, error_msg) nếu fail.
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


def split_prd_and_stories(response: str):
    """
    Tách response của requirement-agent thành các phần.

    Trả về: (prd_text, entities, stories, error_msg)
      - prd_text : phần markdown trước JSON block đầu tiên
      - entities : JSON array entities (có field "component") — có thể None
      - stories  : JSON array stories (có field "title")
      - error_msg: None nếu thành công

    KHÔNG ghi file — caller (adapter) chịu trách nhiệm ghi entities.json,
    stories.json, requirements.md. Parser chỉ parse, không có side effect.

    Hỗ trợ cả format cũ (1 JSON array) và mới (2 JSON arrays: entities + stories).
    """
    # Ưu tiên extract từ ```json blocks
    json_blocks = re.findall(r'```json\s*([\s\S]*?)```', response)

    parsed_arrays = []
    for block in json_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, list) and parsed:
                parsed_arrays.append(parsed)
        except Exception:
            continue

    # Fallback: không có ```json block → tìm [...] thô
    if not parsed_arrays:
        for match in re.finditer(r'\[[\s\S]*?\]', response):
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list) and parsed:
                    parsed_arrays.append(parsed)
            except Exception:
                continue

    if not parsed_arrays:
        return response, None, None, "No JSON array found in response"

    # Phân loại: entities có "component", stories có "title"
    stories = None
    entities = None
    for arr in parsed_arrays:
        first = arr[0] if arr else {}
        if not isinstance(first, dict):
            continue
        if "title" in first and stories is None:
            stories = arr
        elif "component" in first and "title" not in first and entities is None:
            entities = arr

    # Format cũ: chỉ có 1 array → treat as stories
    if stories is None and entities is None and parsed_arrays:
        stories = parsed_arrays[0]

    if not stories:
        return response, entities, None, "No stories array found (need list with 'title' field)"

    # PRD text = phần trước ```json đầu tiên
    first_fence = response.find("```")
    prd_text = response[:first_fence].strip() if first_fence > 0 else ""
    if not prd_text:
        prd_text = "# PRD\n\nRequirement processed successfully."

    return prd_text, entities, stories, None


# ── Signal parsing ────────────────────────────────────────────────────────────

def parse_test_signal(signal: str) -> dict:
    """
    Parse 'TEST_PASS:TASK-01' hoặc 'TEST_FAIL:TASK-01:2:0'.
    Trả về dict: { passed, task_id, permanent, transient }
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


def is_fallback(signal: str) -> bool:
    """Kiểm tra requirement/planner agent có dùng default data không."""
    return signal.endswith(":FALLBACK")


# ── File block parsing ────────────────────────────────────────────────────────

def parse_file_blocks(response: str) -> dict:
    """
    Parse output của dev-agent theo format:

        FILE: src/backend/main.py
        ```python
        [code]
        ```

    Trả về dict { filepath: code_string }.
    Bỏ qua các block rỗng hoặc chỉ có placeholder.
    """
    result = {}

    pattern = re.compile(
        r"FILE:\s*(\S+)\s*\n```[a-zA-Z]*\n(.*?)```",
        re.DOTALL
    )

    for match in pattern.finditer(response):
        filepath = match.group(1).strip()
        code = match.group(2).strip()

        if not code or len(code) < 10:
            continue
        if "[complete file content here]" in code:
            continue

        result[filepath] = code

    if not result:
        fallback_pattern = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
        blocks = [m.group(1).strip() for m in fallback_pattern.finditer(response)
                  if len(m.group(1).strip()) > 20]
        if blocks:
            print(f"      [WARN] parse_file_blocks: no FILE: markers found, "
                  f"got {len(blocks)} raw blocks — cannot map to filenames")

    return result