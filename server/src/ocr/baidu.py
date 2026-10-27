"""百度通用 OCR 适配器（AK/SK 换 access_token）。"""

from __future__ import annotations

import base64
import time

import httpx

from .base import OcrResult


class BaiduOcrProvider:
    name = "baidu"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        secret_key: str,
        model: str = "general_basic",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or "https://aip.baidubce.com").rstrip("/")
        self.api_key = api_key
        self.secret_key = secret_key
        self.model = model or "general_basic"
        self.timeout = timeout
        self._token = ""
        self._token_expiry = 0.0

    def extract(self, image_bytes: bytes) -> OcrResult:
        started = time.perf_counter()
        if not self.api_key or not self.secret_key:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error="百度 OCR 未配置 API Key / Secret Key",
            )

        token, error = self._access_token()
        if error:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=error,
            )

        payload = {
            "image": base64.b64encode(image_bytes).decode("ascii"),
            "language_type": "CHN_ENG",
        }
        endpoint = f"{self.base_url}/rest/2.0/ocr/v1/{self.model}"
        try:
            response = httpx.post(
                endpoint,
                params={"access_token": token},
                data=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"百度 OCR 请求失败：{type(exc).__name__}: {exc}",
            )

        if response.status_code >= 400:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"百度 OCR HTTP {response.status_code}: {_short(response.text)}",
            )

        try:
            body = response.json()
        except ValueError:
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error="百度 OCR 响应不是 JSON",
            )

        if body.get("error_code"):
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"百度 OCR {body.get('error_code')}: {body.get('error_msg') or '未知错误'}",
            )

        words = body.get("words_result") or []
        lines = [
            str(item.get("words", "")).strip()
            for item in words
            if isinstance(item, dict) and str(item.get("words", "")).strip()
        ]
        text = "\n".join(lines).strip()
        return OcrResult(
            text=text,
            provider=self.name,
            model=self.model,
            duration=time.perf_counter() - started,
            error=None if text else "百度 OCR 未识别到文字",
        )

    def _access_token(self) -> tuple[str, str]:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token, ""
        try:
            response = httpx.post(
                f"{self.base_url}/oauth/2.0/token",
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.secret_key,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return "", f"百度 OCR 获取 token 失败：{type(exc).__name__}: {exc}"

        if response.status_code >= 400:
            return "", f"百度 OCR 获取 token HTTP {response.status_code}: {_short(response.text)}"
        try:
            body = response.json()
            token = str(body["access_token"])
            expires = int(body.get("expires_in", 2592000))
        except (KeyError, TypeError, ValueError):
            return "", "百度 OCR token 响应格式异常"

        self._token = token
        self._token_expiry = time.time() + max(60, expires)
        return token, ""


def _short(value: str, limit: int = 300) -> str:
    text = (value or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")
