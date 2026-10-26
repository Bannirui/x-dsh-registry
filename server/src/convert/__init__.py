"""文档转换入口：PDF / DOCX / XLSX -> Markdown。"""

from .base import (
    ConversionError,
    ConversionResult,
    SectionTracker,
    convert_file,
    convert_text,
    write_manifest,
)

__all__ = [
    "ConversionError",
    "ConversionResult",
    "SectionTracker",
    "convert_file",
    "convert_text",
    "write_manifest",
]
