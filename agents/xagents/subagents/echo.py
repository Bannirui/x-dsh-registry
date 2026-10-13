"""回显
"""

from __future__ import annotations

from ..base import SubAgent

NAME = "echo"
DESCRIPTION = "回显输入"


class EchoAgent(SubAgent):
    async def run(self, payload: dict) -> dict:
        return payload


AGENT = EchoAgent(NAME, DESCRIPTION)
