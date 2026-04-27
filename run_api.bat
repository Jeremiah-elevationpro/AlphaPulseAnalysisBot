@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
uvicorn api.server:app --host 0.0.0.0 --port 8000
