"""调用 vendored 拆分/校验脚本。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .assets import follow_assets
from .outline import parse_outline

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


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
    headings = parse_outline(markdown_path.read_text(encoding="utf-8"))
    order = {heading.section_id: index for index, heading in enumerate(headings)}

    def position(chunk: dict) -> tuple[int, str]:
        values = [
            order.get(str(section.get("section_id")))
            for section in chunk.get("source_sections", [])
            if isinstance(section, dict) and section.get("section_id")
        ]
        available = [value for value in values if value is not None]
        return (min(available) if available else len(order), str(chunk.get("chunk_id") or ""))

    title = Path(source_name).stem or "需求文档"
    parts = [f"# {title}", ""]
    for chunk in sorted(chunks, key=position):
        relative = str(chunk.get("source_relative_path") or "")
        if not relative:
            continue
        md_path = requirements_dir / relative
        if not md_path.is_file():
            continue
        content = md_path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        prefix = Path(relative).parent.as_posix()
        if prefix and prefix != ".":
            content = _prefix_asset_refs(content, prefix)
        parts.append(content)
        parts.append("")

    final_path = requirements_dir / "final.md"
    final_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    return final_path


def _prefix_asset_refs(content: str, prefix: str) -> str:
    content = re.sub(
        r"(!\[[^\]]*\]\()(\./)?assets/",
        lambda match: f"{match.group(1)}{prefix}/assets/",
        content,
    )
    content = re.sub(
        r"(<img[^>]*\bsrc=[\"'])(\./)?assets/",
        lambda match: f"{match.group(1)}{prefix}/assets/",
        content,
        flags=re.IGNORECASE,
    )
    return content


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
