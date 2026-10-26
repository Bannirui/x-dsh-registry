"""调用 vendored 拆分/校验脚本。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


class SplitRunError(RuntimeError):
    """拆分管线脚本执行失败。"""


def run_pipeline(
    *,
    task_dir: Path,
    markdown_path: Path,
    chunks_data: dict,
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

    (requirements_dir / "split_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    files = sorted(
        path.relative_to(requirements_dir).as_posix()
        for path in requirements_dir.rglob("*.md")
    )
    functional_count = sum(
        1 for chunk in chunks_data.get("chunks", []) if chunk.get("chunk_type") == "functional_requirement"
    )
    return {
        "treeDir": str(requirements_dir),
        "chunksPath": str(chunks_path),
        "requirementCount": functional_count,
        "chunkCount": len(chunks_data.get("chunks", [])),
        "files": files,
        "validationWarnings": validation_warnings,
    }


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
