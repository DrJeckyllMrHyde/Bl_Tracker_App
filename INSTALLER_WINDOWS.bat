@echo off
cd /d "%~dp0"
echo Installation / verification des ressources necessaires a BL Tracker...
echo.
python --version >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas installe ou n'est pas dans le PATH.
  echo Installe Python 3 depuis https://www.python.org/downloads/windows/ puis relance ce fichier.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Installation terminee. Tu peux lancer LANCER_BL_TRACKER.bat
echo.
pause
