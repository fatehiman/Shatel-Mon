@echo off
REM Build ShatelMon.exe (single file, no console window) into dist\
REM --collect-all playwright bundles the Playwright driver so the automated
REM purchase works from the frozen exe (it drives your installed Chrome).
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ShatelMon ^
  --icon ShatelMon.ico ^
  --add-data "shatel_logo.png;." ^
  --collect-all playwright ^
  ShatelMon.py
echo.
echo Done. Output: dist\ShatelMon.exe
