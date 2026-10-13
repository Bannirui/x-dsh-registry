"""自动发现并注册``xagents.subagents``包下的所有子智能体"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from .base import SubAgent
from . import subagents as _subagents_pkg


def _discover() -> dict[str, SubAgent]:
    registry: dict[str, SubAgent] = {}
    for info in pkgutil.iter_modules(_subagents_pkg.__path__):
        module: ModuleType = importlib.import_module(f"{_subagents_pkg.__name__}.{info.name}")
        agent = getattr(module, "AGENT", None)
        if agent is None:
            continue
        if not isinstance(agent, SubAgent):
            raise TypeError(f"{module.__name__}.AGENT必须是SubAgent 实例")
        if agent.name in registry:
            raise ValueError(f"子智能体名字重复: {agent.name}（来自 {module.__name__}）")
        registry[agent.name] = agent
    return registry


REGISTRY: dict[str, SubAgent] = _discover()


async def run_agent(name: str, payload: dict) -> dict:
    if name not in REGISTRY:
        raise KeyError(f"unknown agent: {name}")
    return await REGISTRY[name].run(payload)
