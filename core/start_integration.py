from scripts.integration_pipeline import load_passed_tasks
from ci.merge.merge_coordinator import run_merge_coordinator

tasks = load_passed_tasks()

result = run_merge_coordinator(
    passed_task_ids=tasks,
    repo_dir="../pos-app-test_v2",
)

print(
    f"Created integration branch: {result['branch']}"
)