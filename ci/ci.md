# 1. merge:
Topological merge 

depends_on_tasks
→ topo_sort
→ merge order
Đây là cách đúng để tránh:

TASK-09 cần API TASK-03
nhưng TASK-09 merge trước

merge --abort
raise RuntimeError

Thay vì:
auto resolve
recursive heal
merge heuristic

Vì merge conflict là semantic conflict.

AI resolve merge conflict tự động rất dễ:

mất code
overwrite logic
tạo silent corruption
--- 
quy trình: 
feature/*
    ↓
integration/run-xxx
    ↓
contract/build/runtime/healing
    ↓
PASS → merge vào develop
FAIL → delete integration branch

# 5. Manifest snapshot

Cực kỳ quan trọng.

integration_manifest.json

sau này:

audit
rollback
blame tracking
AI memory
release provenance

đều cần.
===================================
##  hệ thống 
pos-app-pipeline Pipeline repo chỉ là orchestrator.
    ├── core/
    ├── ci/
    └── scripts/

pos-app-test_v2
    ├── backend/
    ├── frontend/
    └── .git