"""最小可用的 OpenAI 兼容 Chat Completions 客户端（也适配 DeepSeek / 通义 / Ollama）。"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


def _endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        raise LLMError("未配置 llm.base_url")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    temperature: float = 0.3,
    timeout: float = 120,
    json_mode: bool = True,
    max_tokens: int | None = None,
) -> str:
    if not model:
        raise LLMError("未配置 llm.model")
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    for attempt in (1, 2):
        body = dict(payload)
        if attempt == 2:
            body.pop("response_format", None)
        raw = _post(_endpoint(base_url), api_key, body, timeout)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise LLMError("模型返回的不是 JSON：%s" % raw[:300])
        if isinstance(data, dict) and data.get("error"):
            message = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
            raise LLMError("模型接口报错：%s" % message)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("模型响应格式异常：%s" % raw[:300])
        if isinstance(content, list):  # 少数实现返回分段
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        if content:
            return content
        raise LLMError("模型返回了空内容")
    raise LLMError("模型调用失败")


def _post(url: str, api_key: str, body: dict, timeout: float) -> str:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        hint = ""
        if exc.code in (401, 403):
            hint = "\n请检查 llm.api_key 是否正确、是否有该模型权限。"
        elif exc.code == 404:
            hint = "\n请检查 llm.base_url 是否需要带 /v1，以及模型名是否存在。"
        elif exc.code == 429:
            hint = "\n触发限流，稍后再试或降低并发。"
        raise LLMError("模型接口 HTTP %s：%s%s" % (exc.code, detail, hint))
    except urllib.error.URLError as exc:
        raise LLMError("连不上模型接口 %s：%s" % (url, exc.reason))
    except (socket.timeout, TimeoutError):
        raise LLMError("模型接口超时：%s" % url)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


def extract_json(text: str):
    if not text:
        raise LLMError("模型返回为空")
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMError("模型输出不是合法 JSON：%s" % exc)
    raise LLMError("模型输出里找不到 JSON 对象")
