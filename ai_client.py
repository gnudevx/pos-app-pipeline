"""
AI Client - Google GenAI SDK (google-genai) với key rotation + exponential backoff.
pip install google-genai

Nhận GEMINI_API_KEYS (list) từ config.py.
- Tự động rotate sang key tiếp theo khi gặp 429 (quota)
- Exponential backoff cho 503, 502, 500 (server errors)
- Jitter để tránh thundering herd
"""
import time
import random
from google import genai

AGENT_MODELS = {
    "requirement-agent": "gemini-2.5-flash",
    "planner-agent":     "gemini-2.5-flash",
    "dev-agent":         "gemini-2.5-flash",
    "tester-agent":      "gemini-2.5-flash",
}

_clients = {}
_key_index = 0
_MIN_INTERVAL = 4.0
_last_call_time = 0.0

# Exponential backoff config
_MAX_RETRIES = 5
_BASE_WAIT = 2.0  # Start with 2 seconds
_MAX_WAIT = 60.0  # Cap at 60 seconds
_JITTER_FACTOR = 0.1  # Add ±10% jitter


def _get_client(api_key: str) -> genai.Client:
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _throttle():
    """Enforce minimum interval between API calls."""
    global _last_call_time
    gap = time.time() - _last_call_time
    if gap < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - gap)
    _last_call_time = time.time()


def _classify_error(error_str):
    """
    Classify error type to decide retry strategy.
    
    Returns: (error_type, is_retryable)
      - "quota" (429): rotate key + retry
      - "server" (503, 502, 500): exponential backoff + retry
      - "auth" (401, 403): don't retry
      - "other": don't retry
    """
    err_lower = error_str.lower()
    
    if "429" in error_str or "quota" in err_lower or "rate" in err_lower:
        return "quota", True
    
    if "503" in error_str or "unavailable" in err_lower:
        return "server_503", True
    
    if "502" in error_str or "bad gateway" in err_lower:
        return "server_502", True
    
    if "500" in error_str or "internal server" in err_lower:
        return "server_500", True
    
    if "401" in error_str or "403" in error_str or "unauthorized" in err_lower or "forbidden" in err_lower:
        return "auth", False
    
    if "api key" in err_lower or "invalid" in err_lower:
        return "auth", False
    
    return "other", False


def _exponential_backoff(attempt, error_type):
    """
    Calculate wait time with exponential backoff + jitter.
    
    Formula: wait = min(base * 2^attempt + jitter, max_wait)
    """
    if error_type == "quota":
        # Quota errors: aggressive backoff
        wait = min(_BASE_WAIT * (2 ** attempt), _MAX_WAIT)
    elif error_type.startswith("server"):
        # Server errors: moderate backoff
        wait = min(_BASE_WAIT * (1.5 ** attempt), _MAX_WAIT)
    else:
        # Other errors: short backoff
        wait = min(_BASE_WAIT * (1.2 ** attempt), _MAX_WAIT)
    
    # Add jitter: ±10%
    jitter = wait * _JITTER_FACTOR * (2 * random.random() - 1)
    final_wait = max(0.1, wait + jitter)
    
    return final_wait


def call(api_keys, system_prompt, user_prompt, agent_name="default"):
    """
    Gọi Gemini với key rotation + exponential backoff.
    
    Retry strategy:
    - 429 (quota): rotate key + retry with backoff
    - 5xx (server): retry with exponential backoff
    - 4xx (auth): don't retry
    - Other: raise immediately
    
    api_keys: str hoặc list[str].
    """
    global _key_index

    if isinstance(api_keys, str):
        api_keys = [api_keys]
    api_keys = [k for k in api_keys if k]
    if not api_keys:
        raise RuntimeError("No valid GEMINI_API_KEYS configured")

    model_name = AGENT_MODELS.get(agent_name, "gemini-2.5-flash")
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}" if system_prompt else user_prompt

    last_error = None
    total_attempts = len(api_keys) * _MAX_RETRIES
    
    for attempt in range(total_attempts):
        key = api_keys[_key_index % len(api_keys)]
        client = _get_client(key)
        _throttle()

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config={
                    "temperature": 0.2,
                    "max_output_tokens": 8192,
                },
            )
            return (response.text or "").strip()

        except Exception as e:
            err = str(e)
            error_type, is_retryable = _classify_error(err)
            last_error = (error_type, err)
            
            if not is_retryable:
                # Auth errors, invalid keys, etc. → don't retry
                raise
            
            if error_type == "quota":
                # Quota: rotate to next key + backoff
                _key_index += 1
                next_num = _key_index % len(api_keys)
                wait_time = _exponential_backoff(attempt // len(api_keys), "quota")
                print(f"      [{error_type}] key {(attempt % len(api_keys)) + 1} exhausted → "
                      f"rotating to key {next_num + 1}/{len(api_keys)} (wait {wait_time:.1f}s)")
                time.sleep(wait_time)
            
            elif error_type.startswith("server"):
                # Server errors: retry same key with exponential backoff
                wait_time = _exponential_backoff(attempt, error_type)
                retry_num = attempt + 1
                print(f"      [{error_type}] attempt {retry_num}/{total_attempts} (wait {wait_time:.1f}s)")
                time.sleep(wait_time)
            
            else:
                # Should not reach here, but just in case
                raise

    # If we got here, all retries exhausted
    if last_error:
        error_type, err_msg = last_error
        raise RuntimeError(
            f"All {total_attempts} retries exhausted for {agent_name}. "
            f"Last error ({error_type}): {err_msg[:200]}"
        )
    
    raise RuntimeError(f"All {len(api_keys)} API keys exhausted")


def call_raw(api_keys, prompt, agent_name="default"):
    return call(api_keys, "", prompt, agent_name)


def reset():
    global _clients, _key_index, _last_call_time
    _clients = {}
    _key_index = 0
    _last_call_time = 0.0