@echo off
echo Starting Lazy Trading Bot...
echo.

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found. Run: python -m venv venv
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Virtual environment activated.
echo.

echo Starting server on http://localhost:8000
echo API docs: http://localhost:8000/docs
echo.
python server.py
