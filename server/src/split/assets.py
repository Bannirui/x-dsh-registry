"""把转换阶段抽取的图片跟随到所属需求目录，并重写 Markdown 引用。"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

IMAGE_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
IMAGE_HTML = re.compile(r"<img[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
EXTERNAL = ("http://", "https://", "data:", "//", "#")


def follow_assets(
    *,
    task_dir: Path,
    requirements_dir: Path,
    chunks_data: dict,
    manifest: dict,
) -> dict:
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
    report = {
        "copied": [],
        "unmapped": [],
        "missing_source": [],
        "dangling": [],
        "warnings": [],
    }
    if not assets:
        _write_report(requirements_dir, report)
        return _result(requirements_dir, report)

    warnings: list[str] = report["warnings"]
    chunks = chunks_data.get("chunks") or []
    section_to_chunk: dict[str, dict] = {}
    for chunk in chunks:
        for section in chunk.get("source_sections") or []:
            if isinstance(section, dict) and section.get("section_id"):
                section_to_chunk[str(section["section_id"])] = chunk

    copied_by_source: dict[tuple[str, str], str] = {}
    mapped_ids: set[str] = set()

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or f"asset_{len(mapped_ids) + 1}")
        relative = str(asset.get("path") or "").strip()
        source = (task_dir / relative).resolve() if relative else None
        if source is None or not source.is_file():
            report["missing_source"].append({"id": asset_id, "path": relative, "markdown_ref": asset.get("markdown_ref")})
            warnings.append(f"图片 {asset_id} 源文件不存在：{relative}")
            continue

        section_id = str(asset.get("section_id") or "")
        chunk = section_to_chunk.get(section_id)
        if chunk is None:
            report["unmapped"].append({"id": asset_id, "section_id": section_id, "path": relative, "markdown_ref": asset.get("markdown_ref")})
            warnings.append(f"图片 {asset_id} 无法确定所属需求（section_id={section_id or '空'}）")
            continue

        md_relative = str(chunk.get("source_relative_path") or "")
        md_path = requirements_dir / md_relative if md_relative else None
        if md_path is None or not md_path.is_file():
            warnings.append(f"图片 {asset_id} 找不到所属需求 Markdown：{md_relative or chunk.get('chunk_id')}")
            continue

        key = (str(chunk.get("chunk_id")), str(source))
        if key in copied_by_source:
            new_ref = copied_by_source[key]
        else:
            destination_dir = md_path.parent / "assets"
            destination_dir.mkdir(parents=True, exist_ok=True)
            filename = _safe_filename(source.name)
            destination = destination_dir / filename
            if destination.exists():
                destination = destination_dir / f"{asset_id}_{filename}"
            shutil.copy2(source, destination)
            new_ref = f"assets/{destination.name}"
            copied_by_source[key] = new_ref
            report["copied"].append(
                {
                    "id": asset_id,
                    "source": relative,
                    "destination": destination.relative_to(requirements_dir).as_posix(),
                    "chunk_id": chunk.get("chunk_id"),
                    "requirement": chunk.get("canonical_function_name") or chunk.get("title"),
                }
            )

        old_refs = list(dict.fromkeys(
            value
            for value in (asset.get("markdown_ref"), asset.get("path"))
            if isinstance(value, str) and value.strip()
        ))
        content = md_path.read_text(encoding="utf-8")
        replaced = False
        for old_ref in old_refs:
            if old_ref in content:
                content = content.replace(old_ref, new_ref)
                replaced = True
                break
        if replaced:
            md_path.write_text(content, encoding="utf-8")
            mapped_ids.add(asset_id)
        else:
            warnings.append(f"图片 {asset_id} 未在 {md_relative} 中找到引用")

    _rewrite_unmapped_refs(requirements_dir, task_dir, report, mapped_ids)
    _validate_references(requirements_dir, report)
    _write_report(requirements_dir, report)
    return _result(requirements_dir, report)


def _rewrite_unmapped_refs(requirements_dir: Path, task_dir: Path, report: dict, mapped_ids: set[str]) -> None:
    unmapped = {item["id"]: item for item in report["unmapped"]}
    if not unmapped:
        return
    for md_path in sorted(requirements_dir.rglob("*.md")):
        content = md_path.read_text(encoding="utf-8")
        changed = False
        for asset_id, item in unmapped.items():
            if asset_id in mapped_ids:
                continue
            source = (task_dir / item["path"]).resolve()
            if not source.is_file():
                continue
            relative = os.path.relpath(source, md_path.parent).replace(os.sep, "/")
            for old_ref in dict.fromkeys((item.get("markdown_ref"), item["path"])):
                if not isinstance(old_ref, str) or not old_ref:
                    continue
                if old_ref in content:
                    content = content.replace(old_ref, relative)
                    changed = True
                    item["rewritten_in"] = md_path.relative_to(requirements_dir).as_posix()
                    break
        if changed:
            md_path.write_text(content, encoding="utf-8")


def _validate_references(requirements_dir: Path, report: dict) -> None:
    for md_path in sorted(requirements_dir.rglob("*.md")):
        content = md_path.read_text(encoding="utf-8")
        for reference in _extract_references(content):
            if any(reference.startswith(prefix) for prefix in EXTERNAL):
                continue
            target = (md_path.parent / reference).resolve()
            if not target.is_file():
                report["dangling"].append(
                    {
                        "markdown": md_path.relative_to(requirements_dir).as_posix(),
                        "reference": reference,
                    }
                )
    for item in report["dangling"]:
        report["warnings"].append(f"图片引用无法解析：{item['markdown']} -> {item['reference']}")


def _extract_references(content: str) -> list[str]:
    refs: list[str] = []
    for pattern in (IMAGE_MD, IMAGE_HTML):
        for match in pattern.finditer(content):
            value = match.group(1).strip()
            if value and value not in refs:
                refs.append(value)
    return refs


def _safe_filename(value: str) -> str:
    name = Path(value).name
    return re.sub(r'[\\/:*?"<>|]+', "_", name)


def _write_report(requirements_dir: Path, report: dict) -> None:
    (requirements_dir / "assets_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _result(requirements_dir: Path, report: dict) -> dict:
    return {
        "copiedAssetCount": len(report["copied"]),
        "unmappedAssetCount": len(report["unmapped"]),
        "danglingImageCount": len(report["dangling"]),
        "assetWarnings": list(report["warnings"]),
        "assetReportPath": str(requirements_dir / "assets_report.json"),
    }
