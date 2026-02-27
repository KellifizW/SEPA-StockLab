@echo off
REM SEPA-StockLab - First Time Setup Tool
REM This launches the Python dependency checker

cd /d "%~dp0.."
python "%~dp0..\scripts\check_dependencies.py"
if errorlevel 1 (
    pause
    exit /b 1
)
pause
