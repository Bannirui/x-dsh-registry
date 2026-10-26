"""Markdown 标题树解析与规范化。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..convert.base import SectionTracker, clean_heading

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
SECTION_ID_RE = re.compile(r"^(\d+(?:\.\d+)*)[\s._、-]*(.*)$")


@dataclass
class Heading:
    section_id: str
    title: str
    level: int
    start: int
    end: int
    subtree_end: int
    own_end: int
    text: str
    own_text: str

    @property
    def has_body(self) -> bool:
        lines = self.own_text.splitlines()
        body = "\n".join(lines[1:]) if lines else ""
        return bool(body.strip())


def normalize_markdown(markdown: str) -> tuple[str, list[Heading]]:
    """把标题统一成 ``# <number> <title>``，保证后续脚本能按编号定位。"""
    source = markdown.strip()
    if not source:
        return "# 1 全文\n", parse_outline("# 1 全文\n")

    tracker = SectionTracker()
    parts: list[str] = []
    last = 0
    found = False
    for match in HEADING_RE.finditer(source):
        parts.append(source[last : match.start()])
        _, section = tracker.heading(len(match.group(1)), match.group(2))
        parts.append(f"{'#' * section['level']} {section['section_id']} {section['title']}")
        last = match.end()
        found = True
    parts.append(source[last:])

    normalized = "".join(parts).strip()
    if not found:
        normalized = f"# 1 全文\n\n{source}"
    return normalized + "\n", parse_outline(normalized + "\n")


def parse_outline(markdown: str) -> list[Heading]:
    matches = list(HEADING_RE.finditer(markdown))
    headings: list[Heading] = []
    for index, match in enumerate(matches):
        raw = clean_heading(match.group(2))
        parsed = SECTION_ID_RE.match(raw)
        section_id = parsed.group(1) if parsed else ""
        title = (parsed.group(2).strip() if parsed else raw) or raw
        level = len(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        subtree_end = len(markdown)
        for later in matches[index + 1 :]:
            if len(later.group(1)) <= level:
                subtree_end = later.start()
                break
        own_end = end
        if index + 1 < len(matches) and len(matches[index + 1].group(1)) > level:
            own_end = matches[index + 1].start()
        headings.append(
            Heading(
                section_id=section_id,
                title=title,
                level=level,
                start=match.start(),
                end=end,
                subtree_end=subtree_end,
                own_end=own_end,
                text=markdown[match.start() : subtree_end].strip(),
                own_text=markdown[match.start() : own_end].strip(),
            )
        )
    return headings


def outline_text(headings: list[Heading]) -> str:
    return "\n".join(
        f"{'  ' * (heading.level - 1)}- section_id={heading.section_id} level={heading.level} title={heading.title}"
        for heading in headings
    )


def compact_markdown(markdown: str, headings: list[Heading], max_chars: int) -> tuple[str, bool]:
    """文档过长时按章节截取摘要，保留完整标题树。"""
    if max_chars <= 0 or len(markdown) <= max_chars:
        return markdown, False

    parts = ["# 文档标题树", outline_text(headings), "", "# 章节内容摘要"]
    used = sum(len(part) + 1 for part in parts)
    truncated = False
    for heading in headings:
        body = heading.own_text.strip()
        if len(body) > 900:
            body = body[:700] + "\n……\n" + body[-200:]
        block = f"\n## {heading.section_id} {heading.title}\n{body}"
        if used + len(block) > max_chars:
            truncated = True
            break
        parts.append(block)
        used += len(block)
    if truncated:
        parts.append("\n\n[提示] 文档过长，后续章节仅保留标题；请仅依据已给出的标题树和摘要分配 section_id。")
    return "\n".join(parts), True
