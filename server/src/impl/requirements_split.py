"""需求文档拆分 Agent 的文档接入阶段。

当前版本完成 PDF / DOCX / XLSX -> Markdown 与 convert_manifest.json；
语义分块、图片跟随和 OCR 将在后续阶段接到同一入口。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..base import XAgent
from ..config import get_settings
from ..convert import ConversionError, convert_file, convert_text, write_manifest
from ..convert.base import TEXT_KEYS, new_task_dir

NAME = "requirements_split"
DESCRIPTION = "接收 PDF/Word/Excel 需求文档，转换为 Markdown 并输出可追溯的转换清单"

PATH_KEYS = ("path", "source_path", "sourcePath", "file", "filePath", "file_path")
OUTPUT_KEYS = ("output_dir", "outputDir")


def _first_string(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _text(payload: dict) -> str:
    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _resolve_source(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        candidates = [path]
    else:
        repo_root = Path(__file__).resolve().parents[3]
        candidates = [Path.cwd() / path, Path.cwd().parent / path, repo_root / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ConversionError(f"找不到文件：{raw}（请传工作区内的绝对路径）")


def _validate_source(source: Path) -> None:
    from ..convert.base import SUPPORTED_SUFFIXES

    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        allowed = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise ConversionError(f"不支持的文件格式 {source.suffix or '（无扩展名）'}，仅支持 {allowed}")
    limit = get_settings().max_source_mb
    if limit > 0 and source.stat().st_size > limit * 1024 * 1024:
        raise ConversionError(f"文件过大：{source.stat().st_size / 1024 / 1024:.1f}MB，上限为 {limit}MB")


def _output_root(payload: dict) -> Path:
    raw = _first_string(payload, OUTPUT_KEYS)
    root = Path(raw).expanduser() if raw else Path(get_settings().output_dir).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _convert_sync(payload: dict) -> dict:
    raw_path = _first_string(payload, PATH_KEYS)
    text = _text(payload)
    title = _first_string(payload, ("title", "project_name", "projectName"))

    if raw_path:
        source = _resolve_source(raw_path)
        _validate_source(source)
        root = _output_root(payload)
        task_dir = new_task_dir(root, title or source.stem)
        source_dir = task_dir / "source"
        source_dir.mkdir()
        copied = source_dir / source.name
        try:
            shutil.copy2(source, copied)
        except OSError as exc:
            raise ConversionError(f"复制源文件失败：{exc}") from exc
        manifest = convert_file(copied, task_dir)
        manifest["source"]["original_path"] = str(source)
        manifest["source"]["copy_path"] = copied.relative_to(task_dir).as_posix()
        manifest_path = write_manifest(task_dir, manifest)
    elif text:
        root = _output_root(payload)
        task_dir = new_task_dir(root, title or "requirement")
        manifest = convert_text(text, task_dir, title=title)
        manifest_path = task_dir / "convert_manifest.json"
    else:
        raise ConversionError("需要提供 path（文档路径）或 text/material 文本内容")

    markdown_path = task_dir / str(manifest["markdown"]["path"])
    sections = manifest.get("sections") or []
    assets = manifest.get("assets") or []
    warnings = manifest.get("warnings") or []
    ocr_pages = manifest.get("ocr_pages") or []

    message = (
        f"已转换 {manifest['source']['name']}："
        f"{str(manifest['source']['format']).upper()}，"
        f"{len(sections)} 个章节，{len(assets)} 张图片。"
        f"输出目录：{task_dir}"
    )
    if ocr_pages:
        message += f" 有 {len(ocr_pages)} 页需要 OCR。"
    if warnings:
        message += f" 有 {len(warnings)} 条告警，详见 convert_manifest.json。"

    return {
        "kind": "requirements-split-document",
        "taskId": task_dir.name,
        "outputDir": str(task_dir),
        "markdownPath": str(markdown_path),
        "manifestPath": str(manifest_path),
        "sourceName": manifest["source"]["name"],
        "format": manifest["source"]["format"],
        "sectionCount": len(sections),
        "assetCount": len(assets),
        "ocrPageCount": len(ocr_pages),
        "warningCount": len(warnings),
        "ocrPages": ocr_pages,
        "warnings": warnings,
        "sections": sections,
        "message": message,
    }


class RequirementsSplitAgent(XAgent):
    async def run(self, payload: dict) -> dict:
        return await asyncio.to_thread(_convert_sync, payload)


AGENT = RequirementsSplitAgent(NAME, DESCRIPTION)
