"""PDF -> Markdown。

优先使用文本层；文本覆盖率过低的页面写入 ocr_pages，等待后续 OCR/多模态阶段处理。
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import ConversionError, ConversionResult, SectionTracker

IMAGE_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
IMAGE_HTML = re.compile(r"<img[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
PAGE_SEPARATOR = re.compile(r"^---\s*end of page=\d+\s*---$", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,5})\s+(\S.{0,79})$")
LOW_TEXT_CHARS = 30


def convert_pdf(source: Path, assets_dir: Path) -> ConversionResult:
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

    for index, page in enumerate(pages, start=1):
        page_number = int((page.get("metadata") or {}).get("page") or index)
        text = page.get("text") or ""
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

        plain_text = PAGE_SEPARATOR.sub("", text)
        plain_text = IMAGE_MD.sub("", plain_text)
        plain_text = IMAGE_HTML.sub("", plain_text)
        plain_text = re.sub(r"<[^>]+>", "", plain_text)
        plain = re.sub(r"\s+", "", plain_text)
        if len(plain) < LOW_TEXT_CHARS:
            reason = "文本层为空或过少"
            ocr_pages.append({"page": page_number, "reason": reason})
            warnings.append(f"第 {page_number} 页{reason}，当前仅完成文本层转换，后续需要 OCR/多模态识别")

        chunks.append("\n".join(rendered).strip())

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
