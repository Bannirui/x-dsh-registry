"""内存中的长任务管理，供 /tasks 异步接口和 harness 轮询使用。"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from .registry import run_agent

Progress = Callable[[int, str, str], None]


class TaskManager:
    def __init__(self, *, max_concurrency: int = 2, max_jobs: int = 100) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._max_jobs = max(10, max_jobs)
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    async def submit(self, agent: str, payload: dict) -> dict:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = _now()
        job = {
            "taskId": task_id,
            "agent": agent,
            "status": "queued",
            "progress": 0,
            "stage": "排队中",
            "message": "任务已创建，等待执行",
            "createdAt": now,
            "updatedAt": now,
            "result": None,
            "diagnostic": None,
        }
        self._jobs[task_id] = job
        self._evict()
        task = asyncio.create_task(self._run(task_id, agent, dict(payload)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.snapshot(task_id) or {}

    def snapshot(self, task_id: str) -> dict | None:
        job = self._jobs.get(task_id)
        return dict(job) if job else None

    def list(self) -> list[dict]:
        return [
            {
                key: job[key]
                for key in ("taskId", "agent", "status", "progress", "stage", "message", "createdAt", "updatedAt")
            }
            for job in reversed(self._jobs.values())
        ]

    async def _run(self, task_id: str, agent: str, payload: dict) -> None:
        async with self._semaphore:
            self._set(task_id, status="running", progress=1, stage="开始处理", message="正在执行任务")
            loop = asyncio.get_running_loop()

            def progress(value: int, stage: str, message: str = "") -> None:
                loop.call_soon_threadsafe(
                    lambda: self._set(
                        task_id,
                        progress=value,
                        stage=stage,
                        message=message,
                    )
                )

            payload["_progress"] = progress
            try:
                result = await run_agent(agent, payload)
            except Exception as exc:  # noqa: BLE001
                self._set(
                    task_id,
                    status="failed",
                    stage="失败",
                    message=f"{type(exc).__name__}: {exc}",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                )
                return
            self._set(
                task_id,
                status="completed",
                progress=100,
                stage="完成",
                message="任务执行完成",
                result=result,
            )

    def _set(self, task_id: str, **values: Any) -> None:
        job = self._jobs.get(task_id)
        if job is None:
            return
        if job.get("status") in {"completed", "failed"} and values.get("status") not in {"completed", "failed"}:
            return
        if "progress" in values and isinstance(values["progress"], int):
            values["progress"] = max(int(job.get("progress") or 0), min(100, values["progress"]))
        job.update(values)
        job["updatedAt"] = _now()

    def _evict(self) -> None:
        while len(self._jobs) > self._max_jobs:
            victim = next(
                (task_id for task_id, job in self._jobs.items() if job["status"] in {"completed", "failed"}),
                None,
            )
            if victim is None:
                return
            self._jobs.pop(victim, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
