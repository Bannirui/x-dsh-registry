"""OCR profile 解析、本地/云端 provider 分派、超时重试与视觉兜底。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from ..config import get_settings, resolve_credential
from .baidu import BaiduOcrProvider
from .base import OcrAuth, OcrContext, OcrProfile, OcrProvider, OcrResult
from .rapid import RapidOcrProvider
from .vision import VisionOcrProvider

LOCAL_PROVIDERS = {"rapidocr"}
CLOUD_PROVIDERS = {"vision", "baidu"}


def build_ocr_context(payload_ocr: Any = None, profile_id: str = "") -> OcrContext:
    """从任务 payload 或环境变量构建一次 OCR 快照。"""
    settings = get_settings()
    if isinstance(payload_ocr, dict):
        if payload_ocr.get("profiles"):
            return _from_payload(payload_ocr, profile_id, settings)
        if payload_ocr.get("id") and payload_ocr.get("mode"):
            single = {**payload_ocr, "profiles": [payload_ocr], "defaultProfile": payload_ocr.get("id")}
            return _from_payload(single, profile_id, settings)
    return _from_env(settings)


def _from_payload(payload: dict[str, Any], profile_id: str, settings) -> OcrContext:
    enabled = bool(payload.get("enabled", True))
    raw_profiles = payload.get("profiles") or []
    profiles = [
        _parse_profile(item, index)
        for index, item in enumerate(raw_profiles)
        if isinstance(item, dict)
    ]
    profiles = [profile for profile in profiles if profile.id]
    if not profiles:
        return _from_env(settings)

    by_id = {profile.id: profile for profile in profiles}
    selected_id = (profile_id or str(payload.get("defaultProfile") or "")).strip()
    if not selected_id or selected_id == "auto":
        selected = profiles[0]
    else:
        selected = by_id.get(selected_id)
        if selected is None:
            return OcrContext(
                enabled=enabled,
                provider=None,
                profile_id=selected_id,
                mode="profile",
                error=f"默认 OCR profile 不存在：{selected_id}",
            )

    if not enabled:
        return OcrContext(enabled=False, provider=None, profile_id=selected.id, mode=selected.mode)

    primary, fallback, error = _build_profile_providers(selected)
    return OcrContext(
        enabled=True,
        provider=primary,
        fallback_provider=fallback,
        profile_id=selected.id,
        mode=selected.mode,
        timeout=selected.timeout,
        concurrency=selected.concurrency,
        max_pages=selected.max_pages,
        dpi=selected.dpi,
        min_confidence=settings.ocr_min_confidence,
        max_retries=settings.ocr_max_retries,
        error=error,
    )


def _build_profile_providers(
    profile: OcrProfile,
) -> tuple[OcrProvider | None, OcrProvider | None, str]:
    if profile.mode == "local":
        if profile.provider not in LOCAL_PROVIDERS:
            return None, None, f"未知本地 OCR provider：{profile.provider}"
        return RapidOcrProvider(), None, ""

    if profile.mode in {"cloud", "auto"}:
        cloud, error = _build_cloud_provider(profile)
        if profile.mode == "cloud":
            return cloud, None, error
        # auto：本地优先，云端作为兜底；本地不可用时直接使用云端。
        if not _local_available():
            return cloud, None, error
        local = RapidOcrProvider()
        if cloud is None:
            return local, None, ""
        return local, cloud, ""

    return None, None, f"未知 OCR 模式：{profile.mode}"


def _build_cloud_provider(profile: OcrProfile) -> tuple[OcrProvider | None, str]:
    if profile.provider == "vision":
        api_key = resolve_credential(profile.auth.api_key_ref)
        if not profile.base_url:
            return None, f"云 OCR profile「{profile.id}」缺少 Base URL"
        if not profile.model:
            return None, f"云 OCR profile「{profile.id}」缺少 Model"
        if not api_key:
            return None, f"云 OCR 凭据未配置：{profile.auth.api_key_ref or 'API Key'}"
        return (
            VisionOcrProvider(
                base_url=profile.base_url,
                api_key=api_key,
                model=profile.model,
                timeout=profile.timeout,
            ),
            "",
        )

    if profile.provider == "baidu":
        api_key = resolve_credential(profile.auth.api_key_ref)
        secret_key = resolve_credential(profile.auth.secret_key_ref)
        if not profile.model:
            return None, f"百度 OCR profile「{profile.id}」缺少服务名（如 general_basic）"
        if not api_key:
            return None, f"百度 OCR 凭据未配置：{profile.auth.api_key_ref or 'API Key'}"
        if not secret_key:
            return None, f"百度 OCR 凭据未配置：{profile.auth.secret_key_ref or 'Secret Key'}"
        return (
            BaiduOcrProvider(
                base_url=profile.base_url or "https://aip.baidubce.com",
                api_key=api_key,
                secret_key=secret_key,
                model=profile.model,
                timeout=profile.timeout,
            ),
            "",
        )

    return None, f"未知云 OCR provider：{profile.provider}"


def _parse_profile(raw: dict[str, Any], index: int) -> OcrProfile:
    auth_raw = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    options_raw = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    mode = _text(_pick(raw, "mode")) or "local"
    provider = _text(_pick(raw, "provider")) or ("rapidocr" if mode == "local" else "vision")
    auth_type = _text(_pick(auth_raw, "type")) or (
        "ak_sk" if provider == "baidu" else "bearer" if provider == "vision" else "none"
    )
    return OcrProfile(
        id=_text(_pick(raw, "id")) or f"profile-{index + 1}",
        name=_text(_pick(raw, "name")),
        mode=mode,
        provider=provider,
        language=_text(_pick(raw, "language")) or "chi_sim",
        dpi=max(72, min(600, _int(_pick(raw, "dpi"), 180))),
        max_pages=max(0, _int(_pick(raw, "maxPages", "max_pages"), 50)),
        concurrency=max(1, _int(_pick(raw, "concurrency"), 2)),
        base_url=_text(_pick(raw, "baseUrl", "base_url")).rstrip("/"),
        model=_text(_pick(raw, "model")),
        region=_text(_pick(raw, "region")),
        auth=OcrAuth(
            type=auth_type,
            api_key_ref=_text(_pick(auth_raw, "apiKeyRef", "api_key_ref")),
            secret_key_ref=_text(_pick(auth_raw, "secretKeyRef", "secret_key_ref")),
        ),
        options=dict(options_raw),
        timeout=max(1.0, _float(_pick(raw, "timeout"), 60.0)),
    )


def _from_env(settings) -> OcrContext:
    if not settings.ocr_enabled:
        return OcrContext(enabled=False, provider=None, profile_id="env", mode="env")

    choice = (settings.ocr_provider or "auto").strip().lower()
    if choice in {"none", "off", "disabled"}:
        return OcrContext(enabled=False, provider=None, profile_id="env", mode="env")

    local = RapidOcrProvider() if _local_available() else None
    cloud = None
    if settings.ocr_base_url and settings.ocr_model:
        api_key = settings.ocr_api_key or resolve_credential("XAGENTS_OCR_API_KEY")
        if api_key:
            cloud = VisionOcrProvider(
                base_url=settings.ocr_base_url,
                api_key=api_key,
                model=settings.ocr_model,
                timeout=settings.ocr_timeout,
            )

    if choice == "vision":
        primary, fallback = cloud, None
    elif choice == "rapidocr":
        primary, fallback = local, None
    else:
        primary = local or cloud
        fallback = cloud if primary is local and cloud is not None else None

    error = ""
    if primary is None:
        error = "OCR 未启用或未配置"
    return OcrContext(
        enabled=True,
        provider=primary,
        fallback_provider=fallback,
        profile_id="env",
        mode=choice,
        timeout=settings.ocr_timeout,
        concurrency=settings.ocr_concurrency,
        max_pages=settings.ocr_max_pages,
        dpi=settings.ocr_dpi,
        min_confidence=settings.ocr_min_confidence,
        max_retries=settings.ocr_max_retries,
        error=error,
    )


def _local_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def provider_name(ocr_ctx: OcrContext) -> str:
    return ocr_ctx.provider.name if ocr_ctx.provider is not None else "none"


def effective_concurrency(ocr_ctx: OcrContext) -> int:
    if ocr_ctx.provider is None:
        return 1
    if ocr_ctx.provider.name == "rapidocr":
        # RapidOCR 的本地引擎按顺序执行，避免多线程同时调用同一模型。
        return 1
    return max(1, ocr_ctx.concurrency)


def ocr_page(ocr_ctx: OcrContext, image_bytes: bytes, *, page: int) -> OcrResult:
    if ocr_ctx.provider is None:
        return OcrResult(
            text="",
            provider="none",
            error=ocr_ctx.error or "OCR 未启用或未配置",
        )
    if not image_bytes:
        return OcrResult(text="", provider=ocr_ctx.provider.name, error="页面图片为空")

    primary, primary_attempts = _attempt(ocr_ctx, ocr_ctx.provider, image_bytes)
    if _acceptable(primary, ocr_ctx.min_confidence):
        return primary

    if ocr_ctx.fallback_provider is not None and ocr_ctx.fallback_provider is not ocr_ctx.provider:
        fallback, fallback_attempts = _attempt(ocr_ctx, ocr_ctx.fallback_provider, image_bytes)
        fallback.attempts = primary_attempts + fallback_attempts
        if fallback.ok:
            return fallback
        if primary.ok:
            return primary
        return fallback

    return primary


def test_ocr(payload_ocr: Any = None, profile_id: str = "") -> dict[str, Any]:
    """用一张内置样例图验证 profile 的 provider/baseUrl/鉴权是否可用。"""
    ocr_ctx = build_ocr_context(payload_ocr, profile_id)
    if ocr_ctx.provider is None:
        return {
            "ok": False,
            "profile": ocr_ctx.profile_id,
            "provider": "none",
            "model": "",
            "error": ocr_ctx.error or "OCR 未配置",
        }

    image = _sample_image()
    if not image:
        return {
            "ok": False,
            "profile": ocr_ctx.profile_id,
            "provider": provider_name(ocr_ctx),
            "model": getattr(ocr_ctx.provider, "model", ""),
            "error": "无法生成 OCR 测试样例图",
        }

    result = ocr_page(ocr_ctx, image, page=0)
    return {
        "ok": result.ok,
        "profile": ocr_ctx.profile_id,
        "provider": result.provider,
        "model": result.model,
        "duration": round(result.duration, 3),
        "chars": len(result.text),
        "textPreview": result.text[:200],
        "attempts": result.attempts,
        "error": result.error,
    }


def _attempt(ocr_ctx: OcrContext, provider: OcrProvider, image_bytes: bytes) -> tuple[OcrResult, int]:
    attempts = max(1, ocr_ctx.max_retries + 1)
    last: OcrResult | None = None
    for attempt in range(1, attempts + 1):
        result = _call_with_timeout(provider, image_bytes, ocr_ctx.timeout)
        result.attempts = attempt
        if result.ok:
            return result, attempt
        last = result
    return last or OcrResult(text="", provider=provider.name, error="OCR 失败"), attempts


def _acceptable(result: OcrResult, min_confidence: float) -> bool:
    if not result.ok:
        return False
    if result.confidence is None:
        return True
    return result.confidence >= min_confidence


def _call_with_timeout(provider: OcrProvider, image_bytes: bytes, timeout: float) -> OcrResult:
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


def _sample_image() -> bytes:
    try:
        import pymupdf

        document = pymupdf.open()
        try:
            page = document.new_page(width=360, height=140)
            page.insert_text((24, 78), "OCR TEST 123", fontsize=28)
            return page.get_pixmap(dpi=150).tobytes("png")
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - 测试样例失败不影响主流程
        return b""


def _pick(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
