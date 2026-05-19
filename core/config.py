# pos-app-pipeline/config.py
import os
from dotenv import load_dotenv
load_dotenv()

# ── Đường dẫn tuyệt đối đến POS App repo ──────────────
# Cách 1: Tự động resolve (hoạt động nếu 2 repo cùng cấp)
_PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
POS_APP_DIR = os.path.normpath(os.path.join(_PIPELINE_ROOT, "..", "pos-app-test_v2"))

# Cách 2: Override bằng env variable (dùng cho CI hoặc path khác)
POS_APP_DIR = os.environ.get("POS_APP_DIR", POS_APP_DIR)

BACKEND_DIR  = os.path.join(POS_APP_DIR, "src", "backend")
FRONTEND_DIR = os.path.join(POS_APP_DIR, "src", "frontend")

GEMINI_API_KEYS = [
    os.environ.get("GEMINI_KEY_1", ""),
    os.environ.get("GEMINI_KEY_2", ""),
    os.environ.get("GEMINI_KEY_3", "")
]