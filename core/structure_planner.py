"""
structure_planner.py — KG-driven file list planner (v1)

Position in pipeline (NEW — between contract-compiler and scaffold):

  Contract compiler (locks routes)
    ↓
  [Structure Planner]   ← THIS FILE
    ↓
  Smart Scaffold Generator
    ↓
  Slot Injector
    ↓
  Dev agent (fills slots only)

Responsibility:
  - Traverse knowledge_graph.json to understand entity relationships
  - For each task (service), emit the exact list of files that MUST exist
  - Mark each file with its slot type: ROUTES_SLOT, COMPONENT_SLOT, MODEL_SLOT, etc.
  - Output: docs/structure_plan.json

Key principle:
  LLM no longer decides what files to create.
  Structure Planner (deterministic) decides — dev agent only fills bounded slots.

Why this fixes the problems:
  1. No more "LLM rewrites App.tsx from scratch" — slots are bounded regions
  2. No more "missing ProductCard.tsx discovered at test time" — tsc --noEmit catches it during scaffold
  3. KG graph (User→Cart→Checkout→Receipt) drives file list, not hardcoded guesses

Output schema:
{
  "schema_version": "1",
  "task_id": "TASK-02",
  "component": "backend",
  "source_dir": "src/backend",
  "files": [
    {
      "path": "src/backend/app/routes/cart.py",
      "role": "routes",          # routes | model | service | component | page | store | config
      "slot": "ROUTES_SLOT",     # the [SLOT] marker the dev agent fills
      "owned_by": "TASK-02",     # which task owns this file
      "imports_from": [          # files this slot needs to import from
        "src/backend/app/routes/products.py"
      ],
      "related_entities": ["ENT-03", "ENT-04"],
      "must_exist_before_slot_fill": true  # scaffold must write this before dev LLM runs
    }
  ],
  "graph_context": {             # subgraph relevant to this task — injected into dev prompt
    "neighbors": [...],
    "edges": [...]
  }
}
"""

import json
import os
import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# SLOT TYPE REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

# Maps file role → slot marker name used in scaffold templates
SLOT_MARKERS = {
    "routes":    "ROUTES_SLOT",
    "model":     "MODEL_SLOT",
    "service":   "SERVICE_SLOT",
    "component": "COMPONENT_SLOT",
    "page":      "PAGE_SLOT",
    "store":     "STORE_SLOT",
    "api_client": "API_CLIENT_SLOT",
    "config":    "CONFIG_SLOT",
    "test":      "TEST_SLOT",
    "main":      "MAIN_ROUTER_SLOT",  # special: main.py include_router block
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_kg(path: str = "docs/knowledge_graph.json") -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_architecture(path: str = "docs/architecture.json") -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_contracts(contracts_dir: str = "docs/contracts") -> dict:
    """Load all .contract.json files → task_id → contract dict."""
    result = {}
    if not os.path.isdir(contracts_dir):
        return result
    for fname in os.listdir(contracts_dir):
        if not fname.endswith(".contract.json"):
            continue
        task_id = fname.replace(".contract.json", "")
        with open(os.path.join(contracts_dir, fname), encoding="utf-8") as f:
            result[task_id] = json.load(f)
    return result


def _extract_source_dir(service: dict) -> str:
    """Mirror logic from contract_normalizer._extract_source_dir."""
    files = service.get("file_structure", [])
    component = service.get("component", "backend")

    INTERNAL_DIRS = {"app", "src", "lib", "pkg"}

    if files:
        parts_list = [f.split("/") for f in files if f.endswith((".py", ".ts", ".tsx"))]
        if parts_list:
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

            if common and "." in common[-1]:
                common = common[:-1]

            cut_at = len(common)
            for i in range(1, len(common)):
                if common[i] in INTERNAL_DIRS:
                    cut_at = i
                    break
            common = common[:cut_at]

            if len(common) >= 2:
                return "/".join(common)

    return "src/frontend" if component == "frontend" else "src/backend"


def _detect_role(filepath: str) -> str:
    """Infer file role from path conventions."""
    p = filepath.lower()
    if "/routes/" in p or p.endswith("router.py"):
        return "routes"
    if "/models/" in p or p.endswith("model.py") or p.endswith(".py") and "model" in p:
        return "model"
    if "/services/" in p and p.endswith(".py"):
        return "service"
    if "/pages/" in p or p.endswith("page.tsx") or p.endswith("Page.tsx"):
        return "page"
    if "/components/" in p or p.endswith(".tsx") and "component" in p.lower():
        return "component"
    if "/store" in p or p.endswith("store.ts") or p.endswith("Store.ts"):
        return "store"
    if "/api/" in p and p.endswith((".ts", ".tsx")):
        return "api_client"
    if p.endswith("main.py"):
        return "main"
    if p.endswith("test_") or "/tests/" in p:
        return "test"
    if p.endswith((".json", ".yml", ".yaml", ".txt", ".env")):
        return "config"
    return "component"


def _infer_imports(filepath: str, all_files: list[str], service: dict) -> list[str]:
    """
    Infer which other files this file will likely import from,
    based on role and resource relationships.

    Only considers files WITHIN the same task / already existing files.
    """
    role = _detect_role(filepath)
    imports = []

    if role == "routes":
        # Routes import from models in same service
        for f in all_files:
            if _detect_role(f) == "model" and f != filepath:
                imports.append(f)
        # Routes in same service that this file depends on (e.g. cart → products)
        for dep_name in service.get("depends_on", []):
            for f in all_files:
                if dep_name.lower().replace(" ", "_") in f.lower() and _detect_role(f) == "routes":
                    imports.append(f)

    elif role in ("page", "component"):
        # Frontend pages import from api_client and store
        for f in all_files:
            r = _detect_role(f)
            if r in ("api_client", "store") and f != filepath:
                imports.append(f)

    elif role == "store":
        for f in all_files:
            if _detect_role(f) == "api_client" and f != filepath:
                imports.append(f)

    elif role == "main":
        # main.py imports all route files
        for f in all_files:
            if _detect_role(f) == "routes" and f != filepath:
                imports.append(f)

    return list(dict.fromkeys(imports))  # deduplicate, preserve order


# ══════════════════════════════════════════════════════════════════════════════
# KG GRAPH TRAVERSAL
# ══════════════════════════════════════════════════════════════════════════════

def _get_graph_context(task_id: str, service: dict, kg: Optional[dict]) -> dict:
    """
    Traverse KG to find related entities and edges relevant to this task.
    Returns a subgraph context dict for injection into the dev agent prompt.

    Key insight: if Cart task neighbors are Product and Inventory in KG,
    inject product.py and inventory.py into dev context so LLM won't
    write cart logic ignoring stock deduction.
    """
    if not kg:
        return {"neighbors": [], "edges": [], "neighbor_files": []}

    entity_refs = service.get("entity_refs", [])
    nodes = kg.get("nodes", {})
    edges = kg.get("edges", [])

    # Find directly referenced nodes
    ref_set = set(entity_refs)

    # Find neighbors (1-hop traversal)
    neighbor_entities = set()
    relevant_edges = []
    for edge in edges:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in ref_set or dst in ref_set:
            relevant_edges.append(edge)
            if src in ref_set:
                neighbor_entities.add(dst)
            if dst in ref_set:
                neighbor_entities.add(src)

    # Build neighbor node info
    neighbors = []
    for ent_id in neighbor_entities - ref_set:
        node = nodes.get(ent_id, {})
        neighbors.append({
            "id": ent_id,
            "name": node.get("name", ent_id),
            "component": node.get("component", "unknown"),
            "domain_tags": node.get("domain_tags", []),
            "ownership": node.get("ownership", "global"),
        })

    return {
        "neighbors": neighbors,
        "edges": relevant_edges,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CORE: PLAN FILES FOR ONE TASK
# ══════════════════════════════════════════════════════════════════════════════

def plan_task_structure(
    task_id: str,
    service: dict,
    contract: dict,
    kg: Optional[dict],
    all_services: list[dict],
) -> dict:
    """
    Plan the complete file structure for one task/service.

    Args:
        task_id:      e.g. "TASK-02"
        service:      service dict from architecture.json
        contract:     loaded contract file for this task
        kg:           knowledge graph (may be None)
        all_services: all services (for cross-task import resolution)

    Returns:
        structure plan dict (saved to docs/structure_plans/TASK-XX.plan.json)
    """
    component = service.get("component", "backend")
    source_dir = contract.get("source_dir") or _extract_source_dir(service)
    file_structure = service.get("file_structure", [])

    # Build file entry list
    file_entries = []
    all_paths = list(file_structure)

    for filepath in file_structure:
        role = _detect_role(filepath)
        slot = SLOT_MARKERS.get(role, "CONTENT_SLOT")

        # Determine imports (within this task's files + cross-task deps)
        imports_from = _infer_imports(filepath, all_paths, service)

        # Cross-task: find files from depends_on services
        cross_task_imports = []
        for dep_task_name in service.get("depends_on", []):
            for other_svc in all_services:
                if other_svc.get("name") == dep_task_name or other_svc.get("task_id") == dep_task_name:
                    for other_file in other_svc.get("file_structure", []):
                        if _detect_role(other_file) in ("routes", "model", "service", "store", "api_client"):
                            cross_task_imports.append(other_file)

        all_imports = list(dict.fromkeys(imports_from + cross_task_imports))

        # Skip infra/config files from slot system — they don't have bounded slots
        must_scaffold = role not in ("test",)  # tests generated separately
        is_slot_file = role not in ("config",)

        file_entries.append({
            "path": filepath,
            "role": role,
            "slot": slot if is_slot_file else None,
            "owned_by": task_id,
            "imports_from": all_imports,
            "must_exist_before_slot_fill": must_scaffold,
        })

    # KG traversal for graph context
    graph_context = _get_graph_context(task_id, service, kg)

    # Add neighbor files to graph_context so dev agent can load them as context
    neighbor_files = []
    for other_svc in all_services:
        if other_svc.get("task_id") in [e.get("id") for e in graph_context["neighbors"]]:
            neighbor_files.extend(other_svc.get("file_structure", []))
    graph_context["neighbor_files"] = neighbor_files

    return {
        "schema_version": "1",
        "task_id": task_id,
        "component": component,
        "source_dir": source_dir,
        "files": file_entries,
        "routes": contract.get("routes", []),
        "graph_context": graph_context,
        "entity_refs": service.get("entity_refs", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def plan_all_tasks(
    architecture: dict,
    contracts: dict,
    kg: Optional[dict],
    output_dir: str = "docs/structure_plans",
) -> dict[str, dict]:
    """
    Run structure planning for every task in architecture.json.

    Returns: task_id → plan dict
    Side effect: writes docs/structure_plans/TASK-XX.plan.json for each task
    """
    os.makedirs(output_dir, exist_ok=True)
    services = architecture.get("services", [])
    deployment = architecture.get("deployment")
    if deployment and "task_id" in deployment:
        services = services + [deployment]

    plans: dict[str, dict] = {}

    for service in services:
        task_id = service.get("task_id")
        if not task_id:
            continue

        contract = contracts.get(task_id, {})
        plan = plan_task_structure(
            task_id=task_id,
            service=service,
            contract=contract,
            kg=kg,
            all_services=services,
        )
        plans[task_id] = plan

        out_path = os.path.join(output_dir, f"{task_id}.plan.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        print(
            f"      [structure-planner] {task_id}: "
            f"{len(plan['files'])} files, "
            f"{len(plan['graph_context']['neighbors'])} KG neighbors"
        )

    return plans


def load_plan(task_id: str, plans_dir: str = "docs/structure_plans") -> Optional[dict]:
    path = os.path.join(plans_dir, f"{task_id}.plan.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_structure_planner(prompt: str = "") -> str:
    """
    Entry point called from adapter_agent.py as agent step.

    Reads: docs/architecture.json, docs/knowledge_graph.json, docs/contracts/
    Writes: docs/structure_plans/TASK-XX.plan.json for each task
    """
    arch = _load_architecture()
    if not arch:
        raise RuntimeError("architecture.json not found — run architect-agent first")

    kg = _load_kg()
    if not kg:
        print("      [structure-planner] WARNING: knowledge_graph.json not found — proceeding without KG")

    contracts = _load_contracts()
    if not contracts:
        print("      [structure-planner] WARNING: no contract files found in docs/contracts/")

    plans = plan_all_tasks(arch, contracts, kg)

    total_files = sum(len(p["files"]) for p in plans.values())
    print(
        f"      [structure-planner] DONE — "
        f"{len(plans)} tasks planned, "
        f"{total_files} total files"
    )
    return "STRUCTURE_PLANNED"


# ══════════════════════════════════════════════════════════════════════════════
# FORMAT GRAPH CONTEXT FOR DEV PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def format_graph_context_for_dev(plan: dict, pos_app_dir: str = "") -> str:
    """
    Serialize graph_context from a structure plan into a text block
    suitable for injection into the dev agent prompt.

    This is the key mechanism for making dev agent "graph-aware":
    instead of only seeing its own task's files, it sees the neighbors
    from the KG so it won't write Cart logic that ignores stock deduction.
    """
    ctx = plan.get("graph_context", {})
    neighbors = ctx.get("neighbors", [])
    edges = ctx.get("edges", [])
    neighbor_files = ctx.get("neighbor_files", [])

    if not neighbors and not neighbor_files:
        return ""

    lines = ["# Graph-aware context (from Knowledge Graph traversal)"]
    lines.append("# These entities/files are RELATED to your task — read them before writing code.")
    lines.append("")

    if neighbors:
        lines.append("## Related entities (KG neighbors):")
        for n in neighbors:
            tags = ", ".join(n.get("domain_tags", [])) or "general"
            lines.append(
                f"  - {n['id']} ({n['name']}) "
                f"[{n.get('component','?')}] tags=[{tags}] ownership={n.get('ownership','global')}"
            )
        lines.append("")

    if edges:
        lines.append("## Relevant KG relationships:")
        for e in edges:
            lines.append(
                f"  - {e.get('from')} --[{e.get('type')}]--> {e.get('to')}"
                f"  (confidence={e.get('confidence', 0):.2f}: {e.get('reason','')})"
            )
        lines.append("")

    # Read actual code from neighbor files
    if neighbor_files and pos_app_dir:
        lines.append("## Related files code (read to understand system state):")
        for rel_path in neighbor_files[:6]:  # cap at 6 to avoid context bloat
            full = os.path.join(pos_app_dir, rel_path)
            if not os.path.exists(full):
                continue
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # Show first 80 lines
                snippet = "\n".join(content.splitlines()[:80])
                lines.append(f"\n### {rel_path}")
                lines.append(f"```\n{snippet}\n```")
            except Exception:
                pass

    return "\n".join(lines)