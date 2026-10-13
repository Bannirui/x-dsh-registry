"""需求澄清
"""

from __future__ import annotations

from .. import llm
from ..base import SubAgent, as_list, dump

NAME = "clarification"
DESCRIPTION = "需求澄清"


class ClarificationAgent(SubAgent):
    SYSTEM = (
        "你是需求澄清专家。对每条需求识别歧义、缺失项与不可验收之处，提出澄清问题。"
        "只输出 JSON：{\"items\": [...], \"questions\": [{\"itemId\": str, \"question\": str}]}。"
        "若某条需求信息已足够明确，将其 status 设为 clarified，并补充 acceptance_criteria；"
        "否则 status 设为 needs_clarification，把问题同时写入该 item 的 questions。"
    )

    async def run(self, payload: dict) -> dict:
        items = as_list(payload.get("items"))
        result = await llm.complete_json(self.SYSTEM, f"待澄清需求：\n{dump(items)}")
        out_items = result.get("items") if isinstance(result, dict) else None
        questions = result.get("questions") if isinstance(result, dict) else None
        if not isinstance(out_items, list) or not out_items:
            raise RuntimeError("clarification：模型未返回有效的 items")
        return {"items": out_items, "questions": as_list(questions)}


AGENT = ClarificationAgent(NAME, DESCRIPTION)
