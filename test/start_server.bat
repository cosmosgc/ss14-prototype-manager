@echo off
cd "G:\Development\ss14\prototype manager\test"
call "..\venv\Scripts\activate.bat"
start /b "..\venv\Scripts\python.exe" app.py
timeout /t 3 /nobreak > nul
start http://localhost:5000
pause
