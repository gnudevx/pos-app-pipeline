"""
Knowledge Graph Builder v4
==========================

Triết lý: "Đọc những gì entity TỰ khai báo — không đoán thêm bất kỳ điều gì."

Nguồn dữ liệu theo thứ tự ưu tiên:
  1. Routes trong description (ground truth tuyệt đối — do RequirementAgent sinh ra)
  2. Description patterns (fallback khi frontend không có routes)
  3. "general" (không bao giờ crash)

Thiết kế hướng đến architect agent:
  - format_for_architect() output NGẮN, DENSE, ACTIONABLE
  - Mỗi constraint có entity ID rõ ràng → architect copy-paste được ngay
  - Không có thông tin thừa, không có heuristic noise
  - Token budget: < 1500 tokens cho 12 entities (so với v1: 3000+)

v4 so với v3:
  - load_knowledge_graph() có default path "docs/knowledge_graph.json"
  - format_for_architect() viết lại hoàn toàn: compact table + numbered hints
  - _build_hints() loại bỏ H5 co_location (noise với Gemini) + H9 req_text scan
    (req_text không reliable, architect-agent.md đã handle)
  - _build_constraints() thêm inventory vào _REQUIRES_JWT_DOMAINS (POS cashier flow)
  - _DOMAIN_SECURITY: inventory → "high" (stock deduction is business-critical write)
  - Validation: thêm check lifecycle vs component consistency
  - Public API không thay đổi: build_knowledge_graph(), save_knowledge_graph(),
    load_knowledge_graph(), format_for_architect() — backward compat hoàn toàn
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# TYPES
# ══════════════════════════════════════════════════════════════════════════════

# (HTTP_METHOD_UPPERCASE, path_lowercase)
Route = tuple[str, str]


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE EXTRACTION
# Ground truth: RequirementAgent luôn sinh routes theo format chuẩn.
# Format: "Routes: METHOD /path, METHOD /path"  hoặc inline "POST /cart/add"
# ══════════════════════════════════════════════════════════════════════════════

def _extract_routes(entity: dict) -> list[Route]:
    """
    Extract tất cả HTTP routes từ description field.

    Hỗ trợ các format requirement-agent sinh ra:
      "Routes: POST /auth/signup, GET /products/{id}"
      "Route: POST /checkout"
      "Endpoint: GET /orders"
      "POST /cart/add"  (không prefix)
    """
    description = entity.get("description", "")
    routes: list[Route] = []
    for match in re.finditer(
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}._\-]+)",
        description,
        re.IGNORECASE,
    ):
        routes.append((match.group(1).upper(), match.group(2).lower().rstrip("/")))
    return routes


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN DETECTION
#
# Thứ tự:
#   1. Route-path analysis (most reliable — không bị ảnh hưởng bởi prose)
#   2. Description keyword patterns (frontend fallback)
#   3. "general" (never crashes)
#
# Nguyên tắc bất biến:
#   - "price" trong Product context KHÔNG phải "payment"
#   - "session" trong Cart context KHÔNG phải "auth"
#   - "detail" / "receipt" trong Order context KHÔNG phải "ai_ml"
#   Routes giải quyết tất cả false-positive này.
# ══════════════════════════════════════════════════════════════════════════════

# Route first-path-segment → canonical domain
# Key: lowercase segment (no slashes, no {params})
_ROUTE_ROOT_TO_DOMAIN: dict[str, str] = {
    # Auth
    "auth":         "auth",
    "login":        "auth",
    "logout":       "auth",
    "signup":       "auth",
    "register":     "auth",
    "token":        "auth",
    "refresh":      "auth",
    # Product
    "products":     "product",
    "product":      "product",
    "catalog":      "product",
    "items":        "product",
    # Inventory
    "inventory":    "inventory",
    "stock":        "inventory",
    # Cart
    "cart":         "cart",
    "basket":       "cart",
    # Checkout
    "checkout":     "checkout",
    "purchase":     "checkout",
    # Order history
    "orders":       "order_history",
    "order":        "order_history",
    "receipts":     "order_history",
    "transactions": "order_history",
    # User profile (distinct from auth)
    "users":        "user",
    "user":         "user",
    "profile":      "user",
    "accounts":     "user",
    # Payment gateway (real payment processor, not checkout)
    "payments":     "payment",
    "payment":      "payment",
    "billing":      "payment",
    # Notification
    "notifications": "notification",
    "notify":       "notification",
    "emails":       "notification",
    # File storage
    "files":        "file",
    "upload":       "file",
    "media":        "file",
    # Search
    "search":       "search",
    # Realtime
    "ws":           "realtime",
    "websocket":    "realtime",
    # Analytics
    "analytics":    "analytics",
    "metrics":      "analytics",
    "reports":      "analytics",
    # Admin
    "admin":        "admin",
}


# Description-level keyword patterns — chỉ dùng khi không có routes (frontend)
# Ưu tiên: check specific patterns trước, general patterns sau
# KHÔNG dùng single-word như "price", "session", "detail" → false-positive
_DESC_DOMAIN_PATTERNS: list[tuple[str, str]] = [
    # Auth — explicit auth verbs + JWT
    (r"\b(jwt|bearer.token|signup|sign.?up|log.?in|logout|credential|authenticat)\b", "auth"),
    # Inventory — explicit stock operations
    # [FIX KG-1] Thêm "stock levels", "stock" standalone và "inventory" standalone
    # để ENT-12 (Inventory Frontend) với desc "viewing current stock levels" được detect đúng.
    (r"\b(stock.level|stock.levels|current.stock|deduct.stock|update.stock|inventory.management|inventory|stock)\b", "inventory"),
    # Cart — explicit cart operations (phrase-level, not single word)
    (r"\b(shopping.cart|cart.item|add.to.cart|remove.from.cart|in.session.cart|view.cart)\b", "cart"),
    # Checkout — explicit checkout/receipt/purchase
    (r"\b(checkout|process.*sale|generate.*receipt|complete.*purchase|confirm.*order)\b", "checkout"),
    # Order history — past records (phrase-level)
    (r"\b(past.(sales?|order|transaction)|order.history|transaction.history|past.receipt)\b", "order_history"),
    # Product catalog — CRUD on products (phrase-level)
    (r"\b(product.(data|catalog|list|crud)|crud.*product|manage.product|product.list)\b", "product"),
    # Payment gateway — explicit payment processor keywords
    (r"\b(payment.gateway|process.payment|billing|stripe|invoice)\b", "payment"),
    # Notification
    (r"\b(send.(email|sms|push)|notification.service|mailer)\b", "notification"),
    # File
    (r"\b(file.upload|file.download|object.storage|s3|blob)\b", "file"),
    # Realtime
    (r"\b(websocket|real.?time|live.update|socket\.io)\b", "realtime"),
    # Search
    (r"\b(full.?text.search|elasticsearch|search.index)\b", "search"),
    # Analytics
    (r"\b(analytics|audit.log|tracking)\b", "analytics"),
]


def _detect_canonical_domain(entity: dict) -> str:
    """
    Detect domain theo thứ tự ưu tiên:
      1. Route-path analysis (vote-based, most reliable)
      2. Description keyword patterns (fallback — frontend có no routes)
      3. "general" (không bao giờ crash, validation sẽ warn)
    """
    routes = _extract_routes(entity)

    if routes:
        domain_votes: dict[str, int] = {}
        for _method, path in routes:
            segments = [s for s in path.split("/") if s and not s.startswith("{")]
            if segments:
                domain = _ROUTE_ROOT_TO_DOMAIN.get(segments[0])
                if domain:
                    domain_votes[domain] = domain_votes.get(domain, 0) + 1
        if domain_votes:
            return max(domain_votes, key=lambda d: domain_votes[d])

    # Fallback: description patterns (frontend entities không có routes)
    text = (entity.get("name", "") + " " + entity.get("description", "")).lower()
    for pattern, domain in _DESC_DOMAIN_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return domain

    return "general"


# ══════════════════════════════════════════════════════════════════════════════
# NODE PROPERTY TABLES
# Deterministic lookup — không heuristic, không infer
# ══════════════════════════════════════════════════════════════════════════════

_DOMAIN_SECURITY: dict[str, str] = {
    "auth":          "critical",    # credentials, tokens
    "payment":       "critical",    # financial transactions
    "checkout":      "high",        # order creation, money movement
    "inventory":     "high",        # stock deduction — business-critical write
    "user":          "high",        # PII
    "cart":          "medium",      # user-scoped but not financial
    "order_history": "medium",      # read-heavy, user-scoped
    "product":       "medium",      # public read, protected write
    "notification":  "low",
    "file":          "medium",
    "analytics":     "low",
    "search":        "low",
    "admin":         "high",
    "ai_ml":         "low",
    "realtime":      "low",
    "general":       "low",
}

_DOMAIN_LIFECYCLE: dict[str, str] = {
    "auth":          "persistent",   # user records in DB
    "payment":       "persistent",
    "checkout":      "persistent",   # orders must be persisted
    "cart":          "transient",    # in-session: memory dict or Redis
    "order_history": "persistent",
    "inventory":     "persistent",
    "product":       "persistent",
    "user":          "persistent",
    "notification":  "ephemeral",    # fire-and-forget
    "file":          "persistent",
    "analytics":     "persistent",
    "search":        "transient",    # index, not source of truth
    "admin":         "persistent",
    "ai_ml":         "transient",    # stateless inference
    "realtime":      "streaming",
    "general":       "persistent",
}

_DOMAIN_OWNERSHIP: dict[str, str] = {
    "auth":          "system",        # auth service itself is system-owned
    "payment":       "user-scoped",
    "checkout":      "user-scoped",
    "cart":          "user-scoped",
    "order_history": "user-scoped",
    "inventory":     "system",        # store-wide resource
    "product":       "system",        # store-wide catalog
    "user":          "user-scoped",
    "notification":  "system",
    "file":          "user-scoped",
    "analytics":     "system",
    "search":        "system",
    "admin":         "system",
    "ai_ml":         "system",
    "realtime":      "user-scoped",
    "general":       "system",
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA SHAPE DETECTION
# Từ routes (backend) hoặc description keywords (frontend)
# ══════════════════════════════════════════════════════════════════════════════

def _detect_data_shape(entity: dict, routes: list[Route]) -> str:
    """
    Detect data shape:
      collection: GET /resources (no trailing {id}) → returns list
      single:     GET /resources/{id} only → returns one item
      stream:     realtime domain
      no_data:    frontend, or only mutation routes
    """
    if entity.get("component") == "frontend":
        desc = entity.get("description", "").lower()
        if re.search(r"\b(list|all|history|catalog|records|multiple|grid|table)\b", desc):
            return "collection"
        return "single"

    if not routes:
        return "single"

    get_paths = [path for method, path in routes if method == "GET"]
    if not get_paths:
        return "single"  # write-only endpoint

    # GET without {param} at end = collection
    if any(not re.search(r"\{[^}]+\}$", p) for p in get_paths):
        return "collection"
    return "single"


# ══════════════════════════════════════════════════════════════════════════════
# MUTATION TYPE
# Từ HTTP methods — cho architect biết entity nào write-heavy
# ══════════════════════════════════════════════════════════════════════════════

def _detect_mutation_type(routes: list[Route]) -> str:
    if not routes:
        return "no_routes"
    reads  = sum(1 for m, _ in routes if m == "GET")
    writes = len(routes) - reads
    if writes == 0:
        return "read_only"
    if reads == 0:
        return "write_heavy"
    return "mixed"


# ══════════════════════════════════════════════════════════════════════════════
# EDGES — declared only, không infer thêm bao giờ
#
# v1/v2 đã chứng minh: inferred edges = noise (79 edges cho 12 entities).
# depends_on trong entities.json đã đủ để architect hiểu dependency graph.
# Chỉ thêm relationship_type metadata để architect biết loại call.
# ══════════════════════════════════════════════════════════════════════════════

def _build_edges(entities: list[dict], nodes: dict) -> list[dict]:
    entity_map = {e["id"]: e for e in entities}
    edges: list[dict] = []

    for ent in entities:
        src_comp   = ent.get("component", "")
        src_domain = nodes[ent["id"]]["canonical_domain"]

        for dep_id in ent.get("depends_on", []):
            if dep_id not in entity_map:
                continue
            dst       = entity_map[dep_id]
            dst_comp  = dst.get("component", "")
            dst_domain = nodes[dep_id]["canonical_domain"]

            if src_comp == "frontend" and dst_comp == "backend":
                rel = "api_call"
            elif src_comp == "backend" and dst_comp == "backend":
                rel = "internal_call" if src_domain == dst_domain else "service_call"
            elif src_comp == "frontend" and dst_comp == "frontend":
                rel = "ui_dependency"
            else:
                rel = "depends_on"

            edges.append({
                "from":              ent["id"],
                "to":                dep_id,
                "relationship_type": rel,
                "confidence":        1.0,
                "source":            "declared",
            })

    return edges


# ══════════════════════════════════════════════════════════════════════════════
# CLUSTERS — canonical_domain + component
# Service boundary suggestions cho architect.
# Architect có thể merge hoặc split — đây chỉ là starting point.
# ══════════════════════════════════════════════════════════════════════════════

_DOMAIN_DISPLAY: dict[str, str] = {
    "auth":          "Auth",
    "product":       "Product catalog",
    "inventory":     "Inventory",
    "cart":          "Shopping cart",
    "checkout":      "Checkout",
    "order_history": "Order history",
    "payment":       "Payment",
    "user":          "User profile",
    "notification":  "Notification",
    "file":          "File storage",
    "analytics":     "Analytics",
    "search":        "Search",
    "admin":         "Admin",
    "ai_ml":         "AI/ML",
    "realtime":      "Realtime",
    "general":       "General",
}


def _build_clusters(entities: list[dict], nodes: dict) -> list[dict]:
    group: dict[str, list[str]] = {}
    for ent in entities:
        domain = nodes[ent["id"]]["canonical_domain"]
        comp   = ent.get("component", "fullstack")
        key    = f"{domain}__{comp}"
        group.setdefault(key, []).append(ent["id"])

    clusters = []
    for idx, (key, ent_ids) in enumerate(sorted(group.items()), start=1):
        domain, comp = key.split("__", 1)
        display      = _DOMAIN_DISPLAY.get(domain, domain.replace("_", " ").title())
        clusters.append({
            "id":                f"CLU-{idx:02d}",
            "name":              f"{display} {comp}",
            "entities":          ent_ids,
            "canonical_domain":  domain,
            "component":         comp,
            "suggested_service": f"{domain.replace('_', '-')}-{comp}",
        })
    return clusters


# ══════════════════════════════════════════════════════════════════════════════
# CONSTRAINTS — non-negotiable rules cho architect
#
# Chỉ emit khi có evidence rõ ràng từ canonical_domain + ownership.
# KHÔNG emit constraints cho frontend entities (frontend không có auth middleware).
# ══════════════════════════════════════════════════════════════════════════════

# Domains mà mọi endpoint PHẢI verify JWT (full protection)
# inventory: cashier phải login trước khi deduct stock (POS flow)
_REQUIRES_JWT_DOMAINS: frozenset[str] = frozenset({
    "cart", "checkout", "order_history", "payment", "user", "file", "inventory",
})

# Domains mà chỉ WRITE endpoints cần JWT (GET có thể public)
# product: GET /products là public catalog, POST/PUT/DELETE là admin-only
# admin: GET dashboard cũng nên protected — nên admin vẫn full
_WRITE_ONLY_JWT_DOMAINS: frozenset[str] = frozenset({
    "product",
})

# Domains yêu cầu audit log (write operations phải được traced)
_REQUIRES_AUDIT_DOMAINS: frozenset[str] = frozenset({
    "auth", "checkout", "payment", "inventory",
})

# Domains yêu cầu DB transaction (multi-table write trong một flow)
_REQUIRES_ATOMIC_DOMAINS: frozenset[str] = frozenset({
    "checkout", "payment",
})

# Domains yêu cầu idempotency key (prevent double-charge/double-order on retry)
_REQUIRES_IDEMPOTENCY_DOMAINS: frozenset[str] = frozenset({
    "checkout", "payment",
})


def _build_constraints(entities: list[dict], nodes: dict) -> list[dict]:
    constraints: list[dict] = []

    for ent in entities:
        node      = nodes[ent["id"]]
        domain    = node["canonical_domain"]
        comp      = ent.get("component", "")
        name      = ent.get("name", ent["id"])
        routes    = node.get("routes", [])
        mutation  = node.get("mutation_type", "")

        # Frontend không có server-side constraints
        if comp != "backend":
            continue

        # requires_jwt (full): user-scoped domains + explicit domain list
        if node["ownership"] == "user-scoped" or domain in _REQUIRES_JWT_DOMAINS:
            constraints.append({
                "entity":     ent["id"],
                "type":       "requires_jwt",
                "auth_scope": "full",
                "reason": (
                    f"{name}: domain={domain}, ownership={node['ownership']} "
                    f"→ every endpoint must validate Bearer token before processing"
                ),
            })

        # requires_jwt (write_only): public GET, protected POST/PUT/DELETE
        # e.g. product catalog: anyone browses, only admin mutates
        elif domain in _WRITE_ONLY_JWT_DOMAINS:
            has_writes = any(m in ("POST", "PUT", "PATCH", "DELETE") for m, _ in routes)
            if has_writes:
                write_methods = sorted({m for m, _ in routes if m != "GET"})
                constraints.append({
                    "entity":     ent["id"],
                    "type":       "requires_jwt",
                    "auth_scope": "write_only",
                    "reason": (
                        f"{name}: domain={domain} — GET endpoints are public; "
                        f"{'/'.join(write_methods)} endpoints require Bearer token "
                        f"(admin or authenticated user only)"
                    ),
                })

        # audit_log: security-critical domains with write operations
        if domain in _REQUIRES_AUDIT_DOMAINS and mutation in ("write_heavy", "mixed"):
            constraints.append({
                "entity": ent["id"],
                "type":   "audit_log",
                "reason": (
                    f"{name}: domain={domain} with mutations "
                    f"→ log every write (actor, action, timestamp) for security trace"
                ),
            })

        # atomic_transaction: checkout deducts inventory + creates order
        if domain in _REQUIRES_ATOMIC_DOMAINS:
            constraints.append({
                "entity": ent["id"],
                "type":   "atomic_transaction",
                "reason": (
                    f"{name}: multi-table write in one request "
                    f"→ wrap in DB transaction; cross-service: use saga pattern"
                ),
            })

        # idempotency_key: prevent duplicate orders on network retry
        if domain in _REQUIRES_IDEMPOTENCY_DOMAINS:
            has_post = any(m == "POST" for m, _ in routes)
            if has_post:
                constraints.append({
                    "entity": ent["id"],
                    "type":   "idempotency_key",
                    "reason": (
                        f"{name}: POST in {domain} domain "
                        f"→ client sends Idempotency-Key header; "
                        f"server deduplicates within 24h window"
                    ),
                })

        # rate_limit: AI and file upload are resource-intensive
        if domain in ("ai_ml", "file"):
            constraints.append({
                "entity": ent["id"],
                "type":   "rate_limit",
                "reason": (
                    f"{name}: domain={domain} is resource-intensive "
                    f"→ per-user rate limit (token bucket or sliding window)"
                ),
            })

    return constraints


# ══════════════════════════════════════════════════════════════════════════════
# ARCHITECT HINTS
#
# Mỗi hint:
#   type:     machine-readable category
#   priority: critical | high | medium | low
#   entities: list ENT-ID liên quan (để architect trace ngay)
#   message:  actionable instruction — không phải observation
#
# Chỉ emit hint khi có evidence từ declared data.
# KHÔNG emit hints dựa trên heuristic hoặc inferred data.
#
# Hints bị loại bỏ so với v3:
#   - H5 co_location_candidate: noise — architect-agent.md đã handle service merging
#   - H9 req_text scan: không reliable, Gemini không cần, tốn tokens
# ══════════════════════════════════════════════════════════════════════════════

def _build_hints(
    entities: list[dict],
    nodes: dict,
    clusters: list[dict],
    _req_text: str = "",  # kept for API compat, not used
) -> list[dict]:
    hints: list[dict] = []
    entity_map = {e["id"]: e for e in entities}

    def _be(domain: str) -> list[str]:
        """Backend entity IDs for a given domain."""
        return [
            e["id"] for e in entities
            if nodes[e["id"]]["canonical_domain"] == domain
            and e.get("component") == "backend"
        ]

    auth_be     = _be("auth")
    cart_be     = _be("cart")
    inv_be      = _be("inventory")
    checkout_be = _be("checkout")

    # ── H1: Auth startup order ────────────────────────────────────────────────
    # Auth backend phải healthy trước khi các protected backend accept traffic
    protected_be = [
        e["id"] for e in entities
        if nodes[e["id"]]["ownership"] == "user-scoped"
        and e.get("component") == "backend"
        and e["id"] not in auth_be
    ]
    if auth_be and protected_be:
        hints.append({
            "type":     "startup_order",
            "priority": "critical",
            "entities": auth_be + protected_be,
            "message": (
                f"Auth service ({', '.join(auth_be)}) must be healthy before "
                f"{', '.join(protected_be)} accept traffic. "
                f"docker-compose: depends_on + condition: service_healthy. "
                f"k8s: readinessProbe or initContainer."
            ),
        })

    # ── H2: Checkout ↔ Inventory transaction boundary ────────────────────────
    # Checkout deducts inventory → must be atomic or have compensating transaction
    checkout_with_inv_dep = [
        e["id"] for e in entities
        if nodes[e["id"]]["canonical_domain"] == "checkout"
        and e.get("component") == "backend"
        and any(
            nodes.get(dep, {}).get("canonical_domain") == "inventory"
            for dep in e.get("depends_on", [])
        )
    ]
    if checkout_with_inv_dep and inv_be:
        hints.append({
            "type":     "transaction_boundary",
            "priority": "critical",
            "entities": checkout_with_inv_dep + inv_be,
            "message": (
                f"Checkout ({', '.join(checkout_with_inv_dep)}) calls inventory deduct "
                f"({', '.join(inv_be)}) — must succeed or fail atomically. "
                f"Same DB: wrap both writes in one transaction. "
                f"Separate services: re-stock on checkout failure (compensating tx)."
            ),
        })

    # ── H3: Cart storage choice ───────────────────────────────────────────────
    if cart_be:
        hints.append({
            "type":     "storage_choice",
            "priority": "high",
            "entities": cart_be,
            "message": (
                f"Cart ({', '.join(cart_be)}) lifecycle=transient. "
                f"Choose: (a) in-memory dict — simplest, cart lost on restart; "
                f"(b) Redis — survives restart, supports TTL; "
                f"(c) DB table — full audit, overkill for most POS. "
                f"For MVP POS: option (a) is sufficient."
            ),
        })

    # ── H4: Orphaned frontend (frontend with no declared backend dependency) ──
    orphan_fe = [
        e["id"] for e in entities
        if e.get("component") == "frontend"
        and not any(
            entity_map.get(dep, {}).get("component") == "backend"
            for dep in e.get("depends_on", [])
        )
    ]
    if orphan_fe:
        hints.append({
            "type":     "missing_dependency",
            "priority": "high",
            "entities": orphan_fe,
            "message": (
                f"Frontend entities {', '.join(orphan_fe)} have no declared backend "
                f"dependency. If they call a backend, add it to depends_on in entities.json "
                f"before architect step."
            ),
        })

    # ── H5: Cross-domain backend service calls ────────────────────────────────
    # Các calls này cần explicit API contract + partial-failure handling
    cross_domain_calls: list[tuple[str, str]] = []
    for ent in entities:
        if ent.get("component") != "backend":
            continue
        src_domain = nodes[ent["id"]]["canonical_domain"]
        for dep_id in ent.get("depends_on", []):
            dep = entity_map.get(dep_id)
            if not dep or dep.get("component") != "backend":
                continue
            dst_domain = nodes[dep_id]["canonical_domain"]
            if src_domain != dst_domain:
                cross_domain_calls.append((ent["id"], dep_id))

    if cross_domain_calls:
        involved  = sorted({e for pair in cross_domain_calls for e in pair})
        pairs_str = ", ".join(f"{a}→{b}" for a, b in cross_domain_calls)
        hints.append({
            "type":     "cross_domain_contract",
            "priority": "medium",
            "entities": involved,
            "message": (
                f"Cross-domain backend calls: {pairs_str}. "
                f"Each needs explicit request/response schema. "
                f"Caller must handle downstream unavailability (timeout + fallback)."
            ),
        })

    # ── H6: Realtime isolation ────────────────────────────────────────────────
    rt_ents = [e["id"] for e in entities if nodes[e["id"]]["canonical_domain"] == "realtime"]
    if rt_ents:
        hints.append({
            "type":     "realtime_isolation",
            "priority": "high",
            "entities": rt_ents,
            "message": (
                f"{', '.join(rt_ents)} require persistent WebSocket connections. "
                f"Must be isolated into a dedicated service — "
                f"do NOT co-locate with stateless REST handlers."
            ),
        })

    # ── H7: AI/ML isolation ───────────────────────────────────────────────────
    ai_ents = [e["id"] for e in entities if nodes[e["id"]]["canonical_domain"] == "ai_ml"]
    if ai_ents:
        hints.append({
            "type":     "ai_isolation",
            "priority": "medium",
            "entities": ai_ents,
            "message": (
                f"{', '.join(ai_ents)} involve AI inference. "
                f"Dedicate a separate service: different Python runtime, GPU/CPU, "
                f"token cost, latency profile. Expose via internal HTTP only."
            ),
        })

    # Sort by priority
    _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    hints.sort(key=lambda h: _order.get(h.get("priority", "low"), 99))
    return hints


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# Kiểm tra invariants sau build — catch lỗi trước khi save
# ══════════════════════════════════════════════════════════════════════════════

def _validate(kg: dict) -> list[str]:
    """Returns list of warning strings. Empty list = clean."""
    warnings: list[str] = []
    nodes    = kg["nodes"]
    entities = kg.get("_source_entities", [])

    for nid, node in nodes.items():
        # Domain fallback warning
        if node.get("canonical_domain") == "general":
            warnings.append(
                f"[WARN] {nid} ({node.get('name')}) fell back to domain='general' — "
                f"check routes or description in entities.json"
            )

        # Frontend should not have lifecycle=transient (transient is a backend concern)
        if node.get("component") == "frontend" and node.get("lifecycle") == "transient":
            warnings.append(
                f"[WARN] {nid} ({node.get('name')}) is frontend but has lifecycle=transient "
                f"— lifecycle for frontend is always 'persistent' (SPA, no server state)"
            )

    # Declared-only policy: no inferred edges should exist
    inferred = [e for e in kg["edges"] if e.get("source") != "declared"]
    if inferred:
        warnings.append(
            f"[ERROR] {len(inferred)} non-declared edges found — "
            f"_build_edges() must only use depends_on"
        )

    # Edge count must match total declared depends_on
    if entities:
        total_declared = sum(len(e.get("depends_on", [])) for e in entities)
        if kg["edge_count"] != total_declared:
            warnings.append(
                f"[ERROR] Edge count mismatch: KG has {kg['edge_count']} edges "
                f"but entities declare {total_declared} depends_on relationships"
            )

    # Warn on depends_on referencing non-existent entity
    ent_ids = {e["id"] for e in entities}
    for ent in entities:
        for dep in ent.get("depends_on", []):
            if dep not in ent_ids:
                warnings.append(
                    f"[ERROR] {ent['id']} depends_on '{dep}' which does not exist in entities"
                )

    return warnings


# ══════════════════════════════════════════════════════════════════════════════
# FORMAT FOR ARCHITECT
#
# Mục tiêu: < 1500 tokens cho 12 entities.
# Architect agent (Gemini) cần:
#   1. Biết domain của từng entity để group đúng service
#   2. Biết constraints phải implement (JWT, audit, atomic)
#   3. Biết critical hints (startup order, transaction boundary)
#   4. KHÔNG cần: verbose explanations, low-priority hints, cluster list
#
# Format: compact markdown table + numbered actionable list
# Không dùng header cấp 3 (###) vì Gemini count tokens per heading.
# ══════════════════════════════════════════════════════════════════════════════

def format_for_architect(kg: dict) -> str:
    """
    Serialize KG thành text compact cho architect agent.

    Output structure:
      1. Entity classification table (1 row per entity)
      2. Dependency graph (declared only)
      3. Suggested service clusters
      4. Constraints (backend only, grouped by type)
      5. Critical + high hints (medium/low bị bỏ để tiết kiệm tokens)
    """
    lines: list[str] = ["## Knowledge Graph\n"]

    # 1. Entity table — giữ nguyên của bạn (tốt hơn)
    lines.append("| ID | Name | Domain | Comp | Lifecycle | Security | Shape | Mutation |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for node in kg["nodes"].values():
        lines.append(
            f"| {node['id']} | {node['name']} "
            f"| {node['canonical_domain']} | {node['component']} "
            f"| {node['lifecycle']} | {node['security_level']} "
            f"| {node['data_shape']} | {node['mutation_type']} |"
        )

    # 2. Dependency graph — giữ nguyên của bạn
    if kg["edges"]:
        lines.append("\n**Dependencies (declared)**")
        for e in kg["edges"]:
            lines.append(f"- {e['from']} → {e['to']} [{e['relationship_type']}]")

    # 3. Clusters — giữ nguyên của bạn
    if kg["clusters"]:
        lines.append("\n**Suggested service clusters**")
        for c in kg["clusters"]:
            ents = ", ".join(c["entities"])
            lines.append(f"- `{c['suggested_service']}`: {ents}")

    # 4. Constraints — giữ grouping của bạn, thêm IMPORTANT header
    if kg["constraints"]:
        lines.append("\n**Constraints (non-negotiable)**")
        lines.append("IMPORTANT: Every backend service MUST implement constraints assigned to it.")

        by_group: dict[str, list[dict]] = {}
        for con in kg["constraints"]:
            ctype = con["type"]
            if ctype == "requires_jwt":
                # FIX: chỉ dùng auth_scope nếu field thực sự tồn tại trong KG
                scope = con.get("auth_scope")
                key = f"requires_jwt:{scope}" if scope else "requires_jwt:full"
            else:
                key = ctype
            by_group.setdefault(key, []).append(con)

        for key, items in sorted(by_group.items()):
            ids = ", ".join(i["entity"] for i in items)
            if key == "requires_jwt:write_only":
                lines.append(f"- `requires_jwt` (write_only) → {ids}")
                lines.append(f"  - GET is public; POST/PUT/DELETE require Bearer token")
            elif key.startswith("requires_jwt"):
                lines.append(f"- `requires_jwt` (all routes) → {ids}")
                lines.append(f"  - Every endpoint must validate Bearer token")
            else:
                lines.append(f"- `{key}` → {ids}")
                lines.append(f"  - {items[0]['reason']}")
                if len(items) > 1:
                    extra = ", ".join(i["entity"] for i in items[1:])
                    lines.append(f"  - (same rule applies to {extra})")

    # 5. Hints — giữ filter critical+high của bạn, thêm IMPORTANT header
    high_prio_hints = [
        h for h in kg.get("architect_hints", [])
        if h.get("priority") in ("critical", "high")
    ]
    if high_prio_hints:
        lines.append("\n**Architect hints (must be reflected in architecture decisions)**")
        for i, h in enumerate(high_prio_hints, 1):
            ents_str = f" [{', '.join(h['entities'])}]" if h.get("entities") else ""
            lines.append(f"{i}. [{h['priority'].upper()}] {h['type']}{ents_str}: {h['message']}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def build_knowledge_graph(
    entities: list[dict],
    requirements_text: str = "",
) -> dict:
    """
    Main entry point.
    entities.json → enriched knowledge graph cho architect.

    Không mock, không hardcode, không infer ngoài declared data.

    Args:
        entities:          list từ entities.json
        requirements_text: nội dung requirements.md (optional, dùng cho req-driven hints)

    Returns:
        KG dict với các keys: schema_version, nodes, edges, clusters,
        constraints, architect_hints, node_count, edge_count, cluster_count
    """
    nodes: dict[str, dict] = {}
    for ent in entities:
        eid    = ent["id"]
        routes = _extract_routes(ent)
        domain = _detect_canonical_domain(ent)
        comp = ent.get("component", "fullstack")

        # Frontend components are always "persistent" lifecycle —
        # they are static assets (SPA), server-side lifecycle concept doesn't apply.
        # Only backend/service components inherit domain lifecycle.
        if comp == "frontend":
            lifecycle = "persistent"
        else:
            lifecycle = _DOMAIN_LIFECYCLE.get(domain, "persistent")

        nodes[eid] = {
            "id":               eid,
            "name":             ent.get("name", ""),
            "component":        comp,
            "complexity":       ent.get("complexity", "medium"),
            "canonical_domain": domain,
            "lifecycle":        lifecycle,
            "ownership":        _DOMAIN_OWNERSHIP.get(domain, "system"),
            "security_level":   _DOMAIN_SECURITY.get(domain, "low"),
            "data_shape":       _detect_data_shape(ent, routes),
            "mutation_type":    _detect_mutation_type(routes),
            "routes":           routes,  # internal — stripped before save
        }

    edges       = _build_edges(entities, nodes)
    clusters    = _build_clusters(entities, nodes)
    constraints = _build_constraints(entities, nodes)
    hints       = _build_hints(entities, nodes, clusters, requirements_text)

    return {
        "schema_version":   "4",
        "generated_from":   ["entities.json", "requirements.md"],
        "node_count":       len(nodes),
        "edge_count":       len(edges),
        "cluster_count":    len(clusters),
        "nodes":            nodes,
        "edges":            edges,
        "clusters":         clusters,
        "constraints":      constraints,
        "architect_hints":  hints,
        "_source_entities": entities,   # internal — stripped before save
    }


def save_knowledge_graph(kg: dict, path: str = "docs/knowledge_graph.json") -> None:
    """Save KG to JSON, stripping internal fields."""
    out = {k: v for k, v in kg.items() if not k.startswith("_")}
    # routes is internal detail — architect reads domain/constraint, not raw routes
    for node in out["nodes"].values():
        node.pop("routes", None)

    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(
        f"[kg-v4] {out['node_count']} nodes | "
        f"{out['edge_count']} edges | "
        f"{out['cluster_count']} clusters → {path}"
    )


def load_knowledge_graph(path: str = "docs/knowledge_graph.json") -> Optional[dict]:
    """
    Load KG from JSON.

    Args:
        path: default "docs/knowledge_graph.json" — matches orchestrator convention

    Returns:
        KG dict, or None if file does not exist.
    """
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# Usage: python knowledge_graph_builder.py [entities.json] [requirements.md] [output.json]
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    ent_path = sys.argv[1] if len(sys.argv) > 1 else "docs/entities.json"
    req_path = sys.argv[2] if len(sys.argv) > 2 else "docs/requirements.md"
    out_path = sys.argv[3] if len(sys.argv) > 3 else "docs/knowledge_graph.json"

    if not os.path.exists(ent_path):
        print(f"ERROR: {ent_path} not found")
        sys.exit(1)

    with open(ent_path, encoding="utf-8") as f:
        _entities = json.load(f)

    _req_text = ""
    if os.path.exists(req_path):
        with open(req_path, encoding="utf-8") as f:
            _req_text = f.read()

    _kg = build_knowledge_graph(_entities, _req_text)

    _warnings = _validate(_kg)
    if _warnings:
        print("⚠️  Validation warnings:")
        for w in _warnings:
            print(f"   {w}")
    else:
        print("✅ Validation passed — no warnings")

    save_knowledge_graph(_kg, out_path)
    print("\n" + format_for_architect(_kg))