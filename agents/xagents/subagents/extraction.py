"""需求抽取
"""

from __future__ import annotations

from .. import llm
from ..base import SubAgent, as_str, item

NAME = "extraction"
DESCRIPTION = "需求抽取"


class ExtractionAgent(SubAgent):
    SYSTEM = (
        "你是资深需求分析师，负责从原始材料中抽取原子化、可追踪的软件需求。"
        "只输出 JSON，格式为 {\"items\": [RequirementItem, ...]}。"
        "RequirementItem 字段：id,title,description,category,priority,source,status,"
        "acceptance_criteria,questions,recommendations。"
        "id 形如 REQ-001；category ∈ functional|non-functional|constraint|data|interface；"
        "priority ∈ must|should|could|wont；status 固定为 extracted。"
    )

    async def run(self, payload: dict) -> dict:
        raw = as_str(payload.get("raw"))
        result = await llm.complete_json(self.SYSTEM, f"原始材料：\n{raw}")
        raw_items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            raise RuntimeError("extraction：模型未返回有效的 items")
        items = [
            item(
                as_str(it.get("id"), f"REQ-{i + 1:03d}"),
                as_str(it.get("title"), "未命名需求"),
                as_str(it.get("description")),
                category=as_str(it.get("category"), "functional"),
                priority=as_str(it.get("priority"), "should"),
                source=as_str(it.get("source"), "原始材料"),
                status="extracted",
            )
            for i, it in enumerate(raw_items)
        ]
        return {"items": items}


AGENT = ExtractionAgent(NAME, DESCRIPTION)
