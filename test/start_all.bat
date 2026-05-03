@echo off
cd "G:\Development\ss14\prototype manager\test"
start "Flask" "G:\Development\ss14\prototype manager\.venv\Scripts\python.exe" app.py
timeout /t 3 /nobreak > nul
start http://localhost:5000
echo Flask app started. Press any key to stop...
pause
