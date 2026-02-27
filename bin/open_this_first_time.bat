@echo off
REM =================================================================
REM SEPA-StockLab - First Time Setup Tool
REM This script checks all required dependencies before use.
REM =================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0.."

REM Call Python dependency checker
python "%~dp0..\scripts\check_dependencies.py"
if errorlevel 1 (
    pause
    exit /b 1
)
pause


