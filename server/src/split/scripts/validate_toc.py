"""
validate_toc.py — 校验分块输出的 .md 文件是否与原文档目录结构一致

用法: python scripts/validate_toc.py <output_dir>

校验逻辑:
1. 从 chunks.json 提取所有 source_sections（即原文档目录条目）
2. 遍历 output_dir 中所有 .md 输出文件（排除原始 PDF、MinerU、chunks.json）
3. 按章节编号匹配，报告差异
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def parse_toc_from_chunks(chunks: List[dict]) -> List[Tuple[str, str, str]]:
    """
    从 chunks.json 提取文档目录结构。
    返回 [(section_id, section_title, chunk_id)]
    """
    toc = []
    seen = set()
    for chunk in chunks:
        cid = chunk["chunk_id"]
        for sec in chunk.get("source_sections", []):
            sid = sec.get("section_id", "")
            stitle = sec.get("title", "")
            key = (sid, stitle)
            if key not in seen or (sid and stitle):
                seen.add(key)
                toc.append((sid, stitle, cid))
    return toc


def scan_output_files(output_dir: Path) -> Dict[str, list]:
    """
    扫描输出目录中的所有 .md 文件（排除原始文件）。
    返回 {section_id: [{"path": ..., "title": ..., "file_name": ...}, ...]}
    支持重复 section_id（MinerU 编号冲突时）。
    """
    exclude_patterns = [
        r"^MinerU_markdown_.*\.md$",
        r"^fixed_MinerU_markdown_.*\.md$",
        r"^chunks\.json$",
    ]

    result = {}

    for f in sorted(output_dir.rglob("*.md")):
        rel = f.relative_to(output_dir)
        name = f.stem  # 不含扩展名

        # 跳过原始文件
        skip = False
        for pat in exclude_patterns:
            if re.match(pat, f.name):
                skip = True
                break
        if skip:
            continue

        # 跳过 chunk_ 前缀的残留文件
        if re.match(r'^chunk_\d+_', f.name):
            continue

        # 解析出 section_id
        m = re.match(r'^([\d.]+)(.+)$', name)
        if m:
            sid = m.group(1)
            title = m.group(2)
            entry = {"path": str(rel), "title": title, "file_name": f.name}
            result.setdefault(sid, []).append(entry)
        else:
            result.setdefault(f"__extra__{name}", []).append(
                {"path": str(rel), "title": name, "file_name": f.name}
            )

    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/validate_toc.py <output_dir>")
        sys.exit(1)

    output_dir = Path(sys.argv[1])
    if not output_dir.exists():
        print(f"[ERROR] 目录不存在: {output_dir}")
        sys.exit(1)

    chunks_json = output_dir / "chunks.json"
    if not chunks_json.exists():
        print(f"[ERROR] chunks.json 不存在: {chunks_json}")
        sys.exit(1)

    with open(chunks_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("chunks", [])

    # 合并章节没有独立输出文件，它们的内容已经写入 primary chunk 文件。
    merged_sections = set()
    for chunk in chunks:
        for section in chunk.get("merged_from", []) or []:
            if isinstance(section, dict) and section.get("section_id"):
                merged_sections.add(section["section_id"])
        for section in chunk.get("source_sections", []) or []:
            if isinstance(section, dict) and section.get("role") == "merged" and section.get("section_id"):
                merged_sections.add(section["section_id"])

    # --- 1. 提取原文档目录 ---
    toc = [
        entry for entry in parse_toc_from_chunks(chunks)
        if entry[0] not in merged_sections
    ]
    print(f"\n{'='*60}")
    print(f"  原文档目录结构（共 {len(toc)} 个条目）")
    print(f"{'='*60}")
    for sid, stitle, cid in toc:
        print(f"  {sid:8s} {stitle}")
    print()

    # --- 2. 扫描输出文件 ---
    output_files = scan_output_files(output_dir)
    total_files = sum(len(v) for v in output_files.values())
    print(f"{'='*60}")
    print(f"  输出文件结构（共 {total_files} 个）")
    print(f"{'='*60}")
    for sid in sorted(output_files.keys()):
        if sid.startswith("__extra__"):
            for entry in output_files[sid]:
                print(f"  [?] {entry['path']}")
        else:
            for entry in output_files[sid]:
                print(f"  {sid:8s} -> {entry['path']}")
    print()

    # --- 3. 对比校验 ---
    print(f"{'='*60}")
    print(f"  校验结果")
    print(f"{'='*60}")

    errors = []
    warnings = []
    matched = 0

    # 按 section_id 分组 toc 条目（可能有重复编号）
    toc_by_sid: Dict[str, List[Tuple[str, str]]] = {}
    for sid, stitle, cid in toc:
        toc_by_sid.setdefault(sid, []).append((stitle, cid))

    for sid, entries in sorted(toc_by_sid.items()):
        if sid in output_files:
            out_entries = output_files[sid]
            expected_titles = [e[0] for e in entries]

            # 输出文件应覆盖所有同编号的章节
            matched_here = 0
            for out in out_entries:
                actual_title = out["title"]
                title_ok = any(
                    expected.replace(" ", "") == actual_title.replace(" ", "")
                    or expected in actual_title
                    or actual_title in expected
                    for expected in expected_titles
                )
                if title_ok:
                    matched_here += 1
                    matched += 1
                    print(f"  [OK] {sid} {actual_title} -> {out['path']}")
                else:
                    warnings.append(f"{sid}: {actual_title} 无匹配章节")
                    print(f"  [WARN] {sid} {actual_title} -> 无匹配章节")

            # 检查是否有 TOC 条目未匹配到输出文件
            unmatched_toc = len(entries) - matched_here
            if unmatched_toc > 0:
                expected_str = "、".join(expected_titles)
                warnings.append(f"{sid}: 有 {unmatched_toc} 个章节未生成独立文件 ({expected_str})")
                print(f"  [WARN] {sid} '{expected_str}': 缺少 {unmatched_toc} 个文件")
        else:
            errors.append(f"{sid} {'/'.join(e[0] for e in entries)}: 未找到对应输出文件")
            print(f"  [MISS] {sid} {'/'.join(e[0] for e in entries)}: 缺失")

    # 检查额外文件
    for sid in sorted(output_files.keys()):
        if sid.startswith("__extra__"):
            for entry in output_files[sid]:
                warnings.append(f"额外文件: {entry['path']}")
                print(f"  [?] 额外文件: {entry['path']}")

    print()
    print(f"{'='*60}")
    print(f"  总结")
    print(f"{'='*60}")
    print(f"  目录条目: {len(toc)}")
    print(f"  输出文件: {total_files}")
    print(f"  匹配通过: {matched}")
    print(f"  警告: {len(warnings)}")
    print(f"  缺失: {len(errors)}")

    if errors:
        print(f"\n  [FAIL] 校验不通过 — {len(errors)} 个章节缺失")
        for e in errors:
            print(f"     • {e}")
        sys.exit(1)
    elif warnings:
        print(f"\n  [WARN] 校验通过，但有 {len(warnings)} 个警告")
        for w in warnings:
            print(f"     • {w}")
    else:
        print(f"\n  [PASS] 校验完全通过！目录结构与原文档一致")


if __name__ == "__main__":
    main()
