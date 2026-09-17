@echo off
echo ================================
echo  Vioris Desktop Agent
echo ================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.14+
    pause
    exit /b 1
)

REM Create data directories
if not exist "data" mkdir data
if not exist "logs" mkdir logs
if not exist "credentials" mkdir credentials

REM Run in demo mode
if "%1"=="--demo" (
    echo Starting in demo mode...
    python main.py --demo
) else (
    echo Starting Vioris Agent...
    python main.py %*
)

pause
