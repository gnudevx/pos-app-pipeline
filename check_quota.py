#!/usr/bin/env python3
"""Check Gemini API quota status cho tung key."""
from google import genai
from config import GEMINI_API_KEYS


def check_key(index, api_key):
    print(f"\n🔍 Key {index + 1}: {api_key[:12]}...")
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="models/gemini-2.5-flash",
            contents="Say OK in one word"
        )
        print(f"  ✅ ACTIVE — response: {(response.text or '').strip()[:30]}")
        return True
    except Exception as e:
        err = str(e)
        if "429" in err or "quota" in err.lower():
            print(f"  ❌ QUOTA EXHAUSTED")
        elif "401" in err or "api key" in err.lower():
            print(f"  ❌ INVALID KEY")
        else:
            print(f"  ❌ ERROR: {err[:120]}")
        return False


if __name__ == "__main__":
    keys = [k for k in GEMINI_API_KEYS if k]
    if not keys:
        print("❌ No keys found in GEMINI_API_KEYS")
    else:
        active = sum(check_key(i, k) for i, k in enumerate(keys))
        print(f"\n📊 {active}/{len(keys)} keys active")