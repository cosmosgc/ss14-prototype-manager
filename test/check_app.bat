@echo off
cd "G:\Development\ss14\prototype manager\test"
"..\venv\Scripts\python.exe" -m py_compile app.py
if errorlevel 1 (
    echo Syntax Error in app.py
) else (
    echo Syntax OK for app.py
)
pause
