@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python est necessaire. Consultez le fichier LISEZ_MOI.txt.
  pause
  exit /b 1
)
echo Cette configuration telecharge un module gratuit et cree des cles locales.
echo Elle n'installe pas le certificat sur vos appareils.
py -3 -m pip install --user cryptography
if errorlevel 1 (
  echo Installation du module impossible. Verifiez la connexion Internet et le guide.
  pause
  exit /b 1
)
py -3 tls_setup.py
if errorlevel 1 (
  echo La configuration n'a pas pu se terminer. Conservez ce message et consultez le guide.
  pause
  exit /b 1
)
pause
