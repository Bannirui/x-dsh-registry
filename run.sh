#!/usr/bin/env bash
#
# run:
#   - 启动Python子智能体服务
#   - 构建并安装harness插件
#   - 跑需求流水线
#
# usage:
#   cp .env.example .env    # 填入DEEPSEEK_API_KEY
#   ./run.sh
#   ./run.sh "your inputs"
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
HARNESS="$ROOT/harness"
AGENTS="$ROOT/agents"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

: "${DEEPSEEK_API_KEY:?请先在.env或环境变量里设置DEEPSEEK_API_KEY}"

PORT="${XAGENTS_PORT:-8077}"

# 子智能体服务依赖FastAPI/uvicorn/openai，必须用build.sh建好的venv
PY="$AGENTS/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "未找到venv($AGENTS/.venv)：请先运行 ./build.sh 安装依赖" >&2
  exit 1
fi

echo ">>> 1/5 启动子智能体服务(127.0.0.1:$PORT, python=$PY)"
( cd "$AGENTS" && XAGENTS_PORT="$PORT" "$PY" -m xagents ) &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null || { echo "子智能体服务启动失败" >&2; exit 1; }

cd "$HARNESS"
echo ">>> 2/5 安装harness依赖"
if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi

echo ">>> 3/5 构建插件+离线自检"
npm run --silent build
node scripts/verify.mjs

export DSH_HOME="${DSH_HOME:-$HARNESS/.dsh-home}"
DSH="./node_modules/.bin/dsh"

echo ">>> 4/5 安装插件到headless profile"
"$DSH" --profile headless --dump-default-config >/dev/null
# pnpm的file: 依赖不会自动刷新，先remove再add才能装到最新构建。
"$DSH" plugin --profile headless remove x-harness >/dev/null 2>&1 || true
"$DSH" plugin --profile headless add "file:$HARNESS" >/dev/null

TASK="${1:-只调用requirements_pipeline一次处理这段需求，然后用中文总结结果并结束，不要调用其它工具：用户必须能够注册账号并登录；登录接口响应时间必须小于 500ms；系统必须符合 GDPR 对个人数据的处理要求。}"
echo ">>> 5/5 运行（实时进度）"
echo "    $TASK"
echo
# --json 把运行事件流出来，交给stream.mjs渲染进度；否则headless在模型思考时完全静默。
"$DSH" --profile headless --json "$TASK" | node "$HARNESS/scripts/stream.mjs"
