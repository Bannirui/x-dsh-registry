"""需求补全推荐
"""

from __future__ import annotations

from .. import llm
from ..base import SubAgent, as_list, dump

NAME = "completion"
DESCRIPTION = "需求补全推荐"


class CompletionAgent(SubAgent):
    SYSTEM = (
        "你是需求补全与推荐专家。对每条需求补全遗漏的验收标准、边界条件与非功能属性，"
        "并给出实现建议。只输出 JSON："
        "{\"items\": [...], \"recommendations\": [{\"itemId\": str, \"suggestion\": str}]}。"
        "所有 item 的 status 设为 completed。"
    )

    async def run(self, payload: dict) -> dict:
        items = as_list(payload.get("items"))
        result = await llm.complete_json(self.SYSTEM, f"待补全需求：\n{dump(items)}")
        out_items = result.get("items") if isinstance(result, dict) else None
        recs = result.get("recommendations") if isinstance(result, dict) else None
        if not isinstance(out_items, list) or not out_items:
            raise RuntimeError("completion：模型未返回有效的 items")
        return {"items": out_items, "recommendations": as_list(recs)}


AGENT = CompletionAgent(NAME, DESCRIPTION)
