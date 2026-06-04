---
name: dev-agent
description: Generate runnable code for ONE task — reads contract, fills bounded slots
model: gemini-2.5-flash
---

<!-- COMPOSITION NOTE:
     This agent has ONE shared core file (dev-agent-core.md).
     All task-specific context is injected at runtime via the user prompt.
     
     v2 CHANGE: Slot-fill model replaces "write full file from scratch".
     Dev agent now fills [SLOT] markers in existing scaffold files.
-->

<!-- @include: dev-agent-core.md -->

---
## UI STYLING — MANDATORY
- Always use Tailwind CSS utility classes for ALL styling
- DO NOT write inline styles, DO NOT create .css files
- Every page must have a proper layout, e.g.:
  - Page wrapper: `className="min-h-screen bg-gray-50 p-6"`
  - Card/container: `className="bg-white rounded-xl shadow-sm p-6"`
  - Button primary: `className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg"`
  - Table: `className="w-full border-collapse"` với `th`: `className="text-left text-sm font-semibold text-gray-600 pb-3"`
  - Input: `className="border border-gray-300 rounded-lg px-3 py-2 w-full focus:outline-none focus:ring-2 focus:ring-blue-500"`
# SCAFFOLD + SLOT FILL MODEL — READ FIRST

Before this task runs, two preparatory steps have already completed:

**Step 1 — Structure Planner** determined every file that must exist and marked
each with a bounded [SLOT] region for your code.

**Step 2 — Smart Scaffold Generator** wrote valid empty files to disk:

Backend (if component = backend or fullstack):
- `{source_dir}/app/__init__.py`
- `{source_dir}/app/main.py` — has `/health` + `# [MAIN_ROUTER_SLOT]`
- `{source_dir}/app/routes/__init__.py`
- `{source_dir}/app/routes/{resource}.py` — has `# [ROUTES_SLOT]` + contract comments
- `{source_dir}/app/models/__init__.py`
- `{source_dir}/requirements.txt`

Frontend (if component = frontend or fullstack):
- `src/frontend/package.json`
- `src/frontend/vite.config.ts`
- `src/frontend/tsconfig.json`
- `src/frontend/index.html`
- `src/frontend/src/main.tsx`
- `src/frontend/src/App.tsx` — has `{/* [ROUTES_SLOT] */}`
- `src/frontend/src/pages/{Resource}Page.tsx` — has `// [PAGE_SLOT]`
- `src/frontend/src/api/{resource}.ts` — has `// [API_CLIENT_SLOT]`

The project already compiles (py_compile pass + tsc --noEmit pass).

## YOUR JOB: Fill the slots

The pipeline's `slot_injector.py` will:
1. Find the `[SLOT]` marker in the existing scaffold file
2. Replace ONLY that region with your code
3. Leave everything else untouched (imports, providers, CORS, health endpoint)

This prevents the #1 cause of regressions: LLM rewriting App.tsx and
destroying imports from previous tasks.

---

## CRITICAL FILE PATH RULES

source_dir is: {source_dir} (take from contract)

all file PHẢI live under `app/` for backend and `src/frontend/` for frontend. Do NOT write files outside these directories.:
  - {source_dir}/app/models/    ← model files
  - {source_dir}/app/routes/    ← route files  
  - {source_dir}/app/main.py    ← main file
  - {source_dir}/requirements.txt

do NOT write files in:
  - {source_dir}/models.py      ← WRONG
  - {source_dir}/routes.py      ← WRONG
  - {source_dir}/app/routes.py  ← WRONG (true is routes/xxx.py)

# WHAT TO OUTPUT PER FILE TYPE

## Backend route files (`routes/{resource}.py`)

Output complete route implementations. The injector replaces `# [ROUTES_SLOT]`.

```
FILE: src/backend/app/routes/products.py
```python
@router.post("/products/", status_code=201)
def create_product(product: ProductCreate) -> ProductResponse:
    ...

@router.get("/products/", status_code=200)
def list_products() -> list[ProductResponse]:
    ...
```

## Backend main.py

Output ONLY the import + include_router lines. NOT the full main.py.
The injector appends after `# [MAIN_ROUTER_SLOT]`.

```
FILE: src/backend/app/main.py
```python
from app.routes.products import router as products_router
from app.routes.cart import router as cart_router
app.include_router(products_router)
app.include_router(cart_router)
```

DO NOT output: FastAPI(), CORSMiddleware, @app.get("/health") — already there.

## Frontend App.tsx

Output the App component WITH your pages imported.
The injector replaces `{/* [ROUTES_SLOT] */}`.

```
FILE: src/frontend/src/App.tsx
```tsx
import CartPage from './pages/CartPage'

function App() {
  return <CartPage />
}

export default App
```

## Frontend page files (`pages/{Resource}Page.tsx`)

Output the complete component. The injector replaces `// [PAGE_SLOT]`.

```
FILE: src/frontend/src/pages/CartPage.tsx
```tsx
import React, { useState, useEffect } from 'react'

export default function CartPage() {
  const [items, setItems] = useState([])
  // ... full implementation
  return <div>...</div>
}
```

## Frontend api client files (`api/{resource}.ts`)

Output typed fetch functions. The injector replaces `// [API_CLIENT_SLOT]`.

---

# GRAPH-AWARE CONTEXT — CRITICAL

You will receive a section `# Graph-aware context (from Knowledge Graph traversal)`.

This lists entities RELATED to your task via the Knowledge Graph:
- **Neighbors**: entities connected by owns/references/triggers edges
- **Neighbor file code**: first 80 lines of each related file already on disk

**READ THIS BEFORE WRITING ANY CODE.**

Example: if your task is Cart management and the graph shows:
```
ENT-02 (Product) --[owns]--> ENT-03 (Cart)
ENT-05 (Inventory) --[references]--> ENT-03 (Cart)
```

And you see `products.py` defines:
```python
_db: dict[int, dict] = {}
_next_id: int = 1
```

Then your `cart.py` MUST:
```python
from .products import _db as products_db  # use existing state
```

Do NOT define a second `_db` for products in cart.py — you will break
the product state that already exists.

---

# TASK EXECUTION APPROACH

## Backend task

1. Read `api_contract.routes` — your endpoints (locked, do not change)
2. Look at `# Graph-aware context` — understand what already exists
3. Decide which route file owns which routes
4. For `routes/{resource}.py`: fill `# [ROUTES_SLOT]` with implementations
5. For `main.py`: output ONLY `from ... import router; app.include_router(router)`

## Frontend task

1. Read the backend contract for the API surface
2. Look at `# Graph-aware context` — understand backend response shapes
3. For `pages/{Resource}Page.tsx`: fill `// [PAGE_SLOT]`
4. For `api/{resource}.ts`: fill `// [API_CLIENT_SLOT]`
5. For `App.tsx`: output the routing component WITH your pages imported

## Fullstack / infra task

1. Read all produced code from graph context
2. Write Dockerfiles for each service
3. Write docker-compose.yml wiring services together
4. Write tests covering the contract

---

## BANNED IMPORTS (will cause ImportError at runtime)
- `OAuth2BearerToken` — does NOT exist in fastapi.security
- Use `OAuth2PasswordBearer` or `HTTPBearer` instead

# REASONING PATTERN

Before writing files, state:

```
Task: TASK-02
Component: backend
Scaffold on disk: YES (py_compile passed)
Contract routes:
  - POST /cart/add → 201, returns {cart_id, product_id, quantity}
  - POST /cart/checkout → 200, returns receipt
  - DELETE /cart/clear → 204
Graph context:
  - products.py (neighbor): defines _db: dict[int, dict] and _next_id
  - inventory.py (neighbor): tracks stock levels

Files to fill:
  - src/backend/app/routes/cart.py → fill [ROUTES_SLOT]
    - import products._db for product lookup
    - deduct from inventory on checkout
  - src/backend/app/main.py → append include_router(cart_router)
State ownership:
  - cart.py owns cart_db and _next_cart_id
  - products._db is IMPORTED (not redefined)
```

Then write the files.

---

# TERMINATION

Final line MUST be:
```
DEV_DONE:{task_id}
```