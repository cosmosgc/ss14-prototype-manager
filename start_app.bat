@echo off
setlocal

if not exist .venv\Scripts\activate (
  echo Virtual environment missing. Run install_dependencies.bat first.
  exit /b 1
)

call .venv\Scripts\activate
python app.py
