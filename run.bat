@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Muon chay app bang Python cua moi truong khac (vd moi truong da cai PaddleOCR-VL):
rem   tao file python.txt chi chua duong dan toi python.exe do (khong co dau ngoac kep),
rem   hoac dat bien moi truong SACH_CHUNG_PYTHON.
set "PY=%SACH_CHUNG_PYTHON%"
if "%PY%"=="" if exist python.txt set /p PY=<python.txt
if "%PY%"=="" (
  if not exist ".venv\Scripts\python.exe" (
    echo Dang tao moi truong Python lan dau...
    py -3 -m venv .venv 2>nul || python -m venv .venv
  )
  set "PY=.venv\Scripts\python.exe"
)
if not exist "%PY%" (
  echo Khong tim thay Python: %PY%
  echo Cai Python 3.10+ tai https://www.python.org/downloads/ hoac sua lai file python.txt
  pause
  exit /b 1
)
echo Dang dung Python: %PY%
rem Moi truong tao bang uv khong co san pip: thu ensurepip, neu van khong co thi dung uv
"%PY%" -m pip --version >nul 2>&1 || "%PY%" -m ensurepip --upgrade >nul 2>&1
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  uv pip install -q --python "%PY%" -r requirements.txt
) else (
  "%PY%" -m pip install -q -r requirements.txt
)
set SACH_CHUNG_OPEN=1
"%PY%" app.py
pause
