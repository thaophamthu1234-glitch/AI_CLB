"""Đọc thời khóa biểu từ ảnh chụp bằng PaddleOCR-VL, rồi chuyển thành các dòng
(class_name, weekday, period, subject) để nhập vào bảng `timetable`.

Gồm hai phần tách biệt:
  1. ocr_image()        – chạy PaddleOCR-VL, trả về Markdown (bảng nằm dưới dạng HTML <table>).
  2. parse_timetable()  – thuần Python, không cần GPU: tìm bảng, nhận cột Thứ / hàng Tiết,
                          chuẩn hóa tên môn. Có thể kiểm thử riêng với Markdown mẫu.

Cấu hình bằng biến môi trường (đều không bắt buộc):
  SACH_CHUNG_OCR_VERSION   v1 | v1.5 | v1.6 (mặc định v1.6)
  SACH_CHUNG_OCR_BACKEND   native (mặc định, chạy model ngay trong tiến trình) | vllm-server | sglang-server | ...
  SACH_CHUNG_OCR_SERVER    URL server khi dùng backend *-server, vd http://127.0.0.1:8118/v1
"""
import glob
import html
import os
import re
import tempfile
import threading
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser

# ============================================================
# 1. Chạy PaddleOCR-VL
# ============================================================
_pipeline = None
_lock = threading.Lock()

INSTALL_HINT = (
    "Chưa cài PaddleOCR. Trên máy chạy app, cài PaddlePaddle (bản GPU nếu có card NVIDIA) theo hướng dẫn tại "
    "https://www.paddleocr.ai, rồi chạy:  pip install \"paddleocr[doc-parser]\""
)


def ocr_status():
    """(True, "") nếu dùng được; ngược lại (False, lý do cụ thể) để giáo viên biết sửa ở đâu."""
    import sys
    try:
        import paddleocr  # noqa: F401
    except ModuleNotFoundError as e:
        if e.name == "paddleocr":
            return False, ("Python đang chạy app chưa có PaddleOCR. App đang chạy bằng: " + sys.executable +
                           ". Nếu bạn đã cài PaddleOCR ở môi trường Python khác (vd môi trường của dự án khác), "
                           "hãy cài vào đúng Python này, hoặc chạy app bằng Python đó (xem README, mục "
                           "Nhập thời khóa biểu bằng ảnh).")
        return False, f"PaddleOCR đã cài nhưng thiếu thư viện phụ thuộc: {e.name}. Python đang dùng: {sys.executable}"
    except Exception as e:   # vd PaddlePaddle lỗi CUDA/DLL khi import
        return False, f"Không nạp được PaddleOCR ({type(e).__name__}: {e}). Python đang dùng: {sys.executable}"
    return True, ""


def ocr_available():
    return ocr_status()[0]


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from paddleocr import PaddleOCRVL
        kwargs = {"pipeline_version": os.environ.get("SACH_CHUNG_OCR_VERSION", "v1.6")}
        backend = os.environ.get("SACH_CHUNG_OCR_BACKEND")
        if backend:
            kwargs["vl_rec_backend"] = backend
        server = os.environ.get("SACH_CHUNG_OCR_SERVER")
        if server:
            kwargs["vl_rec_server_url"] = server
        _pipeline = PaddleOCRVL(**kwargs)   # lần đầu sẽ tải model (~vài GB), các lần sau dùng lại
    return _pipeline


def _result_to_markdown(res):
    """Lấy Markdown từ một kết quả PaddleOCR-VL; thử lần lượt các cách mà các bản PaddleOCR 3.x hỗ trợ."""
    md = getattr(res, "markdown", None)
    if isinstance(md, dict) and md.get("markdown_texts"):
        return md["markdown_texts"]
    if isinstance(md, str) and md.strip():
        return md
    with tempfile.TemporaryDirectory() as d:
        res.save_to_markdown(save_path=d)
        parts = []
        for f in sorted(glob.glob(os.path.join(d, "**", "*.md"), recursive=True)):
            with open(f, encoding="utf-8") as fh:
                parts.append(fh.read())
        return "\n\n".join(parts)


def ocr_image(path):
    """Chạy OCR một ảnh, trả về chuỗi Markdown. Ném RuntimeError kèm hướng dẫn nếu chưa cài."""
    ok, why = ocr_status()
    if not ok:
        raise RuntimeError(why)
    with _lock:   # model chiếm nhiều VRAM: mỗi lúc chỉ xử lý một ảnh
        output = _get_pipeline().predict(path)
    return "\n\n".join(_result_to_markdown(r) for r in output)


# ============================================================
# 2. Tách bảng từ Markdown/HTML
# ============================================================
class _TableParser(HTMLParser):
    """Đọc mọi <table> trong HTML, trải các ô gộp (rowspan/colspan) thành lưới chữ nhật."""

    def __init__(self):
        super().__init__()
        self.tables, self._rows, self._row, self._cell = [], None, None, None
        self._span = (1, 1)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._span = (int(a.get("rowspan") or 1), int(a.get("colspan") or 1))
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(("".join(self._cell).strip(), *self._span))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append(_expand(self._rows))
            self._rows = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _expand(rows):
    grid, pending = [], {}   # pending[(r, c)] = text còn kéo xuống từ rowspan
    for r, cells in enumerate(rows):
        out, c = [], 0
        it = iter(cells)
        while True:
            while (r, c) in pending:
                out.append(pending.pop((r, c)))
                c += 1
            cell = next(it, None)
            if cell is None:
                break
            text, rs, cs = cell
            for k in range(cs):
                out.append(text)
                for dr in range(1, rs):
                    pending[(r + dr, c + k)] = text
            c += cs
        grid.append(out)
    width = max((len(r) for r in grid), default=0)
    return [r + [""] * (width - len(r)) for r in grid]


def extract_tables(markdown):
    """Trả về danh sách bảng (mỗi bảng là list các hàng, mỗi hàng là list chuỗi)."""
    p = _TableParser()
    p.feed(markdown)
    tables = p.tables
    # Bảng kiểu Markdown: | a | b |
    block = []
    for line in markdown.splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if len(block) >= 2:
            rows = [[html.unescape(c.strip()) for c in l.strip().strip("|").split("|")] for l in block]
            rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) or not c for c in r)]
            if rows:
                tables.append(_expand([[(c, 1, 1) for c in r] for r in rows]))
        block = []
    return tables


# ============================================================
# 3. Hiểu bảng thời khóa biểu
# ============================================================
def fold(s):
    """Bỏ dấu, chữ thường, gộp khoảng trắng: 'Thứ Hai' -> 'thu hai'."""
    s = unicodedata.normalize("NFD", s or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


_DAY_WORDS = {"hai": 2, "ba": 3, "tu": 4, "nam": 5, "sau": 6, "bay": 7}


def weekday_of(cell):
    """'Thứ 2' / 'THỨ HAI' / 'T2' / 'Hai' → 2..7; không phải tên thứ → None."""
    f = fold(cell)
    m = re.fullmatch(r"(?:thu|t)\s*\.?\s*([2-7])", f)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(?:thu\s*)?(hai|ba|tu|nam|sau|bay)", f)
    if m:
        return _DAY_WORDS[m.group(1)]
    return None


def period_of(cell):
    """'Tiết 3' / '3' / 'T3' → 3; không phải số tiết → None."""
    f = fold(cell)
    m = re.fullmatch(r"(?:tiet|t)?\s*\.?\s*(\d{1,2})", f)
    if m and 1 <= int(m.group(1)) <= 10:
        return int(m.group(1))
    return None


# Ô không cần sách giáo khoa: bỏ qua
SKIP = {"chao co", "sinh hoat", "sinh hoat lop", "shl", "sh lop", "shcn", "nghi", "", "-", "x"}

# Tên viết tắt hay gặp trên TKB → tên môn đầy đủ
ALIASES = {
    "toan": "Toán", "van": "Ngữ văn", "ngu van": "Ngữ văn", "nv": "Ngữ văn", "tieng viet": "Tiếng Việt",
    "khtn": "Khoa học tự nhiên", "khoa hoc tu nhien": "Khoa học tự nhiên",
    "anh": "Tiếng Anh", "ta": "Tiếng Anh", "tieng anh": "Tiếng Anh", "av": "Tiếng Anh", "anh van": "Tiếng Anh",
    "ls&dl": "Lịch sử và Địa lí", "ls-dl": "Lịch sử và Địa lí", "lsdl": "Lịch sử và Địa lí",
    "su-dia": "Lịch sử và Địa lí", "su dia": "Lịch sử và Địa lí", "lich su va dia li": "Lịch sử và Địa lí",
    "su": "Lịch sử", "lich su": "Lịch sử", "dia": "Địa lí", "dia li": "Địa lí", "dia ly": "Địa lí",
    "gdcd": "Giáo dục công dân", "giao duc cong dan": "Giáo dục công dân",
    "tin": "Tin học", "tin hoc": "Tin học", "cn": "Công nghệ", "cong nghe": "Công nghệ",
    "gdtc": "Giáo dục thể chất", "td": "Giáo dục thể chất", "the duc": "Giáo dục thể chất",
    "giao duc the chat": "Giáo dục thể chất",
    "am nhac": "Âm nhạc", "nhac": "Âm nhạc", "an": "Âm nhạc", "mi thuat": "Mĩ thuật", "my thuat": "Mĩ thuật",
    "mt": "Mĩ thuật", "nt": "Nghệ thuật", "nghe thuat": "Nghệ thuật",
    "hdtn": "Hoạt động trải nghiệm, hướng nghiệp", "hdtnhn": "Hoạt động trải nghiệm, hướng nghiệp",
    "hoat dong trai nghiem": "Hoạt động trải nghiệm, hướng nghiệp",
    "ly": "Vật lí", "vat ly": "Vật lí", "vat li": "Vật lí", "hoa": "Hóa học", "hoa hoc": "Hóa học",
    "sinh": "Sinh học", "sinh hoc": "Sinh học",
}


def clean_subject(cell, known=()):
    """Chuẩn hóa một ô môn học. Trả về '' nếu ô không cần sách (Chào cờ, Sinh hoạt...)."""
    text = (cell or "").split("\n")[0]
    text = re.split(r"\s+[-–]\s+|\(", text)[0].strip()   # bỏ tên GV: "Toán - Cô Lan", "Toán (Cô Lan)"
    f = fold(text)
    if f in SKIP or any(f.startswith(k) for k in ("chao co", "sinh hoat")):
        return ""
    if f in ALIASES:
        return ALIASES[f]
    for name in known:   # khớp với tên môn đã có trong hệ thống (sách, TKB cũ)
        if fold(name) == f:
            return name
    best, score = None, 0.0
    for name in set(known) | set(ALIASES.values()):
        r = SequenceMatcher(None, fold(name), f).ratio()
        if r > score:
            best, score = name, r
    if best and score >= 0.8:   # lỗi OCR nhỏ: 'Toan', 'Ngũ văn'
        return best
    return text[:1].upper() + text[1:]


def detect_class_name(text):
    """Tìm 'Lớp 6A1' / 'LỚP: 7/2' trong chữ OCR."""
    m = re.search(r"l[ớo]p\s*[:.]?\s*(\d{1,2}\s*[/.]?\s*[A-Za-zĐđ]?\s*\d{0,2})", text or "", re.I)
    return re.sub(r"\s+", "", m.group(1)).upper() if m else None


def _orient(grid):
    """Tìm hàng chứa tên các Thứ. Nếu thứ nằm ở cột đầu (bảng xoay ngang) thì chuyển vị."""
    def header_row(g):
        for i, row in enumerate(g[:4]):
            days = {j: weekday_of(c) for j, c in enumerate(row) if weekday_of(c)}
            if len(days) >= 2:
                return i, days
        return None, None

    i, days = header_row(grid)
    if days:
        return grid, i, days
    t = [list(col) for col in zip(*grid)] if grid else []
    i, days = header_row(t)
    return (t, i, days) if days else (grid, None, None)


def parse_grid(grid, class_name, known=()):
    """Một bảng TKB của một lớp → (rows, warnings)."""
    grid, h, days = _orient(grid)
    if days is None:
        return [], ["Không tìm thấy hàng tiêu đề có các Thứ (Thứ 2 … Thứ 7)."]
    rows, warnings, offset, last = [], [], 0, 0
    for r in grid[h + 1:]:
        lead = [c for j, c in enumerate(r) if j not in days]
        joined = fold(" ".join(lead))
        period = next((period_of(c) for c in reversed(lead) if period_of(c)), None)
        if "chieu" in joined and offset == 0 and last:
            offset = last              # buổi chiều đánh số lại từ 1 → nối tiếp sau buổi sáng
        if period is None:
            continue
        if period + offset <= last and offset == 0:
            offset = last              # số tiết bị đếm lại mà không ghi "Chiều"
        p = period + offset
        last = max(last, p)
        for j, wd in days.items():
            if j < len(r):
                subj = clean_subject(r[j], known)
                if subj:
                    rows.append(dict(class_name=class_name, weekday=wd, period=p, subject=subj))
    if not rows:
        warnings.append("Đọc được bảng nhưng không có ô môn học nào.")
    if last > 10:
        warnings.append(f"Có tới {last} tiết/ngày: kiểm tra lại cách đánh số buổi chiều.")
    return rows, warnings


def parse_timetable(markdown, class_name=None, known=()):
    """Markdown từ OCR → (class_name, rows, warnings). Chọn bảng cho nhiều ô môn học nhất."""
    class_name = (class_name or "").strip() or detect_class_name(markdown)
    tables = extract_tables(markdown)
    if not tables:
        return class_name, [], ["Không tìm thấy bảng nào trong ảnh. Hãy chụp thẳng, đủ sáng, rõ toàn bộ bảng."]
    best = ([], ["Không bảng nào giống thời khóa biểu."])
    for t in tables:
        rows, w = parse_grid(t, class_name or "?", known)
        if len(rows) > len(best[0]):
            best = (rows, w)
    rows, warnings = best
    if not class_name:
        warnings = warnings + ["Không đọc được tên lớp trong ảnh, hãy nhập tay."]
    return class_name, rows, warnings
