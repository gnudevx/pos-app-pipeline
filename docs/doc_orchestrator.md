 In this file will remind how project oprate in via Orchestrator and how it working + some question about this situation 
===============================================
python orchestrator.py
        ↓
orchestrator.py
        ↓ Call run_agent()
adapter.py
        ↓ AGENT_BACKEND = "mock"
mock_agents.py   →   write docs/tasks.json
        ↓
orchestrator.py read docs/tasks.json
        ↓
Jira sync is real
        ↓
Dev loop + Test loop (mock)

# 1: Phase 3 — what did Dev Agent work?
write  code → commit → push branch → create Pull Request
(this is job of dev-agent, yet merge into branch main) 

# 2: Phase 4 — GitHub Actions (ci.yml) is really do it:

PR just create → GitHub Actions self-trigger
→ Run lint + test + build
→ If pass: permit to merge into main
→ If fail: block merge, notify a error
# 3: reason for phase 3 implement when in phase 2 created code AI test and push it into git
Phase 3: dev-agent push code on branch
              ↓
Phase 4: GitHub Actions self-ask:
         "The qualify of code can be enough condition to merge?"
              ↓
    Lint pass?  → V
    Test pass?  → V  
    Build pass? → V
              ↓
         permission merge into main
              ↓
    Deploy staging automation
              ↓
Phase 5: Human approve → deploy production

# 4: when we call Claude real,those files .claude/agents/*.md may be implemented?
AGENT_BACKEND = "claude"
adapter.py call:
  claude --agent dev-agent --print "TASK-01"
        ↓
Claude Code CLI:
  1. Find file .claude/agents/dev-agent.md
  2. READ entire sources the system prompt
  3. READ CLAUDE.md automation (convention of Claude Code)
  4. implement the demand bash, read, write as declear
  5. Return output
→ YES, file .md Implemented — this is instruction set
→ File .md = "contact to work" of agent


# 5 về file adapter: 
adapter.py 
├── Gemini model init + gọi API          → ai_client.py  Gemini singleton + 2 call wrapper (call có system prompt, call_raw không có). Sau này muốn swap sang Claude hoặc OpenAI, chỉ sửa file này.
├── Đọc file, strip fence, parse JSON    → parser.py  
├── Logic từng agent (req/plan/dev/test) → adapter.py (giữ lại)
└── Git operations                       → git_ops.py