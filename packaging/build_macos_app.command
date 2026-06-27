#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."

APP_PATH="dist/颗粒度计数台.app"
if [ -e "$APP_PATH" ]; then
  echo "构建已停止：$APP_PATH 已存在。请先手动移动旧版本，避免被覆盖。"
  exit 2
fi

python3 -m PyInstaller \
  --windowed \
  --name "颗粒度计数台" \
  --add-data "static:static" \
  --hidden-import "PIL._tkinter_finder" \
  app.py

echo "构建完成：$APP_PATH"
