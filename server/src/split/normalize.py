"""把模型输出规范成可被现有拆分脚本消费的 chunks.json。"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .outline import Heading

ALLOWED_TYPES = {
    "preamble",
    "overview",
    "system_overview",
    "system_requirements",
    "interface",
    "appendix",
    "functional_requirement",
}

NON_FUNCTIONAL_HINTS = {
    "preamble": ("变更记录", "修订记录", "前言", "导言", "目录", "封面"),
    "overview": ("概述", "范围", "术语", "定义", "简介"),
    "system_overview": ("系统功能描述", "系统概述", "系统描述"),
    "system_requirements": ("系统需求", "需求分析"),
    "interface": ("接口", "信号"),
    "appendix": ("附录", "缩略语", "参考资料", "标定值"),
}


class SplitError(RuntimeError):
    """语义分块失败，消息直接展示给用户。"""


def build_chunks(
    raw: object,
    headings: list[Heading],
    *,
    document_name: str,
    source_format: str,
    input_path: str,
    warnings: list[str],
) -> dict:
    if not headings:
        raise SplitError("未检测到 Markdown 标题，无法执行语义拆分")
    if not isinstance(raw, dict):
        raise SplitError("模型没有返回 JSON 对象")

    by_id = {heading.section_id: heading for heading in headings if heading.section_id}
    by_title = {
        _key(heading.title): heading
        for heading in headings
        if heading.section_id and heading.title
    }
    drafts = _drafts_from_raw(raw.get("chunks"), by_id, by_title, warnings)
    if not drafts:
        warnings.append("模型没有给出可用 chunk，已退回按标题逐节拆分")
        drafts = _fallback_drafts(headings)

    drafts = _merge_same_canonical(drafts, warnings)
    assigned: dict[str, dict] = {}
    selected: list[dict] = []
    for draft in drafts:
        section_ids = []
        for section_id in sorted(
            draft["section_ids"], key=lambda value: by_id[value].start
        ):
            if section_id in assigned:
                warnings.append(f"章节 {section_id} 被重复分配，已保留首次归属")
                continue
            assigned[section_id] = draft
            section_ids.append(section_id)
        if not section_ids:
            warnings.append(f"chunk「{draft.get('title') or draft.get('canonical')}」没有可用章节，已跳过")
            continue
        draft["section_ids"] = section_ids
        selected.append(draft)

    if not selected:
        raise SplitError("所有 chunk 都没有有效章节")

    _cover_unassigned(headings, assigned, selected)
    _assign_chunk_ids(selected)

    chunks: list[dict] = []
    for draft in selected:
        chunks.append(_finalize_chunk(draft, by_id, headings, assigned))

    _normalize_requirement_paths(chunks, headings, warnings)
    consistency_issues = _collect_consistency_issues(raw, chunks)

    types: dict[str, int] = {}
    for chunk in chunks:
        chunk_type = chunk["chunk_type"]
        types[chunk_type] = types.get(chunk_type, 0) + 1

    document_info = raw.get("document_info") if isinstance(raw.get("document_info"), dict) else {}
    document_info = {
        "name": document_info.get("name") or document_name,
        "type": document_info.get("type") or source_format,
        "input_path": document_info.get("input_path") or input_path,
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_pages": document_info.get("total_pages"),
    }

    seed = raw.get("project_relation_seed") if isinstance(raw.get("project_relation_seed"), dict) else {}
    seed.setdefault("requirements", [])
    seed.setdefault("relation_delta", [])
    seed["consistency_issues"] = consistency_issues

    summary = {
        "total_chunks": len(chunks),
        "chunk_types": types,
        "merge_count": sum(1 for chunk in chunks if len(chunk["source_sections"]) > 1),
        "subfunction_categories": sorted(
            {
                str(chunk.get("subfunction"))
                for chunk in chunks
                if chunk.get("subfunction")
            }
        ),
    }

    return {
        "document_info": document_info,
        "chunking_summary": summary,
        "project_relation_seed": seed,
        "chunks": chunks,
        "merge_records": raw.get("merge_records") if isinstance(raw.get("merge_records"), list) else [],
    }


def _drafts_from_raw(raw_chunks, by_id, by_title, warnings: list[str]) -> list[dict]:
    drafts: list[dict] = []
    if not isinstance(raw_chunks, list):
        return drafts
    for index, item in enumerate(raw_chunks, start=1):
        if not isinstance(item, dict):
            continue
        section_ids = _extract_section_ids(item)
        resolved: list[str] = []
        for value in section_ids:
            section_id = _resolve_section_id(value, by_id, by_title)
            if section_id is None:
                warnings.append(f"chunk {index} 引用了不存在的章节：{value}")
                continue
            if section_id not in resolved:
                resolved.append(section_id)
        if not resolved:
            warnings.append(f"chunk {index} 没有有效章节，已跳过")
            continue

        title = _text(item.get("title"))
        chunk_type = _text(item.get("chunk_type"))
        canonical = _text(item.get("canonical_function_name"))
        first_title = by_id[resolved[0]].title
        if not chunk_type:
            chunk_type = "functional_requirement" if canonical else _classify(title or first_title)
        if chunk_type not in ALLOWED_TYPES:
            chunk_type = _classify(title or first_title)
        if chunk_type == "functional_requirement":
            canonical = canonical or title or first_title
            canonical = _clean_name(canonical)
        else:
            canonical = ""

        drafts.append(
            {
                "raw": item,
                "section_ids": resolved,
                "chunk_type": chunk_type,
                "canonical": canonical,
                "title": title or canonical or first_title,
            }
        )
    return drafts


def _extract_section_ids(chunk: dict) -> list[str]:
    values: list[str] = []
    for key in ("section_ids", "source_section_ids"):
        raw = chunk.get(key)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item).strip())
    for segment in chunk.get("physical_segments") or []:
        if isinstance(segment, dict):
            value = segment.get("section_id") or segment.get("source_section_id")
            if value:
                values.append(str(value))
    for section in chunk.get("source_sections") or []:
        if isinstance(section, dict):
            value = section.get("section_id")
            if value:
                values.append(str(value))
    result: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in result:
            result.append(value)
    return result


def _resolve_section_id(value: str, by_id: dict[str, Heading], by_title: dict[str, Heading]) -> str | None:
    value = value.strip()
    if value in by_id:
        return value
    matched = re.match(r"^(\d+(?:\.\d+)*)[\s._、-]*(.*)$", value)
    if matched and matched.group(1) in by_id:
        return matched.group(1)
    section = by_title.get(_key(value))
    return section.section_id if section else None


def _merge_same_canonical(drafts: list[dict], warnings: list[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    result: list[dict] = []
    for draft in drafts:
        canonical = draft["canonical"]
        if draft["chunk_type"] != "functional_requirement" or not canonical:
            result.append(draft)
            continue
        if canonical not in seen:
            seen[canonical] = draft
            result.append(draft)
            continue
        first = seen[canonical]
        for section_id in draft["section_ids"]:
            if section_id not in first["section_ids"]:
                first["section_ids"].append(section_id)
        warnings.append(f"同名功能「{canonical}」出现多次，已合并为一个 chunk")
    return result


def _cover_unassigned(headings: list[Heading], assigned: dict[str, dict], selected: list[dict]) -> None:
    for heading in headings:
        if not heading.section_id or heading.section_id in assigned:
            continue
        has_descendants = any(
            other.start > heading.start and other.start < heading.subtree_end and other.level > heading.level
            for other in headings
        )
        if has_descendants and not heading.has_body:
            continue
        draft = {
            "raw": {},
            "section_ids": [heading.section_id],
            "chunk_type": _classify(heading.title),
            "canonical": "",
            "title": heading.title,
        }
        assigned[heading.section_id] = draft
        selected.append(draft)


def _assign_chunk_ids(drafts: list[dict]) -> None:
    used: set[str] = set()
    next_index = 1
    for draft in drafts:
        raw_id = _text(draft["raw"].get("chunk_id")) if isinstance(draft.get("raw"), dict) else ""
        chunk_id = raw_id if re.fullmatch(r"chunk_\d+", raw_id) and raw_id not in used else ""
        while not chunk_id:
            candidate = f"chunk_{next_index:03d}"
            next_index += 1
            if candidate not in used:
                chunk_id = candidate
        used.add(chunk_id)
        draft["chunk_id"] = chunk_id


def _finalize_chunk(
    draft: dict,
    by_id: dict[str, Heading],
    headings: list[Heading],
    assigned: dict[str, dict],
) -> dict:
    raw = draft["raw"] if isinstance(draft.get("raw"), dict) else {}
    section_ids = sorted(draft["section_ids"], key=lambda value: by_id[value].start)
    first = by_id[section_ids[0]]
    dsl_dimension = _string_list(raw.get("dsl_dimension"))

    source_sections: list[dict] = []
    merged_from: list[dict] = []
    physical_segments: list[dict] = []
    for index, section_id in enumerate(section_ids):
        heading = by_id[section_id]
        role = "primary" if index == 0 else "merged"
        section = {
            "section_id": section_id,
            "title": heading.title,
            "page": None,
            "role": role,
            "dsl_dimension": dsl_dimension,
        }
        source_sections.append(section)
        if index > 0:
            merged_from.append(dict(section))
        segment = {"section_id": section_id, "title": heading.title, "role": role}
        child = _first_assigned_descendant(heading, headings, assigned)
        if child is not None:
            segment["end_before_section_id"] = child.section_id
        physical_segments.append(segment)

    chunk_type = draft["chunk_type"]
    canonical = draft["canonical"] or None
    chunk = {
        "chunk_id": draft["chunk_id"],
        "title": _clean_name(draft.get("title") or canonical or first.title),
        "chunk_type": chunk_type,
        "content": _text(raw.get("content")),
        "source_sections": source_sections,
        "physical_segments": physical_segments,
        "merged_from": merged_from,
        "sub_elements": raw.get("sub_elements") if isinstance(raw.get("sub_elements"), list) else [],
        "subfunction": raw.get("subfunction"),
        "semantic_description": _text(raw.get("semantic_description")),
        "keywords": _string_list(raw.get("keywords")),
        "canonical_function_name": canonical,
        "parent_requirement": None,
        "requirement_path": raw.get("requirement_path") if isinstance(raw.get("requirement_path"), list) else None,
        "hierarchy_evidence": raw.get("hierarchy_evidence") if isinstance(raw.get("hierarchy_evidence"), list) else [],
        "merge_reason": _text(raw.get("merge_reason")),
    }
    return chunk


def _first_assigned_descendant(
    heading: Heading,
    headings: list[Heading],
    assigned: dict[str, dict],
) -> Heading | None:
    for other in headings:
        if other.start <= heading.start:
            continue
        if other.start >= heading.subtree_end:
            break
        if other.level > heading.level and other.section_id in assigned:
            return other
    return None


def _normalize_requirement_paths(chunks: list[dict], headings: list[Heading], warnings: list[str]) -> None:
    functional = [chunk for chunk in chunks if chunk["chunk_type"] == "functional_requirement"]
    functional_by_section: dict[str, str] = {}
    for chunk in functional:
        if chunk.get("canonical_function_name"):
            for section in chunk["source_sections"]:
                functional_by_section[section["section_id"]] = chunk["canonical_function_name"]
    canonical_names = {chunk["canonical_function_name"] for chunk in functional if chunk["canonical_function_name"]}
    by_id = {heading.section_id: heading for heading in headings}

    for chunk in functional:
        canonical = chunk["canonical_function_name"]
        path = _clean_path(chunk.get("requirement_path"))
        if not path:
            own_section = chunk["source_sections"][0]["section_id"]
            path = [{"section_id": own_section, "name": canonical}]
            for ancestor in _functional_ancestors(by_id.get(own_section), headings, functional_by_section):
                path.insert(0, ancestor)

        cleaned: list[dict] = []
        for entry in path:
            section_id = _text(entry.get("section_id"))
            name = _text(entry.get("name"))
            mapped = functional_by_section.get(section_id)
            if mapped:
                name = mapped
            elif name not in canonical_names and name != canonical:
                warnings.append(f"chunk「{canonical}」的父路径节点「{name}」不是功能需求，已从 requirement_path 移除")
                continue
            if name and (not cleaned or cleaned[-1]["name"] != name):
                cleaned.append({"section_id": section_id, "name": name})

        if not cleaned or cleaned[-1]["name"] != canonical:
            cleaned.append(
                {
                    "section_id": chunk["source_sections"][0]["section_id"],
                    "name": canonical,
                }
            )
        chunk["requirement_path"] = cleaned
        chunk["parent_requirement"] = cleaned[-2]["name"] if len(cleaned) > 1 else None

        evidence = [
            item
            for item in (chunk.get("hierarchy_evidence") or [])
            if isinstance(item, dict) and item.get("section_id") and item.get("text")
        ]
        if not evidence:
            evidence = [
                {"section_id": entry["section_id"], "text": f"{entry['section_id']} {entry['name']}"}
                for entry in cleaned
            ]
        chunk["hierarchy_evidence"] = evidence


def _functional_ancestors(
    heading: Heading | None,
    headings: list[Heading],
    functional_by_section: dict[str, str],
) -> list[dict]:
    if heading is None:
        return []
    ancestors: list[Heading] = []
    for other in headings:
        if other.start >= heading.start:
            break
        if other.subtree_end <= heading.start:
            continue
        if other.level < heading.level and other.section_id in functional_by_section:
            ancestors.append(other)
    result: list[dict] = []
    for item in ancestors:
        name = functional_by_section[item.section_id]
        if not result or result[-1]["name"] != name:
            result.append({"section_id": item.section_id, "name": name})
    return result


def _clean_path(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        section_id = _text(item.get("section_id"))
        name = _clean_name(item.get("name"))
        if section_id and name:
            result.append({"section_id": section_id, "name": name})
    return result


def _collect_consistency_issues(raw: dict, chunks: list[dict]) -> list[dict]:
    seed = raw.get("project_relation_seed") if isinstance(raw.get("project_relation_seed"), dict) else {}
    issues = seed.get("consistency_issues") if isinstance(seed.get("consistency_issues"), list) else []
    normalized: list[dict] = []
    for item in issues:
        if isinstance(item, dict):
            message = _text(item.get("issue") or item.get("message"))
            if message:
                normalized.append({"issue": message, "evidence": item.get("evidence") or []})
        elif isinstance(item, str) and item.strip():
            normalized.append({"issue": item.strip(), "evidence": []})

    for chunk in chunks:
        if chunk["chunk_type"] != "functional_requirement":
            continue
        canonical = chunk["canonical_function_name"]
        sections = [item["section_id"] for item in chunk["source_sections"]]
        chapter_prefixes = {section.split(".")[0] for section in sections if section}
        if len(sections) > 1 and len(chapter_prefixes) > 1 and re.search(r"[、，]|以及|和", canonical):
            normalized.append(
                {
                    "issue": f"功能「{canonical}」跨多个章节合并，请确认没有把不同功能聚类",
                    "evidence": [
                        {"section_id": item["section_id"], "text": item["title"]}
                        for item in chunk["source_sections"]
                    ],
                }
            )

    return normalized


def _fallback_drafts(headings: list[Heading]) -> list[dict]:
    drafts: list[dict] = []
    for heading in headings:
        if not heading.section_id:
            continue
        if not heading.has_body:
            has_descendants = any(
                other.start > heading.start and other.start < heading.subtree_end and other.level > heading.level
                for other in headings
            )
            if has_descendants:
                continue
        chunk_type = _classify(heading.title)
        canonical = _clean_name(heading.title) if chunk_type == "functional_requirement" else ""
        drafts.append(
            {
                "raw": {},
                "section_ids": [heading.section_id],
                "chunk_type": chunk_type,
                "canonical": canonical,
                "title": heading.title,
            }
        )
    if not drafts and headings:
        heading = headings[0]
        drafts.append(
            {
                "raw": {},
                "section_ids": [heading.section_id],
                "chunk_type": "overview",
                "canonical": "",
                "title": heading.title,
            }
        )
    return drafts


def _classify(title: str) -> str:
    text = _clean_name(title)
    for chunk_type, keywords in NON_FUNCTIONAL_HINTS.items():
        if any(keyword in text for keyword in keywords):
            return chunk_type
    return "functional_requirement"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_name(value: object) -> str:
    text = _text(value)
    text = re.sub(r"^(?:UserReq|FR|REQ)[._\-\s]*\d+[_\-\s]*", "", text, flags=re.IGNORECASE)
    return text.strip(" :：-—_")


def _key(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
