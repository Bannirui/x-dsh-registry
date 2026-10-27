"""HTTP 服务（FastAPI + uvicorn）把子智能体暴露给harness。

路由：
  GET  /health          存活探针（不需要模型凭据）
  GET  /ready           就绪探针（校验模型凭据，失败返回 503）
  GET  /agents          列出所有子智能体
  POST /agents/{name}   运行一个子智能体，体 {"input": {...}}
  POST /ocr/test        用样例图验证 OCR profile
  POST /llm/test        验证语义分块 LLM 的端点/模型/密钥

所有错误都以harness契约形式返回：``{"output": null, "stopReason": "error", "diagnostic": "..."}`` 不向调用方抛裸异常/堆栈
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import llm
from .config import ConfigError, get_settings, normalize_proxy_env, require_api_key
from .ocr import test_ocr
from .registry import REGISTRY, run_agent
from .tasks import TaskManager

normalize_proxy_env()

logger = logging.getLogger("xagents")


class RunBody(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class AgentInfo(BaseModel):
    name: str
    description: str


class AgentsResponse(BaseModel):
    agents: list[AgentInfo]


class RunResponse(BaseModel):
    output: Any = None
    stopReason: str
    diagnostic: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    agents: list[str] | None = None


class TaskSubmitResponse(BaseModel):
    taskId: str
    status: str


class OcrTestBody(BaseModel):
    ocr: dict[str, Any] = Field(default_factory=dict)
    profileId: str = ""


class LlmTestBody(BaseModel):
    llm: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    taskId: str
    agent: str
    status: str
    progress: int
    stage: str
    message: str
    createdAt: str
    updatedAt: str
    result: Any = None
    diagnostic: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # 凭据可在 harness Web 的 Models 页面配置，启动时不强制要求
    try:
        require_api_key(settings)
    except ConfigError as exc:
        logger.warning("%s（配置后自动生效，无需重启）", exc)
    logger.info(
        "xagents启动：model=%s base_url=%s workers=%s agents=%s",
        settings.model,
        settings.base_url,
        settings.workers,
        list(REGISTRY),
    )
    try:
        yield
    finally:
        await llm.aclose()
        logger.info("xagents已关闭")


app = FastAPI(
    title="x-agents",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

TASKS = TaskManager(
    max_concurrency=get_settings().task_concurrency,
    max_jobs=get_settings().task_max_jobs,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


def _error(status: int, diagnostic: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"output": None, "stopReason": "error", "diagnostic": diagnostic},
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(ok=True)


@app.post("/ocr/test")
async def ocr_test(body: OcrTestBody) -> Any:
    try:
        return await asyncio.to_thread(test_ocr, body.ocr, body.profileId)
    except Exception as exc:  # noqa: BLE001 - 测试接口返回可读诊断
        logger.exception("OCR 测试失败")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/llm/test")
async def llm_test(body: LlmTestBody) -> Any:
    try:
        return await llm.test_llm(body.llm)
    except Exception as exc:  # noqa: BLE001 - 测试接口返回可读诊断
        logger.exception("语义分块 LLM 测试失败")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/ready", response_model=HealthResponse, responses={503: {"model": RunResponse}})
async def ready() -> Any:
    try:
        require_api_key()
    except ConfigError as exc:
        return _error(503, str(exc))
    return HealthResponse(ok=True, agents=list(REGISTRY))


@app.get("/agents", response_model=AgentsResponse)
async def list_agents() -> AgentsResponse:
    return AgentsResponse(
        agents=[AgentInfo(name=name, description=agent.description) for name, agent in REGISTRY.items()]
    )


@app.post("/agents/{name}", response_model=RunResponse, responses={404: {"model": RunResponse}, 500: {"model": RunResponse}})
async def run(name: str, body: RunBody) -> Any:
    if name not in REGISTRY:
        return _error(404, f"unknown agent: {name}")
    try:
        output = await run_agent(name, body.input)
    except Exception as exc:  
        logger.exception("服务 %s 执行失败", name)
        return _error(500, f"{type(exc).__name__}: {exc}")
    return RunResponse(output=output, stopReason="completed")


@app.post("/tasks/{name}", response_model=TaskSubmitResponse, responses={404: {"model": RunResponse}})
async def submit_task(name: str, body: RunBody) -> Any:
    if name not in REGISTRY:
        return _error(404, f"unknown agent: {name}")
    snapshot = await TASKS.submit(name, body.input)
    logger.info("创建长任务 %s（agent=%s）", snapshot.get("taskId"), name)
    return TaskSubmitResponse(taskId=snapshot["taskId"], status=snapshot["status"])


@app.get("/tasks", response_model=list[TaskResponse])
async def list_tasks() -> Any:
    return TASKS.list()


@app.get("/tasks/{task_id}", response_model=TaskResponse, responses={404: {"model": RunResponse}})
async def get_task(task_id: str) -> Any:
    snapshot = TASKS.snapshot(task_id)
    if snapshot is None:
        return _error(404, f"未知任务：{task_id}")
    return TaskResponse(**snapshot)
