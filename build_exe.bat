@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Dong goi Sach Chung thanh SachChung.exe (thu muc dist\SachChung) va file SachChung-windows.zip
rem Dung moi truong .venv rieng, KHONG dung Python co PaddleOCR, de file .exe nhe.
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip --version >nul 2>&1 || "%PY%" -m ensurepip --upgrade >nul 2>&1
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  uv pip install -q --python "%PY%" -r requirements.txt pyinstaller
) else (
  "%PY%" -m pip install -q -r requirements.txt pyinstaller
)
echo Dang dong goi...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --name SachChung --icon assets\icon.ico ^
  --exclude-module paddle --exclude-module paddleocr --exclude-module paddlex ^
  --exclude-module torch --exclude-module cv2 --exclude-module numpy ^
  app.py
if errorlevel 1 (
  echo Dong goi that bai.
  pause
  exit /b 1
)
copy /y assets\HUONG_DAN.txt dist\SachChung\ >nul
if exist SachChung-windows.zip del SachChung-windows.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SachChung' -DestinationPath 'SachChung-windows.zip'"
echo.
echo Xong: dist\SachChung\SachChung.exe  va  SachChung-windows.zip (de dua len GitHub Releases)
pause
