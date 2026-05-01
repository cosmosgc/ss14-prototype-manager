@echo off
setlocal enabledelayedexpansion

echo Checking Node.js...
where node >nul 2>nul
if %errorlevel% neq 0 (
  echo Node.js not found. Install from https://nodejs.org/
  pause
  exit /b 1
)

echo Installing dependencies...
call npm install
if %errorlevel% neq 0 (
  echo npm install failed with error code %errorlevel%
  pause
  exit /b %errorlevel%
)

echo Building CSS and JS...
call npm run build
if %errorlevel% neq 0 (
  echo Build failed with error code %errorlevel%
  pause
  exit /b %errorlevel%
)

echo Done! Output in static/dist/ (CSS + JS)
pause
  exit /b %errorlevel%
)

echo Building CSS with Tailwind...
call npm run build:css
if %errorlevel% neq 0 (
  echo Tailwind build failed with error code %errorlevel%
  pause
  exit /b %errorlevel%
)

echo Building with Vite...
call npm run build
if %errorlevel% neq 0 (
  echo Vite build failed with error code %errorlevel%
  pause
  exit /b %errorlevel%
)

echo Done! Output in static/dist/
pause
