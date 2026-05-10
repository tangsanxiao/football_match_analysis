#!/bin/bash
# 一键再次打开工作台浏览器页(适合服务已经在跑、你只是关掉了浏览器标签)。
# 用法:
#   ./open.sh           # 默认 8765
#   PORT=9090 ./open.sh # 换端口
PORT="${PORT:-8765}"
URL="http://localhost:${PORT}/"
if curl -sf "$URL" >/dev/null 2>&1; then
  echo "→ $URL"
  open "$URL"
else
  echo "❌ 服务未在 $URL 运行。先启动:"
  echo "   open ~/Applications/'Football Analysis.app'   # 双击 App"
  echo "   或在项目根:  ./run.sh"
  exit 1
fi
