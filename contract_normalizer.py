# contract_normalizer.py
# REAL CONTRACT-FIRST PIPELINE
#
# Thay đổi so với Pseudo Contract-First:
#   [OLD] normalize in-place vào tasks.json
#   [NEW] compile → write docs/contracts/TASK-XX.contract.json (artifact riêng)
#
# Folder layout sau khi chạy:
#   docs/
#     requirements.md
#     stories.json
#     tasks.json          ← planner output (raw)
#     contracts/
#       TASK-01.contract.json   ← EXECUTABLE CONTRACT (locked artifact)
#       TASK-02.contract.json
#   src/backend/tests/    ← tester viết vào đây, đọc từ contracts/

import json
import os
import re
import datetime


# ── Schema cứng: method → default status_code ──────────────────────────────
DEFAULT_STATUS = {
    "get":    200,
    "post":   201,
    "put":    200,
    "patch":  200,
    "delete": 204,
}

# ── Schema cứng: route pattern → response_fields bắt buộc ─────────────────
#   Đây là nguồn truth duy nhất cho cả DEV lẫn TESTER.
#   Key format: "<method>:<path_prefix_regex>"
CONTRACT_ROUTE_SCHEMAS = {
    r"^post:/products/?$": {
        "request_body": {
            "name": "str",
            "price": "float",
            "stock": "int"
        },
        "response_body": {
            "id": "int",
            "name": "str",
            "price": "float",
            "stock": "int"
        },
        "errors": [
            {"status_code": 422, "when": "invalid_payload"}
        ],
        "rules": ["price >= 0", "stock >= 0"],
        "response_example": {"id": 1, "name": "Keyboard", "price": 99.5, "stock": 12}
    },
    r"^get:/products/?$": {
        "request_body": {},
        "response_body": {"items": "list"},
        "errors": [],
        "rules": [],
        "response_example": {"items": [{"id": 1, "name": "Keyboard", "price": 99.5, "stock": 12}]}
    },
    r"^get:/products/\{": {
        "request_body": {},
        "response_body": {"id": "int", "name": "str", "price": "float", "stock": "int"},
        "errors": [
            {"status_code": 404, "when": "product_not_found"}
        ],
        "rules": [],
        "response_example": {"id": 1, "name": "Keyboard", "price": 99.5, "stock": 12}
    },
    r"^put:/products/\{": {
        "request_body": {
            "name": "str",
            "price": "float",
            "stock": "int"
        },
        "response_body": {"id": "int", "name": "str", "price": "float", "stock": "int"},
        "errors": [
            {"status_code": 404, "when": "product_not_found"},
            {"status_code": 422, "when": "invalid_payload"}
        ],
        "rules": ["price >= 0", "stock >= 0"],
        "response_example": {"id": 1, "name": "Keyboard", "price": 20.0, "stock": 50}
    },
    r"^delete:/products/\{": {
        "request_body": {},
        "response_body": {},
        "errors": [
            {"status_code": 404, "when": "product_not_found"}
        ],
        "rules": [],
        "response_example": None
    },
    r"^post:/cart/add$": {
        "request_body": {
            "product_id": "int",
            "quantity": "int"
        },
        "response_body": {"product_id": "int", "quantity": "int"},
        "errors": [
            {"status_code": 404, "when": "product_not_found"},
            {"status_code": 400, "when": "quantity_invalid"}
        ],
        "rules": ["quantity must be > 0", "product must exist"],
        "response_example": {"product_id": 1, "quantity": 2}
    },
    r"^get:/cart/?$": {
        "request_body": {},
        "response_body": {"items": "list"},
        "errors": [],
        "rules": ["cart persists in memory"],
        "response_example": {"items": [{"product_id": 1, "quantity": 1}]}
    },
    r"^post:/cart/checkout$": {
        "request_body": {},
        "response_body": {"id": "int", "total": "float"},
        "errors": [
            {"status_code": 400, "when": "cart_empty"}
        ],
        "rules": ["cart must not be empty", "clears cart after checkout"],
        "preconditions": ["cart must not be empty"],
        "depends_on": ["POST /products", "POST /cart/add"],
        "response_example": {"id": 1, "total": 99.5}
    },
    r"^delete:/cart/clear$": {
        "request_body": {},
        "response_body": {},
        "errors": [],
        "rules": ["empties cart immediately"],
        "response_example": None
    },
    r"^get:/health$": {
        "request_body": {},
        "response_body": {"status": "str"},
        "errors": [],
        "rules": [],
        "response_example": {"status": "ok"}
    },
}
# Contract file schema version — bump khi thay đổi format
CONTRACT_SCHEMA_VERSION = "2.0"


def resolve_route_schema(method: str, path: str) -> dict:
    """Tra CONTRACT_ROUTE_SCHEMAS → full schema cho 1 route."""
    key = f"{method.lower()}:{path.rstrip('/')}"
    for pattern, schema in CONTRACT_ROUTE_SCHEMAS.items():
        if re.match(pattern, key, re.IGNORECASE):
            return schema
    return {}


def resolve_response_fields(method: str, path: str) -> dict:
    """Backward compat — trả về response_body từ schema mới."""
    return resolve_route_schema(method, path).get("response_body", {})


def normalize_route(path: str) -> str:
    """Chuẩn hoá path: leading slash, no trailing slash, collapse //."""
    if not path:
        return "/"
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"/+", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    # Normalize path params → {param} for pattern matching
    path = re.sub(r"\{[^}]+\}", "{param}", path)
    return path


def normalize_contract_route(r: dict) -> dict:
    """
    Normalize 1 route entry:
      - method lowercase
      - path chuẩn
      - status_code enforce default
      - response_fields annotate từ schema cứng nếu chưa có
    """
    method = r.get("method", "get").lower().strip()
    path   = r.get("path", "/").strip()

    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"/+", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    status = r.get("status_code")
    if not isinstance(status, int) or status < 100:
        status = DEFAULT_STATUS.get(method, 200)

    # response_fields: ưu tiên từ planner → fallback schema cứng
    schema = resolve_route_schema(method, path)

    request_body  = r.get("request_body")  or schema.get("request_body", {})
    response_body = r.get("response_body") or schema.get("response_body", {})
    errors        = r.get("errors")        or schema.get("errors", [])
    rules         = r.get("rules")         or schema.get("rules", [])
    preconditions = r.get("preconditions") or schema.get("preconditions", [])
    depends_on    = r.get("depends_on")    or schema.get("depends_on", [])
    response_example = r.get("response_example") or schema.get("response_example")

    # Backward compat alias
    response_fields = r.get("response_fields") or response_body

    return {
        "method":           method,
        "path":             path,
        "status_code":      status,
        "request_body":     request_body,
        "response_body":    response_body,
        "response_fields":  response_fields,   # ← kept for backward compat
        "errors":           errors,
        "rules":            rules,
        "preconditions":    preconditions,
        "depends_on":       depends_on,
        "response_example": response_example,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CORE: normalize tasks.json in-place (giữ backward compat)
# ══════════════════════════════════════════════════════════════════════════════

def normalize_tasks_to_contracts(tasks_json: dict) -> dict:
    """
    [Backward compat] Normalize api_contract của mọi task trong tasks.json.
    Dùng để update tasks.json sau khi planner chạy.
    """
    issues = []
    for sprint in tasks_json.get("sprints", []):
        for task in sprint.get("tasks", []):
            routes_raw = task.get("api_contract", {}).get("routes", [])
            normalized = []
            for r in routes_raw:
                try:
                    normalized.append(normalize_contract_route(r))
                except Exception as e:
                    issues.append(f"{task.get('id','?')}: {e}")
            task.setdefault("api_contract", {})["routes"] = normalized

    if issues:
        for i in issues:
            print(f"  [contract-normalizer] WARNING: {i}")

    return tasks_json


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Export contracts as separate artifact files
#
#   docs/contracts/TASK-01.contract.json
#   docs/contracts/TASK-02.contract.json
#   ...
#
# Đây là bước chuyển từ Pseudo → REAL CONTRACT-FIRST:
#   - Contract trở thành artifact độc lập, không phụ thuộc tasks.json
#   - DEV agent đọc contract file → không tự bịa route
#   - TESTER agent đọc contract file → không infer, không blind access
#   - Contract có thể versioned, diffed, replayed độc lập
# ══════════════════════════════════════════════════════════════════════════════

def export_contracts_to_files(tasks_json: dict, contracts_dir: str = "docs/contracts") -> list[str]:
    """
    Đọc tasks.json đã normalize → ghi mỗi task thành 1 contract file riêng.

    Returns:
        List[str]: danh sách file paths đã ghi.

    Contract file format:
    {
      "schema_version": "2.0",
      "task_id": "TASK-01",
      "summary": "...",
      "component": "backend",
      "compiled_at": "2025-...",
      "routes": [
        {
          "method": "post",
          "path": "/products/",
          "status_code": 201,
          "response_fields": {"id": "int", "name": "str", "price": "float", "stock": "int"}
        }
      ]
    }
    """
    os.makedirs(contracts_dir, exist_ok=True)
    written = []
    compiled_at = datetime.datetime.utcnow().isoformat() + "Z"

    for sprint in tasks_json.get("sprints", []):
        for task in sprint.get("tasks", []):
            task_id = task.get("id")
            if not task_id:
                continue

            routes = task.get("api_contract", {}).get("routes", [])

            contract = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "task_id":        task_id,
                "summary":        task.get("summary", ""),
                "component":      task.get("component", "fullstack"),
                "compiled_at":    compiled_at,
                "routes":         routes,   # đã normalized bởi normalize_tasks_to_contracts
            }

            out_path = os.path.join(contracts_dir, f"{task_id}.contract.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(contract, f, indent=2, ensure_ascii=False)

            written.append(out_path)
            print(f"  [contract-export] {out_path} ({len(routes)} routes)")

    return written


def load_contract(task_id: str, contracts_dir: str = "docs/contracts") -> dict | None:
    """
    Load contract artifact cho 1 task.
    Đây là hàm DEV agent và TESTER agent gọi — không đọc tasks.json trực tiếp.

    Returns:
        dict contract hoặc None nếu chưa compile.
    """
    path = os.path.join(contracts_dir, f"{task_id}.contract.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_contracts(contracts_dir: str = "docs/contracts") -> list[str]:
    """Trả về danh sách task_id đã có contract file."""
    if not os.path.exists(contracts_dir):
        return []
    return [
        f.replace(".contract.json", "")
        for f in sorted(os.listdir(contracts_dir))
        if f.endswith(".contract.json")
    ]
