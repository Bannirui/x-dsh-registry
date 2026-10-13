x-harness
---

## 1 REQUIREMENTS

- node 22+
- python 3.10+（`build.sh` 会自动创建 `agents/.venv` 并安装 FastAPI/uvicorn/openai 等生产依赖）
- [deepseek api key](https://platform.deepseek.com/api_keys)

## 2 QUICK START

启动服务端

```sh
./build.sh

# 填写DEEPSEEK_API_KEY and X_HARNESS_TOKEN
cp .env.example .env
./run-web.sh
```

客户端调用

```sh
POST /x/requirements
  header: Authorization: Bearer <X_HARNESS_TOKEN>
  body:   {"material": "用户必须能注册并登录；登录响应时间<500ms"}
  -> 200 {"text": "<主Agent的最终答复>"}
```

### 2.1 要点

- 本版harness只允许绑定`127.0.0.1`，直接`--host 0.0.0.0`会被安全策略拒绝返回"intentionally not supported"，要让外部调用就保持 `WEB_HOST=127.0.0.1`，在前面加反向代理/隧道
  - nginx：`location / { proxy_pass http://127.0.0.1:8080; }`（并在 nginx 层做 TLS + 鉴权）
  - caddy：`reverse_proxy 127.0.0.1:8080`
  - 临时：`ssh -L 8080:127.0.0.1:8080 user@host`
- 无论是否走代理，务必设置`X_HARNESS_TOKEN`，否则接口无鉴权
- 失败的运行会返回错误码
  - `502`带`detail`，例如key错误
  - `405`表示方法不
  - `401`表示token不对

### 2.2 修改并发阈值

并发上限由`harness/cordis.patch.yml`的`maxConcurrent`控制

```yaml
config:
  maxConcurrent: 4     # 同时运行的主Agent数；<=0表示不限
```


## 3 DEV

- 1 在`agents/xagents/subagents/`下新建一个文件，如`my_agent.py`，定义`SubAgent`子类（`async def run`）并赋值给模块级`AGENT`（`name` 全局唯一
- 2 删除就直接删文件，`registry.py`会自动扫描，无需改注册代码。
- 3 在`harness/cordis.patch.yml`的`agents:`列表里加/删名字（外部服务可单独给`baseUrl`）
- 4 若要进入流水线，把它加/删进`cordis.patch.yml`的`pipeline:`列表即可

> 子智能体服务端为 FastAPI + uvicorn，模型调用走官方 `openai` SDK；无凭据时不再离线兜底，`DEEPSEEK_API_KEY` 缺失会导致服务启动失败。详见 `agents/README.md`。