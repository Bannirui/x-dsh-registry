"""``python -m xxx``启动服务uvicorn"""

from __future__ import annotations

import logging

import uvicorn

from .config import get_settings, normalize_proxy_env


def main() -> None:
    normalize_proxy_env()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    uvicorn.run(
        "src.server:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        log_level=settings.log_level,
        timeout_graceful_shutdown=settings.graceful_timeout,
    )


if __name__ == "__main__":
    main()
