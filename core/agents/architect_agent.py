"""
architect_agent.py — patched version

ROOT CAUSE fixes áp dụng tại đây:
  - Lỗi 1: strip task_id khỏi mọi service sau khi parse
  - Lỗi 1: validate depends_on không chứa TASK-* pattern
  - Lỗi 4: strip task_id khỏi deployment node
  - Lỗi 7: kiểm tra source_dir collision giữa các backend services
  - Lỗi 8: validate frontend chỉ depends on backend tương ứng, không chain frontend-frontend
"""

import os
import json
import re
import infra.ai_client as ai_client
import contracts.parser as p
from config import GEMINI_API_KEYS
from planning.knowledge_graph_builder import load_knowledge_graph, format_for_architect


# ── Public entry point ────────────────────────────────────────────────────────

def run(prompt: str) -> str:
    return _gemini_architect(prompt)


# ── Post-processing: strip injected IDs ──────────────────────────────────────

def _strip_injected_ids(architecture: dict) -> dict:
    """
    FIX lỗi 1 & 4: Xoá task_id bị inject sẵn bởi model.

    Architect-agent.md quy định Task Materializer mới được assign ID.
    Model thường confabulate task_id vì thấy pattern trong example.
    Hàm này là safety net — chạy sau mọi parse attempt.
    """
    for svc in architecture.get("services", []):
        svc.pop("task_id", None)

    deploy = architecture.get("deployment", {})
    if isinstance(deploy, dict):
        deploy.pop("task_id", None)

    return architecture


def _validate_depends_on(architecture: dict) -> list[str]:
    """
    FIX BUG A-1 + A-2: Kiểm tra VÀ TỰ SỬA depends_on chứa TASK-* strings / self-loops.

    Vấn đề gốc: LLM sinh task_id (TASK-01..12) rồi dùng TASK-ID trong depends_on
    thay vì service name. _strip_injected_ids() xoá task_id khỏi service objects
    nhưng depends_on đã lưu TASK-ID → resolve fail hoàn toàn ở materializer.

    Fix: xây dựng position-based map TASK-0N → service name dựa vào thứ tự services
    (LLM thường gán TASK-01 cho service[0], TASK-02 cho service[1]).
    - Nếu map được → tự fix thành service name.
    - Self-loop → drop.
    - Không map được → drop và warn.

    Trả về list warning messages.
    """
    warnings = []
    services = architecture.get("services", [])
    service_names = {svc["name"] for svc in services}

    # Position-based TASK-ID → service name map
    task_id_to_name: dict[str, str] = {}
    for svc in services:
        tid = svc.get("task_id", "")
        if tid:
            task_id_to_name[tid] = svc["name"]

    for svc in services:
        original_deps = svc.get("depends_on", [])
        fixed_deps = []
        changed: list[str] = []
        unknown_deps: list[str] = []

        for dep in original_deps:
            if re.match(r"^TASK-\d+$", dep, re.IGNORECASE):
                key = dep.upper()
                resolved = task_id_to_name.get(key) or task_id_to_name.get(dep)
                if resolved and resolved != svc["name"]:
                    fixed_deps.append(resolved)
                    changed.append(f"{dep}→{resolved}")
                else:
                    changed.append(f"{dep}(dropped-unresolvable)")
            elif dep == svc["name"]:
                # [FIX A-2] Self-dep at architect layer
                changed.append(f"{dep}(self-loop, dropped)")
            elif dep not in service_names:
                unknown_deps.append(dep)
                fixed_deps.append(dep)  # keep but warn
            else:
                fixed_deps.append(dep)
        svc["depends_on"] = fixed_deps
        if changed:
            warnings.append(
                f"[FIX A-1/A-2] '{svc['name']}': depends_on fixed: {changed}"
            )
        if unknown_deps:
            warnings.append(
                f"[WARN] '{svc['name']}': depends_on references unknown services {unknown_deps}"
            )

    return warnings


def _fix_cross_service_dep_consistency(architecture: dict) -> list[str]:
    """
    FIX BUG A-4: Checkout Backend thiếu dep vào Cart Backend dù có cross_service_calls.
    FIX BUG A-5: Backend không được depend on Frontend (cross-layer).

    Logic:
    1. Với mỗi backend service có cross_service_calls → đảm bảo service đó
       nằm trong depends_on (thêm vào nếu thiếu).
    2. Loại bỏ bất kỳ dep nào mà target là frontend component.

    Trả về list messages (fix + warn).
    """
    messages = []
    services = architecture.get("services", [])

    # Build name → component map
    comp_map = {svc["name"]: svc.get("component", "") for svc in services}
    # Build cross_service_calls target name set per service
    # cross_service_calls thường là list of strings (service names) hoặc dicts
    def _extract_call_targets(calls) -> list[str]:
        targets = []
        for c in (calls or []):
            if isinstance(c, str):
                targets.append(c)
            elif isinstance(c, dict):
                # {"service": "Cart Backend"} or {"target": "...", "endpoint": "..."}
                for key in ("service", "target", "name"):
                    if key in c:
                        targets.append(c[key])
                        break
        return targets

    for svc in services:
        if svc.get("component") != "backend":
            continue

        current_deps = list(svc.get("depends_on", []))
        changed = False

        # A-4: ensure cross_service_calls targets are in depends_on
        call_targets = _extract_call_targets(svc.get("cross_service_calls", []))
        for target in call_targets:
            if target in comp_map and target not in current_deps and target != svc["name"]:
                current_deps.append(target)
                messages.append(
                    f"[FIX A-4] '{svc['name']}': added missing dep '{target}' "
                    f"(found in cross_service_calls)"
                )
                changed = True

        # A-5: remove frontend deps from backend
        clean_deps = []
        for dep in current_deps:
            dep_normalized = dep.strip()
            is_frontend = comp_map.get(dep_normalized) == "frontend"
            if not is_frontend and svc.get("component") == "backend":
                is_frontend = any(kw in dep_normalized for kw in (" UI", " Frontend", "Frontend ", "UI "))
            if is_frontend:
                messages.append(f"[FIX A-5] '{svc['name']}': removed cross-layer dep on frontend '{dep}'")
                changed = True
            else:
                clean_deps.append(dep)

        # A-6: remove self-deps   ← THÊM VÀO ĐÂY
        before = len(clean_deps)
        clean_deps = [d for d in clean_deps if d != svc["name"]]
        if len(clean_deps) < before:
            messages.append(f"[FIX A-6] '{svc['name']}': removed self-dep")
            changed = True

        if changed:
            svc["depends_on"] = clean_deps

    return messages


def _validate_source_dir_collision(architecture: dict) -> list[str]:
    """
    FIX lỗi 7: Phát hiện source_dir trùng nhau giữa backend services.

    Nếu 2 services có cùng source_dir, scaffold sẽ ghi đè lên nhau.
    """
    seen: dict[str, str] = {}
    warnings = []

    for svc in architecture.get("services", []):
        if svc.get("component") != "backend":
            continue
        sd = svc.get("source_dir", "")
        if not sd:
            continue
        if sd in seen:
            warnings.append(
                f"[WARN] source_dir collision: '{svc['name']}' and '{seen[sd]}' "
                f"both use '{sd}' — scaffold will overwrite files."
            )
        else:
            seen[sd] = svc["name"]

    return warnings


def _validate_frontend_deps(architecture: dict) -> list[str]:
    """
    FIX lỗi 8: Frontend service chỉ nên depends on backend tương ứng,
    không nên chain frontend → frontend → frontend.

    Rule: frontend depends_on phải chứa ít nhất 1 backend service.
    Nếu chỉ depends on frontend khác → warning (tạo build bottleneck không cần thiết).
    """
    backend_names = {
        svc["name"] for svc in architecture.get("services", [])
        if svc.get("component") == "backend"
    }
    warnings = []

    for svc in architecture.get("services", []):
        if svc.get("component") != "frontend":
            continue
        deps = svc.get("depends_on", [])
        if not deps:
            continue
        has_backend_dep = any(d in backend_names for d in deps)
        if not has_backend_dep:
            warnings.append(
                f"[WARN] '{svc['name']}' (frontend) depends only on other frontends {deps} "
                f"— should depend on its corresponding backend directly."
            )

    return warnings


# ── Truncation repair (giữ nguyên từ version gốc) ────────────────────────────

def repair_truncated_json(text: str) -> tuple:
    start = text.find("{")
    if start < 0:
        return None, "No { found"

    fragment = text[start:]
    stack = []
    in_string = False
    escape_next = False

    for ch in fragment:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            stack.append('obj')
        elif ch == '}':
            if stack and stack[-1] == 'obj':
                stack.pop()
        elif ch == '[':
            stack.append('arr')
        elif ch == ']':
            if stack and stack[-1] == 'arr':
                stack.pop()

    repair = fragment
    if in_string:
        repair += '"'
    repair = repair.rstrip()
    while repair and repair[-1] in (',', ':'):
        repair = repair[:-1].rstrip()
    for item in reversed(stack):
        repair += ']' if item == 'arr' else '}'

    try:
        return json.loads(repair), None
    except json.JSONDecodeError:
        pass

    repair2 = re.sub(r',\s*([\]\}])', r'\1', repair)
    try:
        return json.loads(repair2), None
    except json.JSONDecodeError as e:
        return None, f"repair failed: {e}"


def _try_parse_patched(response: str):
    clean = response.replace("ARCHITECT_DONE", "").strip()

    arch, err = p.extract_json_object(clean)
    if arch:
        return arch, None

    json_blocks = re.findall(r'```json\s*([\s\S]*?)```', clean)
    for block in json_blocks:
        try:
            arch = json.loads(block.strip())
            if isinstance(arch, dict):
                return arch, None
        except json.JSONDecodeError as e:
            err = str(e)

    fixed = re.sub(r',\s*([\]\}])', r'\1', clean)
    start = fixed.find("{")
    end   = fixed.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            arch = json.loads(fixed[start:end])
            if isinstance(arch, dict):
                return arch, None
        except json.JSONDecodeError as e:
            err = str(e)

    arch, repair_err = repair_truncated_json(clean)
    if arch and isinstance(arch, dict):
        n_services = len(arch.get("services", []))
        print(f"      [architect] Truncation repair succeeded ({n_services} services recovered)")
        return arch, None

    return None, repair_err or err


# ── Path normalization (giữ nguyên từ version gốc) ───────────────────────────

def _normalize_architecture_paths(architecture: dict) -> dict:
    for svc in architecture.get("services", []):
        component = svc.get("component", "backend")
        if component != "backend":
            continue

        source_dir = None
        for fp in svc.get("file_structure", []):
            if "app/main.py" in fp.replace("\\", "/"):
                source_dir = fp.replace("\\", "/").split("app/main.py")[0].rstrip("/")
                break

        if not source_dir:
            name = svc.get("name", "service").lower().replace(" ", "_")
            source_dir = f"src/services/{name}"

        normalized = []
        for fp in svc.get("file_structure", []):
            fp = fp.replace("\\", "/")
            if fp == f"{source_dir}/models.py":
                fp = f"{source_dir}/app/models/base.py"
            elif fp == f"{source_dir}/app/routes.py":
                service_name = source_dir.split("/")[-1]
                fp = f"{source_dir}/app/routes/{service_name}.py"
            elif fp == f"{source_dir}/main.py":
                fp = f"{source_dir}/app/main.py"
            normalized.append(fp)

        svc["file_structure"] = normalized
        svc["source_dir"] = source_dir

    return architecture


def _normalize_plan_paths(plan: dict) -> dict:
    if not isinstance(plan, dict):
        return plan

    source_dir = plan.get("source_dir", "")
    if not source_dir:
        for entry in plan.get("files", []):
            fp_n = (entry.get("path", "") if isinstance(entry, dict) else str(entry)).replace("\\", "/")
            if "app/main.py" in fp_n:
                source_dir = fp_n.split("app/main.py")[0].rstrip("/")
                break

    if not source_dir:
        return plan

    service_name = source_dir.split("/")[-1]

    def _fix_path(fp: str) -> str:
        fp = fp.replace("\\", "/")
        if fp == f"{source_dir}/models.py":
            return f"{source_dir}/app/models/user.py"
        if fp == f"{source_dir}/app/routes.py":
            return f"{source_dir}/app/routes/{service_name}.py"
        if fp == f"{source_dir}/main.py":
            return f"{source_dir}/app/main.py"
        return fp

    if "files" not in plan:
        return plan

    fixed_files = []
    for entry in plan["files"]:
        if isinstance(entry, dict):
            old_path = entry.get("path", "")
            new_path = _fix_path(old_path)
            fixed_files.append({**entry, "path": new_path} if new_path != old_path else entry)
        else:
            fixed_files.append(_fix_path(str(entry)))
    plan["files"] = fixed_files
    return plan


# ── Main architect function ───────────────────────────────────────────────────
def _fix_token_field_consistency(architecture: dict) -> list[str]:
    """
    FIX BUG A-8: Đảm bảo refresh endpoint dùng cùng field name với login response.

    Vấn đề: LLM sinh POST /auth/login trả {"token": "str"} nhưng
    POST /auth/refresh nhận {"refresh_token": "str"} — tên field khác nhau.
    Dev agent sinh code verify refresh_token như separate token → 401.
    Tester lấy token từ login → gửi vào refresh_token → server reject.

    Fix: nếu login trả "token" (không có "refresh_token") → đổi refresh
    request_body từ {"refresh_token": ...} thành {"token": ...}.
    """
    messages = []
    for svc in architecture.get("services", []):
        routes = svc.get("api_routes", [])

        # Tìm login route và field token nó trả về
        login_token_field = None
        login_has_refresh_token = False
        for r in routes:
            path = r.get("path", "").rstrip("/")
            if r.get("method", "").upper() == "POST" and path.endswith("/login"):
                resp = r.get("response_body") or r.get("response_example") or {}
                for field in resp:
                    if field == "refresh_token":
                        login_has_refresh_token = True
                    if "token" in field.lower() and field != "refresh_token":
                        login_token_field = field  # thường là "token" hoặc "access_token"
                break

        # Chỉ fix nếu login KHÔNG trả refresh_token riêng
        if not login_token_field or login_has_refresh_token:
            continue

        # Tìm refresh route trong cùng service
        for r in routes:
            path = r.get("path", "").rstrip("/")
            if r.get("method", "").upper() == "POST" and path.endswith("/refresh"):
                req_body = r.get("request_body") or {}
                if "refresh_token" in req_body:
                    req_body[login_token_field] = req_body.pop("refresh_token")
                    r["request_body"] = req_body
                    messages.append(
                        f"[FIX A-8] '{svc['name']}': /auth/refresh request_body "
                        f"'refresh_token' → '{login_token_field}' "
                        f"(login response chỉ có '{login_token_field}', không có 'refresh_token')"
                    )

    return messages
def _gemini_architect(prompt: str) -> str:
    system = p.load_agent_instruction("architect-agent", backend="gemini")
    if not system or len(system.strip()) < 50:
        raise RuntimeError("architect-agent.md not found or empty")

    if not os.path.exists("docs/entities.json"):
        raise RuntimeError("entities.json not found — run requirement-agent first")
    if not os.path.exists("docs/requirements.md"):
        raise RuntimeError("requirements.md not found — run requirement-agent first")

    with open("docs/entities.json", encoding="utf-8") as f:
        entities_json = f.read()
    with open("docs/requirements.md", encoding="utf-8") as f:
        requirements_md = f.read()

    try:
        entities_list = json.loads(entities_json)
        expected_min_services = max(2, len(entities_list) // 2)
    except Exception:
        expected_min_services = 2

    # Load Knowledge Graph — FIX: format_for_architect giờ include constraints + hints
    kg_context = ""
    kg = load_knowledge_graph()
    if kg:
        kg_context = format_for_architect(kg)
        print(f"      [architect] Knowledge Graph loaded ({len(kg_context)} chars)")
    else:
        print("      [architect] WARNING: knowledge_graph.json not found")

    prompts = [
        # Attempt 1: full context
        f"""# Knowledge Graph
{kg_context if kg_context else "(not available)"}

# entities.json
{entities_json}

# requirements.md
{requirements_md}

CRITICAL OUTPUT RULES:
- Output ONLY valid JSON then ARCHITECT_DONE
- No markdown fences around the JSON
- First character must be {{
- Last character must be }}
- Every string value must be properly closed with "
- Every object must be properly closed with }}
- Every array must be properly closed with ]
- Do NOT include task_id in any service or deployment
- depends_on must use SERVICE NAMES only (e.g. "Auth Backend"), never TASK-IDs
""",
        # Attempt 2: simplified output request
        f"""Design the architecture for this system. Output ONLY valid JSON.

# entities.json (COMPLETE — include ALL entities as services)
{entities_json}

# requirements.md
{requirements_md}

# Constraints to implement (from knowledge graph)
{_extract_constraints_text(kg) if kg else "(not available)"}

IMPORTANT — to avoid truncation:
- Keep descriptions SHORT (max 1 line each)
- Keep file_structure to 3-4 essential files per service
- Keep api_routes to the most critical routes only (max 5 per service)
- Output the entire JSON in one response — do not stop early
- Do NOT include task_id anywhere
- depends_on must use SERVICE NAMES only

Output format:
{{
  "schema_version": "1",
  "tech_stack": {{"backend": "FastAPI + Pydantic v2", "frontend": "React 18 + TypeScript + Vite", "testing": "pytest (backend), Jest (frontend)", "containerization": "Docker + docker-compose"}},
  "services": [...],
  "shared_types": [],
  "deployment": {{"name": "Deployment", "includes": ["docker-compose.yml"], "depends_on": [...]}}
}}
""",
        # Attempt 3: minimal JSON
        f"""Output a MINIMAL but COMPLETE architecture JSON.
Keep descriptions short. Keep file_structure to 3 files max per service.
STILL include api_routes — at least 1-2 routes per backend service.
Do NOT include task_id. depends_on uses service names only.

Entities:
{entities_json}

Output ONLY valid JSON in this exact shape:
{{
  "schema_version": "1",
  "tech_stack": {{"backend": "FastAPI + Pydantic v2", "frontend": "React 18 + TypeScript + Vite", "testing": "pytest", "containerization": "Docker"}},
  "services": [
    {{
      "name": "...",
      "component": "backend",
      "entity_refs": [...],
      "description": "one line",
      "constraints": [],
      "storage_type": "in_memory | sqlite | postgres",
      "file_structure": ["path/main.py", "path/routes/x.py", "requirements.txt"],
      "api_routes": [
        {{"method": "POST", "path": "/x/y", "status_code": 201,
          "request_body": {{"field": "str"}},
          "response_body": {{"id": "int"}},
          "errors": []}}
      ],
      "cross_service_calls": [],
      "shared_types": [],
      "depends_on": []
    }}
  ],
  "shared_types": [],
  "deployment": {{"name": "Deployment", "includes": ["docker-compose.yml"], "depends_on": [...]}}
}}

RULES:
- Every backend service MUST have at least 1 route in api_routes
- Frontend services: api_routes = [], cross_service_calls = []
- Output starts with {{ ends with }}
- All strings closed, all arrays closed
- NO task_id anywhere
""",
    ]

    architecture = None
    for attempt, user_prompt in enumerate(prompts):
        print(f"      [architect] attempt {attempt + 1}/{len(prompts)}...")
        response = ai_client.call(GEMINI_API_KEYS, system, user_prompt, "architect-agent")

        os.makedirs("docs", exist_ok=True)
        with open(f"docs/debug_architect_raw_{attempt+1}.txt", "w", encoding="utf-8") as f:
            f.write(response)
        print(f"      [architect] response {len(response)} chars")

        arch, err = _try_parse_patched(response)

        if arch:
            # FIX lỗi 1 & 4: strip task_id trước mọi validation
            arch = _strip_injected_ids(arch)

            n_services = len(arch.get("services", []))
            total_routes = sum(len(s.get("api_routes", [])) for s in arch.get("services", []))

            if n_services < expected_min_services:
                print(f"      [architect] Only {n_services} services — continuing")
                architecture = architecture or arch
                continue

            if total_routes == 0:
                print(f"      [architect] {n_services} services but 0 routes total — continuing")
                architecture = architecture or arch
                continue

            print(f"      [architect] JSON parsed OK — {n_services} services, {total_routes} routes")
            architecture = arch
            break

        print(f"      [architect] Parse failed: {err}")

    if architecture is None:
        raise RuntimeError(
            f"Architect returned invalid JSON after {len(prompts)} attempts.\n"
            f"See docs/debug_architect_raw_*.txt"
        )

    if "services" not in architecture:
        raise RuntimeError("Architect output missing 'services'")

    # ── Post-processing pipeline ──────────────────────────────────────────────

    # FIX lỗi 7: detect source_dir collisions
    architecture = _normalize_architecture_paths(architecture)

    # FIX BUG A-1/A-2: validate + auto-fix depends_on (TASK-IDs → service names, self-loops)
    dep_warnings = _validate_depends_on(architecture)
    for w in dep_warnings:
        print(f"      [architect] {w}")

    # FIX BUG A-4/A-5: cross_service_calls consistency + remove backend→frontend deps
    cross_fixes = _fix_cross_service_dep_consistency(architecture)
    for w in cross_fixes:
        print(f"      [architect] {w}")
    # FIX BUG A-7: phá circular dependency trước khi dep-graph validate
    cycle_fixes = _break_cycles(architecture)
    for w in cycle_fixes:
        print(f"      [architect] {w}")
    # FIX BUG A-8: token field consistency giữa login response và refresh request
    token_fixes = _fix_token_field_consistency(architecture)
    for w in token_fixes:
        print(f"      [architect] {w}")
    # ──────────────────────────────────────────────────────────────────────────
    collision_warnings = _validate_source_dir_collision(architecture)
    for w in collision_warnings:
        print(f"      [architect] {w}")
    # FIX lỗi 8: validate frontend dep chain
    fe_warnings = _validate_frontend_deps(architecture)
    for w in fe_warnings:
        print(f"      [architect] {w}")

    # FIX lỗi 2 & 3: inject constraints từ KG vào từng service nếu model bỏ qua
    if kg:
        architecture = _inject_kg_constraints(architecture, kg)

    with open("docs/architecture.json", "w", encoding="utf-8") as f:
        json.dump(architecture, f, indent=2, ensure_ascii=False)

    n_services = len(architecture.get("services", []))
    n_routes   = sum(len(s.get("api_routes", [])) for s in architecture.get("services", []))
    print(f"      [gemini] architecture.json ({n_services} services, {n_routes} routes)")
    return "ARCHITECT_DONE"


# ── Helper: inject KG constraints vào architecture (safety net) ───────────────
def _break_cycles(architecture: dict) -> list[str]:
    """
    FIX BUG A-7: Phát hiện và phá circular dependency trong architecture.

    LLM đôi khi sinh A depends_on B và B depends_on A (mutual dep).
    Chiến lược phá vòng: với mỗi cặp cycle, xóa edge có ít semantic weight hơn.
    
    Rule ưu tiên giữ dep:
      - backend depends on auth → giữ (auth là foundation)
      - checkout depends on cart → giữ (domain logic rõ ràng)
      - service có startup_order thấp hơn → giữ dep vào nó
    
    Fallback khi không có rule: giữ dep của service có name alphabetically nhỏ hơn,
    xóa dep ngược lại.
    
    Trả về list messages mô tả các edge đã xóa.
    """
    messages = []
    services = architecture.get("services", [])
    
    # Build name → service map
    name_map = {svc["name"]: svc for svc in services}
    
    # Kahn's algorithm để detect cycle và tìm các edge cần xóa
    # Build adjacency: name → set of depends_on names
    adj: dict[str, set] = {svc["name"]: set(svc.get("depends_on", [])) for svc in services}
    
    changed = True
    while changed:
        changed = False
        # Tìm cycle bằng DFS
        visited: set[str] = set()
        path: list[str] = []
        
        def _dfs(node: str) -> list[str] | None:
            if node in path:
                # Tìm thấy cycle — trả về cycle path
                idx = path.index(node)
                return path[idx:]
            if node in visited:
                return None
            path.append(node)
            for dep in list(adj.get(node, [])):
                result = _dfs(dep)
                if result is not None:
                    return result
            path.pop()
            visited.add(node)
            return None
        
        cycle = None
        for node in list(adj.keys()):
            if node not in visited:
                path = []
                cycle = _dfs(node)
                if cycle:
                    break
        
        if not cycle:
            break  # Không còn cycle
        
        # Tìm edge yếu nhất trong cycle để xóa
        # Edge yếu nhất: cạnh từ service "upstream" (auth, catalog) → service "downstream"
        # Nếu không phân biệt được → xóa edge đầu tiên trong cycle (alphabetical fallback)
        
        # Ưu tiên giữ dep vào: auth, catalog, product — đây là foundation services
        FOUNDATION_KEYWORDS = ("auth", "catalog", "product", "user", "inventory")
        
        edge_to_remove = None
        # Duyệt từng edge trong cycle: cycle[i] depends_on cycle[i+1]
        for i in range(len(cycle)):
            src = cycle[i]
            dst = cycle[(i + 1) % len(cycle)]
            dst_lower = dst.lower()
            # Nếu dst là foundation service → giữ dep này, không xóa
            if any(kw in dst_lower for kw in FOUNDATION_KEYWORDS):
                continue
            # Candidate để xóa
            if edge_to_remove is None:
                edge_to_remove = (src, dst)
        
        # Fallback: nếu tất cả đều là foundation → xóa edge đầu tiên
        if edge_to_remove is None:
            src = cycle[0]
            dst = cycle[1 % len(cycle)]
            edge_to_remove = (src, dst)
        
        src, dst = edge_to_remove
        adj[src].discard(dst)
        
        # Apply vào architecture
        svc = name_map.get(src)
        if svc and dst in svc.get("depends_on", []):
            svc["depends_on"] = [d for d in svc["depends_on"] if d != dst]
            messages.append(
                f"[FIX A-7] Cycle broken: removed '{src}' → '{dst}' dep "
                f"(cycle: {' → '.join(cycle + [cycle[0]])})"
            )
        
        changed = True
    
    return messages
def _inject_kg_constraints(architecture: dict, kg: dict) -> dict:
    """
    FIX lỗi 2 & 3: Safety net — đảm bảo constraints + hints từ KG
    được phản ánh trong architecture dù model có bỏ qua prompt hay không.

    Logic:
    1. Build map: entity_id → list of constraint types
    2. Với mỗi service, tìm entity_refs → lookup constraints
    3. Merge vào service["constraints"] (không overwrite nếu model đã tự điền)
    4. Inject startup_order vào depends_on nếu thiếu
    5. Inject storage_type cho cart service nếu thiếu
    """
    nodes = kg.get("nodes", {})
    constraints_by_entity: dict[str, list[str]] = {}
    for c in kg.get("constraints", []):
        constraints_by_entity.setdefault(c["entity"], []).append(c["type"])

    # Map entity_id → storage hint
    storage_hints: dict[str, str] = {}
    cross_call_hints: dict[str, list] = {}

    for hint in kg.get("architect_hints", []):
        if hint["type"] == "storage_choice":
            for eid in hint.get("entities", []):
                # Parse "option (a) in-memory dict" → "in_memory"
                msg = hint["message"].lower()
                if "in-memory" in msg or "in_memory" in msg:
                    storage_hints[eid] = "in_memory"
                elif "redis" in msg:
                    storage_hints[eid] = "redis"
                elif "db table" in msg or "database" in msg:
                    storage_hints[eid] = "database"

    # Build startup_order required deps:
    # auth phải healthy trước cart, checkout, order_history
    startup_deps: dict[str, list[str]] = {}
    for hint in kg.get("architect_hints", []):
        if hint["type"] == "startup_order":
            entities = hint.get("entities", [])
            if entities:
                first_entity = entities[0]
                first_name = nodes.get(first_entity, {}).get("name", "")
                if first_name:  # guard: chỉ inject nếu resolve được tên
                    for eid in entities[1:]:
                        startup_deps.setdefault(eid, []).append(first_name) 

    # Build entity → service name map
    entity_to_service: dict[str, str] = {}
    for svc in architecture.get("services", []):
        for eid in svc.get("entity_refs", []):
            entity_to_service[eid] = svc["name"]

    # Apply to each service
    for svc in architecture.get("services", []):
        entity_refs = svc.get("entity_refs", [])

        # Inject constraints
        existing_constraints = set(svc.get("constraints", []))
        for eid in entity_refs:
            for ctype in constraints_by_entity.get(eid, []):
                if ctype not in existing_constraints:
                    existing_constraints.add(ctype)
        if existing_constraints:
            svc["constraints"] = sorted(existing_constraints)

        # Inject storage_type
        if "storage_type" not in svc:
            for eid in entity_refs:
                if eid in storage_hints:
                    svc["storage_type"] = storage_hints[eid]
                    break

        # Inject startup_order deps into depends_on
        required_startup = set()
        for eid in entity_refs:
            for dep_eid in startup_deps.get(eid, []):
                dep_service = entity_to_service.get(dep_eid)
                if dep_service:
                    required_startup.add(dep_service)

        if required_startup:
            current_deps = set(svc.get("depends_on", []))
            missing = required_startup - current_deps
            if missing:
                svc["depends_on"] = sorted(current_deps | missing)
                print(f"      [architect] Injected startup deps into '{svc['name']}': {missing}")

    for svc in architecture.get("services", []):
        if svc.get("component") != "backend":
            continue

        has_jwt = "requires_jwt" in svc.get("constraints", [])
        if not has_jwt:
            continue

        for route in svc.get("api_routes", []):
            # Chỉ set nếu chưa có (không overwrite nếu model đã explicit set False)
            if "auth_required" not in route:
                # Public exceptions: login, signup, health, docs
                PUBLIC_PATHS = {"/auth/login", "/auth/signup", "/health", "/docs", "/openapi.json"}
                route["auth_required"] = route.get("path", "") not in PUBLIC_PATHS

    return architecture


def _extract_constraints_text(kg: dict) -> str:
    """Helper: extract constraints thành plain text cho attempt-2 prompt."""
    if not kg:
        return "(none)"
    lines = []
    nodes = kg.get("nodes", {})
    for c in kg.get("constraints", []):
        name = nodes.get(c["entity"], {}).get("name", c["entity"])
        lines.append(f"- {name}: [{c['type'].upper()}] {c['reason']}")
    return "\n".join(lines) if lines else "(none)"