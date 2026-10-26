#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
HARNESS="$ROOT/harness"
AGENTS="$ROOT/server"
# python -m启动
AGENTS_ALIAS="src"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${DEEPSEEK_API_KEY:?请先在.env或环境变量里设置DEEPSEEK_API_KEY}"

PORT="${XAGENTS_PORT:-8077}"
WEB_PORT="${WEB_PORT:-8080}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
TOKEN="${X_HARNESS_TOKEN:-}"

if [ "$WEB_HOST" != "127.0.0.1" ]; then
  cat >&2 <<'MSG'
错误：本版harness出于安全（防止把可执行代码暴露到网络）**只允许绑定127.0.0.1**，直接--host 0.0.0.0会被拒绝。
     要让外部调用，请保持WEB_HOST=127.0.0.1，然后在前面放反向代理/隧道：
       - nginx: proxy_pass http://127.0.0.1:8080;(并在nginx层做TLS+鉴权)
       - caddy: reverse_proxy 127.0.0.1:8080
       - 临时: ssh -L 8080:127.0.0.1:8080 user@host
MSG
  exit 1
fi

PY="$AGENTS/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "未找到venv($AGENTS/.venv)：请先运行./build.sh安装依赖" >&2
  exit 1
fi

S1=""
WEBPID=""
cleanup() {
  [ -n "$WEBPID" ] && kill "$WEBPID" 2>/dev/null || true
  [ -n "$S1" ] && kill "$S1" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo ">>> 1/4 启动py服务(:$PORT)"
( cd "$AGENTS" && XAGENTS_PORT="$PORT" "$PY" -m "$AGENTS_ALIAS" ) & S1=$!
for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null || { echo "py应用启动失败" >&2; exit 1; }

cd "$HARNESS"
if [ ! -d node_modules ]; then
  echo ">>> 2/4 安装harness依赖"
  npm install --no-audit --no-fund
fi
echo ">>> 2/4 构建插件"
npm run --silent build

export DSH_HOME="${DSH_HOME:-$HARNESS/.dsh-home}"
DSH="./node_modules/.bin/dsh"

echo ">>> 3/4 安装插件到web profile"
"$DSH" --profile web --dump-default-config >/dev/null
"$DSH" plugin --profile web remove x-dsh >/dev/null 2>&1 || true
"$DSH" plugin --profile web add "file:$HARNESS" >/dev/null

echo ">>> 4/4 启动web服务(:${WEB_PORT})"
WEBLOG="$HARNESS/.dsh-home/web.log"
X_HARNESS_TOKEN="$TOKEN" "$DSH" web --host "$WEB_HOST" --port "$WEB_PORT" >"$WEBLOG" 2>&1 & WEBPID=$!

STARTED=0
for _ in $(seq 1 120); do
  if ! kill -0 "$WEBPID" 2>/dev/null; then break; fi
  if curl -s -o /dev/null -m1 "http://127.0.0.1:$WEB_PORT/"; then STARTED=1; break; fi
  sleep 0.5
done

if [ "$STARTED" != "1" ]; then
  echo "错误：web服务未能启动。最近日志（$WEBLOG）：" >&2
  tail -n 50 "$WEBLOG" >&2 || true
  exit 1
fi

AUTH_HINT="（未设置X_HARNESS_TOKEN：无鉴权，仅限本机）"
AUTH_LINE=""
if [ -n "$TOKEN" ]; then
  AUTH_HINT="Authorization: Bearer <X_HARNESS_TOKEN>"
  AUTH_LINE="  -H 'authorization: Bearer $TOKEN' \\"
fi

WEB_URL=""
for _ in $(seq 1 20); do
  WEB_URL="$(grep -oE 'http://[^[:space:]]*token=[^[:space:]]*' "$WEBLOG" | tail -n1 || true)"
  [ -n "$WEB_URL" ] && break
  sleep 0.25
done
[ -n "$WEB_URL" ] || WEB_URL="http://$WEB_HOST:$WEB_PORT/"

cat <<EOF

>>> 服务已启动（日志：$WEBLOG）
    Web UI   : $WEB_URL
    HTTP API : POST http://$WEB_HOST:$WEB_PORT/x/dsh/registry
    鉴权     : $AUTH_HINT
    示例:
      curl -s http://$WEB_HOST:$WEB_PORT/x/dsh/registry\\
        -H 'content-type: application/json' \\
        $AUTH_LINE
        -d '{"material":"用户必须能注册并登录；登录响应时间<500ms"}'

    按 Ctrl-C 停止。
EOF

wait "$WEBPID"
