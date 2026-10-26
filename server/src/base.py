from __future__ import annotations

import json
from dataclasses import dataclass

CATEGORIES = {"functional", "non-functional", "constraint", "data", "interface"}
PRIORITIES = {"must", "should", "could", "wont"}


def item(
    item_id: str,
    title: str,
    description: str,
    *,
    category: str = "functional",
    priority: str = "should",
    source: str = "",
    status: str = "extracted",
) -> dict:
    """构造一条标准需求条目"""
    return {
        "id": item_id,
        "title": title.strip(),
        "description": description.strip(),
        "category": category if category in CATEGORIES else "functional",
        "priority": priority if priority in PRIORITIES else "should",
        "source": source,
        "status": status,
        "acceptance_criteria": [],
        "questions": [],
        "recommendations": [],
    }


def as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def as_list(value: object) -> list:
    return list(value) if isinstance(value, list) else []


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@dataclass
class XAgent:
    """所有子智能体的基类
    约定
    ``async run(payload: dict) -> dict`` 返回值必须是JSON可序列化的dict
    并成为流水线中下一阶段的输入 异步以配合FastAPI/异步LLM客户端
    """

    name: str
    description: str

    async def run(self, payload: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError
