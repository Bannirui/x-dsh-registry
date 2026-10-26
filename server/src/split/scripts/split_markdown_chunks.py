import re, json, sys
from pathlib import Path

SECTION_ID = re.compile(r"^(\d+(?:\.\d+)*)[\s._、-]*(.*)$")

def normalize_title(value):
    value = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", "", value).strip().strip("*#")

def parse_heading(raw, position, level):
    text = normalize_title(raw)
    matched = SECTION_ID.match(text)
    return {
        "position": position,
        "level": level,
        "raw": raw,
        "section_id": matched.group(1) if matched else "",
        "title": normalize_title(matched.group(2)) if matched else text,
    }

def find_headings(content):
    headings = []
    pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    for match in pattern.finditer(content):
        headings.append(parse_heading(match.group(2), match.start(), len(match.group(1))))
    return headings

def resolve_heading(segment, headings):
    section_id = str(segment.get("section_id") or "").strip()
    title = normalize_title(segment.get("title") or "")
    candidates = headings
    if section_id:
        candidates = [item for item in candidates if item["section_id"] == section_id]
    if title:
        candidates = [item for item in candidates if item["title"] == title or normalize_title(item["raw"]) == title]
    if len(candidates) != 1:
        descriptor = f"section_id={section_id!r}, title={title!r}"
        raise ValueError(f"物理片段无法唯一定位：{descriptor}，候选数={len(candidates)}")
    return candidates[0]

def segment_bounds(segment, headings, content_length):
    start_h = resolve_heading(segment, headings)
    start = start_h["position"]
    end_marker = segment.get("end_before_section_id") or segment.get("end_before_heading")
    if end_marker:
        marker = normalize_title(str(end_marker))
        candidates = [h for h in headings if h["position"] > start and 
                      (h["section_id"] == marker or h["title"] == marker or normalize_title(h["raw"]) == marker)]
        if len(candidates) != 1:
            raise ValueError(f"结束边界无法唯一定位：{end_marker!r}，候选数={len(candidates)}")
        end = candidates[0]["position"]
    else:
        # Find next heading that is NOT a child (for flat MinerU headings like ## 8.1 under ## 8)
        start_sid = start_h["section_id"]
        end = content_length
        for h in headings:
            if h["position"] > start and h["level"] <= start_h["level"]:
                # Check if h is a child of start_h (e.g., "8.1" is child of "8")
                child_prefix = start_sid + "."
                if h["section_id"].startswith(child_prefix):
                    continue  # This is a child subsection, skip
                end = h["position"]
                break
    return start, end

def safe_filename(value):
    return re.sub(r'[\\/:*?"<>|]', "_", value)[:80]

def main():
    if len(sys.argv) != 4:
        print("用法: python split_markdown_chunks.py <markdown_path> <chunks_json> <output_dir>")
        raise SystemExit(1)
    markdown_path, chunks_path, output_dir = map(Path, sys.argv[1:])
    content = markdown_path.read_text(encoding="utf-8-sig")
    data = json.loads(chunks_path.read_text(encoding="utf-8-sig"))
    chunks = data.get("chunks", [])
    headings = find_headings(content)
    if not headings:
        raise ValueError("未检测到 Markdown 标题，无法执行精确拆分")
    
    planned = []
    for chunk in chunks:
        segments = chunk.get("physical_segments")
        if not segments:
            raise ValueError(f"{chunk.get('chunk_id')}: 缺少 physical_segments")
        bounds = []
        for segment in segments:
            start, end = segment_bounds(segment, headings, len(content))
            bounds.append((start, end))
            planned.append({"chunk_id": chunk["chunk_id"], "start": start, "end": end, "copy": bool(segment.get("copy", False))})
        chunk["_resolved_bounds"] = bounds

    # Validate no overlapping segments
    ordered = sorted(planned, key=lambda item: (item["start"], item["end"]))
    for i, cur in enumerate(ordered):
        for other in ordered[i+1:]:
            if other["start"] >= cur["end"]:
                break
            if cur["copy"] or other["copy"]:
                continue
            raise ValueError(f"物理片段重叠：{cur['chunk_id']} 与 {other['chunk_id']}。请提供精确 end_before_section_id。")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    for chunk in chunks:
        bounds = chunk.pop("_resolved_bounds")
        parts = [content[start:end].strip() for start, end in bounds]
        filename = f"{chunk['chunk_id']}_{chunk['chunk_type']}_{safe_filename(chunk.get('title', ''))}.md"
        total_chars = sum(end - start for start, end in bounds)
        (output_dir / filename).write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8")
        print(f"[OK] {filename}: {len(parts)} 个物理片段, {total_chars} 字符")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
