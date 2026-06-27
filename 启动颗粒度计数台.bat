@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动颗粒度计数台...
python -m pip install -r requirements.txt --quiet 2>nul
start http://127.0.0.1:8765
python app.py
pause
