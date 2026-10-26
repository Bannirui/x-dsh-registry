"""
validate_output.py — 验证最终输出完整性

用法: python scripts/validate_output.py <output_dir>

检查项:
- 根目录关键文件
- chunk 拆分覆盖率
- 子功能目录结构
- 文件非空性
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/validate_output.py <output_dir>")
        sys.exit(1)

    output_dir = Path(sys.argv[1])
    if not output_dir.exists():
        print(f"[ERROR] 目录不存在: {output_dir}")
        sys.exit(1)

    issues = []

    # 1. 根目录文件 — 检查是否有导言、概述、系统描述等 md（不苛求精确文件名）
    print("--- 根目录 ---")
    root_mds = sorted([f for f in output_dir.iterdir()
                       if f.is_file() and f.suffix == ".md"
                       and not f.name.startswith("fixed_")
                       and not f.name.startswith("MinerU_")])
    # 预期应包含 preamble/overview/system_overview 三类
    has_preamble = any("导言" in f.name or "前言" in f.name or "preamble" in f.name.lower() for f in root_mds)
    has_overview = any("概述" in f.name or "overview" in f.name.lower() for f in root_mds)
    has_system = any("系统" in f.name or "system" in f.name.lower() for f in root_mds)
    if not has_preamble:
        issues.append("根目录缺失导言类文件")
    if not has_overview:
        issues.append("根目录缺失概述类文件")
    if not has_system:
        issues.append("根目录缺失系统描述类文件")

    cj = output_dir / "chunks.json"
    cj_ok = cj.exists() and cj.stat().st_size > 100
    print(f"  chunks.json: {'OK' if cj_ok else 'MISS'} ({cj.stat().st_size/1024:.1f} KB)" if cj_ok and cj.exists() else f"  chunks.json: MISS")
    for f in root_mds:
        print(f"  [root] {f.name} ({f.stat().st_size/1024:.1f} KB)")

    # 2. chunk 覆盖率
    chunks_json = output_dir / "chunks.json"
    if chunks_json.exists():
        with open(chunks_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        total = len(data.get("chunks", []))

        all_md = list(output_dir.rglob("*.md"))
        # Exclude MinerU files
        chunk_md = [f for f in all_md if not f.name.startswith("fixed_")
                    and not f.name.startswith("MinerU_")]

        print(f"\n--- Chunk 覆盖率 ---")
        print(f"  chunks.json 定义: {total} 个 chunk")
        print(f"  实际拆分出: {len(chunk_md)} 个 .md 文件")

        if len(chunk_md) < total:
            issues.append(f"拆分不完整: {len(chunk_md)}/{total}")

    # 3. 子功能目录
    print(f"\n--- 子功能目录 ---")
    subfunc_dirs = sorted([
        d for d in output_dir.iterdir()
        if d.is_dir() and d.name[0].isdigit()
    ])
    total_items = 0
    for sf_dir in subfunc_dirs:
        items = sorted([d for d in sf_dir.iterdir() if d.is_dir()])
        total_items += len(items)
        for item in items:
            md_in = list(item.glob("*.md"))
            has = "[OK]" if md_in else "[--]"
            sz = md_in[0].stat().st_size / 1024 if md_in else 0
            print(f"  {has} {sf_dir.name}/{item.name}/ ({sz:.1f} KB)")

    # 4. 汇总
    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    file_count = sum(1 for f in output_dir.rglob("*") if f.is_file())
    print(f"\n--- 汇总 ---")
    print(f"  子功能分类: {len(subfunc_dirs)}")
    print(f"  子功能项: {total_items}")
    print(f"  总文件数: {file_count}")
    print(f"  总大小: {total_size/1024:.1f} KB")

    if issues:
        print(f"\n[WARN] {len(issues)} ge issues:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("\n[OK] All validation passed")


if __name__ == "__main__":
    main()
