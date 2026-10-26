# -*- coding: utf-8 -*-
"""Generate schema 2 requirement relations from declared document evidence.

The project directory and document title are scope metadata, not Requirement
instances. Containment comes from ``parent_requirement`` plus the source
outline evidence declared by the index parser, never from output directories.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


SCHEMA_VERSION = "2.0"
LEVELS = {"system", "software", "part", "ui"}
INCLUSION_TYPES = {"includes", "inclusion"}


def canonical_title(value: str) -> str:
    return re.sub(r"^(?:UserReq|FR|REQ)[._]\d+[_\-]?", "", value or "").strip()


def normalize_level(value: str) -> str:
    value = (value or "system").strip().lower()
    return {"component": "part", "module": "part"}.get(value, value) if value in LEVELS | {"component", "module"} else "system"


def normalize_evidence(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    result, seen = [], set()
    for item in value:
        record = item if isinstance(item, dict) else {"text": str(item)}
        record = {key: str(record[key]).strip() for key in ("section_id", "source_file", "text") if record.get(key)}
        if not record.get("text"):
            continue
        fingerprint = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if fingerprint not in seen:
            seen.add(fingerprint)
            result.append(record)
    return result


def issue(issues: list, message: str, evidence=None) -> None:
    issues.append({"issue": message, "evidence": normalize_evidence(evidence)})


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python generate_relations.py <itemizer_output_dir>")
        raise SystemExit(1)

    output_dir = Path(sys.argv[1])
    data = json.loads((output_dir / "chunks.json").read_text(encoding="utf-8-sig"))
    seed = data.get("project_relation_seed")
    if not isinstance(seed, dict):
        raise ValueError("chunks.json missing project_relation_seed")

    issues = list(seed.get("consistency_issues", []))
    requirements: dict[str, dict] = {}
    aliases: dict[str, set[str]] = defaultdict(set)
    names_by_chunk: dict[str, str] = {}

    for chunk in data.get("chunks", []):
        if chunk.get("chunk_type") != "functional_requirement":
            continue
        name = (chunk.get("canonical_function_name") or canonical_title(chunk.get("title", ""))).strip()
        if not name:
            issue(issues, f"{chunk.get('chunk_id')}: missing canonical_function_name")
            continue
        if name in requirements:
            issue(issues, f"Duplicate canonical function name: {name}", chunk.get("hierarchy_evidence"))
            continue
        requirements[name] = {
            "name": name,
            "aliases": sorted({value for value in [chunk.get("title"), *chunk.get("aliases", [])] if value and value != name}),
            "requirement_level": normalize_level(chunk.get("requirement_level") or chunk.get("type") or "system"),
        }
        if chunk.get("source_relative_path"):
            requirements[name]["source_relative_path"] = chunk["source_relative_path"]
        names_by_chunk[chunk.get("chunk_id", "")] = name
        aliases[name].add(name)
        for alias in requirements[name]["aliases"]:
            aliases[alias].add(name)

    def resolve(value: str, target_chunk_id: str = "") -> str | None:
        if target_chunk_id and target_chunk_id in names_by_chunk:
            return names_by_chunk[target_chunk_id]
        candidates = aliases.get((value or "").strip(), set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    # Overview spellings become aliases only when one form explicitly equals a
    # detailed canonical name. This is an exact mapping, never fuzzy matching.
    for entry in seed.get("requirements", []):
        if not isinstance(entry, dict):
            issue(issues, "Invalid project_relation_seed requirement")
            continue
        forms = [str(entry.get("name") or "").strip(), *[str(item).strip() for item in entry.get("aliases", [])]]
        canonical_matches = {form for form in forms if form in requirements}
        if len(canonical_matches) != 1:
            if any(forms):
                issue(issues, "Overview requirement does not map to one canonical internal requirement", entry.get("evidence"))
            continue
        canonical = next(iter(canonical_matches))
        for form in forms:
            if form and form != canonical:
                requirements[canonical]["aliases"] = sorted(set(requirements[canonical]["aliases"] + [form]))
                aliases[form].add(canonical)

    relations: dict[tuple[str, str], dict] = {}

    def add_inclusion(source: str, target: str, evidence) -> None:
        if source == target:
            issue(issues, f"Ignoring self inclusion: {source}", evidence)
            return
        key = (source, target)
        relation = relations.setdefault(key, {
            "source": source,
            "target": target,
            "relation_type": "inclusion",
            "subtype": None,
            "is_inferred": False,
            "evidence": [],
        })
        relation["evidence"].extend(normalize_evidence(evidence))

    # The index parser records the direct parent from the original outline.
    for chunk in data.get("chunks", []):
        if chunk.get("chunk_type") != "functional_requirement":
            continue
        child = names_by_chunk.get(chunk.get("chunk_id", ""))
        parent = chunk.get("parent_requirement")
        if parent is None:
            continue
        source = resolve(parent)
        if not child or not source:
            issue(issues, f"Hierarchy endpoint cannot be resolved: {parent} -> {child or chunk.get('title')}", chunk.get("hierarchy_evidence"))
            continue
        add_inclusion(source, child, chunk.get("hierarchy_evidence"))

    # Supplement only with explicitly stated overview relations. It must not
    # introduce the document/project as an artificial Requirement root.
    for edge in seed.get("relation_delta", []):
        if not isinstance(edge, dict):
            issue(issues, "Invalid project_relation_seed relation")
            continue
        relation_type = edge.get("rel_type") or edge.get("relation_type") or "includes"
        if relation_type not in INCLUSION_TYPES:
            issue(issues, f"Unsupported seed relation type: {relation_type}", edge.get("evidence"))
            continue
        source = resolve(edge.get("from") or edge.get("source") or "")
        target = resolve(edge.get("to") or edge.get("target") or "", edge.get("target_chunk_id") or "")
        if not source or not target:
            issue(issues, "Overview relation endpoint is not a unique internal requirement", edge.get("evidence"))
            continue
        add_inclusion(source, target, edge.get("evidence"))

    for relation in relations.values():
        relation["evidence"] = normalize_evidence(relation["evidence"])

    output = {
        "schema_version": SCHEMA_VERSION,
        "requirements": sorted(requirements.values(), key=lambda item: item["name"]),
        "relations": sorted(relations.values(), key=lambda item: (item["source"], item["target"])),
        "external_references": [],
        "consistency_issues": issues,
    }
    (output_dir / "requirement_relations.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[OK] schema={SCHEMA_VERSION} requirements={len(requirements)} relations={len(relations)} issues={len(issues)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
