"""DOCX -> Markdown，保留标题、段落、表格和图片引用。"""

from __future__ import annotations

import html
import re
from pathlib import Path

from .base import ConversionError, ConversionResult, SectionTracker

VML_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"


def convert_docx(source: Path, assets_dir: Path) -> ConversionResult:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise ConversionError("缺少 python-docx 依赖，请先执行 ./build.sh") from exc

    try:
        document = Document(str(source))
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"DOCX 打开失败：{exc}") from exc

    tracker = SectionTracker()
    blocks: list[str] = []
    assets: list[dict] = []
    warnings: list[str] = []
    current_section = ""

    state = {
        "document": document,
        "assets_dir": assets_dir,
        "assets": assets,
        "warnings": warnings,
        "seen_rid": {},
        "counter": 0,
    }

    def save_image(paragraph) -> list[str]:
        references: list[str] = []
        for blip in paragraph._p.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
            if rid:
                reference = _save_related_image(state, rid, current_section)
                if reference:
                    references.append(reference)
        for image in paragraph._p.iter(VML_IMAGEDATA):
            if rid:
                reference = _save_related_image(state, rid, current_section)
                if reference:
                    references.append(reference)
        return references

    for block in _iter_blocks(document, Paragraph, Table, qn):
        if isinstance(block, Table):
            blocks.append(_table_to_html(block, save_image))
            blocks.append("")
            continue

        level = _heading_level(block)
        text = (block.text or "").strip()
        if level:
            if not text:
                continue
            heading_line, section = tracker.heading(level, text)
            current_section = section["section_id"]
            blocks.append(heading_line)
            blocks.append("")
            continue

        images = save_image(block)
        for reference in images:
            blocks.append(f"![图片]({reference})")
        if text:
            prefix = _list_prefix(block.style.name if block.style else "")
            blocks.append(f"{prefix}{text}" if prefix else text)
            blocks.append("")

    markdown = "\n".join(blocks).strip()
    if not markdown:
        warnings.append("DOCX 未提取到正文内容")

    return ConversionResult(
        markdown=markdown,
        format="docx",
        sections=tracker.sections,
        assets=assets,
        warnings=warnings,
    )


def _iter_blocks(document, paragraph_cls, table_cls, qn):
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield paragraph_cls(child, document)
        elif child.tag == qn("w:tbl"):
            yield table_cls(child, document)


def _heading_level(paragraph) -> int:
    style = paragraph.style.name if paragraph.style is not None else ""
    matched = re.match(r"^(?:heading|标题)\s*([1-6])$", style.strip(), re.IGNORECASE)
    if matched:
        return int(matched.group(1))
    if style.strip().lower() in {"title", "标题"}:
        return 1
    properties = paragraph._p.pPr
    if properties is not None and properties.outlineLvl is not None:
        try:
            return max(1, min(6, int(properties.outlineLvl.val) + 1))
        except (TypeError, ValueError):
            return 0
    return 0


def _list_prefix(style: str) -> str:
    lowered = (style or "").lower()
    if "list bullet" in lowered or "列表段落" in lowered:
        return "- "
    if "list number" in lowered or "列表编号" in lowered:
        return "1. "
    return ""


def _save_related_image(state: dict, rid: str, section_id: str) -> str | None:
    try:
        part = state["document"].part.related_parts[rid]
        blob = part.blob
    except (KeyError, AttributeError):
        state["warnings"].append(f"图片关系 {rid} 无法读取")
        return None

    if rid in state["seen_rid"]:
        reference = state["seen_rid"][rid]
    else:
        state["counter"] += 1
        extension = _image_extension(part)
        filename = f"img_{state['counter']:03d}.{extension}"
        path = state["assets_dir"] / filename
        path.write_bytes(blob)
        reference = path.relative_to(state["assets_dir"].parent.parent).as_posix()
        state["seen_rid"][rid] = reference

    state["assets"].append(
        {
            "id": f"img_{len(state['assets']) + 1:03d}",
            "path": reference,
            "source": str(part.partname),
            "section_id": section_id,
            "markdown_ref": reference,
        }
    )
    return reference


def _image_extension(part) -> str:
    partname = str(getattr(part, "partname", ""))
    suffix = Path(partname).suffix.lstrip(".").lower()
    if suffix in {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "svg", "webp"}:
        return "jpg" if suffix == "jpeg" else suffix
    content_type = str(getattr(part, "content_type", ""))
    if "jpeg" in content_type:
        return "jpg"
    matched = re.search(r"image/([a-zA-Z0-9.+-]+)", content_type)
    if matched:
        candidate = matched.group(1).lower()
        if candidate in {"png", "gif", "bmp", "tiff", "svg", "webp"}:
            return candidate
    return "png"


def _table_to_html(table, save_image) -> str:
    rows: list[str] = []
    for row_index, row in enumerate(table.rows):
        cells: list[str] = []
        for cell in row.cells:
            fragments: list[str] = []
            for paragraph in cell.paragraphs:
                for reference in save_image(paragraph):
                    fragments.append(f'<img src="{html.escape(reference)}" alt="图片" />')
                text = (paragraph.text or "").strip()
                if text:
                    fragments.append(html.escape(text))
            value = "<br />".join(fragments) or "&nbsp;"
            tag = "th" if row_index == 0 else "td"
            cells.append(f"<{tag}>{value}</{tag}>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"
