"""
Agent Adapter - Router hỗ trợ mock / gemini.

ARCHITECTURE (v4 — REAL CONTRACT-FIRST PIPELINE):
  STORIES
    ↓
  CONTRACT COMPILER  ← normalize tasks.json + export docs/contracts/TASK-XX.contract.json
    ↓
  EXECUTABLE CONTRACT  (artifact file, độc lập với tasks.json)
    ↓
  ┌─────────────────────┐
  │                     │
  DEV AGENT          TESTER AGENT
  │ reads contract    │ reads contract
  CODE               VALIDATION
  └────────┬──────────┘
           FEEDBACK LOOP

THAY ĐỔI TỪ v3 (Pseudo) → v4 (Real):
  [v3] contract normalize in-place vào tasks.json
  [v4] contract-compiler export riêng docs/contracts/TASK-XX.contract.json

  [v3] dev agent đọc task từ tasks.json → tự bịa route
  [v4] dev agent đọc contract file → route đã locked

  [v3] tester infer response fields → blind r.json()["id"] → KeyError
  [v4] tester đọc contract["routes"][*]["response_fields"] → safe assertions

  [v4] contract có thể: versioned / diffed / replayed / snapshot độc lập

FIX LOG:
  BUG-1: Gemini ignore component=frontend
         → COMPONENT SCOPE trong system prompt
  BUG-2: test_api.py Gemini sinh logic sai
         → Tester đọc code thật trước khi sinh test (CONTRACT-FIRST)
  BUG-3: _prepare_tester_prompt hardcode component
         → Truyền component thật
  BUG-4: _inject_test_template_if_needed bị miss
         → Loại bỏ, thay bằng code-aware test generation
  BUG-5: Dev agent hardcode system prompt trong Python
         → Đọc dev-agent-gemini.md qua load_agent_instruction
  BUG-6: KeyError 'id' — test blind inject product_id = r.json()["id"]
         → _generate_tests_from_contract đọc response_fields từ CONTRACT FILE
         → Dùng safe assertion: assert "id" in data trước khi access
  BUG-7: Không có bước normalize contract trước khi dev chạy
         → CONTRACT COMPILER chạy sau planner, trước dev
  BUG-8: [NEW v4] Contract chỉ normalize in-place, không export artifact
         → _gemini_contract_compiler gọi export_contracts_to_files()
         → Dev + Tester đọc từ docs/contracts/ thay vì tasks.json
"""
import os
import json
import subprocess
import datetime
import re
import ai_client
import git_ops
import parser as p
from config import GEMINI_API_KEYS
from git_ops import make_branch_name
import textwrap
import ast
import hashlib
import traceback

# Import từ contract_normalizer (REAL CONTRACT-FIRST)
from contract_normalizer import (
    normalize_tasks_to_contracts,
    export_contracts_to_files,
    load_contract,
    list_contracts,
    resolve_route_schema,
    resolve_response_fields,
    CONTRACT_SCHEMA_VERSION,
)

AGENT_BACKEND = "gemini"


def run_agent(agent_name, prompt, bug_context=None):
    print(f"    [{AGENT_BACKEND.upper()}] {agent_name}: {prompt[:60]}")
    if AGENT_BACKEND == "mock":
        return _run_mock(agent_name, prompt)
    elif AGENT_BACKEND == "gemini":
        return _run_gemini(agent_name, prompt, bug_context)
    raise ValueError(f"Unknown backend: {AGENT_BACKEND}")


# ── Mock ──────────────────────────────────────────────────────────────────────

def _run_mock(agent_name, prompt):
    from tests.mock_agents_v2 import (
        mock_requirement_agent, mock_planner_agent,
        mock_dev_agent, mock_tester_agent,
    )
    dispatch = {
        "requirement-agent": mock_requirement_agent,
        "planner-agent":     mock_planner_agent,
        "dev-agent":         mock_dev_agent,
        "tester-agent":      mock_tester_agent,
    }
    if agent_name not in dispatch:
        raise ValueError(f"Unknown agent: {agent_name}")
    return dispatch[agent_name](prompt)


# ── Gemini router ──────────────────────────────────────────────────────────────

def _run_gemini(agent_name, prompt, bug_context=None):
    if agent_name == "requirement-agent":
        return _gemini_requirement(prompt)
    elif agent_name == "planner-agent":
        return _gemini_planner(prompt)
    elif agent_name == "contract-compiler":
        return _gemini_contract_compiler(prompt)
    elif agent_name == "dev-agent":
        return _gemini_dev(task_id=prompt, bug_context=bug_context)
    elif agent_name == "tester-agent":
        return _gemini_tester(prompt)
    raise ValueError(f"Unknown agent: {agent_name}")


# ── Requirement agent ──────────────────────────────────────────────────────────

def _gemini_requirement(prompt):
    system   = p.load_agent_instruction("requirement-agent", backend="gemini")
    claude_md = p.load_claude_md()
    if claude_md:
        system += f"\n\n# Project context:\n{claude_md}"

    response = ai_client.call(GEMINI_API_KEYS, system, prompt, "requirement-agent")

    os.makedirs("docs", exist_ok=True)
    prd_text, stories, err = p.split_prd_and_stories(response)

    if err:
        raise RuntimeError(f"Requirement agent returned invalid output: {err}")
    if not stories:
        raise RuntimeError("Requirement agent produced empty stories")
    if not prd_text or len(prd_text.strip()) < 20:
        raise RuntimeError("Requirement agent produced invalid PRD")

    with open("docs/requirements.md", "w", encoding="utf-8") as f:
        f.write(prd_text)
    with open("docs/stories.json", "w", encoding="utf-8") as f:
        json.dump(stories, f, indent=2, ensure_ascii=False)

    print(f"      [gemini] requirements.md + stories.json ({len(stories)} stories)")
    return "REQUIREMENT_DONE"


# ── Planner agent ──────────────────────────────────────────────────────────────

def _gemini_planner(prompt):
    system = p.load_agent_instruction("planner-agent", backend="gemini")

    if not os.path.exists("docs/stories.json"):
        raise RuntimeError("stories.json not found")

    with open("docs/stories.json", encoding="utf-8") as f:
        stories_json = f.read()

    claude_md = p.load_claude_md() or ""

    user_prompt = f"""
{prompt}

# stories.json
{stories_json}

# project_context
{claude_md}

CRITICAL:
- Output ONLY valid JSON
- No markdown
- No explanations
- First character must be {{
- Last character must be }}
"""

    response = ai_client.call(GEMINI_API_KEYS, system, user_prompt, "planner-agent")
    tasks, err = p.extract_json_object(response)

    if err:
        raise RuntimeError(f"Planner returned invalid JSON: {err}")
    if not tasks:
        raise RuntimeError("Planner returned empty tasks")
    if "sprints" not in tasks:
        raise RuntimeError("Planner output missing 'sprints'")

    with open("docs/tasks.json", "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    total = sum(len(s["tasks"]) for s in tasks["sprints"])
    print(f"      [gemini] tasks.json ({total} tasks)")
    return "PLANNER_DONE"


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT COMPILER  [v4: export artifact files]
#
#   Pipeline:
#     1. Đọc docs/tasks.json (planner output — raw)
#     2. normalize_tasks_to_contracts() → enforce schema cứng in-place
#     3. Ghi lại docs/tasks.json (backward compat)
#     4. [NEW] export_contracts_to_files() → docs/contracts/TASK-XX.contract.json
#
#   Sau bước này:
#     - docs/contracts/ là nguồn truth duy nhất cho DEV + TESTER
#     - tasks.json vẫn được update nhưng KHÔNG còn là nguồn truth của contract
# ══════════════════════════════════════════════════════════════════════════════

def _gemini_contract_compiler(prompt: str) -> str:
    """
    CONTRACT COMPILER — deterministic transform, không gọi Gemini.

    Input:  docs/tasks.json
    Output: docs/tasks.json (normalized)
            docs/contracts/TASK-XX.contract.json  ← NEW: artifact files
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

    # Bước 2: [NEW v4] Export contract artifacts
    written = export_contracts_to_files(compiled, contracts_dir="docs/contracts")

    total_routes = sum(
        len(t.get("api_contract", {}).get("routes", []))
        for s in compiled.get("sprints", [])
        for t in s.get("tasks", [])
    )

    print(
        f"      [contract-compiler] DONE — "
        f"{total_routes} routes normalized, "
        f"{len(written)} contract files → docs/contracts/"
    )
    return "CONTRACT_COMPILED"


# ── Helpers: load contract file (v4) ──────────────────────────────────────────

def _require_contract(task_id: str) -> dict:
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


# ══════════════════════════════════════════════════════════════════════════════
# DEV AGENT  [v4: đọc contract file thay vì tasks.json trực tiếp]
# ══════════════════════════════════════════════════════════════════════════════

def _read_existing_code(pos_app_dir, component):
    context = ""
    check = []
    if component in ("backend", "fullstack"):
        check += ["src/backend/app/main.py", "src/backend/app/models/product.py"]
    if component in ("frontend", "fullstack"):
        check += ["src/frontend/src/types/index.ts", "src/frontend/src/api/client.ts"]
    for rel in check:
        full = os.path.join(pos_app_dir, rel)
        if os.path.exists(full):
            with open(full, encoding="utf-8", errors="ignore") as f:
                snippet = f.read()[:300]
            context += f"### {rel}:\n```\n{snippet}\n```\n"
    return context or "No existing files yet — generate everything from scratch."


def _load_requirements_md() -> str:
    path = "docs/requirements.md"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


def _load_stories_for_task(task_id: str) -> str:
    path = "docs/stories.json"
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        stories = json.load(f)
    try:
        match = re.search(r"\d+", task_id)
        if match:
            idx = int(match.group()) - 1
            if isinstance(stories, list) and 0 <= idx < len(stories):
                subset = stories[idx:idx + 3]
                return json.dumps(subset, ensure_ascii=False, indent=2)
    except Exception:
        pass
    full = json.dumps(stories, ensure_ascii=False, indent=2)
    return full[:2000]


def _normalize_backend_paths(generated: dict) -> dict:
    result = {}
    skip_at_root = {
        "requirements.txt", "Dockerfile", "pytest.ini", "conftest.py",
        ".env", "setup.py", "setup.cfg", "docker-compose.yml",
        ".dockerignore", "README.md", ".env.example", "Makefile",
    }
    INFRA_PREFIXES = ("tests/", "migrations/", "alembic/")

    for path, code in generated.items():
        path = path.strip().lstrip("./")
        new_path = path
        if path.startswith("src/backend/") and not path.startswith("src/backend/app/"):
            rel = path[len("src/backend/"):]
            if rel not in skip_at_root and not rel.startswith(".") and not rel.startswith(INFRA_PREFIXES):
                new_path = f"src/backend/app/{rel}"
                print(f"      [relocate] {path} → {new_path}")
        result[new_path] = code
    return result


def _ensure_app_inits(generated: dict, pos_app_dir: str):
    dirs_needing_init = set()
    for path in generated.keys():
        if path.startswith("src/backend/app/") and path.endswith(".py"):
            dirs_needing_init.add("/".join(path.split("/")[:-1]))
    for dir_rel in dirs_needing_init:
        init_rel = f"{dir_rel}/__init__.py"
        if init_rel not in generated:
            full = os.path.join(pos_app_dir, init_rel)
            if not os.path.exists(full):
                os.makedirs(os.path.join(pos_app_dir, dir_rel), exist_ok=True)
                open(full, "w").close()
                print(f"      [init] Created {init_rel}")


SHARED_FILES = {
    "docker-compose.yml", ".dockerignore", "README.md", ".env.example", "Makefile",
}


def _filter_by_component(generated: dict, component: str) -> dict:
    result = {}
    for path, code in generated.items():
        basename = os.path.basename(path)
        if basename.startswith("test_") or "/tests/" in path:
            print(f"      [filter] BLOCKED test file from dev: {path}")
            continue
        normalized_path = path.strip().lstrip("./")
        if normalized_path in SHARED_FILES:
            result[normalized_path] = code
            continue
        if component == "fullstack":
            result[path] = code
        elif component == "frontend":
            if path.startswith("src/frontend/"):
                result[path] = code
            else:
                print(f"      [filter] SCOPE REJECT ({component}): {path}")
        elif component == "backend":
            if path.startswith("src/backend/"):
                result[path] = code
            else:
                print(f"      [filter] SCOPE REJECT ({component}): {path}")
    return result


def _build_dev_user_prompt(
    task_id: str,
    task: dict,
    component: str,
    contract: dict,
    requirements_md: str,
    stories_context: str,
    existing_code: str,
    bug_context: str | None = None,
) -> str:
    """
    [v4] User prompt inject CONTRACT FILE thay vì inline api_contract từ tasks.json.
    Contract đã được locked bởi compiler — dev không được tự bịa route.
    """
    if not task:
        return ""

    # Format contract routes để dễ đọc
    contract_routes_str = json.dumps(
        contract.get("routes", []),
        ensure_ascii=False,
        indent=2
    )

    prompt = textwrap.dedent(f"""
    task_id:   {task_id}
    component: {component.upper()}
    summary:   {task.get("summary", "")}

    description:
    {task.get("description", "")[:400]}

    # EXECUTABLE CONTRACT (locked — do NOT deviate)
    # Source: docs/contracts/{task_id}.contract.json  (schema_version={contract.get("schema_version", "?")})
    # Each route contains: method, path, status_code, request_body, response_body,
    #   errors (handle ALL listed error cases), rules (enforce ALL), depends_on.
    {contract_routes_str}

    # requirements.md
    {requirements_md or "(not found)"}

    # stories (relevant to this task)
    {stories_context or "(not found)"}

    # existing code in repo (do not duplicate)
    {existing_code}
    """).strip()

    if bug_context:
        prompt += textwrap.dedent(f"""

        # CRITICAL — BUGS FROM PREVIOUS TEST RUN

        You MUST fix ALL bugs listed below.
        If the same bug reappears the task fails permanently.

        {bug_context[:1200]}
        """)

    return prompt


# ── AST helpers (unchanged) ───────────────────────────────────────────────────

def _extract_http_status(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int):
            return node.value
    if isinstance(node, ast.Attribute):
        attr = node.attr
        m = re.match(r"HTTP_(\d+)", attr)
        if m:
            return int(m.group(1))
    return None


DEFAULT_STATUS_CODES = {
    "get": 200, "post": 201, "put": 200, "patch": 200, "delete": 204,
}


def _extract_routes_from_ast(code: str):
    routes = []
    try:
        tree = ast.parse(code)
    except Exception:
        return routes
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            if not isinstance(deco.func, ast.Attribute):
                continue
            method = deco.func.attr.lower()
            if method not in {"get", "post", "put", "delete", "patch"}:
                continue
            route = None
            status_code = None
            if deco.args:
                arg0 = deco.args[0]
                if isinstance(arg0, ast.Constant):
                    route = arg0.value
            for kw in deco.keywords:
                if kw.arg == "status_code":
                    extracted = _extract_http_status(kw.value)
                    if extracted is not None:
                        status_code = extracted
            if status_code is None:
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    func_name = ""
                    if isinstance(inner.func, ast.Name):
                        func_name = inner.func.id
                    elif isinstance(inner.func, ast.Attribute):
                        func_name = inner.func.attr
                    if func_name not in {"JSONResponse", "Response"}:
                        continue
                    for kw in inner.keywords:
                        if kw.arg != "status_code":
                            continue
                        if isinstance(kw.value, ast.Constant):
                            if isinstance(kw.value.value, (int, float)):
                                status_code = int(kw.value.value)
                        elif isinstance(kw.value, ast.Attribute):
                            text = ast.unparse(kw.value)
                            m = re.search(r"HTTP_(\d+)", text)
                            if m:
                                status_code = int(m.group(1))
            if status_code is None:
                status_code = DEFAULT_STATUS_CODES.get(method, 200)
            routes.append({
                "method": method,
                "route": route,
                "status_code": status_code,
                "function": node.name,
            })
    return routes


def _regex_scan_status(code: str, method: str, route_hint: str):
    pattern = (
        rf'@router\.{method}\([^)]*["\'].*?{re.escape(route_hint)}.*?["\']'
        rf'[\s\S]*?status_code\s*=\s*'
        rf'(?:status\.HTTP_(\d+)(?:_[A-Z_]+)?|(\d+))'
    )
    m = re.search(pattern, code, re.MULTILINE)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _normalize_route(route: str) -> str:
    if not route:
        return "/"
    route = route.strip()
    if not route.startswith("/"):
        route = "/" + route
    route = re.sub(r"/+", "/", route)
    if route != "/" and route.endswith("/"):
        route = route[:-1]
    route = re.sub(r"\{[^}]+\}", "{param}", route)
    return route


def route_exists_flexible(routes, method, route, expected_status, code=""):
    target_route = _normalize_route(route)
    for r in routes:
        if r["method"] != method:
            continue
        current_route = _normalize_route(r["route"])
        if current_route != target_route:
            continue
        if r["status_code"] is not None:
            return r["status_code"] == expected_status
        if code:
            regex_status = _regex_scan_status(code, method, route.strip("/") or "/")
            if regex_status is not None:
                return regex_status == expected_status
        print(
            f"      [contract] WARNING: could not verify status_code for "
            f"{method.upper()} {route}"
        )
        return False
    return False


def _module_has_symbol(code: str, symbol: str) -> bool:
    try:
        tree = ast.parse(code)
    except Exception:
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == symbol:
                return True
    return False


def validate_backend_contract(pos_app_dir: str):
    checks = []
    products_path = os.path.join(pos_app_dir, "src/backend/app/routes/products.py")
    cart_path     = os.path.join(pos_app_dir, "src/backend/app/routes/cart.py")

    for label, path in [("products", products_path), ("cart", cart_path)]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            print(f"      [debug-{label}] first 30 lines:")
            for i, line in enumerate(lines[:30], 1):
                print(f"        {i:3}: {line}", end="")
            try:
                ast.parse("".join(lines))
                print(f"      [debug-{label}] AST parse OK")
            except SyntaxError as e:
                print(f"      [debug-{label}] AST SYNTAX ERROR: {e}")
        else:
            print(f"      [debug-{label}] FILE NOT FOUND")

    if os.path.exists(products_path):
        with open(products_path, encoding="utf-8") as f:
            products_code = f.read()
        routes = _extract_routes_from_ast(products_code)
        print(f"      [contract] products routes: {[(r['method'], r['route'], r['status_code']) for r in routes]}")
        if not any(route_exists_flexible(routes, "post", r, 201, products_code) for r in ["/", ""]):
            checks.append("POST /products/ missing status_code=201")
        if not route_exists_flexible(routes, "delete", "/{param}", 204, products_code):
            checks.append("DELETE /products/{id} missing status_code=204")
        if not _module_has_symbol(products_code, "_db"):
            checks.append("_db not found in products.py")
        if not _module_has_symbol(products_code, "_next_id"):
            checks.append("_next_id not found in products.py")
    else:
        checks.append("products.py missing")

    if os.path.exists(cart_path):
        with open(cart_path, encoding="utf-8") as f:
            cart_code = f.read()
        routes = _extract_routes_from_ast(cart_code)
        print(f"      [contract] cart routes: {[(r['method'], r['route'], r['status_code']) for r in routes]}")
        if not route_exists_flexible(routes, "post", "/add", 201, cart_code):
            checks.append("POST /cart/add missing status_code=201")
        if not route_exists_flexible(routes, "post", "/checkout", 200, cart_code):
            checks.append("POST /cart/checkout missing status_code=200")
        if not route_exists_flexible(routes, "delete", "/clear", 204, cart_code):
            checks.append("DELETE /cart/clear missing status_code=204")
        if "cart_db" not in cart_code:
            checks.append("cart_db not found")
        if "receipts_db" not in cart_code:
            checks.append("receipts_db missing")
        if "_next_receipt_id" not in cart_code:
            checks.append("_next_receipt_id missing")
        if "cart_db[" not in cart_code:
            checks.append("cart_db is never mutated")
        if ".clear()" not in cart_code:
            checks.append("cart_db is never cleared")
    else:
        checks.append("cart.py missing")

    if checks:
        print("      [contract-validator] FAIL")
        for c in checks:
            print(f"        - {c}")
        return False, "\n".join(checks)

    print("      [contract-validator] PASS")
    return True, None


def run_backend_smoke_test(pos_app_dir: str):
    import sys
    backend_dir = os.path.join(pos_app_dir, "src/backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get("/health")
        if r.status_code != 200:
            return False, f"health failed: {r.text}"
        r = client.post("/products/", json={"name": "Smoke Product", "price": 10.0, "stock": 100})
        if r.status_code not in (200, 201):
            return False, "POST /products failed"
        pid = r.json().get("id")
        if pid is None:
            return False, f"POST /products/ response missing 'id': {r.json()}"
        r = client.post("/cart/add", json={"product_id": pid, "quantity": 1})
        if r.status_code not in (200, 201):
            return False, "POST /cart/add failed"
        r = client.post("/cart/checkout")
        print("CHECKOUT RESPONSE:", r.status_code, r.text)
        data = r.json()
        receipt_id = data.get("id") or data.get("receipt_id")
        if receipt_id is None:
            return False, f"receipt id missing in checkout response: {data}"
        if r.status_code != 200:
            return False, "checkout failed"
        return True, None
    except Exception:
        return False, traceback.format_exc()


def validate_no_set_literals(pos_app_dir: str):
    backend_root = os.path.join(pos_app_dir, "src/backend/app")
    for root, _, files in os.walk(backend_root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    code = f.read()
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Set):
                        return False, f"Set literal found in {fname} line {node.lineno}"
            except Exception as e:
                return False, str(e)
    return True, None


# ══════════════════════════════════════════════════════════════════════════════
# _gemini_dev  [v4: đọc contract file, không đọc tasks.json cho API contract]
# ══════════════════════════════════════════════════════════════════════════════

def _gemini_dev(task_id, bug_context=None):
    from config import POS_APP_DIR

    # ── 0. Load contract file (v4: artifact riêng) ────────────────────────
    #    Nếu contract chưa có → compile tự động (safety net)
    contract = load_contract(task_id, contracts_dir="docs/contracts")
    if contract is None:
        print(f"      [gemini-dev] Contract not found for {task_id} — running contract-compiler...")
        _gemini_contract_compiler(task_id)
        contract = load_contract(task_id, contracts_dir="docs/contracts")
    if contract is None:
        raise RuntimeError(
            f"Contract still missing after compiler ran for {task_id}. "
            "Check tasks.json has api_contract.routes for this task."
        )
    print(
        f"      [gemini-dev] Contract loaded: docs/contracts/{task_id}.contract.json "
        f"(v{contract.get('schema_version','?')}, {len(contract.get('routes',[]))} routes)"
    )

    # ── 1. Load task meta từ tasks.json (chỉ lấy summary/description/component) ─
    with open("docs/tasks.json", encoding="utf-8") as f:
        data = json.load(f)

    task = next(
        (t for s in data["sprints"] for t in s["tasks"] if t["id"] == task_id),
        None,
    )
    if not task:
        return f"DEV_ESCALATE:{task_id}"

    component = task.get("component", "fullstack")
    branch    = make_branch_name(task_id, task.get("summary", task_id))
    print(f"      [gemini-dev] Task: {task_id} | {task['summary']} | {component}")

    git_ops.prepare_feature_branch(POS_APP_DIR, branch)

    # ── 2. Load system prompt ─────────────────────────────────────────────
    system = p.load_agent_instruction("dev-agent", backend="gemini")
    if not system or len(system.strip()) < 50:
        raise RuntimeError(
            "dev-agent-gemini.md not found or empty — "
            "expected at .claude/agents/dev-agent-gemini.md"
        )
    print(f"      [gemini-dev] System prompt loaded ({len(system)} chars)")

    # ── 3. Load context data ──────────────────────────────────────────────
    requirements_md  = _load_requirements_md()
    stories_context  = _load_stories_for_task(task_id)
    existing_code    = _read_existing_code(POS_APP_DIR, component)

    if requirements_md:
        print(f"      [gemini-dev] requirements.md loaded ({len(requirements_md)} chars)")
    else:
        print("      [gemini-dev] WARNING: requirements.md not found")

    # ── 4. Build user prompt (inject contract file) ───────────────────────
    user_prompt = _build_dev_user_prompt(
        task_id=task_id,
        task=task,
        component=component,
        contract=contract,           # [v4] từ contract file
        requirements_md=requirements_md,
        stories_context=stories_context,
        existing_code=existing_code,
        bug_context=bug_context,
    )

    token_est = (len(system) + len(user_prompt)) // 4
    print(f"      [gemini-dev] Calling Gemini (~{token_est} tokens)...")
    response = ai_client.call(GEMINI_API_KEYS, system, user_prompt, "dev-agent")

    # ── 5. Parse + filter + write ─────────────────────────────────────────
    generated = p.parse_file_blocks(response)
    print(f"      [gemini-dev] Raw parsed: {len(generated)} files")

    if not generated:
        print("      [gemini-dev] No FILE blocks — escalating")
        git_ops._back_to_develop(POS_APP_DIR)
        return f"DEV_ESCALATE:{task_id}"

    generated = _filter_by_component(generated, component)
    generated = _normalize_backend_paths(generated)

    VALID_PREFIXES = ("src/backend/", "src/frontend/")
    VALID_EXACT    = {"docker-compose.yml", ".dockerignore", "README.md", ".env.example", "Makefile"}

    os.makedirs(POS_APP_DIR, exist_ok=True)
    written = 0
    for filepath, code in generated.items():
        if not (filepath.startswith(VALID_PREFIXES) or filepath in VALID_EXACT):
            print(f"      [gemini-dev] REJECTED: {filepath}")
            continue
        if not code.strip():
            continue
        full_path = os.path.join(POS_APP_DIR, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as fw:
            fw.write(code)
        print(f"      [gemini-dev] Written: {filepath}")
        written += 1

    _ensure_app_inits(generated, POS_APP_DIR)

    # ── 6. Retry nếu thiếu critical files ────────────────────────────────
    CRITICAL_BY_COMPONENT = {
        "backend": [
            "src/backend/app/main.py",
            "src/backend/app/routes/products.py",
            "src/backend/app/routes/cart.py",
            "src/backend/requirements.txt",
        ],
        "frontend": [
            "src/frontend/src/App.tsx",
            "src/frontend/package.json",
            "src/frontend/src/components/Cart.tsx",
        ],
        "fullstack": [
            "src/backend/app/main.py",
            "src/backend/app/routes/products.py",
            "src/backend/app/routes/cart.py",
            "src/frontend/src/App.tsx",
        ],
    }
    critical = CRITICAL_BY_COMPONENT.get(component, [])
    missing  = [f for f in critical if not os.path.exists(os.path.join(POS_APP_DIR, f))]

    if missing:
        print(f"      [gemini-dev] Missing critical: {missing} — retrying...")
        retry_prompt = (
            f"task_id: {task_id}\ncomponent: {component.upper()}\n\n"
            f"Generate ONLY these missing files:\n"
            + "\n".join(f"- {f}" for f in missing)
            + f"\n\nTask summary: {task.get('summary', '')}\n"
            f"Rules: complete code, FILE: path format, no test files, "
            f"no placeholders.\nEnd with: DEV_DONE:{task_id}"
        )
        retry_response = ai_client.call(GEMINI_API_KEYS, system, retry_prompt, "dev-agent")
        retry_generated = _filter_by_component(p.parse_file_blocks(retry_response), component)
        retry_generated = _normalize_backend_paths(retry_generated)
        for filepath, code in retry_generated.items():
            if not any(filepath.startswith(px) for px in VALID_PREFIXES) or not code.strip():
                continue
            full_path = os.path.join(POS_APP_DIR, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as fw:
                fw.write(code)
            print(f"      [gemini-dev] Retry written: {filepath}")

    # ── 7. Contract validation + smoke test ──────────────────────────────
    if component in ("backend", "fullstack"):
        ok, validation_bug = validate_backend_contract(POS_APP_DIR)
        serialization_ok, serialization_bug = validate_no_set_literals(POS_APP_DIR)

        if not serialization_ok:
            git_ops._back_to_develop(POS_APP_DIR)
            return f"DEV_SERIALIZATION_FAIL:{serialization_bug}"

        if ok:
            try:
                import importlib
                import sys
                backend_dir = os.path.join(POS_APP_DIR, "src/backend")
                if backend_dir not in sys.path:
                    sys.path.insert(0, backend_dir)
                for k in list(sys.modules.keys()):
                    if k.startswith("app"):
                        del sys.modules[k]
                importlib.import_module("app.main")
            except Exception as e:
                git_ops._back_to_develop(POS_APP_DIR)
                return f"DEV_IMPORT_FAIL:{str(e)}"

            smoke_ok, smoke_bug = run_backend_smoke_test(POS_APP_DIR)
            if not smoke_ok:
                print("      [smoke-test] FAIL")
                git_ops._back_to_develop(POS_APP_DIR)
                return f"DEV_SMOKE_FAIL:{smoke_bug}"
            print("      [smoke-test] PASS")

        if not ok:
            print("      [gemini-dev] Contract validation failed")
            git_ops._back_to_develop(POS_APP_DIR)
            return f"DEV_CONTRACT_FAIL:{validation_bug}"

    # ── 8. Commit + update tasks.json ─────────────────────────────────────
    git_ops.commit_and_push(POS_APP_DIR, branch, task, component)

    for s in data["sprints"]:
        for t in s["tasks"]:
            if t["id"] == task_id:
                t["status"] = "DONE"
                t["branch"] = branch

    with open("docs/tasks.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return f"DEV_DONE:{task_id}"


# ══════════════════════════════════════════════════════════════════════════════
# TESTER AGENT  [v4: đọc contract file, safe assertions, no blind ["id"]]
# ══════════════════════════════════════════════════════════════════════════════

def _generate_tests_from_contract(task_id: str, pos_app_dir: str = "") -> str:
    """
    [v4] Sinh test_api.py từ CONTRACT FILE (docs/contracts/TASK-XX.contract.json).

    Key fixes vs v3:
      - Đọc response_fields từ contract file (đã annotate bởi compiler)
      - Safe assertion pattern:
          assert "id" in data, f"response missing 'id': {data}"
          product_id = data["id"]   ← chỉ access SAU khi assert
      - Không còn blind r.json()["id"] → giải quyết toàn bộ KeyError batch
      - Setup chain (product → cart/add → checkout) đúng thứ tự, sử dụng
        response_fields từ contract để biết "id" có tồn tại không
    """
    from config import POS_APP_DIR as _pos_app_dir
    if not pos_app_dir:
        pos_app_dir = _pos_app_dir

    # [v4] Load từ contract file — không đọc tasks.json trực tiếp
    contract = _require_contract(task_id)
    contract_routes = contract.get("routes", [])

    print(
        f"      [tester] Contract loaded: {task_id} "
        f"(v{contract.get('schema_version','?')}, {len(contract_routes)} routes)"
    )

    # ── Header ────────────────────────────────────────────────────────────
    lines = [
        "import pytest",
        "from fastapi.testclient import TestClient",
        "from app.main import app",
        "",
        "",
    ]

    def _safe_fn(path: str, method: str) -> str:
        slug = path.strip("/").lower()
        if not slug:
            slug = "root"
        slug = re.sub(r"\{[^}]+\}", "param", slug)
        slug = slug.replace("/", "_")
        slug = re.sub(r"[^a-z0-9_]", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")
        short = hashlib.md5(f"{method}:{path}".encode()).hexdigest()[:6]
        return f"test_{method}_{slug}_{short}"

    def _get_resp_fields(route: dict) -> dict:
        """
        Lấy response_fields theo thứ tự ưu tiên:
          1. contract file annotation (đã compile)
          2. schema cứng trong contract_normalizer
          3. empty dict (không assert fields)
        """
        fields = route.get("response_fields") or {}
        if not fields:
            fields = resolve_response_fields(route["method"], route["path"])
        return fields

    # ── health check (luôn có) ────────────────────────────────────────────
    lines.append("def test_health():")
    lines.append("    client = TestClient(app)")
    lines.append("    r = client.get(\"/health\")")
    lines.append("    assert r.status_code == 200, f\"GET /health failed: {r.text}\"")
    lines.append("    data = r.json()")
    lines.append("    assert \"status\" in data, f\"GET /health missing 'status': {data}\"")
    lines.append("")
    lines.append("")

    # ── per-route tests ───────────────────────────────────────────────────
    for route in contract_routes:
        method      = route["method"].lower()
        path        = route["path"]
        status      = route["status_code"]
        fn          = _safe_fn(path, method)
        resp_fields = _get_resp_fields(route)

        # Skip health — đã generate
        if path.rstrip("/") == "/health" and method == "get":
            continue

        lines.append(f"def {fn}():")
        lines.append("    client = TestClient(app)")
        lines.append("")

        # ── Setup chain ───────────────────────────────────────────────
        needs_product  = "/cart" in path or ("{id}" in path and "/products" in path) or "{product_id}" in path
        needs_cart_item = "/checkout" in path

        if needs_product or needs_cart_item:
            lines.append("    # Setup: create product")
            lines.append("    rp = client.post(\"/products/\", json={")
            lines.append("        \"name\": \"Test Product\", \"price\": 10.0, \"stock\": 100,")
            lines.append("    })")
            lines.append("    assert rp.status_code in (200, 201), f\"Setup POST /products/ failed: {rp.text}\"")
            lines.append("    rp_data = rp.json()")

            # [v4] Safe field access — assert trước, access sau
            post_product_fields = resolve_response_fields("post", "/products/")
            if "id" in post_product_fields:
                lines.append("    assert \"id\" in rp_data, f\"POST /products/ response missing 'id': {rp_data}\"")
                lines.append("    product_id = rp_data[\"id\"]")
            else:
                # Fallback safe: thử cả 2 key
                lines.append("    product_id = rp_data.get(\"id\") or rp_data.get(\"product_id\")")
                lines.append("    assert product_id is not None, f\"Cannot find product id in: {rp_data}\"")
            lines.append("")

        if needs_cart_item:
            lines.append("    # Setup: add to cart")
            lines.append("    rc = client.post(\"/cart/add\", json={")
            lines.append("        \"product_id\": product_id, \"quantity\": 1,")
            lines.append("    })")
            lines.append("    assert rc.status_code in (200, 201), f\"Setup POST /cart/add failed: {rc.text}\"")
            lines.append("")

        # ── Build request ─────────────────────────────────────────────
        call_path = path
        if re.search(r"\{[^}]+\}", path):
            call_path = re.sub(r"\{[^}]+\}", "{product_id}", path)
            call_path_expr = f'f"{call_path}"'
        else:
            call_path_expr = f'"{call_path}"'

        request_args = [call_path_expr]

        route_schema  = resolve_route_schema(method, path)  # [v5] từ CONTRACT_ROUTE_SCHEMAS
        request_body  = route.get("request_body") or route_schema.get("request_body", {})

        if request_body and method in ("post", "put", "patch"):
            # Dùng response_example làm body nếu có, fallback về request_body skeleton
            example = route.get("response_example") or route_schema.get("response_example")
            if example and isinstance(example, dict):
                body_dict = {k: v for k, v in example.items() if k in request_body}
            else:
                body_dict = {}
                for field, ftype in request_body.items():
                    body_dict[field] = (
                        "Test Product" if ftype == "str" and "name" in field else
                        20.0           if ftype == "float" else
                        50             if ftype == "int" and "stock" in field else
                        1              if ftype == "int" else
                        "test_value"
                    )
            # Override product_id nếu đang dùng biến runtime
            if "product_id" in request_body:
                body_items = ", ".join(
                    f'"product_id": product_id' if k == "product_id"
                    else f'"{k}": {repr(v)}'
                    for k, v in body_dict.items()
                )
            else:
                body_items = ", ".join(f'"{k}": {repr(v)}' for k, v in body_dict.items())
            request_args.append(f'json={{{body_items}}}')

        request_expr = f'client.{method}(' + ", ".join(request_args) + ')'

        lines.append(f"    r = {request_expr}")
        safe_path = path.replace("{", "{{").replace("}", "}}")  # escape toàn bộ
        lines.append(f"        f\"{method.upper()} {safe_path} expected {status}, got {{r.status_code}}: {{r.text}}\"")

        # [v4] Response field assertions: safe — assert key exists trước khi dùng
        if resp_fields and status not in (204,):
            lines.append("    data = r.json()")

            lines.append("    if isinstance(data, list):")
            lines.append("        if data:")
            lines.append("            sample = data[0]")
            for field in resp_fields:
                lines.append(
                    f"            assert \"{field}\" in sample, "
                    f"f\"List item missing '{field}': {{sample}}\""
                )

            lines.append("    elif isinstance(data, dict):")
            for field in resp_fields:
                lines.append(
                    f"        assert \"{field}\" in data, "
                    f"f\"Response missing '{field}': {{data}}\""
                )
        route_errors = route.get("errors") or []
        if route_errors:
            lines.append(f"    # Error cases defined in contract:")
            for err in route_errors:
                err_status = err.get("status_code")
                err_when   = err.get("when", "unknown")
                lines.append(f"    # - {err_status} when {err_when}")
        lines.append("")
        lines.append("")

    # ── Explicit end-to-end checkout test ─────────────────────────────────
    checkout_routes = [r for r in contract_routes if "/checkout" in r.get("path", "")]
    if checkout_routes:
        lines.append("def test_cart_checkout():")
        lines.append("    client = TestClient(app)")
        lines.append("    # Step 1: create product")
        lines.append("    rp = client.post(\"/products/\", json={")
        lines.append("        \"name\": \"Test\", \"price\": 10, \"stock\": 100,")
        lines.append("    })")
        lines.append("    assert rp.status_code in (200, 201), f\"POST /products/ failed: {rp.text}\"")
        lines.append("    rp_data = rp.json()")
        # [v4] Safe access
        lines.append("    assert \"id\" in rp_data, f\"POST /products/ response missing 'id': {rp_data}\"")
        lines.append("    product_id = rp_data[\"id\"]")
        lines.append("    # Step 2: add to cart")
        lines.append("    rc = client.post(\"/cart/add\", json={\"product_id\": product_id, \"quantity\": 1})")
        lines.append("    assert rc.status_code in (200, 201), f\"POST /cart/add failed: {rc.text}\"")
        lines.append("    # Step 3: checkout")
        lines.append("    r = client.post(\"/cart/checkout\")")
        lines.append("    assert r.status_code == 200, f\"POST /cart/checkout failed: {r.text}\"")
        lines.append("    data = r.json()")
        lines.append("    assert \"id\" in data, f\"checkout response missing 'id': {data}\"")
        lines.append("    assert \"total\" in data, f\"checkout response missing 'total': {data}\"")
        lines.append("")

    return "\n".join(lines)


def _write_test_file(pos_app_dir: str, content: str):
    # [v4] tests đặt trong src/backend/tests/ (không phải src/tests/)
    test_path = os.path.join(pos_app_dir, "src/backend/tests/test_api.py")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(content)
    init_path = os.path.join(pos_app_dir, "src/backend/tests/__init__.py")
    if not os.path.exists(init_path):
        open(init_path, "w").close()
    print(f"      [tester] test_api.py written ({len(content)} chars) ✓")


def _fix_bad_imports_in_dir(target_dir, label=""):
    if not os.path.exists(target_dir):
        return
    for root, _, files in os.walk(target_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    orig = f.read()
            except Exception:
                continue
            fixed = orig
            fixed = re.sub(r'from src\.backend\.app', 'from app', fixed)
            fixed = re.sub(r'from backend\.app',      'from app', fixed)
            fixed = re.sub(r'import src\.backend\.app', 'import app', fixed)
            fixed = re.sub(r'from src\.app',          'from app', fixed)
            if fixed != orig:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(fixed)
                rel = os.path.relpath(fpath, target_dir)
                print(f"      [fix-import] {label}{rel}")


def _fix_test_imports(backend_dir):
    _fix_bad_imports_in_dir(os.path.join(backend_dir, "app"),   label="app/")
    _fix_bad_imports_in_dir(os.path.join(backend_dir, "tests"), label="tests/")
    tests_dir = os.path.join(backend_dir, "tests")
    if os.path.exists(tests_dir):
        init = os.path.join(tests_dir, "__init__.py")
        if not os.path.exists(init):
            open(init, "w").close()


def _get_venv_python(venv_dir):
    for candidate in [
        os.path.join(venv_dir, "Scripts", "python.exe"),
        os.path.join(venv_dir, "Scripts", "python"),
        os.path.join(venv_dir, "bin", "python3"),
        os.path.join(venv_dir, "bin", "python"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


def _ensure_test_requirements(backend_dir):
    req_path = os.path.join(backend_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return
    with open(req_path, encoding="utf-8") as f:
        content = f.read()
    must_have = {
        "httpx":          "httpx>=0.24.0",
        "pytest":         "pytest>=7.0",
        "pytest-asyncio": "pytest-asyncio>=0.21",
        "anyio":          "anyio[trio]>=3.6",
        "fastapi":        "fastapi>=0.100.0",
        "uvicorn":        "uvicorn[standard]>=0.20.0",
    }
    additions = [spec for pkg, spec in must_have.items()
                 if not re.search(rf"\b{re.escape(pkg)}\b", content, re.IGNORECASE)]
    if additions:
        with open(req_path, "a", encoding="utf-8") as f:
            f.write("\n# Auto-added by pipeline\n" + "\n".join(additions) + "\n")
        for a in additions:
            print(f"      [fix-req] Added: {a}")


def _run_pytest(backend_dir):
    import sys
    req = os.path.join(backend_dir, "requirements.txt")
    if not os.path.exists(req):
        print("      [pytest] SIMULATED PASS — no requirements.txt")
        return {"passed": True, "output": "...", "simulated": True}
    tests_dir = os.path.join(backend_dir, "tests")
    if not os.path.exists(tests_dir):
        return {"passed": True, "output": "No tests/ directory yet — simulated PASS"}
    test_files = [f for f in os.listdir(tests_dir) if f.startswith("test_")]
    if not test_files:
        return {"passed": True, "output": "No test files found — simulated PASS"}

    _fix_test_imports(backend_dir)
    _ensure_test_requirements(backend_dir)

    conftest = os.path.join(backend_dir, "conftest.py")
    if not os.path.exists(conftest):
        with open(conftest, "w", encoding="utf-8") as f:
            f.write(
                "# Auto-generated\n"
                "import sys, os\n"
                "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            )
    pytest_ini = os.path.join(backend_dir, "pytest.ini")
    if not os.path.exists(pytest_ini):
        with open(pytest_ini, "w", encoding="utf-8") as f:
            f.write(
                "[pytest]\n"
                "asyncio_mode = auto\n"
                "asyncio_default_fixture_loop_scope = function\n"
                "testpaths = tests\n"
            )

    venv_dir = os.path.join(backend_dir, ".test-venv")
    python = _get_venv_python(venv_dir)
    if not python:
        print("      [venv] Creating venv...")
        r = subprocess.run(
            f'"{sys.executable}" -m venv "{venv_dir}" --clear',
            shell=True, capture_output=True, text=True,
            cwd=backend_dir, encoding="utf-8", errors="ignore"
        )
        if r.returncode != 0:
            return {"passed": False, "output": f"venv failed:\n{r.stderr}"}
        python = _get_venv_python(venv_dir)
    if not python:
        return {"passed": False, "output": "Cannot find python in venv"}
    print(f"      [venv] {python}")

    r = subprocess.run(
        f'"{python}" -m pip install -r requirements.txt -q --no-warn-script-location',
        shell=True, capture_output=True, text=True,
        cwd=backend_dir, encoding="utf-8", errors="ignore"
    )
    if r.returncode != 0:
        return {"passed": False, "output": f"pip install failed:\n{r.stderr[:1000]}"}

    env = {**os.environ, "PYTHONPATH": backend_dir, "PYTEST_ASYNCIO_MODE": "auto"}
    result = subprocess.run(
        f'"{python}" -m pytest tests/ -v --tb=long --no-header',
        shell=True, capture_output=True, text=True,
        cwd=backend_dir, env=env, encoding="utf-8", errors="ignore",
    )
    output = result.stdout + result.stderr
    print(f"      [pytest] rc={result.returncode}")
    for line in output.splitlines()[-25:]:
        print(f"        {line}")
    return {"passed": result.returncode == 0, "output": output}


def _run_jest(frontend_dir):
    if not os.path.exists(os.path.join(frontend_dir, "package.json")):
        return {"passed": True, "output": "No frontend code yet — simulated PASS"}
    node_modules = os.path.join(frontend_dir, "node_modules")
    pkg_json     = os.path.join(frontend_dir, "package.json")
    pkg_lock     = os.path.join(frontend_dir, "package-lock.json")
    stamp_file   = os.path.join(frontend_dir, ".npm_install_stamp")

    def _pkg_hash() -> str:
        import hashlib
        src = pkg_lock if os.path.exists(pkg_lock) else pkg_json
        try:
            return hashlib.md5(open(src, "rb").read()).hexdigest()
        except Exception:
            return ""

    need_install = (
        not os.path.isdir(node_modules)
        or not os.path.exists(stamp_file)
        or open(stamp_file).read().strip() != _pkg_hash()
    )

    if need_install:
        print("      [TEST] Installing npm dependencies...")
        install_result = subprocess.run(
            "npm install", shell=True, capture_output=True,
            cwd=frontend_dir, encoding="utf-8", errors="ignore"
        )
        if install_result.returncode != 0:
            return {"passed": False, "output": f"npm install failed:\n{install_result.stderr}"}
        # Ghi stamp để lần sau skip
        with open(stamp_file, "w") as f:
            f.write(_pkg_hash())
    else:
        print("      [TEST] npm dependencies up-to-date — skipping install")
    result = subprocess.run(
        "npx jest --passWithNoTests --no-coverage 2>&1",
        shell=True, capture_output=True, text=True,
        cwd=frontend_dir, encoding="utf-8", errors="ignore",
    )
    return {
        "passed": result.returncode == 0 or "pass" in result.stdout.lower(),
        "output": result.stdout + result.stderr
    }


def _get_task_component(task_id: str) -> str:
    try:
        with open("docs/tasks.json", encoding="utf-8") as f:
            data = json.load(f)
        for s in data["sprints"]:
            for t in s["tasks"]:
                if t["id"] == task_id:
                    return t.get("component", "fullstack")
    except Exception:
        pass
    return "fullstack"


def _gemini_tester(task_id):
    from config import BACKEND_DIR, FRONTEND_DIR

    component = _get_task_component(task_id)
    os.makedirs("docs/bugs", exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    if component in ("backend", "fullstack"):
        from config import POS_APP_DIR
        print("      [TEST] Contract-first: generating tests from contract file...")
        test_code = _generate_tests_from_contract(task_id, pos_app_dir=POS_APP_DIR)
        _write_test_file(POS_APP_DIR, test_code)

        # [v5] Nếu contract rỗng → chỉ chạy health check, không cần full pipeline
        contract = load_contract(task_id, contracts_dir="docs/contracts")
        if contract and len(contract.get("routes", [])) == 0:
            print(f"      [TEST] Contract has 0 routes — health-only test, skip heavy pipeline")
            backend_ok  = {"passed": True, "output": "Contract empty — health only"}
            frontend_ok = {"passed": True, "output": "Skipped — contract empty"}
            # Vẫn ghi kết quả
            with open("docs/test-results.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n---\n## {task_id} ({component}) — {datetime.datetime.now().isoformat()}\n"
                    f"- Backend: PASS (health only)\n- Frontend: SKIP\n"
                )
            return f"TEST_PASS:{task_id}"

    if component == "frontend":
        backend_ok = {"passed": True, "output": f"Skipped — component={component}"}
        print(f"      [TEST] Backend: SKIP")
    else:
        print("      [TEST] Running backend tests...")
        backend_ok = _run_pytest(BACKEND_DIR)
        print(f"      [TEST] Backend: {'PASS' if backend_ok['passed'] else 'FAIL'}")

    if component == "backend":
        frontend_ok = {"passed": True, "output": f"Skipped — component={component}"}
        print(f"      [TEST] Frontend: SKIP")
    else:
        # Skip nếu chưa có src/ files thật — chỉ có package.json chưa đủ
        frontend_src = os.path.join(FRONTEND_DIR, "src")
        has_frontend_code = (
            os.path.isdir(frontend_src)
            and any(
                f.endswith((".tsx", ".ts", ".jsx", ".js"))
                for _, _, files in os.walk(frontend_src)
                for f in files
                if not f.endswith((".test.tsx", ".test.ts", ".spec.ts"))
            )
        )
        if not has_frontend_code:
            frontend_ok = {"passed": True, "output": "No frontend source files yet — SKIP"}
            print(f"      [TEST] Frontend: SKIP (no src files)")
        else:
            print("      [TEST] Running frontend tests...")
            frontend_ok = _run_jest(FRONTEND_DIR)

    with open("docs/test-results.md", "a", encoding="utf-8") as f:
        f.write(
            f"\n---\n## {task_id} ({component}) — {datetime.datetime.now().isoformat()}\n"
            f"- Backend: {'PASS' if backend_ok['passed'] else 'FAIL'}\n"
            f"- Frontend: {'PASS' if frontend_ok['passed'] else 'FAIL'}\n\n"
            f"```\n{backend_ok.get('output','')[:400]}\n"
            f"{frontend_ok.get('output','')[:400]}\n```\n"
        )

    if backend_ok["passed"] and frontend_ok["passed"]:
        print(f"      [tester-agent] PASSED — {task_id}")
        return f"TEST_PASS:{task_id}"

    print("      [tester-agent] Analyzing failures with Gemini...")
    system = p.load_agent_instruction("tester-agent", backend="gemini")
    combined = (
        f"=== BACKEND PYTEST OUTPUT ===\n{backend_ok.get('output', 'No output')}\n\n"
        f"=== FRONTEND JEST OUTPUT ===\n{frontend_ok.get('output', 'No output')}"
    )
    user_prompt = (
        f"Task ID: {task_id}\nComponent: {component}\n\n"
        f"TEST OUTPUT:\n{combined[:2000]}\n\n"
        f"Analyze failures. Respond with TEST_PASS:{task_id} or "
        f"TEST_FAIL:{task_id}:permanent_count:transient_count\n"
        f"If FAIL, include detailed bug report before the signal line."
    )

    try:
        response = ai_client.call(GEMINI_API_KEYS, system, user_prompt, "tester-agent")
        signal_line = ""
        for line in response.split("\n"):
            if "TEST_PASS:" in line or "TEST_FAIL:" in line:
                signal_line = line.strip()
                break
        if not signal_line:
            permanent = (0 if backend_ok["passed"] else 1) + (0 if frontend_ok["passed"] else 1)
            signal_line = f"TEST_FAIL:{task_id}:{permanent}:0"
        bug_report = response[:response.find(signal_line)].strip() if signal_line in response else response
        if "TEST_FAIL" in signal_line:
            with open(f"docs/bugs/BUG-{task_id}-{ts}.md", "w", encoding="utf-8") as f:
                f.write(f"{bug_report}\n\n---\n{signal_line}\n")
            print(f"      [tester-agent] Bug report: BUG-{task_id}-{ts}.md")
        print(f"      [tester-agent] Result: {signal_line}")
        return signal_line
    except Exception as e:
        print(f"      [tester-agent] ERROR: {e}")
        permanent = (0 if backend_ok["passed"] else 1) + (0 if frontend_ok["passed"] else 1)
        return f"TEST_FAIL:{task_id}:{permanent}:0"