@echo off
cd "G:\Development\ss14\prototype manager\test"
start "Flask" "G:\Development\ss14\prototype manager\.venv\Scripts\python.exe" app.py
timeout /t 3 /nobreak > nul
echo Testing endpoints...
curl -s http://localhost:5000/api/map-bounds
echo.
curl -s http://localhost:5000/api/map-data | "G:\Development\ss14\prototype manager\.venv\Scripts\python.exe" -m json.tool 2> nul | findstr /c:"chunkSize"
pause
