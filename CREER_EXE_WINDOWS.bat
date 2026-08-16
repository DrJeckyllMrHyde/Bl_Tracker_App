@echo off
cd /d "%~dp0"
echo Creation d'un executable Windows autonome...
echo Cette etape installe Pillow et PyInstaller uniquement pour fabriquer le .exe.
python --version >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas installe ou n'est pas dans le PATH.
  echo Installe Python 3 depuis https://www.python.org/downloads/windows/ puis relance ce fichier.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "BL Tracker" --collect-all PIL bl_tracker.py
mkdir release 2>nul
copy "dist\BL Tracker.exe" "release\BL Tracker.exe"
echo.
echo Termine. Le fichier se trouve dans le dossier release.
pause
