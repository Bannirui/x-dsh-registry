子智能体
---

> 每个子智能体一个文件

## 1 运行

```sh
cd agents
../agents/.venv/bin/python -m xagents     # 默认127.0.0.1:8077
```

## 2 接口

- `GET /health` → `{ "ok": true }`（存活探针，不需要模型凭据）
- `GET /ready` → `{ "ok": true, "agents": [...] }`（就绪探针，校验模型凭据）
- `GET /agents` → `{ "agents": [ { "name": ..., "description": ... } ] }`
- `POST /agents/{name}`，体 `{ "input": {...} }`
  → 成功 `{ "output": {...}, "stopReason": "completed" }`
  → 失败 `{ "output": null, "stopReason": "error", "diagnostic": "..." }`

快速自检（直接打接口，无需 harness）：

```sh
curl -s http://127.0.0.1:8077/health
curl -s http://127.0.0.1:8077/agents
curl -s -X POST http://127.0.0.1:8077/agents/echo \
  -H 'content-type: application/json' -d '{"input":{"hello":"world"}}'
```

## 3 LLM配置

- `DEEPSEEK_API_KEY`：**必填**，模型凭据
- `DEEPSEEK_BASE_URL`：默认`https://api.deepseek.com`
- `XAGENTS_MODEL`：默认`deepseek-chat`
- `XAGENTS_LLM_TIMEOUT`：单次调用超时秒数
- `XAGENTS_LLM_MAX_RETRIES`：失败重试次数
- `XAGENTS_LOG_LEVEL` 日志级别
- `XAGENTS_GRACEFUL_TIMEOUT`： 优雅停机秒数。