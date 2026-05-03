@echo off
cd G:\Development\ss14\prototype manager\test
"..\venv\Scripts\python.exe" create_app.py
if exist app.py (
    echo app.py created successfully
    "..\venv\Scripts\python.exe" -m py_compile app.py
    if errorlevel 1 (
        echo app.py has syntax errors
    ) else (
        echo app.py syntax OK
    )
) else (
    echo Failed to create app.py
)
pause
