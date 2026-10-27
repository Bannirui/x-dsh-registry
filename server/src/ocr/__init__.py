"""图片型 PDF 的 OCR / 多模态识别。"""

from .base import OcrAuth, OcrContext, OcrProfile, OcrResult
from .pipeline import (
    build_ocr_context,
    effective_concurrency,
    ocr_page,
    provider_name,
    test_ocr,
)

__all__ = [
    "OcrAuth",
    "OcrContext",
    "OcrProfile",
    "OcrResult",
    "build_ocr_context",
    "effective_concurrency",
    "ocr_page",
    "provider_name",
    "test_ocr",
]
