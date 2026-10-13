harness TS薄层
---

这一层只做两件事，通常不需要改：

- 1 `src/http-provider.ts`：把一个HTTP子智能体适配成harness的`SubagentProvider`
- 2 `src/index.ts`：`apply(ctx, config)`里注册所有 provider，并注册两个工具：
   - `requirements_pipeline`
   - `x_call_agent`（通用调试：调用任意已注册子智能体）
3. `src/http-api.ts`：在带web服务器的profile（`web`）里注册`POST /x/requirements`，收到请求就新建一个主Agent会话跑任务并返回最终文本（模板来自 `@deepseek-ai/dsh-headless`）。

`agents`配置是对象列表`{ name, baseUrl? }`：`baseUrl`可给每个子智能体单独指定服务地址
（默认都用 `xagents` 服务的 8077；某个子智能体在外部服务时才需要写）。

HTTP并发：`config.maxConcurrent`限制同时运行的主Agent数，超出FIFO排队；`<=0`表示不限。

## 1 BUILD

```sh
npm install
npm run build
npm run verify        # 离线验证编排逻辑，不调模型
```

## 2 安装到profile

需要一个带base的profile（提供`tools`/`subagents`），例如 `headless`：

```sh
export DSH_HOME="$PWD/.dsh-home"
./node_modules/.bin/dsh --profile headless --dump-default-config >/dev/null
# pnpm file: 不会自动刷新，改了代码要先remove再add
./node_modules/.bin/dsh plugin --profile headless remove x-harness 2>/dev/null || true
./node_modules/.bin/dsh plugin --profile headless add "file:$PWD"
```