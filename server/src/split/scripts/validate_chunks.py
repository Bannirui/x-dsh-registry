"""
validate_chunks.py — 校验 chunks.json 完整性

用法: python scripts/validate_chunks.py <chunks_json_path>

校验项:
1. JSON 合法性
2. 必要顶层字段
3. chunk_id 唯一性
4. functional_requirement 的 subfunction 可选，但不参与需求层级
5. 新格式必须提供 physical_segments、需求路径和父子关系证据
6. source_sections/merged_from 必须有 dsl_dimension
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/validate_chunks.py <chunks_json_path>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[ERROR] 文件不存在: {path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON 解析失败: {e}")
            sys.exit(1)

    issues = []

    # 必要字段
    for field in ["document_info", "chunking_summary", "chunks"]:
        if field not in data:
            issues.append(f"缺失顶层字段: {field}")

    if "chunks" not in data:
        print(f"[FAIL] 无 chunks 字段，无法继续校验")
        sys.exit(1)

    # chunk_id 和规范功能名称唯一性
    seen_ids = set()
    requirement_names = set()
    for chunk in data["chunks"]:
        cid = chunk.get("chunk_id", "?")
        if cid in seen_ids:
            issues.append(f"重复 chunk_id: {cid}")
        seen_ids.add(cid)

        ctype = chunk.get("chunk_type", "")
        if "physical_segments" not in chunk:
            issues.append(f"chunk {cid}: 缺少 physical_segments")

        for segment in chunk.get("physical_segments", []):
            if not (segment.get("section_id") or segment.get("source_section_id")):
                issues.append(f"chunk {cid}: physical_segments 条目缺少 section_id")

        if ctype == "functional_requirement":
            name = str(chunk.get("canonical_function_name") or "").strip()
            if not name:
                issues.append(f"chunk {cid}: 缺少 canonical_function_name")
            elif name in requirement_names:
                issues.append(f"chunk {cid}: canonical_function_name 重复: {name}")
            else:
                requirement_names.add(name)

            if "parent_requirement" not in chunk:
                issues.append(f"chunk {cid}: 缺少 parent_requirement")
            path_items = chunk.get("requirement_path")
            if not isinstance(path_items, list) or not path_items:
                issues.append(f"chunk {cid}: requirement_path 必须为非空数组")
            elif not all(isinstance(item, dict) and item.get("section_id") and item.get("name") for item in path_items):
                issues.append(f"chunk {cid}: requirement_path 条目必须含 section_id 和 name")
            elif name and path_items[-1].get("name") != name:
                issues.append(f"chunk {cid}: requirement_path 最后一项必须等于 canonical_function_name")

            evidence = chunk.get("hierarchy_evidence")
            if not isinstance(evidence, list) or not evidence:
                issues.append(f"chunk {cid}: hierarchy_evidence 必须为非空数组")
            elif not all(isinstance(item, dict) and item.get("section_id") and item.get("text") for item in evidence):
                issues.append(f"chunk {cid}: hierarchy_evidence 条目必须含 section_id 和 text")

        for section_list, label in [
            (chunk.get("source_sections", []), "source_sections"),
            (chunk.get("merged_from", []), "merged_from"),
        ]:
            for src in section_list:
                if "dsl_dimension" not in src:
                    issues.append(f"chunk {cid} {label} 缺少 dsl_dimension")

    for chunk in data["chunks"]:
        if chunk.get("chunk_type") != "functional_requirement":
            continue
        cid = chunk.get("chunk_id", "?")
        parent = chunk.get("parent_requirement")
        path_items = chunk.get("requirement_path")
        if parent is not None and parent not in requirement_names:
            issues.append(f"chunk {cid}: parent_requirement 未对应内部功能需求: {parent}")
        if isinstance(path_items, list) and len(path_items) > 1 and isinstance(path_items[-2], dict):
            if parent != path_items[-2].get("name"):
                issues.append(f"chunk {cid}: parent_requirement 必须等于 requirement_path 的直接父节点")
        elif isinstance(path_items, list) and len(path_items) == 1 and parent is not None:
            issues.append(f"chunk {cid}: 顶层 requirement_path 的 parent_requirement 必须为 null")

    # 汇总
    summary = data.get("chunking_summary", {})
    print(f"[INFO] 文档: {data.get('document_info', {}).get('name', 'N/A')}")
    print(f"[INFO] 共 {len(data['chunks'])} 个 chunks")
    print(f"[INFO] 类型: {json.dumps(summary.get('chunk_types', {}), ensure_ascii=False)}")
    print(f"[INFO] 子功能分类: {summary.get('subfunction_categories', [])}")

    if issues:
        print(f"[WARN] {len(issues)} 个问题:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("[OK] 校验通过")


if __name__ == "__main__":
    main()
