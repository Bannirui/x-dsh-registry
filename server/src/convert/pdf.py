"""PDF -> Markdown。

优先使用文本层；文本覆盖率过低的页面写入 ocr_pages，等待后续 OCR/多模态阶段处理。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..ocr import OcrContext, OcrResult, build_ocr_context, effective_concurrency, ocr_page
from .base import ConversionError, ConversionResult, SectionTracker

IMAGE_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
IMAGE_HTML = re.compile(r"<img[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
PAGE_SEPARATOR = re.compile(r"^---\s*end of page=\d+\s*---$", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,5})\s+(\S.{0,79})$")
LOW_TEXT_CHARS = 30


def convert_pdf(source: Path, assets_dir: Path, *, ocr_ctx: OcrContext | None = None) -> ConversionResult:
    try:
        import pymupdf4llm
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
        raise ConversionError("缺少 pymupdf4llm 依赖，请先执行 ./build.sh") from exc

    # 使用成熟的 rag 路径，避免 layout 模型首次运行时额外下载模型。
    try:
        pymupdf4llm.use_layout(False)
    except Exception:
        pass

    try:
        pages = pymupdf4llm.to_markdown(
            str(source),
            page_chunks=True,
            page_separators=True,
            write_images=True,
            image_path=str(assets_dir),
            image_format="png",
            filename=source.stem,
            table_output="markdown",
        )
    except Exception as exc:
        return _fallback_pdf(source, assets_dir, exc)

    if isinstance(pages, str):
        pages = [{"text": pages, "metadata": {"page": 1}, "images": []}]

    tracker = SectionTracker()
    chunks: list[str] = []
    ocr_pages: list[dict] = []
    warnings: list[str] = []
    assets: list[dict] = []
    seen_assets: set[str] = set()
    if ocr_ctx is None:
        ocr_ctx = build_ocr_context()

    try:
        import pymupdf

        document = pymupdf.open(str(source))
    except Exception:
        document = None

    ocr_map = _prefetch_ocr(pages, ocr_ctx, document)

    try:
        for index, page in enumerate(pages, start=1):
            page_number = int((page.get("metadata") or {}).get("page") or index)
            original_text = page.get("text") or ""
            entry = ocr_map.get(page_number)
            if entry is None:
                text = original_text
            else:
                text = entry["text"]
                ocr_pages.append(entry["page_info"])
                warnings.extend(entry["warnings"])

            current_section = ""
            rendered: list[str] = []

            for line in text.splitlines():
                if PAGE_SEPARATOR.match(line.strip()):
                    continue
                heading = HEADING.match(line)
                if heading:
                    heading_line, section = tracker.heading(
                        len(heading.group(1)), heading.group(2), page=page_number
                    )
                    current_section = section["section_id"]
                    rendered.append(heading_line)
                    continue
                numbered = NUMBERED_HEADING.match(line.strip())
                if numbered and _looks_like_heading(numbered.group(2)):
                    heading_line, section = tracker.heading(
                        len(numbered.group(1).split(".")), line.strip(), page=page_number
                    )
                    current_section = section["section_id"]
                    rendered.append(heading_line)
                    continue

                rewritten = line
                for pattern in (IMAGE_MD, IMAGE_HTML):
                    for match in list(pattern.finditer(line)):
                        reference = match.group(1)
                        relative = _relative_asset(reference, assets_dir)
                        if relative is None:
                            warnings.append(f"第 {page_number} 页图片引用无法解析：{reference}")
                            continue
                        rewritten = rewritten.replace(reference, relative)
                        if relative not in seen_assets:
                            seen_assets.add(relative)
                            assets.append(
                                {
                                    "id": f"img_{len(assets) + 1:03d}",
                                    "path": relative,
                                    "source": reference,
                                    "section_id": current_section,
                                    "page": page_number,
                                    "markdown_ref": relative,
                                }
                            )
                rendered.append(rewritten)

            chunks.append("\n".join(rendered).strip())
    finally:
        if document is not None:
            document.close()

    markdown = "\n\n".join(chunk for chunk in chunks if chunk)
    if not markdown.strip():
        return _fallback_pdf(source, assets_dir, ConversionError("未提取到任何文本"))

    return ConversionResult(
        markdown=markdown,
        format="pdf",
        sections=tracker.sections,
        assets=assets,
        ocr_pages=ocr_pages,
        warnings=warnings,
    )


def _prefetch_ocr(pages: list[dict], ocr_ctx: OcrContext, document) -> dict[int, dict]:
    low_pages: list[tuple[int, str]] = []
    for index, page in enumerate(pages, start=1):
        page_number = int((page.get("metadata") or {}).get("page") or index)
        original_text = page.get("text") or ""
        if len(_plain_text(original_text)) < LOW_TEXT_CHARS:
            low_pages.append((page_number, original_text))
    if not low_pages:
        return {}

    results: dict[int, dict] = {}
    if not ocr_ctx.enabled:
        for page_number, original_text in low_pages:
            info = {"page": page_number, "reason": "文本层为空或过少", "status": "pending", "profile": ocr_ctx.profile_id, "mode": ocr_ctx.mode, "error": "OCR 未启用"}
            results[page_number] = {
                "text": original_text,
                "page_info": info,
                "warnings": [f"第 {page_number} 页文本过少，OCR 未启用，已保留原始图片"],
            }
        return results

    if ocr_ctx.provider is None:
        error = ocr_ctx.error or "OCR 未启用或未配置"
        for page_number, original_text in low_pages:
            info = {"page": page_number, "reason": "文本层为空或过少", "status": "failed", "profile": ocr_ctx.profile_id, "mode": ocr_ctx.mode, "error": error}
            results[page_number] = {
                "text": original_text,
                "page_info": info,
                "warnings": [f"第 {page_number} 页 OCR 失败：{error}，已保留原始图片"],
            }
        return results

    max_pages = ocr_ctx.max_pages
    if max_pages > 0 and len(low_pages) > max_pages:
        for page_number, original_text in low_pages[max_pages:]:
            info = {
                "page": page_number,
                "reason": "文本层为空或过少",
                "status": "skipped",
                "profile": ocr_ctx.profile_id,
                "mode": ocr_ctx.mode,
                "error": f"超过单文档 OCR 页数上限 {max_pages}",
            }
            results[page_number] = {
                "text": original_text,
                "page_info": info,
                "warnings": [f"第 {page_number} 页跳过 OCR：{info['error']}"],
            }
        low_pages = low_pages[:max_pages]

    jobs: list[tuple[int, str, bytes]] = []
    for page_number, original_text in low_pages:
        image_bytes = _render_page(document, page_number, ocr_ctx.dpi)
        if not image_bytes:
            info = {"page": page_number, "reason": "文本层为空或过少", "status": "failed", "profile": ocr_ctx.profile_id, "mode": ocr_ctx.mode, "error": "页面渲染失败"}
            results[page_number] = {
                "text": original_text,
                "page_info": info,
                "warnings": [f"第 {page_number} 页 OCR 失败：页面渲染失败"],
            }
            continue
        jobs.append((page_number, original_text, image_bytes))

    if not jobs:
        return results

    workers = effective_concurrency(ocr_ctx)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(ocr_page, ocr_ctx, image_bytes, page=page_number): (page_number, original_text)
            for page_number, original_text, image_bytes in jobs
        }
        for future in as_completed(futures):
            page_number, original_text = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = OcrResult(
                    text="",
                    provider="unknown",
                    error=f"OCR 线程异常：{type(exc).__name__}: {exc}",
                )
            results[page_number] = _ocr_entry(original_text, page_number, result, ocr_ctx)
    return results


def _ocr_entry(original_text: str, page_number: int, result: OcrResult, ocr_ctx: OcrContext) -> dict:
    info = {
        "page": page_number,
        "reason": "文本层为空或过少",
        "status": "completed" if result.ok else "failed",
        "profile": ocr_ctx.profile_id,
        "mode": ocr_ctx.mode,
        "provider": result.provider,
        "model": result.model,
        "chars": len(result.text),
        "duration": round(result.duration, 3),
        "confidence": round(result.confidence, 4) if result.confidence is not None else None,
        "attempts": result.attempts,
    }
    if result.error:
        info["error"] = result.error
    if not result.ok:
        return {
            "text": original_text,
            "page_info": info,
            "warnings": [
                f"第 {page_number} 页 OCR 失败：{result.error or '未识别到文字'}，已保留原始图片"
            ],
        }

    refs = _image_references(original_text)
    marker = (
        f"<!-- OCR page {page_number} provider={result.provider}"
        f" model={result.model or '-'} -->"
    )
    text = marker + "\n\n" + result.text.strip()
    if refs:
        text = text + "\n\n" + "\n".join(refs)
    return {"text": text, "page_info": info, "warnings": []}


def _plain_text(text: str) -> str:
    plain = PAGE_SEPARATOR.sub("", text)
    plain = IMAGE_MD.sub("", plain)
    plain = IMAGE_HTML.sub("", plain)
    plain = re.sub(r"<[^>]+>", "", plain)
    return re.sub(r"\s+", "", plain)


def _image_references(text: str) -> list[str]:
    refs: list[str] = []
    for pattern in (IMAGE_MD, IMAGE_HTML):
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in refs:
                refs.append(value)
    return refs


def _render_page(document, page_number: int, dpi: int) -> bytes:
    if document is None:
        return b""
    try:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=max(72, dpi))
        return pixmap.tobytes("png")
    except Exception:
        return b""


def _looks_like_heading(title: str) -> bool:
    text = title.strip()
    if not text or "|" in text or text.startswith(("-", "*", "•")):
        return False
    return not text.endswith(("。", ".", ";", "；", ",", "，", ":", "："))


def _relative_asset(reference: str, assets_dir: Path) -> str | None:
    try:
        path = Path(reference)
        if not path.is_absolute():
            path = assets_dir / path
        path = path.resolve()
        assets_root = assets_dir.resolve()
        if not path.is_file() or assets_root not in path.parents:
            return None
        task_dir = assets_root.parent.parent
        return path.relative_to(task_dir).as_posix()
    except (OSError, ValueError):
        return None


def _fallback_pdf(source: Path, assets_dir: Path, error: Exception) -> ConversionResult:
    """pymupdf4llm 不可用时退回 PyMuPDF 纯文本提取。"""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise ConversionError(f"PDF 转换失败：{error}") from exc

    try:
        document = pymupdf.open(str(source))
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"PDF 打开失败：{exc}") from exc

    tracker = SectionTracker()
    chunks: list[str] = []
    warnings = [f"PDF 结构化转换失败，已降级为纯文本：{type(error).__name__}: {error}"]
    try:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if not text:
                warnings.append(f"第 {index} 页没有可提取文本，后续需要 OCR")
                continue
            chunks.append(f"<!-- page {index} -->\n\n{text}")
    finally:
        document.close()

    markdown = "\n\n".join(chunks)
    if not markdown.strip():
        raise ConversionError("PDF 没有可提取文本，且 OCR 阶段尚未启用")
    return ConversionResult(
        markdown=markdown,
        format="pdf",
        sections=tracker.sections,
        assets=[],
        warnings=warnings,
    )
