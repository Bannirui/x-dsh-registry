"""Organize extracted Markdown by the requirement tree declared in chunks.json.

The index parser owns semantic boundaries and the original document hierarchy.
This script only materializes that declared hierarchy; it never derives parents
from similar names, directory names, or the legacy ``subfunction`` field.
"""

import json
import re
import shutil
import sys
from pathlib import Path


def sanitize(value: str) -> str:
    value = str(value or "").replace("/", "\uff0f").replace("\\", "\uff3c")
    value = re.sub(r'[:*?"<>|]', " ", value)
    return value.strip().rstrip(".")


def source_section(chunk: dict) -> tuple[str, str]:
    sources = chunk.get("source_sections") or []
    if not sources:
        return "", ""
    return str(sources[0].get("section_id") or ""), str(sources[0].get("title") or "")


def node_name(node: dict) -> str:
    if not isinstance(node, dict):
        raise ValueError("requirement_path entry must be an object")
    section_id = sanitize(node.get("section_id", ""))
    title = sanitize(node.get("name", ""))
    if not section_id or not title:
        raise ValueError("requirement_path entry must contain section_id and name")
    return f"{section_id}{title}"


def root_file_name(chunk: dict) -> str:
    section_id, title = source_section(chunk)
    title = sanitize(title or chunk.get("title") or chunk.get("chunk_id"))
    return f"{sanitize(section_id)}{title}" if section_id else title


PREFIX_PATTERN = re.compile(r'^(?:UserReq|FR|REQ)\d+[_\-]?', re.IGNORECASE)


def requirement_destination(output_dir: Path, chunk: dict) -> Path:
    path = chunk.get("requirement_path")
    if not isinstance(path, list) or not path:
        raise ValueError(f"{chunk.get('chunk_id')}: functional_requirement missing requirement_path")
    canonical = str(chunk.get("canonical_function_name") or "").strip()
    canonical_short = PREFIX_PATTERN.sub("", canonical).strip()
    leaf_name = str(path[-1].get("name", "")).strip() if isinstance(path[-1], dict) else ""
    if not canonical or leaf_name != canonical and leaf_name != canonical_short:
        raise ValueError(
            f"{chunk.get('chunk_id')}: requirement_path leaf '{leaf_name}' "
            f"doesn't match canonical '{canonical}'"
        )

    directory = output_dir
    for node in path:
        directory /= node_name(node)
    leaf = node_name(path[-1])
    return directory / f"{leaf}.md"


def source_files(output_dir: Path, chunks: list[dict]) -> dict[str, Path]:
    by_id = {}
    for file_path in output_dir.glob("chunk_*.md"):
        matches = [chunk["chunk_id"] for chunk in chunks if file_path.name.startswith(f"{chunk['chunk_id']}_")]
        if len(matches) == 1:
            by_id[matches[0]] = file_path
    return by_id


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python organize_tree.py <output_dir>")
        raise SystemExit(1)

    output_dir = Path(sys.argv[1])
    chunks_path = output_dir / "chunks.json"
    if not chunks_path.exists():
        raise FileNotFoundError(f"chunks.json not found: {chunks_path}")
    data = json.loads(chunks_path.read_text(encoding="utf-8-sig"))
    chunks = data.get("chunks", [])
    available = source_files(output_dir, chunks)

    # Build every destination before moving a file, so duplicate hierarchy paths
    # cannot silently overwrite another requirement.
    planned: dict[str, Path] = {}
    used_destinations: dict[Path, str] = {}
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id", "")
        if chunk_id not in available:
            raise FileNotFoundError(f"Missing extracted Markdown for {chunk_id}")
        if chunk.get("chunk_type") == "functional_requirement":
            destination = requirement_destination(output_dir, chunk)
        else:
            destination = output_dir / f"{root_file_name(chunk)}.md"
        if destination in used_destinations:
            raise ValueError(
                f"Duplicate output path: {destination}; "
                f"{used_destinations[destination]} and {chunk_id}"
            )
        planned[chunk_id] = destination
        used_destinations[destination] = chunk_id

    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        source = available[chunk_id]
        destination = planned[chunk_id]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.resolve() != source.resolve():
            raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
        shutil.move(str(source), str(destination))
        if chunk.get("chunk_type") == "functional_requirement":
            chunk["source_relative_path"] = destination.relative_to(output_dir).as_posix()
        print(f"[OK] {chunk_id} -> {destination.relative_to(output_dir)}")

    leftovers = sorted(output_dir.glob("chunk_*.md"))
    if leftovers:
        raise RuntimeError(f"Unassigned extracted files remain: {[item.name for item in leftovers]}")

    chunks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[DONE] Requirement hierarchy materialized and source_relative_path updated")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
