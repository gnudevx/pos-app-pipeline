"""
Verify Jira kết nối thật trước khi tích hợp vào pipeline.
Chạy: python tests/test_jira_connection.py
"""
import urllib.request
import urllib.error
import base64
import json

with open(".mcp.json") as f:
    cfg = json.load(f)["mcpServers"]["jira"]["env"]

JIRA_URL   = cfg["ATLASSIAN_URL"]
JIRA_EMAIL = cfg["ATLASSIAN_EMAIL"]
JIRA_TOKEN = cfg["ATLASSIAN_TOKEN"]

def headers():
    token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

def test_1_auth():
    print("\n[1] Kiểm tra authentication...")
    req = urllib.request.Request(
        f"{JIRA_URL}/rest/api/3/myself", headers=headers()
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    print(f"    ✅ Logged in: {data['displayName']} ({data['emailAddress']})")

def test_2_project():
    print("\n[2] Kiểm tra project POS...")
    req = urllib.request.Request(
        f"{JIRA_URL}/rest/api/3/project", headers=headers()
    )
    with urllib.request.urlopen(req) as r:
        projects = json.loads(r.read())
    if not projects:
        raise Exception("Không tìm thấy project nào")
    for p in projects:
        print(f"    ✅ Project: {p['key']} — {p['name']}")

    # Tìm đúng project POS-APP thay vì lấy projects[0]
    pos_project = next((p for p in projects if p["key"] == "PA"), None)
    if not pos_project:
        raise Exception("Không tìm thấy project PA (POS-APP) — kiểm tra lại Jira")
    print(f"    → Sử dụng project: {pos_project['key']} — {pos_project['name']}")
    return pos_project["key"]

def test_3_create_ticket(project_key):
    print(f"\n[3] Tạo ticket thử trong {project_key}...")
    payload = json.dumps({
        "fields": {
            "project": {"key": project_key},
            "summary": "[PIPELINE-TEST] Auto verify — safe to delete",
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": "Created by POS pipeline test. Safe to delete."}
                ]}]
            },
            "issuetype": {"name": "Task"}
        }
    }).encode()
    req = urllib.request.Request(
        f"{JIRA_URL}/rest/api/3/issue",
        data=payload, headers=headers(), method="POST"
    )
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
    print(f"    ✅ Ticket created: {result['key']}")
    print(f"    🔗 {JIRA_URL}/browse/{result['key']}")
    return result["key"]

if __name__ == "__main__":
    print("=" * 50)
    print("Jira Connection Test")
    print("=" * 50)
    try:
        test_1_auth()
        key = test_2_project()
        test_3_create_ticket(key)
        print("\n✅ TẤT CẢ PASS — Jira sẵn sàng cho pipeline")
        print("   Vào Jira xóa ticket test sau khi verify xong")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"\n❌ HTTP {e.code}: {e.reason}")
        print(f"   Detail: {body[:300]}")
        print("\n   Kiểm tra lại:")
        print("   - ATLASSIAN_URL đúng chưa? (có https://, không có / cuối)")
        print("   - TOKEN có bị thừa space không?")
        print("   - Project đã tạo trên Jira chưa?")
    except Exception as e:
        print(f"\n❌ FAILED: {e}")