@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

set APP_PATH=dist\颗粒度计数台.exe
if exist "%APP_PATH%" (
  echo 构建已停止：%APP_PATH% 已存在。请先手动移动旧版本，避免被覆盖。
  exit /b 2
)

echo 正在安装打包依赖...
python -m pip install -r requirements-build.txt --quiet

echo 正在构建 Windows 可执行文件...
python -m PyInstaller ^
  --windowed ^
  --name "颗粒度计数台" ^
  --add-data "static;static" ^
  app.py

if %ERRORLEVEL% EQU 0 (
  echo 构建完成：%APP_PATH%
) else (
  echo 构建失败，请检查上方错误信息。
)
pause
