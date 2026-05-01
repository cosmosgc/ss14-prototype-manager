@echo off
cd /d "G:\Development\ss14\prototype manager\test"

REM Initialize npm project if not exists
if not exist package.json (
    echo Initializing npm project...
    "..\venv\Scripts\npm.cmd" init -y
)

REM Install Vite and OpenLayers
echo Installing Vite and OpenLayers...
"..\venv\Scripts\npm.cmd" install vite ol

REM Create vite.config.js
echo Creating Vite config...
echo export default { > vite.config.js
echo   root: './static', >> vite.config.js
echo   build: { >> vite.config.js
echo     outDir: '../static/dist', >> vite.config.js
echo     assetsDir: 'assets' >> vite.config.js
echo   } >> vite.config.js
echo } >> vite.config.js

echo Vite setup complete!
pause
