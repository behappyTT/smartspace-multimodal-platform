@echo off
setlocal

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "BACKEND_SCRIPT=%PROJECT_DIR%\run.py"
set "BT_SCRIPT=%PROJECT_DIR%\bluetooth_node\uploader.py"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

start "SmartSpace Backend" cmd /k cd /d "%PROJECT_DIR%" ^& "%PYTHON_EXE%" "%BACKEND_SCRIPT%"
timeout /t 4 /nobreak >nul
start "WT901 Uploader" cmd /k cd /d "%PROJECT_DIR%" ^& "%PYTHON_EXE%" "%BT_SCRIPT%"

echo Open: http://127.0.0.1:8000/
pause
