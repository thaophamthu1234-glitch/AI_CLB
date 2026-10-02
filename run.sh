#!/usr/bin/env bash
# Chạy Sách Chung trên macOS/Linux: ./run.sh
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
SACH_CHUNG_OPEN=1 .venv/bin/python app.py
