@echo off
cd /d "%~dp0"
python --version >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas installe ou n'est pas dans le PATH.
  echo Installe Python 3 depuis https://www.python.org/downloads/windows/ puis relance ce fichier.
  pause
  exit /b 1
)
python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Premiere utilisation : installation du module image Pillow pour afficher les miniatures JPG/WEBP...
  python -m pip install -r requirements.txt
)
python bl_tracker.py
if errorlevel 1 (
  echo.
  echo Une erreur est survenue au lancement.
  echo Lance INSTALLER_WINDOWS.bat puis reessaie.
  pause
)
