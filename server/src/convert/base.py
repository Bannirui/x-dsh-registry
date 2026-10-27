"""公共转换模型与任务目录写入。

转换器只负责返回 Markdown 和清单数据，落盘、命名、防覆盖统一放在这里。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm"}
TEXT_KEYS = ("text", "content", "material", "raw")

if TYPE_CHECKING:
    from ..ocr.base import OcrContext


class ConversionError(RuntimeError):
    """转换失败，消息会直接展示给用户。"""


@dataclass
class ConversionResult:
    markdown: str
    format: str
    sections: list[dict] = field(default_factory=list)
    assets: list[dict] = field(default_factory=list)
    ocr_pages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)


class SectionTracker:
    """为 Markdown 标题分配稳定、可被后续拆分脚本识别的 section_id。"""

    def __init__(self) -> None:
        self._counters = [0] * 7
        self._used: set[str] = set()
        self.sections: list[dict] = []

    def heading(self, level: int, title: str, *, page: int | None = None) -> tuple[str, dict]:
        level = max(1, min(6, int(level)))
        text = clean_heading(title)
        if not text:
            text = "未命名章节"

        matched = re.match(r"^(\d+(?:\.\d+)*)[\s._、-]*(.*)$", text)
        if matched:
            section_id = matched.group(1)
            name = matched.group(2).strip() or text
            parts = [int(part) for part in section_id.split(".")]
            for index, value in enumerate(parts[:6], start=1):
                self._counters[index] = value
            for index in range(len(parts) + 1, 7):
                self._counters[index] = 0
        else:
            self._counters[level] += 1
            for index in range(level + 1, 7):
                self._counters[index] = 0
            parts = [value for value in self._counters[1 : level + 1] if value > 0]
            section_id = ".".join(str(value) for value in parts) or "1"
            name = text

        section_id = self._unique(section_id)
        display = text if matched else f"{section_id} {name}"
        section = {
            "section_id": section_id,
            "title": name,
            "level": level,
            "page": page,
        }
        self.sections.append(section)
        return f"{'#' * level} {display}", section

    def _unique(self, section_id: str) -> str:
        candidate = section_id
        suffix = 2
        while candidate in self._used:
            candidate = f"{section_id}.{suffix}"
            suffix += 1
        self._used.add(candidate)
        return candidate


def clean_heading(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    value = value.replace("**", "").replace("__", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip("*# ")


def extract_sections(markdown: str) -> list[dict]:
    """从 Markdown 标题行提取章节列表。"""
    tracker = SectionTracker()
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown, re.MULTILINE):
        tracker.heading(len(match.group(1)), match.group(2))
    return tracker.sections


def safe_name(value: str, *, default: str = "document", limit: int = 60) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", value or "").strip(" ._")
    return (value or default)[:limit]


def new_task_dir(root: str | Path, stem: str) -> Path:
    """创建唯一任务目录，绝不覆盖已有目录。"""
    root_path = Path(root).expanduser().resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    base = safe_name(stem)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for index in range(1, 1000):
        name = f"{base}-{stamp}" if index == 1 else f"{base}-{stamp}-{index}"
        target = root_path / name
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return target
    raise ConversionError("无法创建唯一的任务输出目录")


def _write_markdown(task_dir: Path, markdown: str) -> Path:
    path = task_dir / "document.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def _write_manifest(task_dir: Path, manifest: dict) -> Path:
    path = task_dir / "convert_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_manifest(task_dir: Path, manifest: dict) -> Path:
    """供编排层在补充来源信息后重写清单。"""
    return _write_manifest(task_dir, manifest)


def _build_manifest(
    *,
    task_dir: Path,
    result: ConversionResult,
    source_name: str,
    source_format: str,
    source_path: Path | None,
    source_chars: int | None = None,
) -> dict:
    return {
        "task_id": task_dir.name,
        "source": {
            "name": source_name,
            "format": source_format,
            "path": str(source_path) if source_path else None,
            "chars": source_chars,
        },
        "markdown": {
            "path": "document.md",
            "chars": len(result.markdown),
        },
        "sections": result.sections,
        "assets": result.assets,
        "ocr_pages": result.ocr_pages,
        "warnings": result.warnings,
        "failures": result.failures,
        "converted_at": datetime.now(timezone.utc).isoformat(),
    }


def convert_file(source: Path, task_dir: Path, *, ocr_ctx: "OcrContext | None" = None) -> dict:
    """转换单个文件并返回 manifest。"""
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise ConversionError(f"不支持的文件格式 {suffix or '（无扩展名）'}，仅支持 {allowed}")

    converter = _converter_for(suffix)
    assets_dir = task_dir / "assets" / source_format_name(suffix)
    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = converter(source, assets_dir, ocr_ctx=ocr_ctx)
    except ConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成可读诊断
        raise ConversionError(f"{source.name} 转换失败：{type(exc).__name__}: {exc}") from exc

    markdown_path = _write_markdown(task_dir, result.markdown)
    manifest = _build_manifest(
        task_dir=task_dir,
        result=result,
        source_name=source.name,
        source_format=result.format,
        source_path=source,
    )
    manifest["markdown"]["path"] = markdown_path.name
    manifest["manifest_path"] = "convert_manifest.json"
    if ocr_ctx is not None:
        manifest["ocr"] = {
            "enabled": ocr_ctx.enabled,
            "profile": ocr_ctx.profile_id,
            "mode": ocr_ctx.mode,
            "provider": ocr_ctx.provider.name if ocr_ctx.provider is not None else "none",
            "error": ocr_ctx.error or None,
        }
    _write_manifest(task_dir, manifest)
    return manifest


def convert_text(text: str, task_dir: Path, *, title: str = "") -> dict:
    """把纯文本写成 Markdown，供没有文件的对话输入使用。"""
    if not text.strip():
        raise ConversionError("输入文本为空，无法转换")
    markdown = text.strip()
    heading = clean_heading(title)
    if heading and not re.search(r"^#{1,6}\s+\S", markdown, re.MULTILINE):
        markdown = f"# {heading}\n\n{markdown}"
    result = ConversionResult(
        markdown=markdown,
        format="text",
        sections=extract_sections(markdown),
    )
    markdown_path = _write_markdown(task_dir, result.markdown)
    manifest = _build_manifest(
        task_dir=task_dir,
        result=result,
        source_name=f"{heading or 'material'}.txt",
        source_format="text",
        source_path=None,
        source_chars=len(text),
    )
    manifest["markdown"]["path"] = markdown_path.name
    manifest["manifest_path"] = "convert_manifest.json"
    _write_manifest(task_dir, manifest)
    return manifest


def source_format_name(suffix: str) -> str:
    return {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx"}[suffix]


def _converter_for(suffix: str) -> Callable[..., ConversionResult]:
    if suffix == ".pdf":
        from .pdf import convert_pdf

        return convert_pdf
    if suffix == ".docx":
        from .docx import convert_docx

        return convert_docx
    from .xlsx import convert_xlsx

    return convert_xlsx
