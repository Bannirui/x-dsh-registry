#!/usr/bin/env bash
#
# usage:
#   ./build.sh            # 安装依赖+编译+自检
#   ./build.sh --clean    # 先清理node_modules/lib/ .venv再构建
#   ./build.sh --typecheck-only
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
HARNESS="$ROOT/harness"
AGENTS="$ROOT/server"
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

# ---- 环境检查 ----
echo ">>> 0/5 检查环境"

command -v node >/dev/null 2>&1 || { echo "缺少node（需要 22+）" >&2; exit 1; }
command -v npm  >/dev/null 2>&1 || { echo "缺少npm" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "缺少python3（需要 3.10+）" >&2; exit 1; }

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "node版本过低：$(node --version)，需要22+" >&2
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

# ---- 安装harness依赖 ----
cd "$HARNESS"
if [ ! -d node_modules ]; then
  echo ">>> 1/5 安装harness依赖"
  npm install --no-audit --no-fund
else
  echo ">>> 1/5 依赖已存在（跳过npm install；--clean可强制重装）"
fi

# ---- 编译TypeScript ----
echo ">>> 2/5 编译TypeScript->lib/"
rm -rf lib
npm run --silent build
if [ ! -f lib/index.js ]; then
  echo "构建失败：未生成lib/index.js" >&2
  exit 1
fi

# ---- Python venv+依赖 ----
echo ">>> 3/5 安装Python（venv: $VENV）"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$AGENTS/requirements.txt"

echo
echo ">>> 构建完成"
echo "    harness : $HARNESS/lib"
echo "    agents  : $VENV（FastAPI+uvicorn+openai）"
echo "    启动HTTP服务：./run.sh"
