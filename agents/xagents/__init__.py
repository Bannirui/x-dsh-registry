"""开发子智能体集合
- 每个子智能体在``xagents/subagents/``下各占一个文件，由``registry``自动发现
- 统一通过HTTP暴露，harness侧把每个子智能体注册成subagent provider
"""

from .base import SubAgent
from .registry import REGISTRY, run_agent

__all__ = ["SubAgent", "REGISTRY", "run_agent"]
