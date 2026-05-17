---
name: dev-agent
description: Generate runnable code for ONE task only
model: gemini-2.5-flash
---

<!-- ═══════════════════════════════════════════════════════════════
     COMPOSITION NOTE (for maintainers):
     This file is the entry point. It includes 4 sub-files:
       - dev-agent-core.md     → output format + global rules + test rules
       - dev-agent-task01.md   → TASK-01: Backend API
       - dev-agent-task02.md   → TASK-02: Frontend UI + Design System
       - dev-agent-task03.md   → TASK-03: Docker + Tests
     
     To update a rule, edit the relevant sub-file only.
     load_agent_instruction() in parser.py must concatenate all 4 files.
════════════════════════════════════════════════════════════════ -->

<!-- @include: dev-agent-core.md -->
<!-- @include: dev-agent-task01.md -->
<!-- @include: dev-agent-task02.md -->
<!-- @include: dev-agent-task03.md -->