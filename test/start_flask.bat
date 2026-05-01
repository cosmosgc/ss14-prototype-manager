@echo off
cd /d G:\Development\ss14\prototype manager\test
call "..\venv\Scripts\activate.bat"
start "Flask App" "..\venv\Scripts\python.exe" app.py
timeout /t 3 /nobreak > nul
start http://localhost:5000
echo Flask app started. Press any key to stop...
pause
