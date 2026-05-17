---
name: dev-agent-task01
description: TASK-01 — Backend API spec and rules
included_by: dev-agent-gemini.md
---

# BACKEND RULES

- Framework: FastAPI
- Validation: Pydantic v2
- Use `model.model_dump()` — NEVER `model.dict()`
- Use `APIRouter` in every route file
- `products.py` and `cart.py` MUST define: `router = APIRouter()`
- Use `HTTPException` for all error responses
- Use `Response(status_code=204)` for DELETE endpoints
- All endpoint functions must be synchronous `def` — NEVER `async def`
- All endpoints must return data matching the declared Pydantic models
- CORS must be configured: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`
- Do NOT use `class Config` — use ONLY `model_config = ConfigDict(from_attributes=True)`

---

# CRITICAL RUNTIME RULE:
- Every PUT endpoint MUST end with:
  return updated_product
- NEVER use bare return
- NEVER return {}

# SHARED STATE CONTRACT (CRITICAL)

`products.py` is the SINGLE SOURCE OF TRUTH.

`products.py` MUST define EXACTLY:

```python
_db: dict[int, dict] = {}
_next_id: int = 1
```

Products MUST be stored as plain dictionaries.

Correct insertion example:
```python
product_data = product.model_dump()
product_data["id"] = _next_id
_db[_next_id] = product_data
```

`cart.py` MUST import EXACTLY:
```python
from .products import _db
```

`cart.py` MUST access products EXACTLY like:
```python
product = _db.get(product_id)
```

Correct field access:
```python
product = _db.get(product_id)
if product is None:
    raise HTTPException(status_code=404, detail="Product not found")
stock = product["stock"]
name  = product["name"]
price = product["price"]
```

DO NOT:
- create `products_db` or duplicate storage
- iterate `_db` as a list (`for p in _db`)
- store Pydantic objects inside `_db`
- use `_db.append(...)`
- use `product.stock`, `product.name` (dict, not model)

WRONG:
```python
for p in _db:
    if p.id == product_id: ...
```
```python
product = _db[product_id]
if product.stock < quantity: ...
```

CORRECT:
```python
product = _db.get(product_id)
if product is None:
    raise HTTPException(status_code=404, detail="Product not found")
if product["stock"] < quantity:
    raise HTTPException(status_code=400, detail="Insufficient stock")
```

Cart items MUST use integer product IDs as keys:
```python
cart_db: dict[int, int] = {}
```

---

# TASK-01 — Backend API

**Required files — generate EXACTLY these 8 files:**

```
src/backend/app/__init__.py
src/backend/app/main.py
src/backend/app/models/__init__.py
src/backend/app/models/product.py
src/backend/app/routes/__init__.py
src/backend/app/routes/products.py
src/backend/app/routes/cart.py
src/backend/requirements.txt
```

### `src/backend/app/__init__.py`
Empty file.

### `src/backend/app/main.py`
- Create FastAPI app
- Include products router with `prefix="/products"`
- Include cart router with `prefix="/cart"`
- Configure `CORSMiddleware`
- `GET /health` returns `{"status": "ok"}`

### `src/backend/app/models/product.py`
- Define: `ProductBase`, `ProductCreate`, `Product`
- Use `ConfigDict(from_attributes=True)`

### `src/backend/app/routes/products.py`
- State ownership (source of truth for product data):
  ```
  _db: dict[int, dict] = {}
  _next_id: int = 1
  ```
- Endpoints:
  - `GET /` — list all products, return `{"items": [...]}` wrapper
  - `POST /` — create product, return 201
  - `GET /{product_id}` — get one, return 404 if missing
  - `DELETE /{product_id}` — delete, return 204, return 404 if missing
  - `PUT /{product_id}` — update product fields, return 200 với full product object, return 404 if missing
### `src/backend/app/routes/cart.py`
- State: `cart_db`, `receipts_db`, `_next_receipt_id`
- Import product state via: `from .products import _db`
- Endpoints:
  - `GET /` — get cart, return 200
  - `POST /add` — add item to cart, MUST use `status_code=201`
  - `DELETE /clear` — clear cart, return 204
  - `POST /checkout` — checkout, return 200; raise `HTTPException(400, "Cart is empty")` if empty
- Timestamps must use: `datetime.utcnow().isoformat()`
- If product is missing: `raise HTTPException(status_code=404, detail="Product not found")`

### `src/backend/requirements.txt`
Exact content:
```
fastapi==0.115.0
uvicorn==0.30.0
pydantic==2.8.0
pytest==8.3.0
httpx==0.27.0
```