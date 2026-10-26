from __future__ import annotations

from dataclasses import dataclass


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
