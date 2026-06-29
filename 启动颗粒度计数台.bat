@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动颗粒度计数台...

echo 正在检查 Python 依赖...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo 依赖安装失败，请检查 Python 和网络连接。
    pause
    exit /b 1
)

set PYTHONUTF8=1
echo 正在启动服务...
start http://127.0.0.1:8765
python app.py
pause
