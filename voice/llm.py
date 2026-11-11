import httpx


class LLMError(RuntimeError):
    pass


def chat(cfg, messages, tools=None, temperature=0.6, model=None):
    base = (cfg.get("llm_base_url") or "").rstrip("/")
    key = cfg.get("llm_api_key") or ""
    chosen = model or cfg.get("llm_model") or ""
    if not base or not key or not chosen:
        raise LLMError("no llm provider configured")
    body = {"model": chosen, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    try:
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=60,
        )
    except httpx.HTTPError as error:
        raise LLMError(str(error)) from error
    if response.status_code != 200:
        raise LLMError(f"{response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]
