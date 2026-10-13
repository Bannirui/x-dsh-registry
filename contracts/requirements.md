# 数据契约（contracts）

三个子智能体之间、以及 harness 与子智能体之间，统一用 **JSON** 交互。
约定：每个子智能体的 `output` 就是下一阶段子智能体的 `input`（可以带自己的字段）。

## 核心对象：RequirementItem

```jsonc
{
  "id": "REQ-001",              // 全局唯一，抽取阶段生成
  "title": "一句话标题",
  "description": "详细描述",
  "category": "functional",     // functional | non-functional | constraint | data | interface
  "priority": "must",           // must | should | could | wont
  "source": "原始材料出处",
  "status": "extracted",        // extracted | needs_clarification | clarified | completed
  "acceptance_criteria": [],    // 验收标准
  "questions": [],              // 澄清阶段填：待澄清问题
  "recommendations": []         // 补全阶段填：补全/推荐建议
}
```

## 阶段输入/输出

### 1. extraction（需求抽取）
- 输入：`{ "raw": "<原始需求材料文本>" }`
- 输出：`{ "items": [RequirementItem, ...] }`

### 2. clarification（需求澄清）
- 输入：抽取的输出 `{ "items": [...] }`
- 输出：`{ "items": [...], "questions": [ { "itemId": "REQ-001", "question": "..." } ] }`
  - 每个 item 的 `status` 更新为 `needs_clarification` 或 `clarified`。

### 3. completion（需求补全推荐）
- 输入：澄清的输出
- 输出：`{ "items": [...], "recommendations": [ { "itemId": "REQ-001", "suggestion": "..." } ] }`
  - item 的 `status` 更新为 `completed`。

## HTTP 协议（Python 服务 <-> harness provider）

- `GET /agents` → `{ "agents": [ { "name": "...", "description": "..." } ] }`
- `POST /agents/{name}`，请求体 `{ "input": <object> }`
  → 成功 `{ "output": <object>, "stopReason": "completed" }`
  → 失败 `{ "output": null, "stopReason": "error", "diagnostic": "..." }`

> 新增子智能体：在 `agents/xagents/subagents/` 下新建一个文件（定义 `SubAgent` 子类并赋值给模块级 `AGENT`），
> 由 `registry.py` 自动注册；再在 `harness/cordis.patch.yml` 的 `agents` 列表里加名字即可。删除就删文件。

## agent4：echo（内置子智能体）

`echo` 是第 4 个子智能体，实现为 `agents/xagents/subagents/echo.py`（与其他子智能体一样的一个文件），
行为是**原样回显输入**，用作占位/链路自检。

- 契约与其他子智能体相同：`POST /agents/echo`，体 `{ "input": <任意 JSON> }`
  → `{ "output": <原样返回的 input>, "stopReason": "completed" }`
- 它默认是需求流水线的**最后一站**（透传，作为未来新阶段的占位）。

## 流水线顺序可配置

`harness/cordis.patch.yml` 的 `pipeline` 决定流水线顺序，每一步的输出作为下一步的输入：

```yaml
pipeline:
  - extraction
  - clarification
  - completion
  - echo
```

新增子智能体后，把它加进 `pipeline` 即可进入流程；不改则是独立可调用。

## 每个子智能体可独立指定服务地址

`harness/cordis.patch.yml` 的 `agents` 是对象列表，每项 `{ name, baseUrl? }`：
- 不写 `baseUrl` → 用顶层 `baseUrl`（默认 8077，即 `xagents` 服务）；
- 写了 `baseUrl` → 指向别的服务（把某个子智能体放到外部服务/别的语言/团队时用）。

这样你可以把不同子智能体部署到不同服务/语言/团队。
