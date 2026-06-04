# pos-app-pipeline/config.py
import os
from dotenv import load_dotenv
load_dotenv()

# ── Đường dẫn tuyệt đối đến POS App repo ──────────────
# Cách 1: Tự động resolve (hoạt động nếu 2 repo cùng cấp)
_PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
POS_APP_DIR = os.path.normpath(os.path.join(_PIPELINE_ROOT, "..", "..", "pos-app-test_v2"))

# Cách 2: Override bằng env variable (dùng cho CI hoặc path khác)
POS_APP_DIR = os.environ.get("POS_APP_DIR", POS_APP_DIR)

BACKEND_DIR  = os.path.join(POS_APP_DIR, "src", "backend")
FRONTEND_DIR = os.path.join(POS_APP_DIR, "src", "frontend")

# ── Canonical frontend paths (single source of truth) ───
# CRITICAL: Choose ONE canonical structure for App.tsx location.
# Must match main.tsx import path and smart_scaffold write path.
# Options:
#   Option 1 (default): src/frontend/src/App.tsx
#   Option 2: src/frontend/src/app/App.tsx (if relocation applied)
# Currently using Option 1 as canonical — all relocation must be aware of this.
FRONTEND_ENTRYPOINT_CANONICAL = os.path.join(FRONTEND_DIR, "src", "App.tsx")
FRONTEND_ENTRYPOINT_RELOCATE_ALT = os.path.join(FRONTEND_DIR, "src", "app", "App.tsx")

def find_frontend_entrypoint(pos_app_dir: str = POS_APP_DIR) -> str:
    """
    Dynamically locate App.tsx after potential relocations.
    Returns canonical path if exists, else tries alternate, else returns canonical.
    """
    canonical = os.path.join(pos_app_dir, "src", "frontend", "src", "App.tsx")
    alternate = os.path.join(pos_app_dir, "src", "frontend", "src", "app", "App.tsx")
    
    if os.path.exists(canonical):
        return canonical
    if os.path.exists(alternate):
        return alternate
    # Default to canonical path (will be created if doesn't exist)
    return canonical

GEMINI_API_KEYS = [
    os.environ.get("GEMINI_KEY_1", ""),
    os.environ.get("GEMINI_KEY_2", ""),
    os.environ.get("GEMINI_KEY_3", ""),
    os.environ.get("GEMINI_KEY_4", "")
]

OPENAI_API_KEYS = [
    os.environ.get("OPENAI_KEY_1", ""),
    os.environ.get("OPENAI_KEY_2", ""),
    os.environ.get("OPENAI_KEY_3", "")
]