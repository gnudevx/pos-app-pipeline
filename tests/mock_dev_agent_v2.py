"""
Mock Dev Agent NANG CAP — tao code scaffold that + Git ops that.
"""
import json
import os
import subprocess
import datetime

DOCS_DIR = "docs"
from config import POS_APP_DIR

ALWAYS_OVERWRITE = {
    "src/backend/requirements.txt",
    "src/backend/app/main.py",
    "src/backend/conftest.py",
    "src/backend/tests/test_products.py",
    "src/frontend/package.json",
    "src/frontend/babel.config.js",
    "src/frontend/tsconfig.json",
    "src/frontend/src/vite-env.d.ts",
    "src/frontend/src/tests/Cart.test.tsx",
    "src/frontend/.eslintrc.cjs",
}


def _git(cmd, cwd=None):
    if cwd is None:
        cwd = POS_APP_DIR
    result = subprocess.run(
        f"git {cmd}", shell=True, capture_output=True, text=True, cwd=cwd
    )
    return result.returncode == 0, result.stdout.strip()


def _write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"      [mock-dev] Created: {path}")


TEMPLATES = {
    "backend": {
        "src/backend/app/__init__.py": "",
        "src/backend/app/main.py": '''\
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import products, cart

app = FastAPI(title="POS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(cart.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "pos-api"}
''',
        "src/backend/app/models/__init__.py": "",
        "src/backend/app/models/product.py": '''\
from pydantic import BaseModel
from typing import Optional


class ProductBase(BaseModel):
    name: str
    price: float
    stock: int = 0
    barcode: Optional[str] = None


class ProductCreate(ProductBase):
    pass


class Product(ProductBase):
    id: int

    class Config:
        from_attributes = True
''',
        "src/backend/app/routes/__init__.py": "",
        "src/backend/app/routes/products.py": '''\
from fastapi import APIRouter, HTTPException
from app.models.product import Product, ProductCreate
from typing import List

router = APIRouter(prefix="/products", tags=["products"])

_db: dict[int, dict] = {}
_next_id = 1


@router.get("/", response_model=List[Product])
def list_products():
    return [Product(id=k, **v) for k, v in _db.items()]


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int):
    if product_id not in _db:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(id=product_id, **_db[product_id])


@router.post("/", response_model=Product)
def create_product(product: ProductCreate):
    global _next_id
    _db[_next_id] = product.model_dump()
    created = Product(id=_next_id, **_db[_next_id])
    _next_id += 1
    return created


@router.delete("/{product_id}")
def delete_product(product_id: int):
    if product_id not in _db:
        raise HTTPException(status_code=404, detail="Product not found")
    del _db[product_id]
    return {"deleted": product_id}
''',
        "src/backend/app/routes/cart.py": '''\
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/cart", tags=["cart"])

_cart: List[dict] = []


class CartItem(BaseModel):
    product_id: int
    name: str
    price: float
    quantity: int = 1


@router.get("/")
def get_cart():
    total = sum(i["price"] * i["quantity"] for i in _cart)
    return {"items": _cart, "total": round(total, 2)}


@router.post("/add")
def add_to_cart(item: CartItem):
    for existing in _cart:
        if existing["product_id"] == item.product_id:
            existing["quantity"] += item.quantity
            return {"cart": _cart}
    _cart.append(item.model_dump())
    return {"cart": _cart}


@router.delete("/clear")
def clear_cart():
    _cart.clear()
    return {"message": "Cart cleared"}


@router.post("/checkout")
def checkout():
    if not _cart:
        raise HTTPException(status_code=400, detail="Cart is empty")
    total = sum(i["price"] * i["quantity"] for i in _cart)
    receipt = {
        "items": list(_cart),
        "total": round(total, 2),
        "timestamp": str(__import__("datetime").datetime.now())
    }
    _cart.clear()
    return {"receipt": receipt, "status": "paid"}
''',
        "src/backend/requirements.txt": (
            "fastapi>=0.115.0\n"
            "uvicorn>=0.30.0\n"
            "pydantic>=2.8.0\n"
            "pytest>=8.0.0\n"
            "httpx>=0.27.0\n"
            "anyio>=4.6.0\n"
        ),
        "src/backend/Dockerfile": '''\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
        "src/backend/conftest.py": '''\
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
''',
        "src/backend/tests/__init__.py": "",
        "src/backend/tests/test_products.py": '''\
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_get_product():
    r = client.post("/products/", json={
        "name": "Coca Cola", "price": 15000, "stock": 100
    })
    assert r.status_code == 200
    pid = r.json()["id"]
    r2 = client.get(f"/products/{pid}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Coca Cola"


def test_cart_flow():
    r = client.post("/products/", json={
        "name": "Pepsi", "price": 12000, "stock": 50
    })
    pid = r.json()["id"]
    r2 = client.post("/cart/add", json={
        "product_id": pid, "name": "Pepsi",
        "price": 12000, "quantity": 2
    })
    assert r2.status_code == 200
    r3 = client.post("/cart/checkout")
    assert r3.status_code == 200
    assert r3.json()["receipt"]["total"] == 24000
''',
    },

    "frontend": {
        # ✅ Fix 1: Thêm @types/react-dom, @types/jest, eslint packages
        "src/frontend/package.json": json.dumps({
            "name": "pos-frontend",
            "version": "1.0.0",
            "scripts": {
                "dev": "vite",
                "build": "tsc && vite build",
                "test": "jest --passWithNoTests",
                "lint": "eslint src --ext .ts,.tsx --max-warnings 0"
            },
            "jest": {
                "testEnvironment": "jsdom",
                "transform": {"^.+\\.(ts|tsx|js|jsx)$": "babel-jest"},
                "moduleFileExtensions": ["ts", "tsx", "js", "jsx"]
            },
            "dependencies": {
                "react": "^18.3.0",
                "react-dom": "^18.3.0"
            },
            "devDependencies": {
                "@types/react": "^18.3.0",
                "@types/react-dom": "^18.3.0",
                "@types/jest": "^29.0.0",
                "@vitejs/plugin-react": "^4.3.0",
                "typescript": "^5.5.0",
                "vite": "^5.4.0",
                "jest": "^29.0.0",
                "jest-environment-jsdom": "^29.0.0",
                "@testing-library/react": "^16.0.0",
                "@testing-library/jest-dom": "^6.0.0",
                "babel-jest": "^29.0.0",
                "@babel/core": "^7.0.0",
                "@babel/preset-env": "^7.0.0",
                "@babel/preset-react": "^7.0.0",
                "@babel/preset-typescript": "^7.0.0",
                "eslint": "^8.57.0",
                "@typescript-eslint/eslint-plugin": "^7.0.0",
                "@typescript-eslint/parser": "^7.0.0"
            }
        }, indent=2),

        "src/frontend/babel.config.js": '''\
module.exports = {
  presets: [
    ["@babel/preset-env", { targets: { node: "current" } }],
    ["@babel/preset-react", { runtime: "automatic" }],
    "@babel/preset-typescript",
  ],
};
''',
        # ✅ Fix 2: ESLint config — thiếu file này là lý do ESLint crash
        "src/frontend/.eslintrc.cjs": '''\
module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
  ],
  ignorePatterns: ["dist", ".eslintrc.cjs"],
  parser: "@typescript-eslint/parser",
  plugins: ["@typescript-eslint"],
  rules: {
    "@typescript-eslint/no-explicit-any": "off",
  },
};
''',
        # ✅ Fix 3: tsconfig thêm types jest
        "src/frontend/tsconfig.json": '''\
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
    "types": ["jest", "node"]
  },
  "include": ["src"]
}
''',
        # ✅ Fix 4: vite-env.d.ts — fix import.meta.env TS2339
        "src/frontend/src/vite-env.d.ts": '''\
/// <reference types="vite/client" />
''',
        "src/frontend/index.html": '''\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>POS App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
''',
        "src/frontend/vite.config.ts": '''\
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
''',
        "src/frontend/Dockerfile": '''\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
''',
        "src/frontend/nginx.conf": '''\
server {
    listen 80;
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }
    location /api {
        proxy_pass http://backend:8000;
    }
}
''',
        "src/frontend/src/main.tsx": '''\
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
''',
        "src/frontend/src/types/index.ts": '''\
export interface Product {
  id: number;
  name: string;
  price: number;
  stock: number;
  barcode?: string;
}

export interface CartItem {
  product_id: number;
  name: string;
  price: number;
  quantity: number;
}

export interface Cart {
  items: CartItem[];
  total: number;
}
''',
        # ✅ Fix 5: import.meta.env — dùng vite-env.d.ts thay vì cast
        "src/frontend/src/api/client.ts": '''\
const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function fetchProducts() {
  const res = await fetch(`${BASE_URL}/products/`);
  if (!res.ok) throw new Error("Failed to fetch products");
  return res.json();
}

export async function addToCart(item: {
  product_id: number;
  name: string;
  price: number;
  quantity: number;
}) {
  const res = await fetch(`${BASE_URL}/cart/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
  if (!res.ok) throw new Error("Failed to add to cart");
  return res.json();
}

export async function checkout() {
  const res = await fetch(`${BASE_URL}/cart/checkout`, { method: "POST" });
  if (!res.ok) throw new Error("Checkout failed");
  return res.json();
}

export async function getCart() {
  const res = await fetch(`${BASE_URL}/cart/`);
  if (!res.ok) throw new Error("Failed to get cart");
  return res.json();
}
''',
        "src/frontend/src/components/ProductCard.tsx": '''\
import { Product } from "../types";

interface Props {
  product: Product;
  onAddToCart: (product: Product) => void;
}

export function ProductCard({ product, onAddToCart }: Props) {
  return (
    <div className="product-card">
      <h3>{product.name}</h3>
      <p className="price">{product.price.toLocaleString()} VND</p>
      <p className="stock">Stock: {product.stock}</p>
      <button onClick={() => onAddToCart(product)} disabled={product.stock === 0}>
        {product.stock > 0 ? "Add to Cart" : "Out of Stock"}
      </button>
    </div>
  );
}
''',
        "src/frontend/src/components/Cart.tsx": '''\
import { CartItem } from "../types";

interface Props {
  items: CartItem[];
  total: number;
  onCheckout: () => void;
  onClear: () => void;
}

export function Cart({ items, total, onCheckout, onClear }: Props) {
  if (items.length === 0) {
    return <div className="cart empty"><p>Cart is empty</p></div>;
  }
  return (
    <div className="cart">
      <h2>Cart</h2>
      {items.map((item) => (
        <div key={item.product_id} className="cart-item">
          <span>{item.name}</span>
          <span>x{item.quantity}</span>
          <span data-testid="item-total">
            {(item.price * item.quantity).toLocaleString()} VND
          </span>
        </div>
      ))}
      <div className="cart-total">
        <strong data-testid="cart-total">
          Total: {total.toLocaleString()} VND
        </strong>
      </div>
      <button onClick={onCheckout}>Checkout</button>
      <button onClick={onClear}>Clear</button>
    </div>
  );
}
''',
        "src/frontend/src/App.tsx": '''\
import { useState, useEffect } from "react";
import { Product, CartItem } from "./types";
import { ProductCard } from "./components/ProductCard";
import { Cart } from "./components/Cart";
import { fetchProducts, addToCart, getCart, checkout } from "./api/client";

export default function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [cartItems, setCartItems] = useState<CartItem[]>([]);
  const [total, setTotal] = useState(0);
  const [receipt, setReceipt] = useState<object | null>(null);

  useEffect(() => {
    fetchProducts().then(setProducts).catch(console.error);
    refreshCart();
  }, []);

  function refreshCart() {
    getCart().then((data) => {
      setCartItems(data.items as CartItem[]);
      setTotal(data.total as number);
    }).catch(console.error);
  }

  async function handleAddToCart(product: Product) {
    await addToCart({
      product_id: product.id,
      name: product.name,
      price: product.price,
      quantity: 1,
    });
    refreshCart();
  }

  async function handleCheckout() {
    const result = await checkout();
    setReceipt(result.receipt as object);
    setCartItems([]);
    setTotal(0);
  }

  async function handleClear() {
    await fetch("http://localhost:8000/cart/clear", { method: "DELETE" });
    refreshCart();
  }

  return (
    <div className="app">
      <header><h1>POS System</h1></header>
      <main>
        <section className="products">
          <h2>Products</h2>
          <div className="product-grid">
            {products.map((p) => (
              <ProductCard key={p.id} product={p} onAddToCart={handleAddToCart} />
            ))}
          </div>
        </section>
        <aside>
          <Cart
            items={cartItems}
            total={total}
            onCheckout={handleCheckout}
            onClear={handleClear}
          />
          {receipt && (
            <div className="receipt">
              <h3>Receipt</h3>
              <pre>{JSON.stringify(receipt, null, 2)}</pre>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}
''',
        "src/frontend/src/tests/Cart.test.tsx": '''\
import { render, screen } from "@testing-library/react";
import { Cart } from "../components/Cart";

const mockItems = [
  { product_id: 1, name: "Coca Cola", price: 15000, quantity: 2 },
];

test("renders empty cart message", () => {
  render(<Cart items={[]} total={0} onCheckout={() => {}} onClear={() => {}} />);
  expect(screen.getByText("Cart is empty")).toBeTruthy();
});

test("renders cart items and total", () => {
  render(
    <Cart items={mockItems} total={30000} onCheckout={() => {}} onClear={() => {}} />
  );
  expect(screen.getByText("Coca Cola")).toBeTruthy();
  expect(screen.getByTestId("cart-total").textContent).toContain("30,000");
});
''',
    },

    "fullstack": {},
}


def mock_dev_agent_v2(task_id):
    with open(f"{DOCS_DIR}/tasks.json", encoding="utf-8") as f:
        data = json.load(f)

    task = next(
        (t for s in data["sprints"] for t in s["tasks"] if t["id"] == task_id),
        None
    )
    if not task:
        print(f"      [mock-dev] Task {task_id} not found")
        return f"DEV_ESCALATE:{task_id}"

    component = task.get("component", "fullstack")
    slug = task["summary"][:25].lower().replace(" ", "-").replace("(", "").replace(")", "")
    branch = f"feature/{task_id}-{slug}"

    print(f"      [mock-dev] Task: {task_id} | Component: {component}")

    templates = TEMPLATES.get(component, {})
    if component == "fullstack":
        templates = {**TEMPLATES["backend"], **TEMPLATES["frontend"]}

    for filepath, content in templates.items():
        full_path = os.path.join(POS_APP_DIR, filepath)
        force_write = filepath in ALWAYS_OVERWRITE
        if not os.path.exists(full_path) or force_write:
            _write_file(full_path, content)

    print(f"      [mock-dev] Git: creating branch {branch}")
    ok, _ = _git(f"checkout -b {branch}")
    if not ok:
        _git(f"checkout {branch}")

    _git("add .")
    ts = datetime.datetime.now().strftime("%Y-%m-%d")
    ok, _ = _git(
        f'commit -m "feat({component}): {task["summary"][:50]} [{task_id}] - {ts}"'
    )
    if not ok:
        print("      [mock-dev] Nothing to commit (files already exist)")

    ok, out = _git(f"push origin {branch} --set-upstream")
    if ok:
        print(f"      [mock-dev] Pushed branch: {branch}")
    else:
        print(f"      [mock-dev] Push skipped (no remote or already pushed)")

    _git("checkout main")

    for s in data["sprints"]:
        for t in s["tasks"]:
            if t["id"] == task_id:
                t["status"] = "DONE"
                t["branch"] = branch
                t["pr"] = f"PR: feature/{task_id} -> main (simulated)"

    with open(f"{DOCS_DIR}/tasks.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"      [mock-dev] DONE: {task_id} -> {branch}")
    return f"DEV_DONE:{task_id}"