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
- Local POS path: D:\Intern\pos-app-test
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