"""图片型 PDF 的 OCR / 多模态识别。"""

from .base import OcrResult
from .pipeline import ocr_page, provider_name

__all__ = ["OcrResult", "ocr_page", "provider_name"]
