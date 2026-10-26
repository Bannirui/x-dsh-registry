"""OCR 提供方选择、超时、重试与视觉兜底。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from functools import lru_cache

from ..config import get_settings
from .base import OcrResult
from .rapid import RapidOcrProvider
from .vision import VisionOcrProvider


@lru_cache(maxsize=1)
def _provider():
    settings = get_settings()
    if not settings.ocr_enabled:
        return None
    choice = (settings.ocr_provider or "auto").lower()
    if choice in {"none", "off", "disabled"}:
        return None
    if choice == "vision":
        return _vision_provider()
    if choice == "rapidocr":
        return RapidOcrProvider()

    try:
        import rapidocr_onnxruntime  # noqa: F401

        return RapidOcrProvider()
    except ImportError:
        return _vision_provider()


def _vision_provider():
    settings = get_settings()
    if settings.ocr_base_url and settings.ocr_api_key and settings.ocr_model:
        return VisionOcrProvider(
            base_url=settings.ocr_base_url,
            api_key=settings.ocr_api_key,
            model=settings.ocr_model,
            timeout=settings.ocr_timeout,
        )
    return None


def provider_name() -> str:
    provider = _provider()
    return provider.name if provider is not None else "none"


def effective_concurrency() -> int:
    provider = _provider()
    if provider is None:
        return 1
    if provider.name == "rapidocr":
        # RapidOCR 的本地引擎按顺序执行，避免多线程同时调用同一模型。
        return 1
    return max(1, get_settings().ocr_concurrency)


def ocr_page(image_bytes: bytes, *, page: int) -> OcrResult:
    provider = _provider()
    if provider is None:
        return OcrResult(
            text="",
            provider="none",
            error="OCR 未启用或未配置",
        )
    if not image_bytes:
        return OcrResult(text="", provider=provider.name, error="页面图片为空")

    settings = get_settings()
    primary, primary_attempts = _attempt(provider, image_bytes, settings.ocr_timeout, page)
    if _acceptable(primary, settings.ocr_min_confidence):
        return primary

    choice = (settings.ocr_provider or "auto").lower()
    if choice == "auto" and provider.name != "vision":
        fallback_provider = _vision_provider()
        if fallback_provider is not None:
            fallback, fallback_attempts = _attempt(
                fallback_provider,
                image_bytes,
                settings.ocr_timeout,
                page,
            )
            fallback.attempts = primary_attempts + fallback_attempts
            if fallback.ok:
                return fallback
            if primary.ok:
                return primary
            return fallback

    return primary


def _attempt(provider, image_bytes: bytes, timeout: float, page: int) -> tuple[OcrResult, int]:
    attempts = max(1, get_settings().ocr_max_retries + 1)
    last: OcrResult | None = None
    for attempt in range(1, attempts + 1):
        result = _call_with_timeout(provider, image_bytes, timeout)
        result.attempts = attempt
        if result.ok:
            return result, attempt
        last = result
    return last or OcrResult(text="", provider=provider.name, error=f"第 {page} 页 OCR 失败"), attempts


def _acceptable(result: OcrResult, min_confidence: float) -> bool:
    if not result.ok:
        return False
    if result.confidence is None:
        return True
    return result.confidence >= min_confidence


def _call_with_timeout(provider, image_bytes: bytes, timeout: float) -> OcrResult:
    if provider.name != "rapidocr":
        return provider.extract(image_bytes)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(provider.extract, image_bytes)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        return OcrResult(
            text="",
            provider=provider.name,
            model=getattr(provider, "model", ""),
            error=f"OCR 超时（>{timeout}s）",
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
