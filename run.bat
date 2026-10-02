@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Dang tao moi truong Python lan dau...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Khong tim thay Python. Cai Python 3.10+ tai https://www.python.org/downloads/ roi chay lai.
  pause
  exit /b 1
)
echo Dang kiem tra thu vien...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
set SACH_CHUNG_OPEN=1
".venv\Scripts\python.exe" app.py
pause
