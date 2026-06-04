"""
contract_normalizer.py — v5: Dynamic task support

Changes from v4:
  [v4] CONTRACT_ROUTE_SCHEMAS hardcoded for TASK-01/02/03 routes only
  [v5] Schema inference from route path + method — works for any domain

  [v4] response_fields hardcoded per task
  [v5] response_fields derived from response_example in route definition
       Fallback: infer from path + method convention

Changes from v5 → v5.1 (BUG FIX):
  [BUG-1] Planner bịa routes vào tasks.json thay vì copy từ architecture.json
          FIX: normalize_task_contract() ưu tiên api_routes từ architecture.json
               nếu api_contract trong tasks.json rỗng hoặc routes không match architecture

  [BUG-3] export_contracts_to_files() không lưu source_dir
          FIX: thêm trường "source_dir" và "routes_dir" vào contract file
               Validator và dev agent đọc source_dir để biết đúng thư mục
"""
import os
import json
import re
from typing import Optional

CONTRACT_SCHEMA_VERSION = "2.0"

# ─────────────────────────────────────────────────────────────────────────────
# ARCHITECTURE.JSON LOADER — source of truth cho routes
# ─────────────────────────────────────────────────────────────────────────────

def _load_architecture() -> dict:
    """Load docs/architecture.json, trả về {} nếu không tồn tại."""
    path = "docs/architecture.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_service_by_task_id(task_id: str, architecture: dict) -> Optional[dict]:
    """
    Tìm service trong architecture.json theo task_id.
    Tìm cả trong services[] lẫn deployment block.
    Nếu không match → log warning rõ ràng (không raise để pipeline tiếp tục).
    """
    for svc in architecture.get("services", []):
        if svc.get("task_id") == task_id:
            return svc

    dep = architecture.get("deployment")
    if dep and dep.get("task_id") == task_id:
        return dep

    all_ids = [s.get("task_id") for s in architecture.get("services", [])]
    if dep:
        all_ids.append(dep.get("task_id"))
    print(
        f"      [contract-compiler] WARNING: {task_id} not found in architecture.json "
        f"(known task_ids: {all_ids}). "
        f"Contract routes will fall back to tasks.json — verify task_id was assigned by materializer."
    )
    return None


def _extract_source_dir(service: dict) -> str:
    """
    Suy ra source directory của service từ file_structure.

    Ví dụ:
      ["src/services/auth_backend/app/main.py", ...]
        → "src/services/auth_backend"          (depth-3, dừng trước "app/")

      ["src/backend/app/main.py", ...]
        → "src/backend"                         (depth-2, dừng trước "app/")

      ["src/frontend/src/App.tsx", ...]
        → "src/frontend"                        (depth-2)
    
    Quy tắc dừng:
      - Bỏ qua các segment "app", "src" (ở vị trí ≥ 2) vì chúng là
        thư mục nội bộ của service, không phải service root.
      - Dừng ngay trước segment đó.

    Fallback: convention theo component nếu file_structure rỗng.
    """
    files = service.get("file_structure", [])
    component = service.get("component", "backend")

    # Các tên thư mục nội bộ — khi gặp ở bất kỳ vị trí nào sau index 1
    # thì đây là ranh giới "bên trong service", dừng trước đó.
    INTERNAL_DIRS = {"app", "src", "lib", "pkg"}

    if files:
        parts_list = [f.split("/") for f in files if f.endswith((".py", ".ts", ".tsx"))]
        if parts_list:
            # Tính common prefix
            common = list(parts_list[0])
            for parts in parts_list[1:]:
                new_common = []
                for a, b in zip(common, parts):
                    if a == b:
                        new_common.append(a)
                    else:
                        break
                common = new_common
                if not common:
                    break

            # Bỏ phần cuối nếu là filename (có extension)
            if common and "." in common[-1]:
                common = common[:-1]

            # Cắt tại vị trí đầu tiên gặp INTERNAL_DIRS (từ index 1 trở đi)
            # index 0 luôn là "src" — bỏ qua
            cut_at = len(common)
            for i in range(1, len(common)):
                if common[i] in INTERNAL_DIRS:
                    cut_at = i
                    break
            common = common[:cut_at]

            if len(common) >= 2:
                return "/".join(common)

    # Fallback theo component
    if component == "frontend":
        return "src/frontend"
    return "src/backend"


def _extract_routes_from_architecture(service: dict) -> list:
    """
    Lấy api_routes từ service trong architecture.json, convert sang contract route format.
    """
    raw_routes = service.get("api_routes", [])
    if not raw_routes:
        return []

    normalized = []
    for r in raw_routes:
        if not isinstance(r, dict):
            continue
        method = r.get("method", "GET").lower()
        path   = r.get("path", "/")
        resp_body = r.get("response_body")          # ← kéo ra đây, trước append
        normalized.append({
            "method":           method,
            "path":             path,
            "status_code":      r.get("status_code") or _infer_status_code(method, path),
            "auth_required":    r.get("auth_required", False),
            "description":      r.get("description", r.get("summary", "")),
            "request_body":     r.get("request_body") or r.get("request_schema") or {},
            "response_example": resp_body if resp_body is not None else (
                r.get("response_example") or r.get("response_schema") or {}
            ),
            "errors":           r.get("errors") or [],
            "rules":            r.get("rules") or [],
            "depends_on":       r.get("depends_on") or [],
        })
    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# STATUS CODE CONVENTIONS
# ─────────────────────────────────────────────────────────────────────────────

_METHOD_DEFAULT_STATUS = {
    "get":    200,
    "post":   201,
    "put":    200,
    "patch":  200,
    "delete": 204,
}

_PATH_STATUS_OVERRIDES = {
    "/health":    {"get": 200},
    "/checkout":  {"post": 200},
    "/login":     {"post": 200},
    "/logout":    {"post": 200},
    "/refresh":   {"post": 200},
}


def _infer_status_code(method: str, path: str) -> int:
    m = method.lower()
    for suffix, overrides in _PATH_STATUS_OVERRIDES.items():
        if path.rstrip("/").endswith(suffix):
            if m in overrides:
                return overrides[m]
    return _METHOD_DEFAULT_STATUS.get(m, 200)


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE FIELD INFERENCE
# ─────────────────────────────────────────────────────────────────────────────
_TYPE_ALIAS = {"int", "float", "bool", "list", "dict", "str"}
def resolve_response_fields(method: str, path: str, response_example: dict = {}) -> dict:
    """
    Returns dict of {field_name: type_hint_str} for response assertions.

    Priority:
      1. response_example in contract (explicit)
      2. Path/method convention inference
      3. Empty dict (no field assertions)
    """
    if response_example and isinstance(response_example, dict):
        result = {}
        for k, v in response_example.items():
            if isinstance(v, str) and v in _TYPE_ALIAS:
                result[k] = v          # đây là type name literal → giữ nguyên
            else:
                result[k] = type(v).__name__  # đây là value thực → lấy type
        return result

    m = method.lower()
    p = path.rstrip("/")

    if p == "/health":
        return {"status": "str"}

    if p.endswith("/login") or p.endswith("/token"):
        return {"token": "str", "refresh_token": "str", "user_id": "int"}
    if p.endswith("/register") or p.endswith("/signup"):
        return {"id": "int"}

    if p.endswith("/checkout"):
        return {"id": "int", "total": "float"}

    if m == "get" and not re.search(r"\{[^}]+\}$", p):
        return {}  # list endpoints — shape unknown without example

    if m in ("get", "post", "put", "patch"):
        if re.search(r"\{[^}]+\}", p) or m == "post":
            return {"id": "int"}

    if m == "delete":
        return {}

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST BODY INFERENCE
# ─────────────────────────────────────────────────────────────────────────────

def resolve_route_schema(method: str, path: str) -> dict:
    """
    Returns {"request_body": {...}} based on path/method conventions.
    Used by tester when contract doesn't have explicit request_body.
    """
    m = method.lower()
    p = path.rstrip("/")

    if p.endswith("/login"):
        return {"request_body": {"email": "str", "password": "str"}}
    if p.endswith("/register"):
        return {"request_body": {"email": "str", "password": "str", "name": "str"}}
    if p.endswith("/add"):
        return {"request_body": {"product_id": "int", "quantity": "int"}}

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTE NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_route(route: dict) -> dict:
    """Ensure a route dict has all required fields."""
    method = route.get("method", "GET").lower()
    path   = route.get("path", "/")

    normalized = {
        "method":           method,
        "path":             path,
        "status_code":      route.get("status_code") or _infer_status_code(method, path),
        "auth_required":    route.get("auth_required", False),
        "description":      route.get("description", ""),
        "request_body":     route.get("request_body") or {},
        "response_example": route.get("response_example") or {},
        "errors":           route.get("errors") or [],
        "rules":            route.get("rules") or [],
        "depends_on":       route.get("depends_on") or [],
    }

    normalized["response_fields"] = resolve_response_fields(
        method, path, normalized["response_example"]
    )

    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# TASK CONTRACT NORMALIZATION  [v5.1 FIX: architecture.json as source of truth]
# ─────────────────────────────────────────────────────────────────────────────

def normalize_task_contract(task: dict, architecture: Optional[dict] = None) -> dict:
    """
    Normalize a single task dict.

    Route source priority (v5.1):
      1. architecture.json api_routes (authoritative — architect designed these)
      2. tasks.json api_contract.routes (planner copy — may be wrong/invented)
      3. Empty list (no routes for this task)

    This fixes BUG-1: Planner bịa routes không match architecture.
    """
    task = dict(task)
    task_id = task.get("id", "")

    if architecture is None:
        architecture = _load_architecture()

    # Try to get routes from architecture.json first
    arch_routes = []
    service = _find_service_by_task_id(task_id, architecture)
    if service:
        arch_routes = _extract_routes_from_architecture(service)

    # Fallback: use routes from tasks.json api_contract
    tasks_routes = []
    api_contract = task.get("api_contract") or {}
    raw_tasks_routes = api_contract.get("routes") or []
    if raw_tasks_routes:
        tasks_routes = [_normalize_route(r) for r in raw_tasks_routes]

    # Decision: prefer architecture routes if available
    if arch_routes:
        final_routes = [_normalize_route(r) for r in arch_routes]
        if tasks_routes:
            arch_paths  = {(r["method"], r["path"]) for r in arch_routes}
            tasks_paths = {(r["method"], r["path"]) for r in tasks_routes}
            if arch_paths != tasks_paths:
                print(
                    f"      [contract-compiler] {task_id}: MISMATCH — "
                    f"architecture has {sorted(arch_paths)}, "
                    f"tasks.json has {sorted(tasks_paths)}. "
                    f"Using architecture routes (authoritative)."
                )
            else:
                print(f"      [contract-compiler] {task_id}: {len(arch_routes)} routes from architecture.json (tasks.json match — OK)")
        else:
            print(f"      [contract-compiler] {task_id}: {len(arch_routes)} routes from architecture.json")
    else:
        final_routes = tasks_routes
        if tasks_routes:
            print(
                f"      [contract-compiler] {task_id}: WARNING — using {len(tasks_routes)} routes "
                f"from tasks.json (no architecture service matched). "
                f"These may be invented by Planner — check architecture.json has task_id={task_id!r}."
            )
        else:
            print(f"      [contract-compiler] {task_id}: no routes found")

    task["api_contract"] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "routes": final_routes,
    }

    return task


def normalize_tasks_to_contracts(tasks_json: dict) -> dict:
    """
    Normalize all tasks in tasks.json.
    Loads architecture.json once and passes to each task normalization.
    """
    result = dict(tasks_json)
    architecture = _load_architecture()  # load once
    sprints = []

    for sprint in result.get("sprints", []):
        sprint = dict(sprint)
        sprint["tasks"] = [
            normalize_task_contract(t, architecture)
            for t in sprint.get("tasks", [])
        ]
        sprints.append(sprint)

    result["sprints"] = sprints
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT FILE EXPORT  [v5.1 FIX: include source_dir and routes_dir]
# ─────────────────────────────────────────────────────────────────────────────

def export_contracts_to_files(tasks_json: dict, contracts_dir: str = "docs/contracts") -> list:
    """
    Export one contract file per task.

    Contract file now includes:
      - source_dir:  root dir of service (e.g. "src/services/auth_backend")
      - routes_dir:  where route files live (e.g. "src/services/auth_backend/app/routes")

    These fields let dev agent and validator agree on file locations (fixes BUG-2 + BUG-3).
    """
    os.makedirs(contracts_dir, exist_ok=True)
    architecture = _load_architecture()
    written = []

    for sprint in tasks_json.get("sprints", []):
        for task in sprint.get("tasks", []):
            task_id = task.get("id")
            if not task_id:
                continue

            # Determine source_dir from architecture.json
            service = _find_service_by_task_id(task_id, architecture)
            if service:
                source_dir = _extract_source_dir(service)
                file_structure = service.get("file_structure", [])
            else:
                # Fallback per component
                component = task.get("component", "fullstack")
                source_dir = "src/frontend" if component == "frontend" else "src/backend"
                file_structure = []

            # Derive routes_dir: source_dir + /app/routes (backend convention)
            component = task.get("component", service.get("component", "fullstack") if service else "fullstack")
            if component in ("backend", "fullstack"):
                routes_dir = f"{source_dir}/app/routes"
            else:
                routes_dir = f"{source_dir}/src"

            contract = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "task_id":        task_id,
                "domain":         task.get("domain", service.get("domain", "") if service else ""),
                "component":      component,
                "layer":          task.get("layer", ""),
                "summary":        task.get("summary", ""),
                "source_dir":     source_dir,       # NEW v5.1 — where to write/read files
                "routes_dir":     routes_dir,       # NEW v5.1 — where to scan for routes
                "file_structure": file_structure,   # NEW v5.1 — from architecture.json
                "routes":         task.get("api_contract", {}).get("routes", []),
                "artifacts":      task.get("artifacts", []),
            }

            path = os.path.join(contracts_dir, f"{task_id}.contract.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(contract, f, indent=2, ensure_ascii=False)

            written.append(path)

    return written


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT FILE LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_contract(task_id: str, contracts_dir: str = "docs/contracts") -> Optional[dict]:
    path = os.path.join(contracts_dir, f"{task_id}.contract.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_contracts(contracts_dir: str = "docs/contracts") -> list:
    if not os.path.exists(contracts_dir):
        return []
    return [
        f.replace(".contract.json", "")
        for f in os.listdir(contracts_dir)
        if f.endswith(".contract.json")
    ]