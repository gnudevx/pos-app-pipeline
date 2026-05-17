---
name: dev-agent-task03
description: TASK-03 — Docker, integration, and test spec
included_by: dev-agent-gemini.md
---

# TASK-03 — Docker and Tests

**Required files — generate EXACTLY these 7 files:**

```
src/backend/Dockerfile
src/backend/tests/__init__.py
src/backend/tests/test_api.py
src/frontend/Dockerfile
src/frontend/src/tests/Cart.test.tsx
docker-compose.yml
.dockerignore
```

---

## Backend test rules (`test_api.py`)

- Use `TestClient` only — synchronous tests only
- Tests must match API paths from TASK-01 exactly — do NOT invent endpoints
- Do NOT import: `asyncio`, `httpx.AsyncClient`, `pytest.mark.asyncio`
- Do NOT use `async def` test functions

Required tests:
- `test_health` — GET /health returns 200, body has `status`
- `test_create_product` — POST /products/ returns 201, body has `id`
- `test_get_products` — GET /products/ returns 200, body is list or has `items`
- `test_add_to_cart` — POST /cart/add returns 201
- `test_get_cart` — GET /cart/ returns 200
- `test_checkout` — POST /cart/checkout returns 200, body has `id` and `total`
- `test_clear_cart` — DELETE /cart/clear returns 204

### Safe assertion pattern (CRITICAL)
Always assert field existence before accessing:
```python
data = r.json()
assert "id" in data, f"Response missing 'id': {data}"
product_id = data["id"]   # only access AFTER assert
```
Never do blind `r.json()["id"]` without asserting first.

### Setup chain for cart tests
```python
# Create product first
rp = client.post("/products/", json={"name": "Test", "price": 10.0, "stock": 100})
assert rp.status_code in (200, 201)
rp_data = rp.json()
assert "id" in rp_data, f"missing id: {rp_data}"
product_id = rp_data["id"]

# Then add to cart
rc = client.post("/cart/add", json={"product_id": product_id, "quantity": 1})
assert rc.status_code in (200, 201)
```

---

## Frontend test rules (`Cart.test.tsx`)

- Import `render`, `screen` from `@testing-library/react`
- Test 1: render empty cart — check empty state message is visible
- Test 2: render cart with one item — check product name and price appear
- Use `toBeInTheDocument()` matcher
- Do NOT use `async` tests unless absolutely required

---

## Docker rules

### `docker-compose.yml`
- Backend service: expose port `8000`, include healthcheck on `/health`
- Frontend service: expose port `5173`, set env `VITE_API_URL=http://localhost:8000`
- Mount source volumes for both services
- `depends_on` frontend → backend

### `src/backend/Dockerfile`
- Base: `python:3.11-slim`
- Install from `requirements.txt`
- CMD: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

### `src/frontend/Dockerfile`
- Base: `node:20-alpine`
- Install deps, expose 5173
- CMD: `npm run dev -- --host 0.0.0.0`

### `.dockerignore`
Exclude: `__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules`, `.test-venv`, `*.egg-info`