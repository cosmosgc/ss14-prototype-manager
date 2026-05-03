@echo off
echo Starting SS14 Map Viewer...
echo.

cd /d "G:\Development\ss14\prototype manager\test"

echo Starting Flask server...
start "Flask Server" "G:\Development\ss14\prototype manager\.venv\Scripts\python.exe" app.py

echo Waiting for server to start...
timeout /t 3 /nobreak > nul

echo Opening browser...
start http://localhost:5000

echo.
echo Flask server is running at http://localhost:5000
echo Press any key to stop the server and exit...
pause > nul

echo Stopping Flask server...
taskkill /f /im python.exe > nul 2>&1

echo Done!
pause
