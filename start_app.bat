@echo off
chcp 65001 >nul
echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Minervini SEPA - Web Interface              ║
echo ║  Starting Flask App...                        ║
echo ║  http://localhost:5000                        ║
echo ║  Press Ctrl+C to stop                         ║
echo ╚══════════════════════════════════════════════╝
echo.

REM 檢查虛擬環境是否存在
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found at .venv
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

REM 激活虛擬環境
call .venv\Scripts\activate.bat

REM 啟動 Flask App
if exist "start_web.py" (
    python start_web.py
) else (
    python app.py
)

pause
