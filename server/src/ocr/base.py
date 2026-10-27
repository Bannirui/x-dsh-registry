from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class OcrResult:
    text: str
    provider: str
    model: str = ""
    duration: float = 0.0
    confidence: float | None = None
    attempts: int = 1
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and self.error is None


class OcrProvider(Protocol):
    name: str
    model: str

    def extract(self, image_bytes: bytes) -> OcrResult: ...


@dataclass(frozen=True)
class OcrAuth:
    """云端凭据引用；只存引用名，真实密钥由 config.resolve_credential 解析。"""

    type: str = "none"
    api_key_ref: str = ""
    secret_key_ref: str = ""


@dataclass(frozen=True)
class OcrProfile:
    """来自 harness 设置页或环境变量的一个 OCR profile。"""

    id: str
    name: str = ""
    mode: str = "local"
    provider: str = "rapidocr"
    language: str = "chi_sim"
    dpi: int = 180
    max_pages: int = 50
    concurrency: int = 2
    base_url: str = ""
    model: str = ""
    region: str = ""
    auth: OcrAuth = field(default_factory=OcrAuth)
    options: dict[str, object] = field(default_factory=dict)
    timeout: float = 60.0


@dataclass
class OcrContext:
    """一次转换任务使用的 OCR 快照；并发任务之间互不影响。"""

    enabled: bool
    provider: OcrProvider | None
    profile_id: str = ""
    mode: str = "env"
    min_confidence: float = 0.5
    max_retries: int = 1
    timeout: float = 60.0
    concurrency: int = 1
    max_pages: int = 50
    dpi: int = 180
    fallback_provider: OcrProvider | None = None
    error: str = ""
