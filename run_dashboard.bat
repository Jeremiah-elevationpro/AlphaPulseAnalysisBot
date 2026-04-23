@echo off
echo Starting AlphaPulse Dashboard...
cd /d "%~dp0"
if exist "venv\Scripts\streamlit.exe" (
    "venv\Scripts\streamlit.exe" run dashboard/app.py --server.port 8501 --server.headless true
) else (
    streamlit run dashboard/app.py --server.port 8501 --server.headless true
)
pause
