@echo off
setlocal

if not exist .venv (
  python -m venv .venv
)

call .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist node_modules (
  npm install
)

echo Dependencies installed.
