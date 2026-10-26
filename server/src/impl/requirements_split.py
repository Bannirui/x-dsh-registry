"""需求文档拆分 Agent：文档接入 + 语义分块 + 物理拆分/目录组织。"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from .. import llm
from ..base import XAgent
from ..config import get_settings
from ..convert import ConversionError, convert_file, convert_text, write_manifest
from ..convert.base import TEXT_KEYS, new_task_dir
from ..split import SplitError, SplitRunError, build_chunks, run_pipeline
from ..split.outline import compact_markdown, normalize_markdown, outline_text
from ..split.prompts import SYSTEM_PROMPT, build_user_prompt

NAME = "requirements_split"
DESCRIPTION = "接收 PDF/Word/Excel 需求文档，转换为 Markdown，按功能语义拆分并组织需求目录"

PATH_KEYS = ("path", "source_path", "sourcePath", "file", "filePath", "file_path")
OUTPUT_KEYS = ("output_dir", "outputDir")

logger = logging.getLogger("xagents.requirements_split")


def _progress(payload: dict, value: int, stage: str, message: str = "") -> None:
    callback = payload.get("_progress")
    if callable(callback):
        try:
            callback(value, stage, message)
        except Exception:  # noqa: BLE001 - 进度上报不能影响主流程
            pass


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


def _output_root(payload: dict, base: Path | None = None) -> Path:
    raw = _first_string(payload, OUTPUT_KEYS)
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (base or Path.cwd()) / root
    else:
        root = Path(get_settings().output_dir).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
    return root.resolve()


def _convert_sync(payload: dict) -> dict:
    _progress(payload, 5, "读取文档", "正在读取并校验源文件")
    raw_path = _first_string(payload, PATH_KEYS)
    text = _text(payload)
    title = _first_string(payload, ("title", "project_name", "projectName"))

    if raw_path:
        source = _resolve_source(raw_path)
        _validate_source(source)
        root = _output_root(payload, source.parent)
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
    ocr_completed = sum(1 for item in ocr_pages if item.get("status") == "completed")
    ocr_failed = sum(1 for item in ocr_pages if item.get("status") in {"failed", "pending", "skipped"})

    message = (
        f"已转换 {manifest['source']['name']}："
        f"{str(manifest['source']['format']).upper()}，"
        f"{len(sections)} 个章节，{len(assets)} 张图片。"
        f"输出目录：{task_dir}"
    )
    if ocr_completed:
        message += f" 已完成 {ocr_completed} 页 OCR。"
    if ocr_failed:
        message += f" 有 {ocr_failed} 页 OCR 未完成。"
    if warnings:
        message += f" 有 {len(warnings)} 条告警，详见 convert_manifest.json。"

    _progress(payload, 35, "文档转换完成", f"已生成 {len(sections)} 个章节")
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
        "ocrCompletedCount": ocr_completed,
        "ocrFailedCount": ocr_failed,
        "warningCount": len(warnings),
        "ocrPages": ocr_pages,
        "warnings": warnings,
        "sections": sections,
        "message": message,
    }


def _prepare_split(conversion: dict) -> dict:
    markdown_path = Path(conversion["markdownPath"])
    task_dir = Path(conversion["outputDir"])
    manifest = json.loads(Path(conversion["manifestPath"]).read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    normalized, headings = normalize_markdown(markdown)
    normalized_path = task_dir / "document.normalized.md"
    normalized_path.write_text(normalized, encoding="utf-8")

    body, compacted = compact_markdown(
        normalized,
        headings,
        get_settings().split_max_chars,
    )
    prompt = build_user_prompt(
        document_name=manifest["source"]["name"],
        source_format=str(manifest["source"]["format"]),
        outline=outline_text(headings),
        body=body,
        compacted=compacted,
    )
    return {
        "taskDir": task_dir,
        "manifest": manifest,
        "normalizedPath": normalized_path,
        "headings": headings,
        "prompt": prompt,
        "compacted": compacted,
    }


async def _semantic_split(conversion: dict, progress) -> dict:
    progress(40, "语义分块", "正在准备标题树并调用拆分模型")
    prepared = await asyncio.to_thread(_prepare_split, conversion)
    warnings: list[str] = []
    if prepared["compacted"]:
        warnings.append("正文超过长度上限，已使用章节摘要进行语义分块")

    prompt = prepared["prompt"]
    raw = None
    last_error: Exception | None = None
    for attempt in range(2):
        raw = await llm.complete_json(SYSTEM_PROMPT, prompt)
        try:
            chunks_data = build_chunks(
                raw,
                prepared["headings"],
                document_name=prepared["manifest"]["source"]["name"],
                source_format=str(prepared["manifest"]["source"]["format"]),
                input_path=conversion["markdownPath"],
                warnings=warnings,
            )
            break
        except SplitError as exc:
            last_error = exc
            prompt = (
                prepared["prompt"]
                + f"\n\n上一次输出存在问题：{exc}。请修正 section_id、requirement_path 或 chunk 结构后重新输出完整 JSON。"
            )
    else:
        raise SplitError(f"模型输出无法通过校验：{last_error}")

    progress(70, "物理拆分", "正在按 section_id 拆分并组织需求目录")
    result = await asyncio.to_thread(
        run_pipeline,
        task_dir=prepared["taskDir"],
        markdown_path=prepared["normalizedPath"],
        chunks_data=chunks_data,
        manifest=prepared["manifest"],
    )
    progress(95, "生成报告", "正在汇总需求、图片和 OCR 结果")
    combined_warnings = warnings + result["validationWarnings"] + result["assetWarnings"]
    return {
        "splitStatus": "completed",
        "treeDir": result["treeDir"],
        "chunksPath": result["chunksPath"],
        "reportPath": result["reportPath"],
        "finalPath": result["finalPath"],
        "normalizedMarkdownPath": str(prepared["normalizedPath"]),
        "requirementCount": result["requirementCount"],
        "chunkCount": result["chunkCount"],
        "splitWarningCount": len(combined_warnings),
        "splitWarnings": combined_warnings,
        "deliverableFiles": result["files"],
        "copiedAssetCount": result["copiedAssetCount"],
        "unmappedAssetCount": result["unmappedAssetCount"],
        "danglingImageCount": result["danglingImageCount"],
        "assetReportPath": result["assetReportPath"],
    }


class RequirementsSplitAgent(XAgent):
    async def run(self, payload: dict) -> dict:
        callback = payload.get("_progress")

        def progress(value: int, stage: str, message: str = "") -> None:
            if callable(callback):
                try:
                    callback(value, stage, message)
                except Exception:  # noqa: BLE001
                    pass

        conversion = await asyncio.to_thread(_convert_sync, payload)
        if payload.get("split") is False or str(payload.get("mode") or "").lower() == "convert":
            conversion.update(
                {
                    "splitStatus": "skipped",
                    "treeDir": "",
                    "chunksPath": "",
                    "reportPath": "",
                    "finalPath": "",
                    "normalizedMarkdownPath": "",
                    "requirementCount": 0,
                    "chunkCount": 0,
                    "splitWarningCount": 0,
                    "splitWarnings": [],
                    "splitError": "",
                    "deliverableFiles": [],
                    "copiedAssetCount": 0,
                    "unmappedAssetCount": 0,
                    "danglingImageCount": 0,
                    "assetReportPath": "",
                }
            )
            return conversion

        try:
            split = await _semantic_split(conversion, progress)
        except Exception as exc:  # noqa: BLE001
            logger.exception("语义拆分失败：%s", exc)
            split = {
                "splitStatus": "failed",
                "splitError": f"{type(exc).__name__}: {exc}",
                "treeDir": "",
                "chunksPath": "",
                "reportPath": "",
                "finalPath": "",
                "normalizedMarkdownPath": "",
                "requirementCount": 0,
                "chunkCount": 0,
                "splitWarningCount": 0,
                "splitWarnings": [],
                "deliverableFiles": [],
                "copiedAssetCount": 0,
                "unmappedAssetCount": 0,
                "danglingImageCount": 0,
                "assetReportPath": "",
            }
        conversion.update(split)

        if split["splitStatus"] == "completed":
            conversion["message"] += (
                f" 已拆分出 {split['requirementCount']} 条功能需求、"
                f"{split['chunkCount']} 个 chunk，目录：{split['treeDir']}。"
            )
            if split.get("finalPath"):
                conversion["message"] += f" 完整汇总：{split['finalPath']}。"
            if split["copiedAssetCount"]:
                conversion["message"] += f" 已跟随 {split['copiedAssetCount']} 张图片。"
            if split["danglingImageCount"]:
                conversion["message"] += f" 有 {split['danglingImageCount']} 个图片引用无法解析。"
            if split["splitWarningCount"]:
                conversion["message"] += f" 拆分阶段有 {split['splitWarningCount']} 条告警，详见 split_report.json。"
        elif split["splitStatus"] == "failed":
            conversion["message"] += f" 语义拆分失败：{split['splitError']}"
        return conversion


AGENT = RequirementsSplitAgent(NAME, DESCRIPTION)
