"""本地 RapidOCR 适配器，默认用于中文扫描页。"""

from __future__ import annotations

import re
import time
from functools import lru_cache

from .base import OcrResult


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


class RapidOcrProvider:
    name = "rapidocr"
    model = "chinese_ppocr_v4"

    def extract(self, image_bytes: bytes) -> OcrResult:
        started = time.perf_counter()
        try:
            result, _ = _engine()(image_bytes)
        except Exception as exc:  # noqa: BLE001
            return OcrResult(
                text="",
                provider=self.name,
                model=self.model,
                duration=time.perf_counter() - started,
                error=f"RapidOCR 执行失败：{type(exc).__name__}: {exc}",
            )
        text, confidence = _result_to_text(result)
        return OcrResult(
            text=text,
            provider=self.name,
            model=self.model,
            duration=time.perf_counter() - started,
            confidence=confidence,
            error=None if text.strip() else "未识别到文字",
        )


def _result_to_text(result) -> tuple[str, float | None]:
    if not result:
        return "", None

    entries: list[dict] = []
    scores: list[float] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, score = item[0], str(item[1]).strip(), item[2]
        if not text:
            continue
        try:
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            scores.append(float(score))
        except (TypeError, ValueError, IndexError):
            continue
        entries.append(
            {
                "y_min": min(ys),
                "y_max": max(ys),
                "y_center": (min(ys) + max(ys)) / 2,
                "x_min": min(xs),
                "text": text,
            }
        )

    if not entries:
        return "", None

    entries.sort(key=lambda item: (item["y_min"], item["x_min"]))
    lines: list[list[dict]] = []
    for entry in entries:
        if not lines:
            lines.append([entry])
            continue
        current = lines[-1]
        current_min = min(item["y_min"] for item in current)
        current_max = max(item["y_max"] for item in current)
        current_height = max(1.0, current_max - current_min)
        entry_height = max(1.0, entry["y_max"] - entry["y_min"])
        overlap = min(current_max, entry["y_max"]) - max(current_min, entry["y_min"])
        same_line = overlap >= 0.5 * min(current_height, entry_height)
        if not same_line:
            center = (current_min + current_max) / 2
            same_line = abs(center - entry["y_center"]) <= 0.5 * max(current_height, entry_height)
        if same_line:
            current.append(entry)
        else:
            lines.append([entry])

    rendered: list[str] = []
    for line in lines:
        line.sort(key=lambda item: item["x_min"])
        rendered.append(_normalize_line(_join_parts([item["text"] for item in line])))
    confidence = sum(scores) / len(scores) if scores else None
    return "\n".join(rendered).strip(), confidence


def _normalize_line(text: str) -> str:
    return re.sub(r"(?<=\d)\s*[.．]\s*(?=\d)", ".", text)


def _join_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    text = parts[0]
    for part in parts[1:]:
        if not text or not part:
            text += part
        elif _is_cjk(text[-1]) and _is_cjk(part[0]):
            text += part
        else:
            text += " " + part
    return text


def _is_cjk(value: str) -> bool:
    return "\u4e00" <= value <= "\u9fff"
