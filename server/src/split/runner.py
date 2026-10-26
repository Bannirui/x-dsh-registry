"""调用 vendored 拆分/校验脚本。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .assets import follow_assets
from .outline import parse_outline

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

FINAL_IMAGE_MD = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+[\"'][^\"']*[\"'])?\))")
FINAL_IMAGE_HTML = re.compile(r"(<img[^>]*\bsrc=[\"'])([^\"']+)([\"'])", re.IGNORECASE)
FINAL_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FINAL_SECTION_ID = re.compile(r"^(\d+(?:\.\d+)*)[\s._、-]*(.*)$")
EXTERNAL_REF_PREFIXES = ("http://", "https://", "data:", "//", "#")


class SplitRunError(RuntimeError):
    """拆分管线脚本执行失败。"""


def run_pipeline(
    *,
    task_dir: Path,
    markdown_path: Path,
    chunks_data: dict,
    manifest: dict | None = None,
    timeout: int = 240,
) -> dict:
    requirements_dir = task_dir / "requirements"
    requirements_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = requirements_dir / "chunks.json"
    chunks_path.write_text(
        json.dumps(chunks_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report: list[dict] = []
    _run_script("validate_chunks.py", chunks_path, report=report, timeout=timeout)
    _run_script(
        "split_markdown_chunks.py",
        markdown_path,
        chunks_path,
        requirements_dir,
        report=report,
        timeout=timeout,
    )
    _run_script("organize_tree.py", requirements_dir, report=report, timeout=timeout)
    organized_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

    try:
        asset_result = follow_assets(
            task_dir=task_dir,
            requirements_dir=requirements_dir,
            chunks_data=organized_chunks,
            manifest=manifest or {},
        )
    except Exception as exc:  # noqa: BLE001
        asset_result = {
            "copiedAssetCount": 0,
            "unmappedAssetCount": 0,
            "danglingImageCount": 0,
            "assetWarnings": [f"图片跟随失败：{type(exc).__name__}: {exc}"],
            "assetReportPath": "",
        }
    report.append(
        {
            "script": "follow_assets.py",
            "copied": asset_result["copiedAssetCount"],
            "unmapped": asset_result["unmappedAssetCount"],
            "dangling": asset_result["danglingImageCount"],
            "warnings": asset_result["assetWarnings"],
        }
    )

    validation_warnings: list[str] = []
    for name in ("validate_toc.py", "validate_output.py"):
        result = _run_script(
            name,
            requirements_dir,
            report=report,
            timeout=timeout,
            fatal=False,
        )
        if result["returncode"] != 0:
            validation_warnings.extend(_warning_lines(name, result["stdout"] + result["stderr"]))

    for name in ("enrich_chunks.py", "generate_relations.py"):
        result = _run_script(
            name,
            requirements_dir,
            report=report,
            timeout=timeout,
            fatal=False,
        )
        if result["returncode"] != 0:
            validation_warnings.append(
                f"{name} 未完成：{_short_error(result['stdout'] + result['stderr'])}"
            )

    final_path = _write_final_markdown(
        requirements_dir=requirements_dir,
        markdown_path=markdown_path,
        chunks=organized_chunks.get("chunks", []),
        source_name=str((manifest or {}).get("source", {}).get("name") or task_dir.name),
    )

    (requirements_dir / "split_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    files = sorted(
        path.relative_to(requirements_dir).as_posix()
        for path in requirements_dir.rglob("*.md")
    )
    functional_count = sum(
        1 for chunk in organized_chunks.get("chunks", []) if chunk.get("chunk_type") == "functional_requirement"
    )
    source = manifest.get("source", {}) if isinstance(manifest, dict) else {}
    ocr_pages = manifest.get("ocr_pages", []) if isinstance(manifest, dict) else []
    ocr_completed = sum(1 for item in ocr_pages if item.get("status") == "completed")
    ocr_failed = sum(1 for item in ocr_pages if item.get("status") in {"failed", "pending", "skipped"})
    conversion_warnings = manifest.get("warnings", []) if isinstance(manifest, dict) else []
    all_warnings = conversion_warnings + validation_warnings + asset_result["assetWarnings"]
    final_report = {
        "task_id": manifest.get("task_id") if isinstance(manifest, dict) else task_dir.name,
        "source": source,
        "requirement_count": functional_count,
        "chunk_count": len(organized_chunks.get("chunks", [])),
        "merge_count": (organized_chunks.get("chunking_summary") or {}).get("merge_count", 0),
        "merge_records": organized_chunks.get("merge_records", []),
        "image_count": len(manifest.get("assets", [])) if isinstance(manifest, dict) else 0,
        "copied_image_count": asset_result["copiedAssetCount"],
        "unmapped_image_count": asset_result["unmappedAssetCount"],
        "dangling_image_count": asset_result["danglingImageCount"],
        "ocr_pages": ocr_pages,
        "ocr_completed_count": ocr_completed,
        "ocr_failed_count": ocr_failed,
        "warnings": all_warnings,
        "tree_dir": str(requirements_dir),
        "chunks_path": str(chunks_path),
        "asset_report_path": asset_result["assetReportPath"],
        "final_path": str(final_path),
        "final_chars": len(final_path.read_text(encoding="utf-8")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = requirements_dir / "report.json"
    report_path.write_text(
        json.dumps(final_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "treeDir": str(requirements_dir),
        "chunksPath": str(chunks_path),
        "reportPath": str(report_path),
        "requirementCount": functional_count,
        "chunkCount": len(organized_chunks.get("chunks", [])),
        "files": files,
        "validationWarnings": validation_warnings,
        "assetWarnings": asset_result["assetWarnings"],
        "copiedAssetCount": asset_result["copiedAssetCount"],
        "unmappedAssetCount": asset_result["unmappedAssetCount"],
        "danglingImageCount": asset_result["danglingImageCount"],
        "assetReportPath": asset_result["assetReportPath"],
        "finalPath": str(final_path),
    }


def _write_final_markdown(
    *,
    requirements_dir: Path,
    markdown_path: Path,
    chunks: list[dict],
    source_name: str,
) -> Path:
    content = _rewrite_local_asset_refs(
        markdown_path.read_text(encoding="utf-8"),
        source_dir=markdown_path.parent,
        target_dir=requirements_dir,
    )
    content = _annotate_semantic_merges(content, chunks)
    content = _collapse_merged_sections(content, chunks).strip()

    title = Path(source_name).stem or "需求文档"
    parts = [f"# {title}", ""]
    if content:
        parts.append(content)
        parts.append("")

    final_path = requirements_dir / "final.md"
    final_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    return final_path


def _annotate_semantic_merges(content: str, chunks: list[dict]) -> str:
    merges = _merge_annotations(chunks)
    if not merges:
        return content

    lines: list[str] = []
    annotated: set[str] = set()
    in_fence = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            lines.append(line)
            continue
        lines.append(line)
        if in_fence:
            continue
        match = FINAL_HEADING.match(line)
        if match is None:
            continue
        parsed = FINAL_SECTION_ID.match(match.group(2).strip())
        if parsed is None:
            continue
        section_id = parsed.group(1)
        entry = merges.get(section_id)
        if entry is None or entry["role"] != "primary" or section_id in annotated:
            continue
        annotated.add(section_id)
        references = "、".join(
            f"{item['section_id']} {item.get('title', '')}".strip()
            for item in entry["merged"]
        )
        note = f"语义合并：本需求合并了 {references}。"
        if entry["reason"]:
            note += f"合并原因：{entry['reason'].rstrip('。.!！;；')}。"
        lines.extend(["", f"> {note}", ""])
    return "\n".join(lines)


def _merge_annotations(chunks: list[dict]) -> dict[str, dict]:
    annotations: dict[str, dict] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("chunk_type") != "functional_requirement":
            continue
        sections = [
            section
            for section in (chunk.get("source_sections") or [])
            if isinstance(section, dict) and section.get("section_id")
        ]
        if len(sections) < 2:
            continue
        sections.sort(key=lambda section: 0 if section.get("role") == "primary" else 1)
        primary = sections[0]
        merged = sections[1:]
        canonical = str(
            chunk.get("canonical_function_name") or chunk.get("title") or primary.get("title") or ""
        ).strip()
        reason = str(chunk.get("merge_reason") or "").strip()
        for index, section in enumerate(sections):
            annotations[str(section["section_id"])] = {
                "role": "primary" if index == 0 else "merged",
                "canonical": canonical,
                "primary": primary,
                "merged": merged,
                "reason": reason,
            }
    return annotations


def _collapse_merged_sections(content: str, chunks: list[dict]) -> str:
    annotations = _merge_annotations(chunks)
    if not annotations:
        return content

    headings = parse_outline(content)
    by_id = {heading.section_id: heading for heading in headings if heading.section_id}
    merged_ids = {
        section_id
        for section_id, entry in annotations.items()
        if entry["role"] == "merged" and section_id in by_id
    }
    if not merged_ids:
        return content

    candidates = sorted((by_id[section_id] for section_id in merged_ids), key=lambda item: item.start)
    selected: list = []
    for heading in candidates:
        if any(heading.start >= top.start and heading.start < top.subtree_end for top in selected):
            continue
        selected.append(heading)
    selected_ids = {heading.section_id for heading in selected}

    insertions: dict[str, list[str]] = {}
    for heading in selected:
        primary_id = str(annotations[heading.section_id]["primary"]["section_id"])
        block = content[heading.start : heading.subtree_end].strip()
        lines = block.splitlines()
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        if body:
            insertions.setdefault(primary_id, []).append(body)

    parts: list[str] = []
    cursor = 0
    for heading in headings:
        if heading.start < cursor:
            continue
        parts.append(content[cursor : heading.start])
        if heading.section_id in selected_ids:
            cursor = heading.subtree_end
            continue
        parts.append(content[heading.start : heading.end])
        cursor = heading.end
        for body in insertions.get(heading.section_id, []):
            separator = "" if parts[-1].endswith("\n\n") else ("\n" if parts[-1].endswith("\n") else "\n\n")
            parts.append(f"{separator}{body}\n\n")
    parts.append(content[cursor:])
    collapsed = "".join(parts)
    return _drop_empty_merge_parents(collapsed, headings, selected)


def _drop_empty_merge_parents(content: str, original_headings: list, merged_headings: list) -> str:
    ancestors = {
        other.section_id
        for heading in merged_headings
        for other in original_headings
        if other.level < heading.level and other.start < heading.start < other.subtree_end
    }
    if not ancestors:
        return content

    while True:
        headings = parse_outline(content)
        target = None
        for heading in headings:
            if heading.section_id not in ancestors:
                continue
            body = "\n".join(heading.own_text.splitlines()[1:]).strip()
            has_descendant = any(
                heading.start < other.start < heading.subtree_end for other in headings
            )
            if not body and not has_descendant:
                target = heading
                break
        if target is None:
            return content
        content = content[: target.start] + content[target.end :]


def _rewrite_local_asset_refs(content: str, *, source_dir: Path, target_dir: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        reference = match.group(2)
        rewritten = _relative_to_target(reference, source_dir, target_dir)
        return f"{match.group(1)}{rewritten}{match.group(3)}"

    content = FINAL_IMAGE_MD.sub(replace, content)
    return FINAL_IMAGE_HTML.sub(replace, content)


def _relative_to_target(reference: str, source_dir: Path, target_dir: Path) -> str:
    if not reference or reference.startswith(EXTERNAL_REF_PREFIXES):
        return reference
    candidate = Path(reference)
    resolved = candidate if candidate.is_absolute() else source_dir / candidate
    try:
        relative = os.path.relpath(resolved, target_dir)
    except ValueError:
        return reference
    return relative.replace(os.sep, "/")


def _run_script(
    name: str,
    *args,
    report: list[dict],
    timeout: int,
    fatal: bool = True,
) -> dict:
    script = SCRIPTS_DIR / name
    if not script.exists():
        raise SplitRunError(f"缺少拆分脚本：{script}")
    try:
        completed = subprocess.run(
            [sys.executable, str(script), *[str(item) for item in args]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(SCRIPTS_DIR),
        )
    except subprocess.TimeoutExpired as exc:
        raise SplitRunError(f"{name} 超时（>{timeout}s）") from exc
    entry = {
        "script": name,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    report.append(entry)
    if fatal and completed.returncode != 0:
        detail = _short_error(completed.stdout + completed.stderr)
        raise SplitRunError(f"{name} 执行失败：{detail}")
    return entry


def _short_error(output: str, limit: int = 1200) -> str:
    text = output.strip()
    return text[-limit:] if len(text) > limit else text


OPTIONAL_ROOT_WARNINGS = (
    "根目录缺失导言类文件",
    "根目录缺失概述类文件",
    "根目录缺失系统描述类文件",
)


def _warning_lines(name: str, output: str) -> list[str]:
    lines: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or any(fragment in stripped for fragment in OPTIONAL_ROOT_WARNINGS)
            or re.search(r"\[WARN\]\s+\d+\s+ge issues", stripped)
        ):
            continue
        if stripped.startswith(("[WARN]", "[MISS]", "[FAIL]", "[ERROR]", "[?]")) or "额外文件" in stripped:
            lines.append(f"{name}: {stripped}")
    return lines
