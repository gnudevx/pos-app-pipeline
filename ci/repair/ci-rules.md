# CI Fix Rules

## Mục đích
File này dùng cho CI Fix Agent (Gemini) đọc khi CI fail.
Agent đọc file này + contract của task + error log → sinh patch fix code.

---

## Scope rules — PHẢI tuân thủ

1. **Chỉ sửa file trong artifacts của task đang fail** — không được sửa file của task khác.
2. **Không được thay đổi contract** — nếu fix yêu cầu đổi method/path/status_code → dừng, báo lỗi, không tự sửa.
3. **Không được thay đổi API signature** đã định nghĩa trong contract.
4. **Không được thêm package mới** vào `package.json` hoặc `requirements.txt` nếu không có trong task description.

---

## Priority fix theo loại lỗi

### Lỗi build backend (pip install / import)
- Kiểm tra `requirements.txt` thiếu package chưa
- Kiểm tra import path sai (relative vs absolute)
- Kiểm tra Pydantic v2 syntax (`model_config` thay vì `class Config`)

### Lỗi build frontend (npm run build / tsc)
- Kiểm tra TypeScript type errors trước
- Kiểm tra import thiếu hoặc sai path
- KHÔNG dùng `import React from 'react'` — React 18 không cần
- API_URL PHẢI là: `const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'`
- Tất cả component PHẢI `export default ComponentName`

### Lỗi lint (non-blocking — chỉ warn, không fail CI)
- Bỏ qua nếu chỉ là style issue
- Fix nếu là unused import hoặc undefined variable

### Lỗi health check (GET /health không trả 200)
- Kiểm tra CORS config (`allow_origins=["*"]`)
- Kiểm tra uvicorn port (default 8000)
- Kiểm tra docker-compose port mapping

---

## Hard stop — KHÔNG tự fix, phải escalate

- Fix yêu cầu thay đổi contract (method, path, status_code, response_fields)
- Fix yêu cầu sửa file của task khác
- Retry count > 2
- Lỗi liên quan đến database schema (nếu có)
- Lỗi authentication/security logic

---

## Output format của fix agent

Agent phải trả về JSON theo format sau:

```json
{
  "task_id": "TASK-01",
  "files_changed": [
    {
      "path": "src/backend/app/main.py",
      "action": "modify",
      "reason": "Missing CORS middleware",
      "patch": "--- a/src/backend/app/main.py\n+++ b/src/backend/app/main.py\n..."
    }
  ],
  "escalate": false,
  "escalate_reason": ""
}
```

Nếu cần escalate:
```json
{
  "task_id": "TASK-01",
  "files_changed": [],
  "escalate": true,
  "escalate_reason": "Fix requires changing contract route POST /products status_code"
}
```

---

## Context agent cần đọc (theo thứ tự)

1. `docs/contracts/TASK-XX.contract.json` — source of truth về API
2. `.claude/agents/dev-agent-taskXX.md` — rules cụ thể của task (stack, design system)
3. CI error log — lỗi cụ thể cần fix
4. File source liên quan từ artifacts list trong tasks.json
