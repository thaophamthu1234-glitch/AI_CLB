"""Máy chủ OCR cho Sách Chung – chạy trên máy có GPU và đã cài PaddleOCR-VL.

App Sách Chung ở trường (kể cả bản .exe không có PaddleOCR) gửi ảnh thời khóa biểu tới đây,
máy chủ đọc bảng bằng PaddleOCR-VL rồi trả kết quả về. Chỉ nhận yêu cầu có đúng mã bí mật.

Chạy:  ocr_server.bat   (hoặc: python ocr_server.py)
Đưa ra internet: ngrok http --domain=<tên-miền-ngrok-của-bạn> 8765   (ocr_server.bat tự làm nếu đã cấu hình)

Cấu hình lưu ở ocr_server_config.json (tự tạo lần đầu, không đẩy lên GitHub):
  token  – mã bí mật; dán vào Cài đặt → Máy chủ OCR của app
  port   – cổng, mặc định 8765
  host   – 127.0.0.1 (chỉ nhận qua ngrok, mặc định) hoặc 0.0.0.0 (cho máy cùng Wi-Fi gọi thẳng)
"""
import json
import os
import secrets
import sys
import tempfile
import time

from flask import Flask, jsonify, request

import timetable_ocr

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CONFIG_PATH = os.path.join(BASE_DIR, "ocr_server_config.json")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    changed = False
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(24)
        changed = True
    cfg.setdefault("port", 8765)
    cfg.setdefault("host", "127.0.0.1")   # "0.0.0.0" nếu muốn máy khác trong cùng Wi-Fi gọi thẳng, không qua ngrok
    if changed:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


CFG = load_config()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def authorized():
    got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return secrets.compare_digest(got, CFG["token"])


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="Ảnh lớn hơn 16 MB."), 413


@app.get("/health")
def health():
    if not authorized():
        return jsonify(error="Sai mã bí mật."), 401
    ok, why = timetable_ocr.ocr_status()
    return jsonify(ok=ok, detail=why or "Sẵn sàng đọc ảnh bằng PaddleOCR-VL.")


@app.post("/ocr")
def ocr():
    if not authorized():
        return jsonify(error="Sai mã bí mật."), 401
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(error="Thiếu ảnh."), 400
    ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
    if ext not in ALLOWED_EXT:
        return jsonify(error=f"Không nhận định dạng {ext}."), 400
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        f.save(tmp)
    t0 = time.time()
    try:
        markdown = timetable_ocr.ocr_image(tmp.name)   # tự xếp hàng: mỗi lúc 1 ảnh
    except Exception as e:
        print(f"  [lỗi] {e}")
        return jsonify(error=f"Đọc ảnh thất bại: {e}"), 500
    finally:
        os.unlink(tmp.name)        # không giữ lại ảnh
    print(f"  Đã đọc 1 ảnh trong {time.time() - t0:.1f}s từ {request.headers.get('X-Forwarded-For', request.remote_addr)}")
    return jsonify(markdown=markdown)


if __name__ == "__main__":
    ok, why = timetable_ocr.ocr_status()
    print("=" * 64)
    print("  Máy chủ OCR Sách Chung")
    print(f"  Địa chỉ trên máy này : http://localhost:{CFG['port']}")
    print(f"  Mã bí mật (token)    : {CFG['token']}")
    print("  → Dán địa chỉ ngrok và mã bí mật vào app: Quản lý → Cài đặt → Máy chủ OCR")
    print(f"  PaddleOCR            : {'sẵn sàng' if ok else 'CHƯA DÙNG ĐƯỢC – ' + why}")
    print("=" * 64)
    if ok and "--no-warmup" not in sys.argv:
        print("  Đang nạp model lên GPU (lần đầu có thể mất 1–2 phút)...")
        try:
            timetable_ocr._get_pipeline()
            print("  Model đã sẵn sàng.")
        except Exception as e:
            print(f"  Không nạp được model: {e}")
    app.run(host=CFG["host"], port=CFG["port"], threaded=True)
