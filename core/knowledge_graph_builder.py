"""
Knowledge Graph Builder

Vị trí trong pipeline:
  Requirement Agent  →  entities.json + requirements.md
    ↓
  [Knowledge Graph Builder]   ← bước này
    ↓
  Architect Agent

Input:  docs/entities.json + docs/requirements.md
Output: docs/knowledge_graph.json

Mục đích:
  - Làm GIÀU thông tin về entities TRƯỚC khi architect đọc
  - Phát hiện quan hệ ẩn giữa entities (beyond depends_on đã khai báo)
  - Xác định domain concepts và semantic clusters
  - Cung cấp constraint hints cho architect (security, ownership, lifecycle)

ĐÂY LÀ BƯỚC ENTITY-LEVEL — không biết task_id, không biết service name.
  - Không dùng TASK-XX ở bất kỳ đâu
  - Không tham chiếu execution order
  - Chỉ nói về ENT-XX và quan hệ giữa chúng

Architect sẽ dùng KG để ra quyết định service boundary + API design.
Task Materializer sau đó mới gán TASK-ID dựa trên output của Architect.

Thứ tự đúng:
  Requirement → Knowledge Graph → Architect → Task Materializer → Dependency Graph

THAY ĐỔI từ v1:
  [v1] Hint 6 (_generate_architect_hints) hardcode "must be TASK-01"
       → sai: KG chạy trước Task Materializer, chưa có task_id nào
  [v2] Hint 6 viết lại: chỉ nói "auth entities phải khởi động trước"
       không tham chiếu TASK-ID cụ thể

Đây là bước DETERMINISTIC (rule-based + heuristic), không gọi AI.
Nếu cần AI inference → đánh dấu confidence < 0.7 để architect tự quyết.

KnowledgeGraph schema:
{
  "schema_version": "1",
  "generated_from": ["entities.json", "requirements.md"],
  "nodes": {
    "ENT-01": {
      "id": "ENT-01",
      "name": "...",
      "component": "backend",
      "complexity": "medium",
      "domain_tags": ["auth", "security"],
      "lifecycle": "persistent",
      "ownership": "user-scoped",
      "security_level": "high",
      "data_shape": "single",
    }
  },
  "edges": [...],
  "clusters": [...],
  "constraints": [...],
  "architect_hints": [...]
}
"""

import json
import os
import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN TAG DETECTION
# ══════════════════════════════════════════════════════════════════════════════

DOMAIN_KEYWORD_MAP = {
    "auth": [
        "auth", "authentication", "login", "signup", "register",
        "jwt", "token", "session", "password", "credential", "oauth",
        "logout", "refresh token",
    ],
    "user": [
        "user", "account", "profile", "member", "customer", "person",
    ],
    "payment": [
        "payment", "billing", "invoice", "checkout", "stripe", "purchase",
        "transaction", "order", "cart", "price", "fee",
    ],
    "product": [
        "product", "item", "catalog", "inventory", "stock", "sku",
        "listing", "variant",
    ],
    "ai_ml": [
        "ai", "ml", "model", "inference", "embedding", "vector",
        "llm", "gpt", "gemini", "claude", "openai", "screening", "ranking",
        "recommendation", "prediction",
    ],
    "realtime": [
        "websocket", "realtime", "real-time", "live", "stream", "socket",
        "push", "notification", "event", "pubsub",
    ],
    "notification": [
        "notification", "email", "sms", "alert", "message", "inbox",
        "mailer", "smtp", "sendgrid",
    ],
    "file": [
        "file", "upload", "download", "attachment", "image", "pdf",
        "document", "storage", "s3", "blob", "media",
    ],
    "admin": [
        "admin", "dashboard", "management", "panel", "backoffice",
        "moderation", "report",
    ],
    "search": [
        "search", "filter", "query", "facet", "elasticsearch", "index",
        "full-text",
    ],
    "analytics": [
        "analytics", "metric", "tracking", "event", "log", "audit",
        "history", "report",
    ],
    "security": [
        "security", "permission", "role", "access", "rbac", "acl",
        "encrypt", "hash", "rate limit",
    ],
    "deployment": [
        "docker", "deploy", "ci", "cd", "kubernetes", "infra",
        "dockerfile", "compose", "nginx",
    ],
}


def _detect_domain_tags(text: str) -> list[str]:
    text_lower = text.lower()
    return [domain for domain, keywords in DOMAIN_KEYWORD_MAP.items()
            if any(kw in text_lower for kw in keywords)]


# ══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _detect_lifecycle(entity: dict, domain_tags: list[str]) -> str:
    text = (entity.get("name", "") + " " + entity.get("description", "")).lower()
    if "realtime" in domain_tags or any(kw in text for kw in ("websocket", "stream", "live", "socket")):
        return "streaming"
    if any(kw in text for kw in ("session", "cache", "temporary", "ephemeral")):
        return "transient"
    return "persistent"


# ══════════════════════════════════════════════════════════════════════════════
# OWNERSHIP DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _detect_ownership(entity: dict, domain_tags: list[str]) -> str:
    text = (entity.get("name", "") + " " + entity.get("description", "")).lower()
    if "admin" in domain_tags:
        return "system"
    if any(kw in text for kw in ("my", "user's", "personal", "cart", "order", "profile", "account")):
        return "user-scoped"
    if any(kw in text for kw in ("catalog", "public", "shared", "global")):
        return "global"
    if "auth" in domain_tags or "user" in domain_tags:
        return "user-scoped"
    return "global"


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY LEVEL DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _detect_security_level(entity: dict, domain_tags: list[str]) -> str:
    if any(t in domain_tags for t in ("auth", "security", "payment")):
        return "high"
    if any(t in domain_tags for t in ("user", "notification", "file")):
        return "medium"
    return "low"


# ══════════════════════════════════════════════════════════════════════════════
# DATA SHAPE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _detect_data_shape(entity: dict, domain_tags: list[str]) -> str:
    text = (entity.get("name", "") + " " + entity.get("description", "")).lower()
    if "streaming" in domain_tags or any(kw in text for kw in ("stream", "websocket", "event")):
        return "stream"
    if any(kw in text for kw in ("list", "search", "feed", "catalog", "history", "collection", "all ")):
        return "collection"
    return "single"


# ══════════════════════════════════════════════════════════════════════════════
# EDGE INFERENCE
# ══════════════════════════════════════════════════════════════════════════════

EDGE_TYPES = {
    "depends_on": "depends_on",
    "owns":       "owns",
    "references": "references",
    "triggers":   "triggers",
    "extends":    "extends",
}

OWNERSHIP_PAIRS = [
    ("user",    ["cart", "order", "profile", "application", "project", "booking"]),
    ("order",   ["product", "item", "payment"]),
    ("job",     ["application", "candidate"]),
    ("project", ["task", "ticket", "issue", "sprint"]),
    ("cart",    ["item", "product"]),
]

TRIGGER_PAIRS = [
    ("payment",  ["notification", "email", "invoice"]),
    ("checkout", ["notification", "email", "receipt"]),
    ("signup",   ["notification", "email", "welcome"]),
    ("order",    ["notification", "email"]),
]


def _infer_edges(entities: list[dict]) -> list[dict]:
    """Infer additional edges beyond explicit depends_on."""
    edges = []
    entity_map = {e["id"]: e for e in entities}

    # 1. Explicit depends_on (confidence=1.0)
    for ent in entities:
        for dep_id in ent.get("depends_on", []):
            if dep_id in entity_map:
                edges.append({
                    "from": ent["id"],
                    "to": dep_id,
                    "type": "depends_on",
                    "confidence": 1.0,
                    "reason": "Declared in entities.json",
                })

    # 2. Frontend → Backend dependency (inferred từ shared keywords)
    for ent in entities:
        if ent.get("component") != "frontend":
            continue
        for other in entities:
            if other.get("component") != "backend":
                continue
            ent_text   = (ent["name"] + " " + ent.get("description", "")).lower()
            other_text = (other["name"] + " " + other.get("description", "")).lower()
            shared_words = set(re.findall(r'\b\w{4,}\b', ent_text)) & \
                           set(re.findall(r'\b\w{4,}\b', other_text))
            stopwords = {"with", "form", "page", "list", "data", "service", "system", "backend", "frontend"}
            shared_words -= stopwords

            already_declared = any(
                e["from"] == ent["id"] and e["to"] == other["id"]
                for e in edges
            )
            if shared_words and not already_declared:
                edges.append({
                    "from": ent["id"],
                    "to": other["id"],
                    "type": "depends_on",
                    "confidence": 0.8,
                    "reason": f"Frontend likely consumes backend API (shared concepts: {list(shared_words)[:3]})",
                })

    # 3. Ownership edges (heuristic)
    for parent_kw, child_kws in OWNERSHIP_PAIRS:
        parent_ents = [
            e for e in entities
            if parent_kw in (e["name"] + " " + e.get("description", "")).lower()
        ]
        for child_kw in child_kws:
            child_ents = [
                e for e in entities
                if child_kw in (e["name"] + " " + e.get("description", "")).lower()
            ]
            for p in parent_ents:
                for c in child_ents:
                    if p["id"] == c["id"]:
                        continue
                    already = any(
                        e["from"] == p["id"] and e["to"] == c["id"] and e["type"] == "owns"
                        for e in edges
                    )
                    if not already:
                        edges.append({
                            "from": p["id"],
                            "to": c["id"],
                            "type": "owns",
                            "confidence": 0.7,
                            "reason": f"Heuristic: {p['name']} likely owns {c['name']}",
                        })

    # 4. Trigger edges (heuristic)
    for trigger_kw, target_kws in TRIGGER_PAIRS:
        trigger_ents = [
            e for e in entities
            if trigger_kw in (e["name"] + " " + e.get("description", "")).lower()
        ]
        for target_kw in target_kws:
            target_ents = [
                e for e in entities
                if target_kw in (e["name"] + " " + e.get("description", "")).lower()
            ]
            for t in trigger_ents:
                for tgt in target_ents:
                    if t["id"] == tgt["id"]:
                        continue
                    already = any(
                        e["from"] == t["id"] and e["to"] == tgt["id"] and e["type"] == "triggers"
                        for e in edges
                    )
                    if not already:
                        edges.append({
                            "from": t["id"],
                            "to": tgt["id"],
                            "type": "triggers",
                            "confidence": 0.65,
                            "reason": f"Heuristic: {t['name']} likely triggers {tgt['name']}",
                        })

    return edges


# ══════════════════════════════════════════════════════════════════════════════
# CLUSTER DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _build_clusters(entities: list[dict], nodes: dict) -> list[dict]:
    clusters = []
    cluster_id = 1

    group_map: dict[str, list[str]] = {}
    for ent in entities:
        node      = nodes[ent["id"]]
        tags      = node["domain_tags"]
        component = ent.get("component", "fullstack")

        priority_order = ["auth", "payment", "ai_ml", "realtime", "notification",
                          "file", "admin", "search", "analytics", "product", "user"]
        primary = next((t for t in priority_order if t in tags), tags[0] if tags else "general")

        key = f"{primary}_{component}"
        group_map.setdefault(key, []).append(ent["id"])

    for key, ent_ids in group_map.items():
        primary_domain, component = key.rsplit("_", 1)
        cluster_name = f"{primary_domain.replace('_', ' ').title()} {component}"
        suggested_service = (
            f"{primary_domain}-{component}" if component != "fullstack" else primary_domain
        )

        clusters.append({
            "id": f"CLU-{cluster_id:02d}",
            "name": cluster_name,
            "entities": ent_ids,
            "primary_domain": primary_domain,
            "component": component,
            "suggested_service": suggested_service,
        })
        cluster_id += 1

    return clusters


# ══════════════════════════════════════════════════════════════════════════════
# CONSTRAINT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _detect_constraints(entities: list[dict], nodes: dict, edges: list[dict]) -> list[dict]:
    constraints = []

    for ent in entities:
        node = nodes[ent["id"]]
        tags = node["domain_tags"]

        if node["ownership"] == "user-scoped" and "auth" not in tags:
            constraints.append({
                "entity": ent["id"],
                "type": "requires_auth",
                "reason": f"{ent['name']} is user-scoped → endpoints must verify JWT",
            })

        if any(t in tags for t in ("ai_ml", "file")):
            constraints.append({
                "entity": ent["id"],
                "type": "rate_limited",
                "reason": f"{ent['name']} is expensive (AI/file) → rate limiting recommended",
            })

        if any(t in tags for t in ("payment", "auth", "security")):
            constraints.append({
                "entity": ent["id"],
                "type": "audit_logged",
                "reason": f"{ent['name']} is security-sensitive → should have audit log",
            })

    return constraints


# ══════════════════════════════════════════════════════════════════════════════
# ARCHITECT HINTS
# Tạo gợi ý cho architect dựa trên graph analysis.
#
# QUAN TRỌNG: Hints chỉ dùng ENT-ID và entity name.
# KHÔNG dùng TASK-XX — task_id chưa tồn tại tại bước này.
# Task Materializer gán TASK-ID SAU khi architect output architecture.json.
# ══════════════════════════════════════════════════════════════════════════════

def _generate_architect_hints(
    entities: list[dict],
    nodes: dict,
    edges: list[dict],
    clusters: list[dict],
) -> list[str]:
    hints = []
    entity_map = {e["id"]: e for e in entities}

    # Hint 1: Entities cùng lifecycle + component → gợi ý chung service
    lifecycle_groups: dict[str, list[str]] = {}
    for ent in entities:
        node = nodes[ent["id"]]
        key  = f"{node['lifecycle']}_{ent.get('component', 'fullstack')}"
        lifecycle_groups.setdefault(key, []).append(ent["id"])

    for key, ids in lifecycle_groups.items():
        if len(ids) >= 2:
            lifecycle, component = key.rsplit("_", 1)
            names = [entity_map[i]["name"] for i in ids]
            hints.append(
                f"[service-grouping] {', '.join(names)} share lifecycle='{lifecycle}' "
                f"({component}) → consider placing in same service"
            )

    # Hint 2: Trigger chains
    trigger_edges = [e for e in edges if e["type"] == "triggers" and e["confidence"] >= 0.6]
    for edge in trigger_edges:
        src  = entity_map.get(edge["from"], {}).get("name", edge["from"])
        dest = entity_map.get(edge["to"],   {}).get("name", edge["to"])
        hints.append(
            f"[async-trigger] {src} triggers {dest} → "
            f"consider event/callback or background task instead of sync call"
        )

    # Hint 3: AI/ML entity → dedicated service
    ai_ents = [e for e in entities if "ai_ml" in nodes[e["id"]]["domain_tags"]]
    if ai_ents:
        names = [e["name"] for e in ai_ents]
        hints.append(
            f"[ai-isolation] {', '.join(names)} involve AI/ML → "
            f"recommend dedicated service (different runtime, token cost, latency)"
        )

    # Hint 4: Realtime entity → WebSocket service
    rt_ents = [e for e in entities if "realtime" in nodes[e["id"]]["domain_tags"]]
    if rt_ents:
        names = [e["name"] for e in rt_ents]
        hints.append(
            f"[websocket] {', '.join(names)} require realtime → "
            f"use FastAPI WebSocket endpoint, separate from REST service"
        )

    # Hint 5: Deployment complexity
    backend_clusters = [c for c in clusters if c["component"] == "backend"]
    if len(backend_clusters) >= 3:
        hints.append(
            f"[deployment-complexity] {len(backend_clusters)} backend service clusters detected → "
            f"docker-compose will have multiple services; ensure health checks and startup order"
        )

    # Hint 6: Auth startup dependency
    # NOTE: Không dùng TASK-XX ở đây — KG chạy trước Task Materializer.
    # Chỉ mô tả quan hệ entity-level: auth entities phải sẵn sàng trước
    # các entity user-scoped khác. Task Materializer sẽ dịch thành depends_on TASK-XX.
    auth_ents = [e for e in entities if "auth" in nodes[e["id"]]["domain_tags"]]
    protected = [
        e for e in entities
        if nodes[e["id"]]["ownership"] == "user-scoped" and e not in auth_ents
    ]
    if auth_ents and protected:
        auth_names      = [e["name"] for e in auth_ents]
        protected_names = [e["name"] for e in protected[:3]]
        hints.append(
            f"[auth-dependency] {', '.join(protected_names)} (and others) are user-scoped → "
            f"their services must declare depends_on the service containing "
            f"{', '.join(auth_names)}. Auth service must start first."
        )

    return hints


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def build_knowledge_graph(
    entities: list[dict],
    requirements_text: str = "",
) -> dict:
    """
    Main entry point.

    Input:
      entities          — list từ docs/entities.json
      requirements_text — nội dung docs/requirements.md (optional)

    Output:
      dict ghi vào docs/knowledge_graph.json

    Chỉ làm việc với ENT-XX. Không tham chiếu TASK-XX hay service name.
    """
    # 1. Build enriched nodes
    nodes: dict[str, dict] = {}
    for ent in entities:
        combined_text = ent.get("name", "") + " " + ent.get("description", "")
        domain_tags   = _detect_domain_tags(combined_text)
        lifecycle     = _detect_lifecycle(ent, domain_tags)
        ownership     = _detect_ownership(ent, domain_tags)
        security      = _detect_security_level(ent, domain_tags)
        data_shape    = _detect_data_shape(ent, domain_tags)

        nodes[ent["id"]] = {
            "id":             ent["id"],
            "name":           ent.get("name", ""),
            "component":      ent.get("component", "fullstack"),
            "complexity":     ent.get("complexity", "medium"),
            "domain_tags":    domain_tags,
            "lifecycle":      lifecycle,
            "ownership":      ownership,
            "security_level": security,
            "data_shape":     data_shape,
        }

    # 2. Infer edges
    edges = _infer_edges(entities)

    # 3. Build clusters
    clusters = _build_clusters(entities, nodes)

    # 4. Detect constraints
    constraints = _detect_constraints(entities, nodes, edges)

    # 5. Generate architect hints (entity-level only, no task_id)
    hints = _generate_architect_hints(entities, nodes, edges, clusters)

    # 6. Enrich hints từ requirements text
    if requirements_text:
        req_lower = requirements_text.lower()
        if "persistent" in req_lower or "database" in req_lower or "store" in req_lower:
            hints.append(
                "[storage] Requirements mention persistent storage → "
                "confirm in-memory dict is acceptable or add DB entity"
            )
        if "scale" in req_lower or "concurrent" in req_lower or "performance" in req_lower:
            hints.append(
                "[scalability] Requirements mention scale/performance → "
                "consider stateless design to allow horizontal scaling"
            )

    return {
        "schema_version": "1",
        "generated_from": ["entities.json", "requirements.md"],
        "node_count":     len(nodes),
        "edge_count":     len(edges),
        "nodes":          nodes,
        "edges":          edges,
        "clusters":       clusters,
        "constraints":    constraints,
        "architect_hints": hints,
    }


def save_knowledge_graph(data: dict, path: str = "docs/knowledge_graph.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(
        f"      [knowledge-graph] {data['node_count']} nodes, "
        f"{data['edge_count']} edges, "
        f"{len(data['clusters'])} clusters → {path}"
    )


def load_knowledge_graph(path: str = "docs/knowledge_graph.json") -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_for_architect(kg: dict) -> str:
    """
    Serialize Knowledge Graph thành text context để inject vào architect prompt.
    Chỉ giữ thông tin hữu ích — không dump toàn bộ JSON.
    """
    lines = ["# Knowledge Graph Context\n"]

    lines.append("## Entity enrichment")
    for node in kg["nodes"].values():
        tags_str = ", ".join(node["domain_tags"]) if node["domain_tags"] else "general"
        lines.append(
            f"- {node['id']} ({node['name']}): "
            f"tags=[{tags_str}] lifecycle={node['lifecycle']} "
            f"ownership={node['ownership']} security={node['security_level']}"
        )

    inferred = [e for e in kg["edges"] if e["confidence"] < 1.0 and e["confidence"] >= 0.65]
    if inferred:
        lines.append("\n## Inferred relationships (high confidence)")
        for edge in inferred:
            lines.append(
                f"- {edge['from']} --[{edge['type']}]--> {edge['to']}  "
                f"(confidence={edge['confidence']:.2f}: {edge['reason']})"
            )

    if kg["clusters"]:
        lines.append("\n## Suggested service clusters")
        for c in kg["clusters"]:
            ent_str = ", ".join(c["entities"])
            lines.append(f"- {c['name']} [{ent_str}] → suggested: {c['suggested_service']}")

    if kg["constraints"]:
        lines.append("\n## Constraints to enforce in API design")
        for con in kg["constraints"]:
            lines.append(f"- {con['entity']}: {con['type']} — {con['reason']}")

    if kg["architect_hints"]:
        lines.append("\n## Architect hints")
        for hint in kg["architect_hints"]:
            lines.append(f"- {hint}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CLI helper
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ent_path = "docs/entities.json"
    req_path = "docs/requirements.md"

    if not os.path.exists(ent_path):
        print(f"ERROR: {ent_path} not found — run requirement-agent first")
        exit(1)

    with open(ent_path, encoding="utf-8") as f:
        entities = json.load(f)

    req_text = ""
    if os.path.exists(req_path):
        with open(req_path, encoding="utf-8") as f:
            req_text = f.read()

    kg = build_knowledge_graph(entities, req_text)
    save_knowledge_graph(kg)

    print("\n--- Architect context preview ---")
    print(format_for_architect(kg))