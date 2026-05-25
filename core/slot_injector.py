"""
slot_injector.py — Patch [SLOT] markers in scaffold files (v2)

FIXES vs v1:
  [BUG-1] MAIN_ROUTER_SLOT never filled:
          inject_all_slots used mode="replace" for ALL slots.
          After attempt 1, MAIN_ROUTER_SLOT marker was consumed → attempt 2
          found no marker → fell through to overwrite entire main.py with only
          include_router lines (no FastAPI app definition) → import crash.
          FIX: MAIN_ROUTER_SLOT always uses mode="append" + dedup via
               inject_main_router(). inject_all_slots detects the slot name
               and dispatches accordingly.

  [BUG-2] Route duplicate on retry:
          When slot marker already consumed, fallback was "overwrite whole file".
          For route files this was fine (LLM outputs full file on retry),
          but for main.py it overwrote the scaffold with only router lines.
          FIX: If MAIN_ROUTER_SLOT already consumed (slot gone), parse
               router lines from generated code and call inject_main_router
               which appends-at-EOF with dedup. Never overwrite main.py blind.

  [BUG-3] Stale sys.path between attempts:
          Not fixed here (adapter_v2.py concern), but inject_all_slots now
          returns per-file slot status so adapter can detect which files
          need reprocessing.
"""

import os
import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# SLOT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_SLOT_PATTERNS = [
    re.compile(r'^\s*#\s*\[(\w+_SLOT)\].*$', re.MULTILINE),
    re.compile(r'^\s*\{/\*\s*\[(\w+_SLOT)\]\s*\*/\}.*$', re.MULTILINE),
    re.compile(r'^\s*//\s*\[(\w+_SLOT)\].*$', re.MULTILINE),
]

def inject_app_tsx(
    app_tsx_path: str,
    llm_output: str,
) -> bool:
    """
    [FIX BUG-A2] Accumulate-mode injection cho App.tsx.
 
    Vấn đề với replace mode:
      TASK-04 → App.tsx có: import LoginPage, import SignupPage, route LoginPage
      TASK-05 → LLM viết lại App.tsx với chỉ ProductList → mất LoginPage
 
    Giải pháp (giống inject_main_router cho main.py):
      - Parse import lines từ LLM output
      - Parse route/component từ return JSX trong LLM output
      - Inject VÀO App.tsx hiện có: append imports mới, merge routes mới
      - Không bao giờ xóa imports/routes từ task trước
 
    App.tsx pattern được quản lý:
      import X from './pages/X'      ← accumulate
      function App() {
        return (
          <Router>            ← nếu có router
            <Route ... />     ← accumulate per task
          </Router>
        )                         
      }
 
    Nếu App.tsx chưa có Router (simple mode), chuyển sang Router khi có >= 2 pages.
    """
    if not os.path.exists(app_tsx_path):
        return False
 
    with open(app_tsx_path, encoding="utf-8") as f:
        current = f.read()
 
    # 1. Extract import lines từ LLM output
    new_imports = _parse_page_imports(llm_output)
 
    # 2. Extract page components được render từ LLM output
    new_routes = _parse_route_entries(llm_output)
 
    if not new_imports and not new_routes:
        print(f"      [slot-injector] App.tsx: no new imports/routes found in LLM output")
        return False
 
    # 3. Dedup: chỉ add imports chưa có
    existing_imports = set(re.findall(r"import\s+(\w+)\s+from\s+'./pages/", current))
    filtered_imports = [
        line for line in new_imports
        if _extract_import_name(line) not in existing_imports
    ]
 
    # 4. Build updated App.tsx
    updated = _accumulate_app_tsx(current, filtered_imports, new_routes)
 
    if updated == current:
        print(f"      [slot-injector] App.tsx: already up-to-date")
        return True
 
    with open(app_tsx_path, "w", encoding="utf-8") as f:
        f.write(updated)
 
    added = len(filtered_imports)
    print(f"      [slot-injector] App.tsx: accumulated {added} new import(s), {len(new_routes)} route(s)")
    return True
 
 
def _parse_page_imports(code: str) -> list[str]:
    """Extract `import X from './pages/...'` lines từ LLM output."""
    return re.findall(
        r"^import\s+\w+\s+from\s+'\.\/pages\/[^']+'\s*$",
        code,
        re.MULTILINE,
    )
 
 
def _parse_route_entries(code: str) -> list[str]:
    """
    Extract JSX route/page entries từ LLM output.
    Hỗ trợ cả hai pattern:
      <Route path="..." element={<X />} />
      <X />   (simple return)
    """
    routes = []
 
    # react-router pattern
    router_routes = re.findall(
        r'<Route\s+[^>]*path=["\'][^"\']*["\'][^>]*/?>',
        code,
    )
    routes.extend(router_routes)
 
    # Simple component usage (nếu không có Route)
    if not router_routes:
        simple = re.findall(r'<(\w+Page)\s*/>', code)
        routes.extend([f"<{c} />" for c in simple])
 
    return list(dict.fromkeys(routes))  # dedup
 
 
def _extract_import_name(import_line: str) -> str:
    """Extract component name từ import line."""
    m = re.search(r"import\s+(\w+)\s+from", import_line)
    return m.group(1) if m else ""
 
 
def _accumulate_app_tsx(current: str, new_imports: list[str], new_routes: list[str]) -> str:
    """
    Merge new_imports và new_routes vào App.tsx hiện có.
    
    Strategy:
    1. Insert new imports sau import block cuối cùng
    2. Nếu App.tsx đang dùng simple return (<div>...</div>), 
       và có routes mới, chuyển sang multi-route pattern
    3. Nếu đã có Router, inject routes vào trong Router
    """
    lines = current.splitlines(keepends=True)
 
    # Tìm vị trí cuối import block
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("import "):
            last_import_idx = i
 
    # Insert new imports sau last import
    if new_imports:
        insert_pos = last_import_idx + 1
        import_block = "\n".join(new_imports) + "\n"
        lines.insert(insert_pos, import_block)
        current = "".join(lines)
 
    # Nếu có ROUTES_SLOT còn lại → fill bằng routes
    if new_routes and "ROUTES_SLOT" in current:
        route_block = "\n".join(new_imports) + "\n\n" if new_imports else ""
        # Build App component với tất cả routes
        all_page_imports = re.findall(r"import\s+(\w+)\s+from\s+'\.\/pages\/", current)
        # Tạo simple multi-page router
        nav_items = "\n      ".join(f"<div><{p} /></div>" for p in all_page_imports)
        app_body = f"""
function App() {{
  return (
    <div>
      {nav_items}
    </div>
  )
}}
 
export default App
""".strip()
        # Replace từ ROUTES_SLOT đến cuối
        slot_idx = current.find("// [ROUTES_SLOT]")
        if slot_idx != -1:
            # Tìm "function App" sau slot (nếu có) và giữ lại
            current = current[:slot_idx] + app_body + "\n"
 
    return current
def _detect_slots(content: str) -> list[tuple[str, int, int]]:
    """
    Find all [SLOT] markers in content.
    Returns list of (slot_name, start_char, end_char) — end_char covers the
    entire hint block (consecutive comment lines after the marker).
    """
    found = []
    for pat in _SLOT_PATTERNS:
        for m in pat.finditer(content):
            slot_name = m.group(1)
            block_start = m.start()
            block_end = m.end()

            remaining = content[block_end:]
            for line in remaining.split("\n"):
                stripped = line.strip()
                if not stripped:
                    break
                if stripped.startswith(("#", "//", "{/*", "*/}", "*")):
                    block_end += len(line) + 1
                else:
                    break

            found.append((slot_name, block_start, block_end))

    seen = set()
    unique = []
    for s in sorted(found, key=lambda x: x[1]):
        if s[0] not in seen:
            seen.add(s[0])
            unique.append(s)
    return unique


def has_slot(filepath: str) -> bool:
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return bool(_detect_slots(content))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SLOT CONTENT EXTRACTION FROM GENERATED CODE
# ══════════════════════════════════════════════════════════════════════════════

def _extract_slot_content(generated_code: str, slot_name: str) -> Optional[str]:
    explicit_pat = re.compile(
        rf'^\s*[#//]*\s*\[{re.escape(slot_name)}\][^\n]*\n([\s\S]*?)(?=^\s*[#//]*\s*\[\w+_SLOT\]|\Z)',
        re.MULTILINE,
    )
    m = explicit_pat.search(generated_code)
    if m:
        return m.group(1).strip()

    cleaned = re.sub(r'^\s*#\s*\[\w+_SLOT\][^\n]*\n', '', generated_code, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*//\s*\[\w+_SLOT\][^\n]*\n', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*\{/\*\s*\[\w+_SLOT\]\s*\*/\}[^\n]*\n', '', cleaned, flags=re.MULTILINE)
    return cleaned.strip()


# ══════════════════════════════════════════════════════════════════════════════
# INJECT ONE SLOT
# ══════════════════════════════════════════════════════════════════════════════

def inject_slot(
    target_path: str,
    slot_name: str,
    new_content: str,
    mode: str = "replace",
) -> bool:
    """
    Inject new_content into the [SLOT_NAME] region of target_path.
    mode="replace": substitute slot marker + hint block with new_content
    mode="append":  add new_content AFTER the slot marker (keeps marker intact
                    so subsequent appends work; marker is kept as a comment anchor)
    Returns True if injection happened, False if slot not found.
    """
    if not os.path.exists(target_path):
        return False

    with open(target_path, encoding="utf-8") as f:
        original = f.read()

    slots = _detect_slots(original)
    target_slot = next((s for s in slots if s[0] == slot_name), None)
    if not target_slot:
        return False

    _, start, end = target_slot

    if mode == "replace":
        updated = original[:start] + new_content + "\n" + original[end:]
    else:  # append — keep the slot marker so later appends still work
        updated = original[:end] + "\n" + new_content + "\n" + original[end:]

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(updated)

    rel = target_path
    print(f"      [slot-injector] {slot_name} → {rel}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# MAIN_ROUTER_SLOT — dedicated handler (append + dedup)
# ══════════════════════════════════════════════════════════════════════════════

def inject_main_router(
    main_py_path: str,
    router_lines: list[str],
) -> bool:
    """
    Inject include_router calls into main.py.

    Uses append-mode so the MAIN_ROUTER_SLOT marker is preserved for
    subsequent tasks (each backend task adds its own router).
    Deduplicates: never registers the same router variable twice.

    Args:
        main_py_path: absolute path to main.py
        router_lines: e.g. [
            "from app.routes.auth import router as auth_router",
            "app.include_router(auth_router, prefix='/auth')",
        ]
    """
    if not os.path.exists(main_py_path):
        return False

    with open(main_py_path, encoding="utf-8") as f:
        content = f.read()

    existing_includes = set(re.findall(r'app\.include_router\((\w+)', content))

    new_lines = []
    for line in router_lines:
        line = line.rstrip()
        if not line:
            continue
        # Skip duplicate include_router calls
        m = re.search(r'app\.include_router\((\w+)', line)
        if m and m.group(1) in existing_includes:
            continue
        # Skip duplicate imports
        if line.startswith(("from ", "import ")) and line in content:
            continue
        new_lines.append(line)

    if not new_lines:
        print(f"      [slot-injector] MAIN_ROUTER_SLOT: all routers already registered")
        return True

    new_block = "\n".join(new_lines)

    # Try append into slot marker first (preserves marker for next task)
    injected = inject_slot(main_py_path, "MAIN_ROUTER_SLOT", new_block, mode="append")

    if not injected:
        # Slot already fully consumed — safe to append at EOF
        with open(main_py_path, "a", encoding="utf-8") as f:
            f.write("\n# Auto-added routers\n" + new_block + "\n")
        print(f"      [slot-injector] MAIN_ROUTER_SLOT: appended at EOF (slot consumed)")

    return True


def _parse_router_lines_from_code(code: str) -> list[str]:
    """
    Extract include_router lines (and their imports) from LLM-generated code.
    Used when dev agent outputs main.py content directly.
    """
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith(("from app.routes", "from app.router", "import app.routes")):
            lines.append(stripped)
        elif "app.include_router(" in stripped or "app.add_router(" in stripped:
            lines.append(stripped)
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# INJECT ALL SLOTS FROM GENERATED FILES
# ══════════════════════════════════════════════════════════════════════════════

def inject_all_slots(
    generated: dict[str, str],
    pos_app_dir: str,
    plan: Optional[dict] = None,
) -> dict[str, list[str]]:
    """
    Process all files in the generated dict.

    Decision tree for each file:
      1. File is main.py AND generated code contains include_router lines
         → always use inject_main_router (append + dedup). Never overwrite main.py.
      2. Target file exists + plan says it has a known slot_name → inject_slot(replace)
      3. Target file exists + has ANY slot → inject into first slot (replace)
      4. Fallback: write file directly (config files, new files with no slot system)

    Returns {"injected": [...], "overwritten": [...], "skipped": [...]}
    """
    file_to_slot: dict[str, str] = {}
    if plan:
        for fe in plan.get("files", []):
            if fe.get("slot"):
                file_to_slot[fe["path"]] = fe["slot"]

    results = {"injected": [], "overwritten": [], "skipped": []}
    for rel_path, code in generated.items():
        if not code or not code.strip():
            results["skipped"].append(rel_path)
            continue

        target = os.path.join(pos_app_dir, rel_path)
        basename = os.path.basename(rel_path)
        if basename == "App.tsx" and os.path.exists(target):
            inject_app_tsx(target, code)
            results["injected"].append(rel_path)
            continue

        # ── Special case: main.py ────────────────────────────────────────────
        # NEVER overwrite main.py blindly — always use router-aware injection.
        # This prevents "include_router-only file" overwriting the scaffold.
        if basename == "main.py" and os.path.exists(target):
            router_lines = _parse_router_lines_from_code(code)
            if router_lines:
                inject_main_router(target, router_lines)
                results["injected"].append(rel_path)
                continue
            # No router lines found — check if code looks like a full main.py
            # (has FastAPI app definition). If yes, only write if target is
            # still the bare scaffold (has MAIN_ROUTER_SLOT marker).
            with open(target, encoding="utf-8") as f:
                existing = f.read()
            if "MAIN_ROUTER_SLOT" in existing and "FastAPI" in code:
                # LLM rewrote full main.py — safe to overwrite scaffold
                with open(target, "w", encoding="utf-8") as f:
                    f.write(code)
                results["overwritten"].append(rel_path)
                print(f"      [slot-injector] MAIN_ROUTER_SLOT → {rel_path}")
                continue
            # Otherwise skip — don't corrupt main.py
            results["skipped"].append(rel_path)
            print(f"      [slot-injector] SKIP main.py (no router lines, slot consumed): {rel_path}")
            continue

        # ── Known slot from plan ─────────────────────────────────────────────
        slot_name = file_to_slot.get(rel_path)
        if os.path.exists(target) and slot_name:
            injected = inject_slot(target, slot_name, code, mode="replace")
            if injected:
                results["injected"].append(rel_path)
                continue

        # ── Any slot in existing file ────────────────────────────────────────
        if os.path.exists(target):
            with open(target, encoding="utf-8") as f:
                existing = f.read()
            slots = _detect_slots(existing)
            if slots:
                primary_slot = slots[0][0]
                slot_content = _extract_slot_content(code, primary_slot)
                injected = inject_slot(target, primary_slot, slot_content or code, mode="replace")
                if injected:
                    results["injected"].append(rel_path)
                    continue

        # ── Fallback: write directly ─────────────────────────────────────────
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(code)
        results["overwritten"].append(rel_path)

    total = len(results["injected"])
    over  = len(results["overwritten"])
    skip  = len(results["skipped"])
    print(f"      [slot-injector] injected={total}, overwritten={over}, skipped={skip}")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# REPAIR AGENT SUPPORT
# ══════════════════════════════════════════════════════════════════════════════

def patch_slot_region(
    target_path: str,
    slot_name: str,
    fixed_code: str,
) -> bool:
    """
    Replace content of a specific slot region with fixed_code.
    Used by repair agent for targeted fixes without rewriting entire file.
    """
    if not os.path.exists(target_path):
        return False

    if inject_slot(target_path, slot_name, fixed_code, mode="replace"):
        return True

    with open(target_path, encoding="utf-8") as f:
        content = f.read()

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content.rstrip() + "\n\n# [REPAIR PATCH]\n" + fixed_code + "\n")

    print(f"      [slot-injector] REPAIR patch applied to {target_path}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def list_unfilled_slots(pos_app_dir: str, component: str = "backend") -> list[dict]:
    """Scan project and return files that still have unfilled [SLOT] markers."""
    unfilled = []
    for root, _, files in os.walk(pos_app_dir):
        for fname in files:
            if fname.endswith((".py", ".ts", ".tsx")):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    slots = _detect_slots(content)
                    if slots:
                        rel = os.path.relpath(fpath, pos_app_dir)
                        unfilled.append({
                            "file": rel,
                            "slots": [s[0] for s in slots],
                        })
                except Exception:
                    pass
    return unfilled