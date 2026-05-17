---
name: dev-agent-task02
description: TASK-02 — Frontend UI spec, rules, and design system
included_by: dev-agent-gemini.md
---

# FRONTEND RULES

## Code Rules
- React 18, TypeScript — NO class components, NO axios, NO redux
- Use `fetch` API only
- Functional components + hooks only
- All React components MUST use: `export default ComponentName`
- All TSX files must return valid JSX with a single parent element
- Use `toLocaleString('vi-VN')` for currency formatting
- Throw `Error` when `response.ok` is false
- NEVER write `import React from 'react'` — React 18 JSX transform does not require it
- API_URL MUST be: `const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'`

## Data Fetching Contract
`fetchProducts` MUST handle both shapes backend may return:
```typescript
const data = await handleResponse(response);
return Array.isArray(data) ? data : (data.items ?? []);
```
Never assume the shape — always normalise at the API layer.

## Package Rules
- `package.json` MUST include every package referenced in any import
- Do NOT import packages not declared in `package.json`

---

# DESIGN SYSTEM — MANDATORY

The UI is a **POS (Point-of-Sale) terminal** for a Vietnamese coffee shop.

## Aesthetic Direction: "Cafe Noir" — warm, editorial, premium

Commit fully to this direction. Every file must serve it.

### Color Palette (CSS variables in `index.css`)
```css
:root {
  --bg:         #1a1410;   /* deep espresso */
  --surface:    #252018;   /* card background */
  --surface-2:  #2e2820;   /* elevated surface */
  --border:     #3d3428;   /* subtle border */
  --accent:     #c8922a;   /* warm gold */
  --accent-dim: #a07420;   /* muted gold */
  --text-1:     #f0e6d3;   /* primary text — cream */
  --text-2:     #a89880;   /* secondary text */
  --text-3:     #6b5d4f;   /* muted text */
  --danger:     #c0392b;
  --success:    #27ae60;
  --radius:     6px;
  --radius-lg:  12px;
}
```

### Typography
```css
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500&display=swap');

body {
  font-family: 'DM Sans', sans-serif;
  background: var(--bg);
  color: var(--text-1);
}

h1, h2, h3 { font-family: 'Playfair Display', serif; }
```

### Layout
- Two-column split: **left 60% product grid**, **right 40% cart panel**
- Cart panel is sticky, full viewport height, with its own scroll
- Header: shop name in serif, thin gold underline, subtle tagline

### Component Visual Specs

#### ProductCard
- Dark card (`--surface`), 1px border (`--border`), `--radius-lg`
- Gold accent top-border on hover (`border-top: 2px solid var(--accent)`)
- Price in `--accent` color, bold
- Stock badge: pill shape, muted when low
- "Add to Cart" button: full width, solid `--accent` background, dark text
- Transition: `transform 0.15s ease, box-shadow 0.15s ease`
- On hover: lift + soft glow (`box-shadow: 0 8px 24px rgba(200,146,42,0.15)`)

#### Cart Panel
- Sticky right panel with `--surface` background, left border
- Line items: product name left, quantity × price right
- Quantity controls: `−` and `+` buttons, pill-shaped, `--accent` on hover
- Subtotal row: gold text, Playfair font, slightly larger
- "Checkout" CTA: full width, `--accent` solid, uppercase letter-spacing
- "Clear Cart" link: small, `--text-3`, underline on hover
- Empty state: centered italic text in `--text-3`, coffee cup emoji

#### AddProductForm
- Compact form in a `--surface-2` card, labeled inputs
- Input style: transparent bg, `--border` bottom-border only, focus glows gold
- Submit button: outlined style with `--accent` border, fills on hover
- Form title in Playfair Display

#### Header
```
☕  Noir Café POS
"Every cup, counted."
```
Thin horizontal rule in `--border` below header.

### Animations
```css
/* Card entrance */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
.product-card { animation: fadeUp 0.3s ease both; }
.product-card:nth-child(2) { animation-delay: 0.05s; }
.product-card:nth-child(3) { animation-delay: 0.10s; }
/* up to nth-child(6) */

/* Button press */
button:active { transform: scale(0.97); }
```

### Responsive
- Below 900px: stack to single column, cart goes below products
- Grid: `grid-template-columns: repeat(auto-fill, minmax(200px, 1fr))`

---

# TASK-02 — Frontend UI

**Required files — generate EXACTLY these 14 files:**

```
src/frontend/package.json
src/frontend/vite.config.ts
src/frontend/tsconfig.json
src/frontend/tsconfig.node.json
src/frontend/babel.config.js
src/frontend/index.html
src/frontend/src/vite-env.d.ts
src/frontend/src/index.css
src/frontend/src/main.tsx
src/frontend/src/types/index.ts
src/frontend/src/api/client.ts
src/frontend/src/App.tsx
src/frontend/src/components/ProductCard.tsx
src/frontend/src/components/Cart.tsx
src/frontend/src/components/AddProductForm.tsx
```

### `src/frontend/src/vite-env.d.ts`
```typescript
/// <reference types="vite/client" />
interface ImportMetaEnv { readonly VITE_API_URL: string; }
interface ImportMeta { readonly env: ImportMetaEnv; }
```

### `src/frontend/tsconfig.node.json`
```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

### `src/frontend/src/index.css`
MUST contain:
- Google Fonts import (Playfair Display + DM Sans)
- All CSS variables listed above
- Base reset: `*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }`
- Body styles (background, font, color)
- Scrollbar styles: thin, dark, `--accent` thumb
- All component classes used in TSX components
- Animations: `fadeUp`, button press
- Responsive breakpoint at 900px

### `src/frontend/src/App.tsx`
- Two-column layout (`.app-layout`)
- Left: header + AddProductForm + product grid
- Right: sticky Cart panel
- Manages state: `products`, `cart` (fetched on mount and after mutations)
- Re-fetches cart after every `addToCart`, `clearCart`, `checkout`
- Shows loading state while fetching

### Required API functions in `src/frontend/src/api/client.ts`
All six: `fetchProducts`, `createProduct`, `addToCart`, `getCart`, `clearCart`, `checkout`