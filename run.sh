#!/bin/bash
# 五人制足球分析工作台 — 快捷启动
#
# 用法:
#   ./run.sh                 # 默认端口 8765
#   PORT=9090 ./run.sh       # 指定端口
#   ./run.sh --no-open       # 不自动打开浏览器
#
# 它做三件事:
#   1. 启动 12_serve_analysis_app.py(workspace web 服务)
#   2. 等服务起来后自动打开浏览器
#   3. 在前台运行,Ctrl+C 即可停服务

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8765}"
OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    --port=*)  PORT="${arg#--port=}" ;;
    -h|--help)
      grep '^#' "$0" | sed -e '/^#!/d' -e 's/^# \?//'
      exit 0
      ;;
  esac
done

if [ ! -x ".venv/bin/python" ]; then
  echo "❌ .venv/bin/python 不存在。先在仓库根创建虚拟环境:"
  echo "   python3 -m venv .venv"
  echo "   .venv/bin/python -m pip install -r requirements-detect.txt"
  exit 1
fi

# 端口已被占用?提示后跳过启动,直接打开浏览器
if curl -sf "http://localhost:${PORT}/" >/dev/null 2>&1; then
  echo "✓ 服务已在 http://localhost:${PORT}/ 运行,直接打开浏览器。"
  [ "$OPEN_BROWSER" = "1" ] && open "http://localhost:${PORT}/"
  exit 0
fi

echo "▶ 启动 workspace web 服务: http://localhost:${PORT}/"
echo "  (Ctrl+C 停止服务)"

# 后台启动服务,等就绪后打开浏览器,然后回到前台等待用户中断
.venv/bin/python scripts/12_serve_analysis_app.py --port "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null; exit 0' INT TERM

# 最多等 15 秒
for i in $(seq 1 30); do
  if curl -sf "http://localhost:${PORT}/" >/dev/null 2>&1; then
    [ "$OPEN_BROWSER" = "1" ] && open "http://localhost:${PORT}/"
    break
  fi
  sleep 0.5
done

wait "$SERVER_PID"
