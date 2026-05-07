@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -m uvicorn api.server:app --host 0.0.0.0 --port 8000
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m uvicorn api.server:app --host 0.0.0.0 --port 8000
) else (
    python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
)
