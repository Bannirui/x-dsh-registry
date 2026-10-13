#!/usr/bin/env bash
#
# 一键构建 x-harness：
#   - 校验依赖版本（node 22+ / python3）
#   - 安装并编译 TypeScript 薄层（harness -> lib/）
#   - 离线自检（不调模型）
#   - 创建 agents/.venv 并安装 Python 生产依赖（FastAPI/uvicorn/openai/httpx）
#
# 用法：
#   ./build.sh            # 安装依赖 + 编译 + 自检
#   ./build.sh --clean    # 先清理 node_modules/lib/.venv 再构建
#   ./build.sh --typecheck-only
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
HARNESS="$ROOT/harness"
AGENTS="$ROOT/agents"
VENV="$AGENTS/.venv"

CLEAN=0
TYPECHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --typecheck-only) TYPECHECK_ONLY=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数：$arg" >&2
      exit 2
      ;;
  esac
done

# ---- 0/4 环境检查 ----
echo ">>> 0/4 检查环境"

command -v node >/dev/null 2>&1 || { echo "缺少 node（需要 22+）" >&2; exit 1; }
command -v npm  >/dev/null 2>&1 || { echo "缺少 npm" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "缺少 python3（需要 3.10+）" >&2; exit 1; }

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "node 版本过低：$(node --version)，需要 22+" >&2
  exit 1
fi
echo "    node   $(node --version)"
echo "    npm    $(npm --version)"
echo "    python $(python3 --version 2>&1 | awk '{print $2}')"

# ---- 清理 ----
if [ "$CLEAN" = "1" ]; then
  echo ">>> 清理旧产物"
  rm -rf "$HARNESS/lib" "$HARNESS/node_modules" "$HARNESS/tsconfig.tsbuildinfo" "$VENV"
fi

if [ "$TYPECHECK_ONLY" = "1" ]; then
  echo ">>> 仅类型检查"
  ( cd "$HARNESS" && npm run --silent typecheck )
  echo ">>> 类型检查通过"
  exit 0
fi

# ---- 1/5 安装 harness 依赖 ----
cd "$HARNESS"
if [ ! -d node_modules ]; then
  echo ">>> 1/5 安装 harness 依赖"
  npm install --no-audit --no-fund
else
  echo ">>> 1/5 依赖已存在（跳过 npm install；--clean 可强制重装）"
fi

# ---- 2/5 编译 TypeScript ----
echo ">>> 2/5 编译 TypeScript -> lib/"
rm -rf lib
npm run --silent build
if [ ! -f lib/index.js ]; then
  echo "构建失败：未生成 lib/index.js" >&2
  exit 1
fi

# ---- 3/5 离线自检 ----
echo ">>> 3/5 离线自检（不调模型）"
node scripts/verify.mjs

# ---- 4/5 Python venv + 依赖 ----
echo ">>> 4/5 安装 Python 子智能体依赖（venv: $VENV）"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$AGENTS/requirements.txt"

# ---- 5/5 Python 自检 ----
echo ">>> 5/5 编译并导入自检"
( cd "$AGENTS" && "$VENV/bin/python" -m compileall -q xagents )
( cd "$AGENTS" && "$VENV/bin/python" -c "import xagents.server" ) && echo "    xagents 导入通过"

echo
echo ">>> 构建完成"
echo "    harness : $HARNESS/lib"
echo "    agents  : $VENV（FastAPI + uvicorn + openai）"
echo "    运行流水线：./run.sh"
echo "    启动 HTTP 服务：./run-web.sh"
