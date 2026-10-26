# -*- coding: utf-8 -*-
"""
enrich_chunks.py — 补充 downstream 所需字段

为 chunks.json 中每个 functional_requirement chunk 补充：
  - canonical_function_name
  - req_id
  - source_relative_path (通过扫描实际文件系统匹配)

用法: cd skills/requirement_itemizer && python scripts/enrich_chunks.py <output_dir>
"""
import json, os, re, sys
sys.stdout.reconfigure(encoding='utf-8')

def extract_req_id(title):
    m = re.match(r'(UserReq|FR|REQ)[._]\d+', title)
    return m.group(0) if m else ""

def extract_canonical_name(title):
    return re.sub(r'^(UserReq|FR|REQ)[._]\d+[_-]?', '', title).strip()

def find_chunk_md(output_dir, chunk):
    """
    目录组织阶段已回写 source_relative_path。这里仅验证该路径；
    不再通过标题、编号或目录遍历进行模糊匹配。
    """
    rel_path = chunk.get("source_relative_path", "")
    if not rel_path:
        return ""
    absolute = os.path.join(output_dir, rel_path)
    return rel_path if os.path.isfile(absolute) else ""


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/enrich_chunks.py <output_dir>")
        sys.exit(1)

    output_dir = sys.argv[1]
    chunks_json = os.path.join(output_dir, "chunks.json")

    with open(chunks_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    for chunk in data.get("chunks", []):
        if chunk.get("chunk_type") != "functional_requirement":
            continue

        title = chunk.get("title", "")

        # canonical_function_name：优先保留索引器声明的名称，避免覆盖已校验的路径
        canonical = chunk.get("canonical_function_name") or extract_canonical_name(title)
        chunk["canonical_function_name"] = canonical

        # req_id
        req_id = extract_req_id(title)
        chunk["req_id"] = req_id

        # source_relative_path
        rel_path = find_chunk_md(output_dir, chunk)
        if not rel_path:
            raise ValueError(
                f"{chunk['chunk_id']} 缺少有效 source_relative_path；"
                "请先运行 organize_tree.py，不能用名称猜测路径。"
            )

        count += 1

    with open(chunks_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已补充 {count} 个 functional_requirement chunk 的字段")
    print(f"   输出: {chunks_json}")


if __name__ == "__main__":
    main()
