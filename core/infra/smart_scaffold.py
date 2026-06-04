"""
smart_scaffold.py — KG-driven scaffold with [SLOT] markers (v1)

Replaces scaffold_agent.py.

Key differences from old scaffold_agent:
  OLD: write blank-but-valid files (empty main.py with /health only)
  NEW: write files WITH typed [SLOT] markers so:
       1. The file is immediately valid (tsc --noEmit / py_compile pass)
       2. Dev agent fills ONLY the slot region — nothing else changes
       3. Slot injector can patch slot → real code with surgical precision

Slot marker format (inline, parseable):
  Python: # [ROUTES_SLOT]
  TypeScript: {/* [ROUTES_SLOT] */}

Verification:
  Backend: py_compile.compile(main.py) — catches import errors immediately
  Frontend: subprocess tsc --noEmit — catches missing imports before test time

Usage (from adapter_agent.py):
    from smart_scaffold import write_smart_scaffold, verify_smart_scaffold
    result = write_smart_scaffold(pos_app_dir, component, contract, plan)
    ok, err = verify_smart_scaffold(pos_app_dir, component, contract)
"""

import os
import sys
import importlib
import subprocess
import py_compile
import json
from typing import Optional

from planning.structure_planner import load_plan, SLOT_MARKERS


# ══════════════════════════════════════════════════════════════════════════════
# BACKEND TEMPLATES WITH SLOTS
# ══════════════════════════════════════════════════════════════════════════════
FRONTEND_SHARED_FILES = {
    "package.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "index.html",
    "main.tsx"
}
def write_file_safely(target_path: str, default_scaffold_content: str):
    """
    Hàm ghi file có Guard bảo vệ chống xung đột add/add.
    Nếu file đã được tạo bởi task trước đó, giữ nguyên file để slot_injector chèn code.
    """
    if os.path.exists(target_path):
        print(f"      [scaffold-guard] File đã tồn tại: {target_path} -> GIỮ NGUYÊN (Kế thừa gối đầu)")
        return
        
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(default_scaffold_content)
    print(f"      [scaffold-guard] Khởi tạo file mới: {target_path}")
def write_frontend_infra_once(pos_app_dir: str) -> dict:
    """
    [FIX BUG-A1] Viết shared frontend infra files MỘT LẦN DUY NHẤT.
 
    Phải được gọi KHI ĐANG Ở TRÊN DEVELOP BRANCH, TRƯỚC KHI bất kỳ
    feature branch frontend nào được tạo.
 
    Các file này KHÔNG BAO GIỜ được viết lại trên feature branch.
    Sau khi committed lên develop, mọi feature branch checkout từ develop
    sẽ có sẵn → không có add/add conflict khi merge.
 
    Trả về: {"written": int, "skipped": int}
    """
    # Import ở đây để hàm này có thể được paste vào smart_scaffold.py
    import os
 
    # Cần import các hàm _make_* từ cùng file — chúng đã có sẵn trong smart_scaffold.py
    # Nếu paste vào file khác thì import thêm:
    # from smart_scaffold import (
    #     _make_frontend_package_json, _make_frontend_tsconfig,
    #     _make_frontend_tsconfig_node, _make_frontend_vite_config,
    #     _make_frontend_index_html, _make_frontend_main_tsx, _make_frontend_app_tsx,
    #     _write_if_missing,
    # )
 
    frontend_dir = os.path.join(pos_app_dir, "src/frontend")
    src_dir = os.path.join(frontend_dir, "src")
    os.makedirs(src_dir, exist_ok=True)
 
    infra = {
        os.path.join(frontend_dir, "package.json"):       _make_frontend_package_json(),
        os.path.join(frontend_dir, "tsconfig.json"):      _make_frontend_tsconfig(),
        os.path.join(frontend_dir, "tsconfig.node.json"): _make_frontend_tsconfig_node(),
        os.path.join(frontend_dir, "vite.config.ts"):     _make_frontend_vite_config(),
        os.path.join(frontend_dir, "index.html"):         _make_frontend_index_html(),
        os.path.join(src_dir, "main.tsx"):                _make_frontend_main_tsx(),
        os.path.join(src_dir, "App.tsx"):                 _make_frontend_app_tsx([]),
    }
 
    written = skipped = 0
    for fpath, content in infra.items():
        if _write_if_missing(fpath, content, pos_app_dir):
            written += 1
        else:
            skipped += 1
 
    print(f"      [frontend-infra] wrote={written}, skipped={skipped} (shared files committed to develop)")
    return {"written": written, "skipped": skipped}
def write_smart_scaffold_patched(
    pos_app_dir: str,
    component: str,
    contract: dict,
    plan=None,
) -> dict:
    """
    [FIX BUG-A1] Phiên bản đã patch của write_smart_scaffold().
 
    Thay đổi DUY NHẤT so với bản gốc:
      - Khi component là "frontend" hoặc "fullstack", SKIP viết infra files.
        Infra files đã được write_frontend_infra_once() viết trên develop.
      - Chỉ viết page files, api client files, store files (các file riêng của task này).
 
    Trong smart_scaffold.py, tìm khối "# Infra files" và thay thế:
 
    CŨ:
        infra = {
            os.path.join(frontend_dir, "package.json"):       _make_frontend_package_json(),
            ...7 files...
        }
        for fpath, content in infra.items():
            if _write_if_missing(fpath, content, pos_app_dir):
                written += 1
            else:
                skipped += 1
 
    MỚI:
        # [FIX BUG-A1] Shared infra files đã được write_frontend_infra_once() viết trên develop.
        # Trên feature branch: KHÔNG viết lại để tránh add/add conflict khi merge.
        # Chỉ viết files riêng của task này (pages, api clients, stores).
        print(f"      [smart-scaffold] SKIP shared infra (already on develop branch)")
        skipped += len(FRONTEND_SHARED_FILES)
    """
    import os
    source_dir = contract.get("source_dir", "src/backend")
    routes = contract.get("routes", [])
    written = skipped = 0

    files_to_write = plan["files"] if plan else []

    # Backend — delegate hoàn toàn về write_smart_scaffold gốc (không shared, không conflict)
    if component in ("backend", "fullstack"):
        backend_result = write_smart_scaffold(pos_app_dir, "backend", contract, plan)
        written += backend_result["written"]
        skipped += backend_result["skipped"]

    # Frontend — chỉ viết files riêng của task
    if component in ("frontend", "fullstack"):
        frontend_dir = _resolve_frontend_dir(pos_app_dir, plan)
        src_dir = os.path.join(frontend_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
 
        # [FIX] SKIP shared infra files — đã được viết trên develop
        print(f"      [smart-scaffold] SKIP shared infra (already on develop branch)")
        skipped += len(FRONTEND_SHARED_FILES)
 
        # Pages — riêng của task này (KHÔNG shared)
        page_files = [f for f in files_to_write if f.get("role") == "page"]
        for fe in page_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            page_name = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_page(page_name, routes), pos_app_dir):
                written += 1
            else:
                skipped += 1
 
        # API client files — riêng của task này
        api_files = [f for f in files_to_write if f.get("role") == "api_client"]
        for fe in api_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            resource = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_api_client(resource, routes), pos_app_dir):
                written += 1
            else:
                skipped += 1
 
        # Store files — riêng của task này
        store_files = [f for f in files_to_write if f.get("role") == "store"]
        for fe in store_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            resource = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_store(resource), pos_app_dir):
                written += 1
            else:
                skipped += 1
 
    print(f"      [smart-scaffold] done — wrote={written}, skipped={skipped}")
    return {"written": written, "skipped": skipped}
def _make_backend_main(router_slots: list[str], source_dir: str) -> str:
    """
    Generate main.py with a MAIN_ROUTER_SLOT placeholder.
    router_slots: list of route file stems (e.g. ["products", "cart"])
    """
    router_import_block = "# [MAIN_ROUTER_SLOT]\n# Dev agent: replace this block with actual include_router calls"
    return f"""\
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {{"status": "ok"}}


{router_import_block}
"""


def _make_backend_route_file(resource_name: str, routes: list[dict]) -> str:
    """
    Generate a route file stub with ROUTES_SLOT.
    The slot is a syntactically valid pass-through so py_compile succeeds.
    """
    route_comments = "\n".join(
        f"#   {r.get('method','GET').upper()} {r.get('path','/')} → {r.get('status_code',200)}"
        for r in routes
    )
    return f"""\
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()

# Contract routes to implement:
{route_comments or "#   (no routes defined in contract)"}

# [ROUTES_SLOT]
# Dev agent: implement each route above, replacing this comment block.
# Rules:
#   - Use EXACT paths, methods, status_codes from the contract above
#   - Return ALL response_fields listed in contract
#   - Handle ALL error cases
#   - NEVER return empty {{}} for 200/201 responses
"""

def clear_scaffold_for_retry(pos_app_dir: str, contract: dict) -> None:
    """
    Xóa sạch các generated files lỗi/rác để scaffold viết lại sạch khi retry.
    """
    source_dir = contract.get("source_dir", "src/backend")
    backend_root = os.path.join(pos_app_dir, source_dir)
    
    if not os.path.exists(backend_root):
        return

    KEEP = {"__init__.py", ".venv"} # Giữ lại cấu trúc lõi và môi trường ảo
    cleared = 0
    
    # Duyệt toàn bộ thư mục gốc của service (ví dụ: src/services/auth)
    for root, dirs, files in os.walk(backend_root):
        # Không đụng vào thư mục môi trường ảo nếu có
        if ".venv" in root.split(os.sep):
            continue
        for fname in files:
            if fname in KEEP:
                continue
            fpath = os.path.join(root, fname)
            try:
                os.remove(fpath)
                cleared += 1
            except Exception:
                pass
    
    print(f"      [scaffold-reset] Cleared {cleared} files (including agent hallucinated files) for clean retry")
 
def _make_backend_model_file(resource_name: str, routes: list = []) -> str:
    """
    Generate model file scaffold with [MODEL_SLOT].
    Injects exact field names from contract routes into the slot comment
    so dev agent uses the correct field names (not invented ones like 'access_token').
    """
    routes = routes or []

    # Build per-route field hints from contract
    route_hints = []
    for route in routes:
        method = route.get("method", "").upper()
        path = route.get("path", "")
        req_body = route.get("request_body") or {}
        resp_fields = route.get("response_fields") or route.get("response_example") or {}
        status = route.get("status_code", 200)

        if req_body:
            fields_str = ", ".join(f"{k}: {v}" for k, v in req_body.items())
            route_hints.append(f"#   {method} {path} → request_body: {{{fields_str}}}")
        if resp_fields:
            fields_str = ", ".join(f"{k}: {v}" for k, v in resp_fields.items())
            route_hints.append(f"#   {method} {path} → response ({status}): {{{fields_str}}}")

    hints_block = "\n".join(route_hints) if route_hints else f"#   (no routes defined for {resource_name})"

    return f"""\
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional, List, Dict, Any


# [MODEL_SLOT]
# Dev agent: define Pydantic models for {resource_name} here.
# Use ConfigDict(from_attributes=True) — never class Config.
#
# CRITICAL — USE EXACT FIELD NAMES FROM CONTRACT BELOW (do NOT rename them):
{hints_block}
#
# Example for auth:
#   class UserCreate(BaseModel):
#       email: EmailStr
#       password: str
#
#   class TokenResponse(BaseModel):
#       token: str          ← MUST be 'token', not 'access_token'
#       token_type: str = 'bearer'
"""


def _make_backend_requirements():
    return """\
fastapi>=0.115.0
uvicorn>=0.30.0
pydantic>=2.13.0
pydantic-core>=2.46.0
httpx>=0.28.1
email-validator>=2.0

PyJWT>=2.8.0

passlib[bcrypt]==1.7.4
bcrypt>=3.2.0,<4.0.0
"""


# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND TEMPLATES WITH SLOTS
# ══════════════════════════════════════════════════════════════════════════════

def _make_frontend_app_tsx(page_slots: list[str]) -> str:
    """
    Generate App.tsx with a ROUTES_SLOT for page wiring.
    Already valid TypeScript — tsc --noEmit passes.
    """
    return """\
import React from 'react'

// [ROUTES_SLOT]
// Dev agent: add page imports and routing here.
// Example:
//   import CartPage from './pages/CartPage'
//   function App() { return <CartPage /> }
//
// IMPORTANT: keep the export default App line below unchanged.

function App() {
  return <div><h1>Loading...</h1></div>
}

export default App
"""


def _make_frontend_page(page_name: str, routes: list[dict]) -> str:
    api_comments = "\n".join(
        f"// {r.get('method','GET').upper()} {r.get('path','/')}"
        for r in routes
    ) or "// (no API routes in contract)"
    return f"""\
import React from 'react'

// API contract for this page:
{api_comments}

// [PAGE_SLOT]
// Dev agent: replace everything below with the full {page_name} component.
// Output must contain: export default function {page_name}() {{ ... }}
// DO NOT copy this comment block into your output.
"""

def _make_frontend_api_client(resource_name: str, routes: list[dict]) -> str:
    fn_comments = "\n".join(
        f"// {r.get('method','GET').upper()} {r.get('path','/')}"
        for r in routes
    )
    return f"""\
// API client for {resource_name}
// Contract endpoints:
{fn_comments or "// (no routes defined)"}

const BASE = import.meta.env.VITE_API_URL ?? ''

// [API_CLIENT_SLOT]
// Dev agent: implement typed fetch functions here.
// Each function must match one contract route above.
// DO NOT copy this comment block into your output.
"""

def _make_frontend_store(resource_name: str) -> str:
    return f"""\
import {{ useState }} from 'react'

// [STORE_SLOT]
// Dev agent: implement {resource_name} state management here.
// Use React useState/useReducer or zustand if in package.json.
// DO NOT copy this comment block into your output.
"""

def _make_frontend_package_json() -> str:
    return """\
{
  "name": "frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "@vitejs/plugin-react": "^4.0.0",
    "typescript": "^5.0.0",
    "vite": "^4.4.0",
    "tailwindcss": "^3.4.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0"
  }
}
"""

def _make_frontend_tsconfig() -> str:
    return """\
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
"""


def _make_frontend_tsconfig_node() -> str:
    return """\
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
"""


def _make_frontend_vite_config() -> str:
    return """\
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\\/api/, ''),
      },
    },
  },
})
"""


def _make_frontend_index_html() -> str:
    return """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>App</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  </head>
  <body class="bg-gray-50 font-sans text-gray-900">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""


def _make_frontend_main_tsx() -> str:
    return """\
import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _write_if_missing(fpath: str, content: str, pos_app_dir: str) -> bool:
    """Write file only if it doesn't exist. Return True if written."""
    rel = os.path.relpath(fpath, pos_app_dir)
    
    # Kích hoạt Scaffold Guard bảo vệ file dùng chung gối đầu từ task trước
    if os.path.exists(fpath):
        print(f"      [scaffold-guard] File đã tồn tại: {rel} -> GIỮ NGUYÊN (Kế thừa gối đầu)")
        return False
        
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"      [scaffold-guard] Khởi tạo file mới: {rel}")
    return True


def _stem(filepath: str) -> str:
    """Extract resource name stem from filepath."""
    return os.path.splitext(os.path.basename(filepath))[0]


def _resource_name(filepath: str) -> str:
    """Convert filepath stem to PascalCase resource name."""
    stem = _stem(filepath)
    return "".join(w.capitalize() for w in stem.replace("-", "_").split("_"))


# ══════════════════════════════════════════════════════════════════════════════
# CORE: WRITE SCAFFOLD FOR ONE TASK
# ══════════════════════════════════════════════════════════════════════════════
def _make_tailwind_config() -> str:
    return """\
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'sans-serif'] },
    },
  },
  plugins: [],
}
"""

def _make_postcss_config() -> str:
    return """\
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
"""

def _make_frontend_index_css() -> str:
    return """\
@tailwind base;
@tailwind components;
@tailwind utilities;
"""

def write_smart_scaffold(
    pos_app_dir: str,
    component: str,
    contract: dict,
    plan: Optional[dict] = None,
) -> dict:
    """
    Write empty-but-valid files WITH [SLOT] markers.

    Args:
        pos_app_dir: root dir of generated project
        component:   "backend" | "frontend" | "fullstack"
        contract:    loaded contract dict
        plan:        structure plan dict (from load_plan); if None, falls back to
                     basic scaffold (backward compatible with old scaffold_agent)

    Returns: {"written": int, "skipped": int}
    """
    source_dir = contract.get("source_dir", "src/backend")
    routes = contract.get("routes", [])
    written = skipped = 0

    files_to_write = plan["files"] if plan else []
    file_paths = [f["path"] for f in files_to_write]

    # ── BACKEND ──────────────────────────────────────────────────────────────
    if component in ("backend", "fullstack"):
        backend_root = os.path.join(pos_app_dir, source_dir)
        app_dir = os.path.join(backend_root, "app")
        routes_dir = os.path.join(app_dir, "routes")
        models_dir = os.path.join(app_dir, "models")

        os.makedirs(routes_dir, exist_ok=True)
        os.makedirs(models_dir, exist_ok=True)

        # Always write: __init__.py files
        for init_path in [
            os.path.join(app_dir, "__init__.py"),
            os.path.join(routes_dir, "__init__.py"),
            os.path.join(models_dir, "__init__.py"),
        ]:
            if _write_if_missing(init_path, "", pos_app_dir):
                written += 1
            else:
                skipped += 1

        # requirements.txt
        req_path = os.path.join(backend_root, "requirements.txt")
        if _write_if_missing(req_path, _make_backend_requirements(), pos_app_dir):
            written += 1
        else:
            skipped += 1

        # main.py — with MAIN_ROUTER_SLOT
        main_path = os.path.join(app_dir, "main.py")
        router_stems = [
            _stem(f["path"]) for f in files_to_write
            if f.get("role") == "routes"
        ] if files_to_write else []
        if _write_if_missing(main_path, _make_backend_main(router_stems, source_dir), pos_app_dir):
            written += 1
        else:
            skipped += 1

        # Route files from plan
        route_files = [f for f in files_to_write if f.get("role") == "routes"]
        if route_files:
            for fe in route_files:
                fpath = os.path.join(pos_app_dir, fe["path"])
                resource = _resource_name(fe["path"])
                # Filter routes relevant to this file by path prefix
                stem = _stem(fe["path"])
                relevant_routes = [
                    r for r in routes
                    if stem.lower() in r.get("path", "").lower()
                ] or routes
                if _write_if_missing(fpath, _make_backend_route_file(resource, relevant_routes), pos_app_dir):
                    written += 1
                else:
                    skipped += 1
        else:
            # Fallback: infer route files from contract paths
            resource_names = set()
            for r in routes:
                parts = [p for p in r.get("path", "").split("/") if p and not p.startswith("{")]
                if parts:
                    resource_names.add(parts[0])
            for rname in resource_names:
                fpath = os.path.join(routes_dir, f"{rname}.py")
                relevant = [r for r in routes if f"/{rname}" in r.get("path", "")]
                if _write_if_missing(fpath, _make_backend_route_file(rname, relevant), pos_app_dir):
                    written += 1
                else:
                    skipped += 1

        # Model files from plan
        model_files = [f for f in files_to_write if f.get("role") == "model"]
        for fe in model_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            resource = _resource_name(fe["path"])
            if _write_if_missing(fpath, _make_backend_model_file(resource, routes), pos_app_dir):
                written += 1
            else:
                skipped += 1

    # ── FRONTEND ──────────────────────────────────────────────────────────────
    if component in ("frontend", "fullstack"):
        # Determine frontend dir
        frontend_dir = _resolve_frontend_dir(pos_app_dir, plan)
        src_dir = os.path.join(frontend_dir, "src")
        os.makedirs(src_dir, exist_ok=True)

        # Infra files
        infra = {
            os.path.join(frontend_dir, "package.json"):       _make_frontend_package_json(),
            os.path.join(frontend_dir, "tsconfig.json"):      _make_frontend_tsconfig(),
            os.path.join(frontend_dir, "tsconfig.node.json"): _make_frontend_tsconfig_node(),
            os.path.join(frontend_dir, "vite.config.ts"):     _make_frontend_vite_config(),
            os.path.join(frontend_dir, "index.html"):         _make_frontend_index_html(),
            os.path.join(src_dir, "main.tsx"):                _make_frontend_main_tsx(),
            os.path.join(src_dir, "App.tsx"):                 _make_frontend_app_tsx([]),
            os.path.join(src_dir, "index.css"): _make_frontend_index_css(),
        }
        for fpath, content in infra.items():
            if _write_if_missing(fpath, content, pos_app_dir):
                written += 1
            else:
                skipped += 1

        # Tailwind + PostCSS config (nằm ngoài infra dict do tái sử dụng hàm)
        tailwind_config = os.path.join(frontend_dir, "tailwind.config.js")
        postcss_config  = os.path.join(frontend_dir, "postcss.config.js")
        if _write_if_missing(tailwind_config, _make_tailwind_config(), pos_app_dir):
            written += 1
        else:
            skipped += 1
        if _write_if_missing(postcss_config, _make_postcss_config(), pos_app_dir):
            written += 1
        else:
            skipped += 1


        # Pages from plan
        page_files = [f for f in files_to_write if f.get("role") == "page"]
        for fe in page_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            page_name = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_page(page_name, routes), pos_app_dir):
                written += 1
            else:
                skipped += 1

        # API client files from plan
        api_files = [f for f in files_to_write if f.get("role") == "api_client"]
        for fe in api_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            resource = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_api_client(resource, routes), pos_app_dir):
                written += 1
            else:
                skipped += 1

        # Store files from plan
        store_files = [f for f in files_to_write if f.get("role") == "store"]
        for fe in store_files:
            fpath = os.path.join(pos_app_dir, fe["path"])
            resource = _resource_name(fe["path"])
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            if _write_if_missing(fpath, _make_frontend_store(resource), pos_app_dir):
                written += 1
            else:
                skipped += 1

    print(f"      [smart-scaffold] done — wrote={written}, skipped={skipped}")
    return {"written": written, "skipped": skipped}


def _resolve_frontend_dir(pos_app_dir: str, plan: Optional[dict]) -> str:
    """Resolve frontend root from plan source_dir or architecture.json."""
    if plan and plan.get("component") == "frontend":
        src = plan.get("source_dir", "src/frontend")
        return os.path.join(pos_app_dir, src)

    # Try architecture.json
    arch_path = "docs/architecture.json"
    if os.path.exists(arch_path):
        try:
            with open(arch_path, encoding="utf-8") as f:
                arch = json.load(f)
            for svc in arch.get("services", []):
                if svc.get("component") == "frontend":
                    fs = svc.get("file_structure", [])
                    if fs:
                        parts = fs[0].split("/")
                        if len(parts) >= 2:
                            return os.path.join(pos_app_dir, "/".join(parts[:2]))
        except Exception:
            pass

    return os.path.join(pos_app_dir, "src/frontend")


# ══════════════════════════════════════════════════════════════════════════════
# VERIFICATION — REAL STATIC ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def verify_smart_scaffold(
    pos_app_dir: str,
    component: str,
    contract: dict,
    plan: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """
    Run REAL static analysis on scaffold:
      - Backend: py_compile on all .py files
      - Frontend: tsc --noEmit (catches missing imports before test time)

    Returns: (ok, error_message)
    """
    source_dir = contract.get("source_dir", "src/backend")
    errors = []
    if plan is None:
        try:
            plan = load_plan(pos_app_dir)
        except Exception:
            plan = None

    source_dir = contract.get("source_dir", "src/backend")
    errors = []
    # ── Backend: py_compile ───────────────────────────────────────────────────
    if component in ("backend", "fullstack"):
        app_dir = os.path.join(pos_app_dir, source_dir, "app")
        if os.path.isdir(app_dir):
            for root, _, files in os.walk(app_dir):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        py_compile.compile(fpath, doraise=True)
                    except py_compile.PyCompileError as e:
                        rel = os.path.relpath(fpath, pos_app_dir)
                        errors.append(f"py_compile: {rel}: {e}")

            if not errors:
                print(f"      [smart-scaffold-verify] backend py_compile OK ({source_dir}/app/)")
            else:
                print(f"      [smart-scaffold-verify] backend py_compile FAIL: {errors[0]}")
                return False, "\n".join(errors)

        # ── Backend: Cô lập môi trường cài đặt & kiểm tra Import ──────────────────
        try:
            req_file = os.path.join(pos_app_dir, source_dir, "requirements.txt")
        
            # 1. Xác định file python.exe độc lập của ứng dụng mục tiêu (pos-app-test_v2)
            import subprocess as _sp

            venv_dir = os.path.join(pos_app_dir, ".venv")

            def _find_venv_python(venv_dir: str):
                for candidate in [
                    os.path.join(venv_dir, "Scripts", "python.exe"),  # Windows
                    os.path.join(venv_dir, "Scripts", "python"),
                    os.path.join(venv_dir, "bin", "python3"),          # Linux/Mac
                    os.path.join(venv_dir, "bin", "python"),
                ]:
                    if os.path.exists(candidate):
                        return candidate
                return None

            target_python = _find_venv_python(venv_dir)

            if target_python is None:
                print(f"      [smart-scaffold-verify] .venv not found — creating...")
                _r_venv = _sp.run(
                    [sys.executable, "-m", "venv", venv_dir],
                    capture_output=True, text=True
                )
                if _r_venv.returncode != 0:
                    return False, f"venv creation failed: {_r_venv.stderr[:300]}"
                target_python = _find_venv_python(venv_dir)

            if target_python is None:
                return False, f"Cannot find python in venv after creation: {venv_dir}"

            if os.path.exists(req_file):
                import subprocess as _sp
                print(f"      [smart-scaffold-verify] Installing requirements into isolated env: {target_python}")
                
                # [FIX WINDOWS LOCK] Ép buộc cài đặt bằng chính python/pip của môi trường đích (.venv)
                # Bổ sung --no-cache-dir để tránh việc đọc ghi đè vào thư mục cache chung gây file lock trên Windows
                # Bổ sung --no-warn-script-location để tắt cảnh báo script path
                _r = _sp.run(
                    [
                        target_python,
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        req_file,
                        "--no-cache-dir",
                        "--disable-pip-version-check",
                        "--no-input"
                    ],
                    capture_output=True,
                    text=True
                )

                if _r.returncode != 0:

                    err = (_r.stderr or "").lower()

                    windows_lock_signals = [
                        "being used by another process",
                        "winerror 32",
                        "permission denied",
                        "access is denied",
                        "file is in use"
                    ]

                    if any(x in err for x in windows_lock_signals):

                        print(
                            "      [smart-scaffold-verify] "
                            "Windows pip file-lock detected → SKIP install verification"
                        )

                    else:
                        return False, (
                            f"scaffold import failed:\n"
                            f"{_r.stderr[:500]}"
                        )

            # 2. Chạy thử nghiệm lệnh import bằng một tiến trình con hoàn toàn độc lập 
            backend_root = os.path.join(pos_app_dir, source_dir)
            import subprocess as _sp
            
            _r_import = _sp.run(
                [
                    target_python,
                    "-c",
                    "import sys\nsys.path.insert(0,'.')\nimport app.main\nprint('OK')"
                ],
                cwd=backend_root,
                capture_output=True,
                text=True
            )
            
            if _r_import.returncode != 0:
                stderr = _r_import.stderr or ""
                # Broken pip internal — lỗi môi trường, không phải code của task
                pip_internal_signals = ["inject_securetransport", "pip._internal", "pip\\_internal"]
                if any(sig in stderr for sig in pip_internal_signals):
                    print(f"      [smart-scaffold-verify] WARNING: broken pip in venv → SKIP import check")
                else:
                    return False, f"scaffold import failed: {stderr[:300]}"
            
            print(f"      [smart-scaffold-verify] backend import OK (Isolated standard)")
        except Exception as e:
            return False, f"scaffold import failed: {e}"

    # ── Frontend: tsc --noEmit ────────────────────────────────────────────────
    if component in ("frontend", "fullstack"):
        frontend_dir = _resolve_frontend_dir(pos_app_dir, plan)

        if not os.path.exists(os.path.join(frontend_dir, "tsconfig.json")):
            print(f"      [smart-scaffold-verify] frontend tsconfig.json missing — skip tsc check")
        elif not os.path.exists(os.path.join(frontend_dir, "node_modules")):
            print(f"      [smart-scaffold-verify] node_modules not installed — skip tsc check")
        else:
            result = subprocess.run(
                "npx tsc --noEmit",
                shell=True,
                capture_output=True,
                text=True,
                cwd=frontend_dir,
                encoding="utf-8",
                errors="ignore",
                timeout=60,
            )
            if result.returncode != 0:
                tsc_errors = result.stdout + result.stderr
                print(f"      [smart-scaffold-verify] tsc --noEmit FAIL")
                # Only fail on actual errors (not slot-comment warnings)
                real_errors = [
                    line for line in tsc_errors.splitlines()
                    if "error TS" in line
                ]
                if real_errors:
                    return False, f"tsc errors:\n" + "\n".join(real_errors[:10])
                print(f"      [smart-scaffold-verify] tsc warnings only — treating as OK")
            else:
                print(f"      [smart-scaffold-verify] frontend tsc --noEmit OK")

    return True, None

# ══════════════════════════════════════════════════════════════════════════════
# STATIC ANALYSIS PIPELINE (called after dev agent fills slots)
# ══════════════════════════════════════════════════════════════════════════════

def run_static_analysis(
    pos_app_dir: str,
    component: str,
    contract: dict,
    plan: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """
    Run comprehensive static analysis AFTER dev agent fills slots.
    Catches errors BEFORE tester agent runs — much faster feedback loop.

    Returns: (all_passed, list_of_errors)
    """
    if plan is None:
        try:
            plan = load_plan(pos_app_dir)
        except Exception:
            plan = None

    source_dir = contract.get("source_dir", "src/backend")
    all_errors = []

    # ── 1. Python: py_compile on ALL .py files ─────────────────────────────
    if component in ("backend", "fullstack"):
        backend_root = os.path.join(pos_app_dir, source_dir)
        for root, _, files in os.walk(backend_root):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    py_compile.compile(fpath, doraise=True)
                except py_compile.PyCompileError as e:
                    rel = os.path.relpath(fpath, pos_app_dir)
                    all_errors.append(f"[py_compile] {rel}: {e}")

        if not any("[py_compile]" in e for e in all_errors):
            print(f"      [static-analysis] py_compile: ALL PASS")
        else:
            for e in [x for x in all_errors if "[py_compile]" in x]:
                print(f"      [static-analysis] {e}")

    # ── 2. Frontend: tsc --noEmit ──────────────────────────────────────────
    if component in ("frontend", "fullstack"):
        frontend_dir = _resolve_frontend_dir(pos_app_dir, plan)
        node_modules = os.path.join(frontend_dir, "node_modules")
        tsconfig = os.path.join(frontend_dir, "tsconfig.json")

        if os.path.exists(tsconfig) and os.path.exists(node_modules):
            try:
                result = subprocess.run(
                    "npx tsc --noEmit 2>&1",
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=frontend_dir,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                print(f"      [static-analysis] tsc: TIMEOUT — skipping")
                result = None
            if result is not None and result.returncode != 0:
                tsc_out = result.stdout + result.stderr
                real_errors = [l for l in tsc_out.splitlines() if "error TS" in l]
                for e in real_errors:
                    all_errors.append(f"[tsc] {e}")
                if real_errors:
                    print(f"      [static-analysis] tsc: {len(real_errors)} errors")
                else:
                    print(f"      [static-analysis] tsc: warnings only (OK)")
            elif result is not None:
                print(f"      [static-analysis] tsc: PASS")
        elif not os.path.exists(node_modules):
            print(f"      [static-analysis] tsc: SKIP (node_modules not installed)")

    # ── 3. Frontend: npm run build (catches bundler errors) ───────────────
    if component in ("frontend", "fullstack"):
        frontend_dir = _resolve_frontend_dir(pos_app_dir, plan)
        pkg_json = os.path.join(frontend_dir, "package.json")
        node_modules = os.path.join(frontend_dir, "node_modules")
        if os.path.exists(pkg_json) and os.path.exists(node_modules):
            try:
                result = subprocess.run(
                    "npm run build 2>&1",
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=frontend_dir,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                print(f"      [static-analysis] npm build: TIMEOUT — skipping")
                result = None
            if result is not None and result.returncode != 0:
                build_out = result.stdout + result.stderr
                # Extract meaningful errors
                build_errors = [
                    l for l in build_out.splitlines()
                    if any(k in l for k in ("error", "Error", "ERROR", "✗", "×"))
                ]
                for e in build_errors[:5]:
                    all_errors.append(f"[npm-build] {e}")
                print(f"      [static-analysis] npm build: FAIL ({len(build_errors)} errors)")
            elif result is not None:
                print(f"      [static-analysis] npm build: PASS")
        elif not os.path.exists(node_modules):
            print(f"      [static-analysis] npm build: SKIP (node_modules not installed)")

    passed = len(all_errors) == 0
    return passed, all_errors