@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python est necessaire. Installez Python 3 depuis https://www.python.org/downloads/windows/
  echo Consultez le fichier LISEZ_MOI.txt.
  pause
  exit /b 1
)
py -3 server.py
echo.
echo VetStock est arrete. Fermez cette fenetre.
pause
