"""集中配置

所有配置均来自环境变量 进程启动时解析一次并缓存 生产环境通过环境变量/密钥管理注入 不读取任何配置文件
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


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，当前为 {raw!r}") from exc


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """解析并缓存配置 允许api_key为空 由启动期校验强制要求。"""
    return Settings(
        api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
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
    )


def require_api_key(settings: Settings | None = None) -> str:
    """返回模型凭据 缺失时抛错"""
    settings = settings or get_settings()
    if not settings.api_key:
        raise ConfigError("DEEPSEEK_API_KEY 未设置：必须提供模型凭据")
    return settings.api_key
