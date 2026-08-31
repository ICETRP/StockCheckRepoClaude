@echo off
cd /d "%~dp0webapp"
echo Installing/checking dependencies...
python -m pip install -q -r requirements.txt
echo.
echo Starting Claude Algo Dashboard in a separate window...
start "Claude Algo Dashboard" python app.py
timeout /t 2 >nul
start "" http://127.0.0.1:5055
echo.
echo Dashboard is running in the "Claude Algo Dashboard" window.
echo Close that window (or Ctrl+C in it) to stop the server.
pause
