# indexer.py — thêm vào root pipeline
"""
scan src/backend/app/ và src/frontend/src/ sau khi mỗi task được viết xong, 
lưu graph vào docs/code_graph.json.
Không cần NetworkX, dùng ast + dict thuần Python.
"""
import ast, os, json, re


def _extract_python_node(fpath: str, rel: str, pos_app_dir: str) -> tuple[dict, list[dict]]:
    """Parse một Python file, trả về (node_dict, edges_list)."""
    try:
        source = open(fpath, encoding="utf-8").read()
        tree = ast.parse(source)
    except Exception:
        return {"id": rel, "type": "component", "component": "backend", "text": f"Error reading {rel}"}, []

    symbols = []   # module-level names: _db, router, _next_id ...
    functions = [] # def foo(...) at module level
    routes = []    # @router.get("/...") → "GET /..."
    imports = []   # internal import edges

    for node in ast.walk(tree):
        # Module-level assignments → symbols
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    symbols.append(t.id)

        # Module-level annotated assignments  (x: int = 0)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                symbols.append(node.target.id)

        # Function / async function definitions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

            # Check decorators for FastAPI routes
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                if not isinstance(deco.func, ast.Attribute):
                    continue
                method = deco.func.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                path_arg = ""
                if deco.args and isinstance(deco.args[0], ast.Constant):
                    path_arg = deco.args[0].value
                routes.append(f"{method} {path_arg}")

        # Internal import edges
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            if module.startswith("app") or module.startswith("."):
                imports.append(module)

    # Build a short text summary for LLM context
    text_parts = []
    if routes:
        text_parts.append("Routes: " + ", ".join(routes))
    if symbols:
        text_parts.append("Module-level vars: " + ", ".join(symbols))

    node = {
        "id": rel,
        "type": "code",
        "component": "backend",
        "symbols": symbols,
        "functions": functions,
        "routes": routes,
        "imports": imports,
        "text": ". ".join(text_parts) if text_parts else f"Python module {rel}",
    }

    edges = [{"from": rel, "to": imp, "rel": "imports"} for imp in imports]
    return node, edges


def _extract_ts_node(fpath: str, rel: str, pos_app_dir: str) -> tuple[dict, list[dict]]:
    """Parse một TypeScript/TSX file, trả về (node_dict, edges_list)."""
    try:
        source = open(fpath, encoding="utf-8", errors="ignore").read()
    except Exception:
        return {"id": rel, "type": "component", "component": "frontend", "text": f"Error reading {rel}"}, []

    # Exported types / interfaces / components
    exports = re.findall(
        r"export\s+(?:default\s+)?(?:function|class|const|interface|type)\s+(\w+)",
        source,
    )
    # Also catch `export { Foo, Bar }` re-exports
    reexports = re.findall(r"export\s*\{([^}]+)\}", source)
    for group in reexports:
        exports.extend(n.strip().split(" as ")[0].strip() for n in group.split(","))

    # Internal imports (relative paths)
    imports = re.findall(r"from\s+['\"]([./][^'\"]+)['\"]", source)

    # API calls referenced in source
    api_calls = re.findall(r"(?:fetch|axios\.\w+)\s*\(\s*[`'\"]([^`'\"]+)[`'\"]", source)

    # Short summary
    text_parts = []
    if exports:
        text_parts.append("Exports: " + ", ".join(exports[:10]))
    if api_calls:
        text_parts.append("API calls: " + ", ".join(api_calls[:5]))

    node = {
        "id": rel,
        "type": "component",
        "component": "frontend",
        "exports": list(dict.fromkeys(exports)),   # deduplicate, preserve order
        "imports": imports,
        "api_calls": api_calls,
        "text": ". ".join(text_parts) if text_parts else f"TypeScript module {rel}",
    }

    edges = [{"from": rel, "to": imp, "rel": "imports"} for imp in imports]
    return node, edges


def build_graph(pos_app_dir: str) -> dict:
    graph: dict = {"nodes": {}, "edges": []}

    # ── Backend: Python files ──────────────────────────────────────────────
    backend_root = os.path.join(pos_app_dir, "src/backend/app")
    if os.path.isdir(backend_root):
        for root, _, files in os.walk(backend_root):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, pos_app_dir).replace("\\", "/")
                node, edges = _extract_python_node(fpath, rel, pos_app_dir)
                if node:
                    graph["nodes"][rel] = node
                    graph["edges"].extend(edges)

    # ── Frontend: TypeScript files ─────────────────────────────────────────
    frontend_root = os.path.join(pos_app_dir, "src/frontend/src")
    if os.path.isdir(frontend_root):
        for root, _, files in os.walk(frontend_root):
            for fname in files:
                if not fname.endswith((".ts", ".tsx")):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, pos_app_dir).replace("\\", "/")
                node, edges = _extract_ts_node(fpath, rel, pos_app_dir)
                if node:
                    graph["nodes"][rel] = node
                    graph["edges"].extend(edges)

    return graph


def save_graph(graph: dict, out_path: str = "docs/code_graph.json"):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)