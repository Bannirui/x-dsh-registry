"""图片型 PDF 的 OCR / 多模态识别。"""

from .base import OcrResult
from .pipeline import effective_concurrency, ocr_page, provider_name

__all__ = ["OcrResult", "effective_concurrency", "ocr_page", "provider_name"]
