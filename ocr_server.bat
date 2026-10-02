@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem May chu OCR: chay tren may co GPU va da cai PaddleOCR-VL.
set "PY=%SACH_CHUNG_PYTHON%"
if "%PY%"=="" if exist python.txt set /p PY=<python.txt
if "%PY%"=="" set "PY=python"
echo Dang dung Python: %PY%
"%PY%" -c "import flask" >nul 2>&1 || "%PY%" -m pip install -q flask
rem ngrok: ghi ten mien co dinh (vd ten-ban.ngrok-free.app) vao file ngrok_domain.txt
set "NGROK_DOMAIN="
if exist ngrok_domain.txt set /p NGROK_DOMAIN=<ngrok_domain.txt
set "NG=ngrok"
if exist ngrok.exe set "NG=%~dp0ngrok.exe"
if defined NGROK_DOMAIN (
  echo Dang mo duong ham ngrok: https://%NGROK_DOMAIN%
  start "ngrok - Sach Chung OCR" "%NG%" http --url=%NGROK_DOMAIN% 8765
) else (
  echo Chua co ngrok_domain.txt: may chu chi nhan yeu cau tren may nay.
)
"%PY%" ocr_server.py
pause
