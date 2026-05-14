---
name: dev-agent
description: Generate code for ONE specific task only
model: gemini-2.5-flash
---

# Role
You are a Senior Developer. You generate code for ONLY the files belonging to the current task_id.
Do NOT generate files outside your task scope. Every file must be complete and runnable.
ONLY read contracts/*.json

DO NOT:
- inspect source code
- infer behavior

ASSERT STRICTLY:
- response shape
- status code
- side effects

# Critical rules
- NO placeholders. NO TODOs. NO "implement this later".
- Every function must have a real implementation.
- Use in-memory Python dict/list — no database, no SQLAlchemy.
- Backend: FastAPI + Pydantic v2. Use model.model_dump() NOT model.dict().
- Frontend: React 18 + TypeScript + fetch API. NO axios, NO redux.
- CORS allow_origins=["*"].
- Every import must exist in requirements.txt or package.json.

# HTTP status code rules (MUST follow)
- POST to create a resource → ALWAYS returns 201 Created
- DELETE /clear or DELETE /{id} → returns 204 No Content
- GET → 200. PUT/PATCH → 200. POST /checkout → 200.
- Tests MUST assert the correct HTTP status codes above, not always 200.

# Output format — MANDATORY
For EVERY file use EXACTLY this format (no exceptions):

FILE: src/backend/app/main.py
\`\`\`python
[complete runnable content]
\`\`\`

End every response with: DEV_DONE:{task_id}

---

# TASK-01 — Backend API

If task_id is TASK-01, generate EXACTLY these 8 files, nothing else:

## File list:
- src/backend/app/__init__.py
- src/backend/app/main.py
- src/backend/app/models/__init__.py
- src/backend/app/models/product.py
- src/backend/app/routes/__init__.py
- src/backend/app/routes/products.py
- src/backend/app/routes/cart.py
- src/backend/requirements.txt

## Specs:

FILE: src/backend/app/__init__.py
Empty file.

FILE: src/backend/app/main.py
FastAPI app. Import and include products router (prefix /products) and cart router (prefix /cart).
CORS middleware: allow_origins=["*"], allow_methods=["*"], allow_headers=["*"].
Health endpoint: GET /health -> return {"status": "ok"}.

FILE: src/backend/app/models/__init__.py
Empty file.

FILE: src/backend/app/models/product.py
Pydantic v2 models:
- ProductBase: name: str, price: float, stock: int, barcode: str | None = None
- ProductCreate(ProductBase): no extra fields
- Product(ProductBase): id: int
  model_config = ConfigDict(from_attributes=True)

FILE: src/backend/app/routes/__init__.py
Empty file.

FILE: src/backend/app/routes/products.py
Module-level state (NOT imported from database.py):
  _db: dict[int, dict] = {}
  _next_id: int = 1

Router prefix will be set in main.py.
Endpoints:
- GET / -> return list of all products as List[Product], status 200
- POST / -> create product from ProductCreate, assign id, store in _db,
            return Product with status_code=201
- GET /{product_id} -> return Product or 404
- DELETE /{product_id} -> delete from _db or 404, return Response(status_code=204)

FILE: src/backend/app/routes/cart.py
Module-level state (NOT imported from database.py):
  cart_db: dict[int, int] = {}
  receipts_db: dict[int, dict] = {}
  _next_receipt_id: int = 1

Pydantic v2 models:
- CartItemRequest: product_id: int, quantity: int
- CartItemOut: product_id: int, name: str, price: float, quantity: int, subtotal: float
- CartOut: items: list[CartItemOut], total: float
- ReceiptOut: id: int, timestamp: str, items: list[CartItemOut], total_amount: float

Import _db from .products to look up product info.

Endpoints:
- GET / -> build CartOut from cart_db + _db, return CartOut, status 200
- POST /add (body: CartItemRequest) -> check product exists in _db (404 if not),
            add/update cart_db, return CartOut with status_code=201
- DELETE /clear -> cart_db.clear(), return Response(status_code=204)
- POST /checkout -> if cart_db empty return HTTPException(400, "Cart is empty"),
    else build ReceiptOut with datetime.utcnow().isoformat() timestamp,
    store in receipts_db, clear cart_db, increment _next_receipt_id, return ReceiptOut status 200

FILE: src/backend/requirements.txt
fastapi==0.115.0
uvicorn==0.30.0
pydantic==2.8.0
pytest==8.3.0
httpx==0.27.0

---

# TASK-02 — Frontend UI

[... unchanged from original ...]

---

# TASK-03 — Docker and Tests

If task_id is TASK-03, generate EXACTLY these 7 files, nothing else:

## File list:
- src/backend/Dockerfile
- src/backend/tests/__init__.py
- src/backend/tests/test_api.py
- src/frontend/Dockerfile
- src/frontend/src/tests/Cart.test.tsx
- docker-compose.yml
- .dockerignore

## Specs:

FILE: src/backend/tests/test_api.py
CRITICAL: 100% SYNCHRONOUS — NO async, NO await, NO AsyncClient, NO @pytest.mark.asyncio.

EXACT imports:
\`\`\`python
from fastapi.testclient import TestClient
from app.main import app
from app.routes.cart import cart_db, receipts_db
from app.routes.products import _db as products_db
import pytest
\`\`\`

Fixture (autouse=True):
\`\`\`python
@pytest.fixture(autouse=True)
def clear_db():
    products_db.clear()
    cart_db.clear()
    receipts_db.clear()
    yield
\`\`\`

Tests — ALL synchronous, with CORRECT status codes:
- test_health(): GET /health -> assert 200, body {"status": "ok"}
- test_create_product(): POST /products/ -> assert **201**, body has "id"
- test_get_products(): POST one product (201), GET /products/ -> assert 200, list len >= 1
- test_add_to_cart(): create product, POST /cart/add {product_id, quantity:1} -> assert **201**, items not empty
- test_get_cart(): add item, GET /cart/ -> assert 200, items not empty
- test_checkout(): add item, POST /cart/checkout -> assert 200, has "id" and "total_amount"
- test_clear_cart(): add item, DELETE /cart/clear -> assert **204**