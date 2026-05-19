```markdown
# PRD

## Problem
Small retail shops lack an efficient system to manage product sales, track inventory, and process customer transactions, leading to manual errors and slow service.

## MVP Features
- Product listing and search functionality.
- Ability to add, remove, and update quantities of items in a sales cart.
- Secure checkout process for completing transactions.
- Basic recording of sales transactions.

## Non-functional Requirements
- **Performance**: Fast response times for product lookup and transaction processing, aiming for sub-second responses.
- **Maintainability**: Modular codebase with clear separation of concerns, well-documented APIs, and adherence to coding standards.
- **Testing**: Comprehensive automated unit, integration, and end-to-end tests for both backend and frontend components.
- **Deployment**: Containerized application services (Docker) for consistent and easy deployment across environments.
- **Usability**: Intuitive and user-friendly interface for quick and error-free sales operations by staff.

# Project context:
# POS App — Shared Project Context

## Project overview
Point-of-sale system for small retail shops.
Platforms: Web (React) + Mobile (React Native).

## Tech stack
- Frontend : React 18, TypeScript, Tailwind CSS
- Backend  : FastAPI (Python 3.11), PostgreSQL 15
- Mobile   : React Native with Expo SDK 51
- Testing  : Jest (frontend), Pytest (backend)
- CI/CD    : GitHub Actions
- Infra    : Vercel (web), Railway (API), Expo EAS (mobile)

## Repository
- Pipeline repo: https://github.com/gnudevx/pos-app
- POS App repo:  https://github.com/gnudevx/pos-app-test_v2  ← dev-agent làm việc ở đây
- Local POS path: D:\Intern\pos-app-pipeline
- Main branch: main
- Branch convention: feature/TASK-{id}-short-description

## Commit convention
feat(scope): description     ← tính năng mới
fix(scope): description      ← sửa bug
test(scope): description     ← thêm/sửa test
chore(scope): description    ← cấu hình, dependency

## Agent roles
- requirement-agent : đọc prompt → viết PRD + user stories
- planner-agent     : đọc stories → chia tasks + sprint plan
- dev-agent         : đọc ticket → viết code → tạo PR
- tester-agent      : chạy test suite → tạo bug report

## Output file paths
- /docs/requirements.md   ← PRD
- /docs/stories.json      ← user stories
- /docs/tasks.json        ← sprint tasks
- /docs/test-results.md   ← kết quả test
- /docs/bugs/             ← bug reports

## Definition of done (mỗi task)
1. Code implement xong
2. Unit test pass (coverage >= 80%)
3. Lint pass (0 error)
4. PR tạo trên GitHub
5. Jira ticket cập nhật → Done
```
```json