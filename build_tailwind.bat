@echo off
setlocal

if not exist node_modules (
  echo node_modules not found. Run install_dependencies.bat first.
  exit /b 1
)

npx @tailwindcss/cli -i ./static/src/input.css -o ./static/dist/output.css --watch
