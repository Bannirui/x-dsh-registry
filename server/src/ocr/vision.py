"""OpenAI 兼容的视觉模型 OCR 适配器。"""

from __future__ import annotations

import base64
import re
import time

import httpx

from .base import OcrResult

PROMPT = (
    "请提取图片中的全部文字，保持原有阅读顺序和表格结构，输出 Markdown。"
    "不要解释，不要省略，不要编造图片中不存在的内容。"
)


class VisionOcrProvider:
    name = "vision"

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def extract(self, image_bytes: bytes) -> OcrResult:
        started = time.perf_counter()
        if not self.base_url or not self.api_key or not self.model:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error="视觉 OCR 未配置 BASE_URL / API_KEY / MODEL",
            )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode("ascii")
                            },
                        },
                    ],
                }
            ],
        }
        try:
            response = httpx.post(
                _endpoint(self.base_url),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"视觉 OCR 请求失败：{type(exc).__name__}: {exc}",
            )
        if response.status_code >= 400:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"视觉 OCR HTTP {response.status_code}: {_short(response.text)}",
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            text = _content_text(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"视觉 OCR 响应格式异常：{type(exc).__name__}",
            )
        return OcrResult(
            text=_strip_fence(text),
            provider=self.name,
            model=self.model,
            duration=time.perf_counter() - started,
            error=None if text.strip() else "视觉模型未返回文字",
        )


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _strip_fence(value: str) -> str:
    text = value.strip()
    matched = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return matched.group(1).strip() if matched else text


def _short(value: str, limit: int = 300) -> str:
    text = value.strip()
    return text[:limit] + ("…" if len(text) > limit else "")
