"""LLM客户端

走官方``openai``SDK OpenAI兼容协议
默认指向DeepSeek
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from openai import AsyncOpenAI

from .config import get_settings, require_api_key

logger = logging.getLogger("xagents.llm")


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=require_api_key(settings),
        base_url=settings.base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


async def aclose() -> None:
    """关闭底层连接池 服务优雅退出时调用"""
    if _client.cache_info().currsize:
        await _client().close()
        _client.cache_clear()


async def complete_json(system: str, user: str) -> object:
    """调用模型并解析出JSON对象 失败（网络/鉴权/格式）时抛异常"""
    settings = get_settings()
    response = await _client().chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    return _extract_json(content)


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
