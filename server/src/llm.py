"""LLM客户端

走官方``openai``SDK OpenAI兼容协议
默认指向DeepSeek
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .config import (
    ConfigError,
    get_settings,
    resolve_api_key,
    resolve_credential,
)

logger = logging.getLogger("xagents.llm")

_clients: dict[tuple[str, str, float, int], AsyncOpenAI] = {}
_client_lock = asyncio.Lock()


@dataclass(frozen=True)
class LlmConfig:
    """一次模型调用使用的连接快照；key 不参与 repr/log。"""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: float = 120.0
    max_retries: int = 2
    api_key_ref: str = ""
    source: str = "env"


def _as_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_positive_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def _as_retry_count(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


def resolve_llm_config(payload_llm: Any = None) -> LlmConfig:
    """把 Settings → 语义分块 LLM 的配置解析成一次调用快照。

    没有 payload 配置时沿用环境变量/默认值；配置了 ``apiKeyRef`` 但凭据库没有
    对应 key 时回退到 Models 页面共享的 DEEPSEEK_API_KEY，便于只改模型/端点。
    """
    settings = get_settings()
    base_url = settings.base_url
    model = settings.model
    timeout = settings.llm_timeout
    max_retries = settings.llm_max_retries
    api_key = resolve_api_key()
    api_key_ref = ""
    source = "env"

    if isinstance(payload_llm, dict) and payload_llm:
        source = "settings"
        base_url = _as_str(
            payload_llm.get("baseUrl"),
            payload_llm.get("base_url"),
        ).rstrip("/") or base_url
        model = _as_str(payload_llm.get("model")) or model
        api_key_ref = _as_str(
            payload_llm.get("apiKeyRef"),
            payload_llm.get("api_key_ref"),
        )
        if api_key_ref:
            api_key = resolve_credential(api_key_ref) or api_key
        timeout = _as_positive_float(payload_llm.get("timeout"), timeout)
        max_retries = _as_retry_count(
            payload_llm.get("maxRetries", payload_llm.get("max_retries")),
            max_retries,
        )

    if not api_key:
        raise ConfigError(
            "模型凭据未配置：请在 harness Web 的 Settings → 语义分块 LLM 填写 API Key，"
            "或在 Models 页面配置 DEEPSEEK_API_KEY"
        )

    return LlmConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        api_key_ref=api_key_ref,
        source=source,
    )


async def _get_client(config: LlmConfig) -> AsyncOpenAI:
    """按连接参数缓存客户端；Settings 改 key/端点后自动使用新客户端。"""
    cache_key = (config.base_url, config.api_key, config.timeout, config.max_retries)
    cached = _clients.get(cache_key)
    if cached is not None:
        return cached
    async with _client_lock:
        cached = _clients.get(cache_key)
        if cached is not None:
            return cached
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        _clients[cache_key] = client
        return client


async def aclose() -> None:
    """关闭底层连接池 服务优雅退出时调用"""
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.close()


async def complete_json(
    system: str,
    user: str,
    config: LlmConfig | None = None,
) -> object:
    """调用模型并解析出JSON对象 失败（网络/鉴权/格式）时抛异常"""
    config = config or resolve_llm_config()
    client = await _get_client(config)
    response = await client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    return _extract_json(content)


async def test_llm(payload_llm: Any = None) -> dict[str, Any]:
    """用一次最小对话验证 Settings → 语义分块 LLM 的连接配置。"""
    config = resolve_llm_config(payload_llm)
    client = await _get_client(config)
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=config.model,
        messages=[{"role": "user", "content": "请只回复：OK"}],
        temperature=0,
        max_tokens=16,
    )
    text = response.choices[0].message.content or ""
    return {
        "ok": bool(text.strip()),
        "baseUrl": config.base_url,
        "model": config.model,
        "apiKeyRef": config.api_key_ref,
        "source": config.source,
        "duration": round(time.perf_counter() - started, 3),
        "textPreview": text.strip()[:100],
        "error": None if text.strip() else "模型返回了空内容",
    }


def _extract_json(text: str) -> object:
    """容忍代码围栏和前后废话地解析第一个JSON对象"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    while start != -1:
        depth = 0
        for end in range(start, len(text)):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    raise RuntimeError("model did not return JSON")
