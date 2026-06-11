@echo off
REM Build ShatelMon.exe (single file, no console window) into dist\
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ShatelMon ^
  --icon ShatelMon.ico ^
  --add-data "shatel_logo.png;." ^
  ShatelMon.py
echo.
echo Done. Output: dist\ShatelMon.exe
