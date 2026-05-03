@echo off
cd G:\Development\ss14\prototype manager\test
"..\venv\Scripts\python.exe" -m py_compile app.py
if %errorlevel% == 0 (
    echo app.py syntax OK
) else (
    echo app.py has syntax errors
)
pause
