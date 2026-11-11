import httpx
import json

from . import settings, usage

TIMEOUT_SECONDS = 180


class LLMError(RuntimeError):
    pass


RANKS = ("imp", "fiend", "baron", "viscount", "marquis", "duke", "lord")


def resolve_brain(requested):
    """base, key and model id for a brain.

    A rank name resolves through the rank.<name> setting, an unset rank falls
    to the default provider settings. "provider|model-id" resolves
    provider.<name>.base_url and provider.<name>.api_key from settings. A plain
    model id rides the default provider.
    """
    name = str(requested or "").strip()
    if name in RANKS:
        name = settings.get(f"rank.{name}")
    if not name:
        return (settings.get("llm_base_url").rstrip("/"), settings.get("llm_api_key"), settings.get("llm_model"))
    if "|" in name:
        provider, _, model_id = name.partition("|")
        base = settings.get(f"provider.{provider}.base_url")
        key = settings.get(f"provider.{provider}.api_key")
        if not base or not key:
            raise LLMError(f"unknown provider: {provider}")
        return (base.rstrip("/"), key, model_id)
    return (settings.get("llm_base_url").rstrip("/"), settings.get("llm_api_key"), name)


def chat(messages, tools=None, temperature=0.6, model=None, brain=None):
    if brain:
        base, key, default_model = brain
    else:
        base = settings.get("llm_base_url").rstrip("/")
        key = settings.get("llm_api_key")
        default_model = settings.get("llm_model")
    chosen = model or default_model
    payload = {"model": chosen, "messages": messages, "temperature": temperature, "reasoning_effort": "low"}
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = httpx.post(
            f"{base}/chat/completions", json=payload, headers=headers, timeout=TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        usage.record_chat(base, chosen, None, ok=False)
        raise LLMError(f"chat request failed: {exc}") from exc
    if response.status_code >= 400:
        usage.record_chat(base, chosen, None, ok=False)
        raise LLMError(f"chat failed with status {response.status_code}: {response.text[:300]}")
    try:
        answer = response.json()
        usage.record_chat(base, chosen, answer)
        return answer["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected chat response: {response.text[:500]}") from exc


def completions_raw(body):
    payload = dict(body)
    if not str(payload.get("model") or "").strip():
        payload["model"] = settings.get("llm_model")
    base = settings.get("llm_base_url").rstrip("/")
    key = settings.get("llm_api_key")
    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = httpx.post(
            f"{base}/chat/completions", json=payload, headers=headers, timeout=TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        usage.record_chat(base, payload["model"], None, ok=False)
        raise LLMError(f"chat request failed: {exc}") from exc
    if response.status_code != 200:
        usage.record_chat(base, payload["model"], None, ok=False)
        raise LLMError(f"chat failed with status {response.status_code}: {response.text[:300]}")
    try:
        answer = response.json()
        usage.record_chat(base, payload["model"], answer)
        return answer
    except ValueError as exc:
        raise LLMError(f"unexpected chat response: {response.text[:300]}") from exc


def stream_completions_raw(body):
    base = settings.get("llm_base_url").rstrip("/")
    key = settings.get("llm_api_key")
    headers = {"Authorization": f"Bearer {key}"}

    def generate():
        seen = {}
        try:
            with httpx.stream(
                "POST", f"{base}/chat/completions", json=body, headers=headers, timeout=TIMEOUT_SECONDS
            ) as response:
                if response.status_code != 200:
                    detail = response.read().decode(encoding="utf-8", errors="replace")[:300]
                    seen["ok"] = False
                    yield sse_error(f"chat failed with status {response.status_code}: {detail}")
                    return
                for line in response.iter_lines(decode_unicode=True):
                    if not line.startswith("data: "):
                        continue
                    yield line + "\n\n"
                    if line.strip() == "data: [DONE]":
                        return
                    if '"usage"' in line:
                        try:
                            seen["usage"] = json.loads(line[6:]).get("usage") or seen.get("usage")
                        except ValueError:
                            pass
        except httpx.HTTPError as exc:
            seen["ok"] = False
            yield sse_error(f"chat request failed: {exc}")
        finally:
            usage.record_chat(base, body.get("model") or settings.get("llm_model"), {"usage": seen.get("usage")},
                              ok=seen.get("ok", True))

    return generate()


def sse_error(message):
    return f"data: {json.dumps({'error': {'message': message}})}\n\ndata: [DONE]\n\n"
