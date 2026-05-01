@echo off
cd "G:\Development\ss14\prototype manager"
call .venv\Scripts\activate
python -m py_compile "test\map_renderer.py"
if errorlevel 1 (
    echo Syntax check FAILED
) else (
    echo Syntax check PASSED
)
pause
