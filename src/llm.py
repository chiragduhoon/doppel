"""LLM provider layer.

One function — generate(system, history) — with the provider picked by env:
  LLM_PROVIDER=gemini     (default, free tier via https://aistudio.google.com/apikey)
  LLM_PROVIDER=anthropic  (paid, needs ANTHROPIC_API_KEY and `pip install anthropic`)

history is a list of {"role": "user" | "assistant", "content": str}.
"""

import os
import time

DEFAULT_MODELS = {
    "gemini": "gemini-flash-lite-latest",
    "anthropic": "claude-opus-4-8",
}

_gemini_client = None
_anthropic_client = None


def generate(system: str, history: list[dict]) -> str:
    provider = (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    if provider not in DEFAULT_MODELS:
        raise RuntimeError(f"Unknown LLM_PROVIDER '{provider}' (use gemini or anthropic)")
    model = (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODELS[provider]

    # Both APIs want the conversation to start with a user message.
    while history and history[0]["role"] != "user":
        history = history[1:]
    if not history:
        return ""

    last_err = None
    for attempt in range(3):
        try:
            if provider == "anthropic":
                return _generate_anthropic(system, history, model)
            return _generate_gemini(system, history, model)
        except Exception as e:  # noqa: BLE001 - provider SDKs raise different types
            last_err = e
            if attempt < 2 and _is_retryable(e):
                time.sleep(8 * (attempt + 1))
                continue
            raise
    raise last_err


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(t in msg for t in ("429", "rate", "resource_exhausted", "overloaded", "529", "503", "timeout"))


def _generate_gemini(system: str, history: list[dict], model: str) -> str:
    global _gemini_client
    from google import genai
    from google.genai import types

    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey and put it in .env")
        _gemini_client = genai.Client(api_key=api_key)

    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part(text=m["content"])],
        )
        for m in history
    ]
    resp = _gemini_client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=1.0,
            max_output_tokens=1024,  # headroom for the model's internal "thinking" tokens + the actual reply
        ),
    )
    return (resp.text or "").strip()


def _generate_anthropic(system: str, history: list[dict], model: str) -> str:
    global _anthropic_client
    import anthropic

    if _anthropic_client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set (needed because LLM_PROVIDER=anthropic)")
        _anthropic_client = anthropic.Anthropic()

    resp = _anthropic_client.messages.create(
        model=model,
        max_tokens=500,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=history,
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
