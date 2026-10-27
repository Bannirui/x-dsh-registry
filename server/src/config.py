"""集中配置

静态配置来自环境变量，进程启动时解析一次并缓存；模型/OCR 凭据从 harness 凭据库读取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class ConfigError(RuntimeError):
    """配置缺失或非法"""


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前为 {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，当前为 {raw!r}") from exc


# Models 页面写入凭据库时使用的引用名（不是环境变量）。
_CREDENTIAL_REFS = ("DEEPSEEK_API_KEY", "DEEPSEEK_OFFICIAL_API_KEY")
_credential_cache: tuple[str, int, int, dict[str, str]] | None = None


def normalize_proxy_env() -> None:
    """去掉 NO_PROXY 中 IPv6 地址的方括号。

    httpx 会把 ``NO_PROXY`` 里的 ``[::1]`` 误判为域名并生成非法挂载键
    ``all://*[::1]``，创建客户端时抛 ``InvalidURL: Invalid port``。
    这里统一改写成 ``::1``，幂等且可重复调用。
    """
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        hosts: list[str] = []
        for host in raw.split(","):
            item = host.strip()
            if not item:
                continue
            if item.startswith("[") and item.endswith("]"):
                item = item[1:-1]
            if item not in hosts:
                hosts.append(item)
        os.environ[name] = ",".join(hosts)


def _dsh_home() -> Path:
    raw = os.environ.get("DSH_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".dsh"


def _credential_refs() -> dict[str, str]:
    """读取 harness 凭据库的 refs 段（Models / OCR 设置页写入）。"""
    global _credential_cache
    path = _dsh_home() / ".credentials.yaml"
    try:
        stat = path.stat()
    except OSError:
        return {}
    location = str(path)
    stamp = (location, stat.st_mtime_ns, stat.st_size)
    if _credential_cache is not None and _credential_cache[:3] == stamp:
        return _credential_cache[3]
    try:
        import yaml
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - 凭据文件不可读时视为未配置
        return {}
    raw_refs = document.get("refs") if isinstance(document, dict) else None
    refs: dict[str, str] = {}
    if isinstance(raw_refs, dict):
        for name, value in raw_refs.items():
            if isinstance(name, str) and isinstance(value, str) and value.strip():
                refs[name] = value.strip()
    _credential_cache = (*stamp, refs)
    return refs


def resolve_credential(ref: str) -> str:
    """解析一个凭据引用，只从 harness 凭据库读取。"""
    name = (ref or "").strip()
    if not name:
        return ""
    return _credential_refs().get(name, "")


def resolve_api_key() -> str:
    """只使用 harness Web Models 页面保存的 key。"""
    for ref in _CREDENTIAL_REFS:
        value = resolve_credential(ref)
        if value:
            return value
    return ""


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    host: str
    port: int
    workers: int
    log_level: str
    llm_timeout: float
    llm_max_retries: int
    graceful_timeout: int
    output_dir: str
    max_source_mb: int
    split_max_chars: int
    ocr_enabled: bool
    ocr_provider: str
    ocr_base_url: str
    ocr_api_key: str
    ocr_model: str
    ocr_dpi: int
    ocr_timeout: float
    ocr_max_retries: int
    ocr_min_confidence: float
    ocr_concurrency: int
    ocr_max_pages: int
    task_concurrency: int
    task_max_jobs: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """解析并缓存配置 允许api_key为空 由启动期校验强制要求。"""
    return Settings(
        api_key=resolve_api_key(),
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        model=os.environ.get("XAGENTS_MODEL", "deepseek-chat"),
        host=os.environ.get("XAGENTS_HOST", "127.0.0.1"),
        port=_get_int("XAGENTS_PORT", 8077),
        workers=_get_int("XAGENTS_WORKERS", 1),
        log_level=os.environ.get("XAGENTS_LOG_LEVEL", "info").lower(),
        llm_timeout=_get_float("XAGENTS_LLM_TIMEOUT", 120.0),
        llm_max_retries=_get_int("XAGENTS_LLM_MAX_RETRIES", 2),
        graceful_timeout=_get_int("XAGENTS_GRACEFUL_TIMEOUT", 30),
        output_dir=os.environ.get(
            "XAGENTS_OUTPUT_DIR",
            str(Path(__file__).resolve().parents[2] / "outputs"),
        ),
        max_source_mb=_get_int("XAGENTS_MAX_SOURCE_MB", 200),
        split_max_chars=_get_int("XAGENTS_SPLIT_MAX_CHARS", 120000),
        ocr_enabled=_get_bool("XAGENTS_OCR_ENABLED", True),
        ocr_provider=os.environ.get("XAGENTS_OCR_PROVIDER", "auto").strip().lower(),
        ocr_base_url=os.environ.get("XAGENTS_OCR_BASE_URL", "").strip().rstrip("/"),
        ocr_api_key=os.environ.get("XAGENTS_OCR_API_KEY", "").strip(),
        ocr_model=os.environ.get("XAGENTS_OCR_MODEL", "").strip(),
        ocr_dpi=_get_int("XAGENTS_OCR_DPI", 180),
        ocr_timeout=_get_float("XAGENTS_OCR_TIMEOUT", 60.0),
        ocr_max_retries=_get_int("XAGENTS_OCR_MAX_RETRIES", 1),
        ocr_min_confidence=_get_float("XAGENTS_OCR_MIN_CONFIDENCE", 0.5),
        ocr_concurrency=_get_int("XAGENTS_OCR_CONCURRENCY", 1),
        ocr_max_pages=_get_int("XAGENTS_OCR_MAX_PAGES", 50),
        task_concurrency=_get_int("XAGENTS_TASK_CONCURRENCY", 2),
        task_max_jobs=_get_int("XAGENTS_TASK_MAX_JOBS", 100),
    )


def require_api_key(settings: Settings | None = None) -> str:
    """返回模型凭据 缺失时抛错（每次调用都会重新解析，支持 Web 端热更新）"""
    key = resolve_api_key()
    if not key and settings is not None:
        key = settings.api_key
    if not key:
        raise ConfigError(
            "模型凭据未配置：请在 harness Web 的 Models 页面填写 API Key"
        )
    return key
