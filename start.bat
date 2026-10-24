@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import aiohttp" 2>nul || python -m pip install aiohttp
python server.py %*
pause
