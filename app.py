"""Sách Chung – điều phối mượn sách giáo khoa luân phiên trong thời gian chờ sách chính thức.

Chạy:  pip install -r requirements.txt
       python app.py
Mở:    http://localhost:5000   (điện thoại cùng Wi-Fi: http://<IP-máy-tính>:5000)
Mã PIN giáo viên mặc định: 1234  (đổi bằng biến môi trường SACH_CHUNG_PIN)
"""
import base64
import csv
import io
import math
import os
import re
import socket
import sqlite3
import tempfile
import threading
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from functools import wraps

import qrcode
import timetable_ocr
from flask import (Flask, Response, abort, flash, g, redirect, render_template_string,
                   request, session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sach_chung.db")
DATA_DIR = os.path.join(BASE_DIR, "data")
TEACHER_PIN = os.environ.get("SACH_CHUNG_PIN", "1234")
LOAN_DAYS = 1                    # mượn về nhà qua đêm, trả sáng hôm sau
EBOOK_HOME = "https://taphuan.nxbgd.vn"   # SGK điện tử miễn phí chính thức của NXB Giáo dục VN
SHARE_PER_BOOK = 2              # trên lớp: 2 học sinh ngồi cùng bàn dùng chung 1 cuốn
EDITION_SUGGESTIONS = ["Kết nối tri thức với cuộc sống", "Chân trời sáng tạo", "Cánh diều"]
WEEKDAYS = {2: "Thứ 2", 3: "Thứ 3", 4: "Thứ 4", 5: "Thứ 5", 6: "Thứ 6", 7: "Thứ 7"}

app = Flask(__name__)
app.secret_key = os.environ.get("SACH_CHUNG_SECRET", "doi-chuoi-nay-khi-trien-khai-that")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024   # ảnh TKB tối đa 16 MB
OCR_EXPORT_DIR = os.path.join(DATA_DIR, "ocr")

# ============================================================
# Cơ sở dữ liệu
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    has_device INTEGER NOT NULL DEFAULT 0,      -- 1 = có điện thoại/máy + mạng để đọc SGK điện tử
    has_own_book INTEGER NOT NULL DEFAULT 0     -- 1 = đã có sách chính thức
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    grade INTEGER NOT NULL,
    class_name TEXT,                 -- lớp đang giữ bộ sách này
    condition TEXT NOT NULL DEFAULT 'Tốt',
    source TEXT DEFAULT 'Thư viện',  -- Thư viện / Phụ huynh tặng / Giáo viên / Trường khác
    edition TEXT                     -- bộ sách; để trống = theo bộ sách trường đang dạy
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    out_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    returned_at TEXT
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade INTEGER NOT NULL,
    subject TEXT NOT NULL,
    week INTEGER NOT NULL,
    title TEXT NOT NULL,
    ebook_url TEXT,
    worksheet_url TEXT,
    note TEXT,
    tasks TEXT                       -- nhiệm vụ/câu hỏi do giáo viên soạn, mỗi dòng một ý
);
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL,
    weekday INTEGER NOT NULL,        -- 2..7 (Thứ 2..Thứ 7)
    period INTEGER NOT NULL,         -- tiết 1..10
    subject TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def read_csv_rows(path_or_file):
    if isinstance(path_or_file, str):
        with open(path_or_file, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    text = path_or_file.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def to_bool(v):
    return 1 if str(v).strip().lower() in ("1", "co", "có", "yes", "true", "x") else 0


def import_students(db, rows):
    n = 0
    for r in rows:
        if not (r.get("name") or "").strip():
            continue
        db.execute("INSERT INTO students(name, class_name, has_device, has_own_book) VALUES (?,?,?,?)",
                   (r["name"].strip(), r["class_name"].strip(),
                    to_bool(r.get("has_device", 0)), to_bool(r.get("has_own_book", 0))))
        n += 1
    return n


def import_books(db, rows):
    n = 0
    for r in rows:
        if not (r.get("subject") or "").strip():
            continue
        qty = int(r.get("quantity") or 1)
        for _ in range(qty):
            db.execute("INSERT INTO books(subject, grade, class_name, condition, source, edition) "
                       "VALUES (?,?,?,?,?,?)",
                       (r["subject"].strip(), int(r["grade"]), (r.get("class_name") or "").strip() or None,
                        (r.get("condition") or "Tốt").strip(), (r.get("source") or "Thư viện").strip(),
                        (r.get("edition") or "").strip() or None))
            n += 1
    return n


def import_lessons(db, rows):
    n = 0
    for r in rows:
        if not (r.get("title") or "").strip():
            continue
        # Trong CSV, các nhiệm vụ ngăn cách bằng dấu "|"; trong form thì mỗi dòng một ý
        tasks = "\n".join(t.strip() for t in re.split(r"[|\n]", r.get("tasks") or "") if t.strip())
        db.execute("INSERT INTO lessons(grade, subject, week, title, ebook_url, worksheet_url, note, tasks) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (int(r["grade"]), r["subject"].strip(), int(r["week"]), r["title"].strip(),
                    (r.get("ebook_url") or EBOOK_HOME).strip(), (r.get("worksheet_url") or "").strip(),
                    (r.get("note") or "").strip(), tasks))
        n += 1
    return n


def parse_weekday(v):
    """Nhận '2', 'Thứ 2', 'T2', 'thu 3'... → 2..7"""
    m = re.search(r"\d+", str(v))
    d = int(m.group()) if m else 0
    if d not in WEEKDAYS:
        raise ValueError("weekday")
    return d


def import_timetable(db, rows):
    n = 0
    for r in rows:
        if not (r.get("class_name") or "").strip() or not (r.get("subject") or "").strip():
            continue
        db.execute("INSERT INTO timetable(class_name, weekday, period, subject) VALUES (?,?,?,?)",
                   (r["class_name"].strip(), parse_weekday(r["weekday"]), int(r["period"]), r["subject"].strip()))
        n += 1
    return n


def get_setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] is not None else default


def set_setting(db, key, value):
    db.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def school_edition(db):
    return get_setting(db, "edition", "")


# Sách "đúng bộ": không ghi bộ sách, hoặc trường chưa chọn bộ, hoặc trùng bộ trường dạy.
# Dùng kèm tham số :ed = school_edition(db)
MATCH_SQL = "(COALESCE(b.edition, '') = '' OR :ed = '' OR b.edition = :ed)"


def is_reference(book, edition):
    return bool(book["edition"]) and bool(edition) and book["edition"] != edition


def class_grade(class_name):
    m = re.match(r"\s*(\d+)", class_name or "")
    return int(m.group(1)) if m else None


# Dữ liệu mẫu nhúng sẵn: dùng khi không có thư mục data/ (chỉ cần tải 1 file app.py là chạy được)
SAMPLE_CSV = {
    "students.csv": """name,class_name,has_device,has_own_book
Nguyễn Minh An,6A1,0,0
Trần Bảo Châu,6A1,1,0
Lê Gia Huy,6A1,0,0
Phạm Ngọc Lan,6A1,1,1
Võ Đức Minh,6A1,0,0
Đặng Thu Ngân,6A1,1,0
Bùi Quốc Phong,6A1,0,0
Hồ Thảo Vy,6A1,1,1
Ngô Hải Đăng,6A2,0,0
Dương Khánh Linh,6A2,1,0
Lý Tuấn Kiệt,6A2,0,0
Mai Phương Thảo,6A2,1,1
Trịnh Văn Tài,6A2,0,0
Huỳnh Mỹ Duyên,6A2,1,0
""",
    "books.csv": """subject,grade,class_name,quantity,condition,source
Toán,6,6A1,3,Tốt,Thư viện
Ngữ văn,6,6A1,2,Tốt,Phụ huynh tặng
Khoa học tự nhiên,6,6A1,1,Hơi cũ,Giáo viên
Toán,6,6A2,2,Tốt,Thư viện
Ngữ văn,6,6A2,2,Tốt,Trường khác
""",
    "lessons.csv": """grade,subject,week,title,ebook_url,worksheet_url,note,tasks
6,Toán,1,Tập hợp và các phần tử của tập hợp,,,Đọc bài trên SGK điện tử trước buổi học,Viết tập hợp các chữ cái trong từ "HỌC SINH"|Cho 2 ví dụ về tập hợp trong đời sống|Dùng kí hiệu ∈ và ∉ để viết 2 câu về tập hợp em vừa nêu
6,Ngữ văn,1,Bài 1 – Đọc hiểu văn bản (theo SGK),,,Ghi lại 3 câu hỏi em thắc mắc,Văn bản kể về ai? Nhân vật đó gặp chuyện gì?|Chọn 1 chi tiết em thích nhất và giải thích vì sao|Ghi lại 3 câu hỏi em thắc mắc để hỏi thầy cô
6,Toán,2,Tập hợp các số tự nhiên,,,,Liệt kê các số tự nhiên nhỏ hơn 6|Tìm số liền trước và liền sau của 99
6,Khoa học tự nhiên,2,Giới thiệu về Khoa học tự nhiên,,,Làm phiếu học tập giáo viên phát trên lớp,Kể 3 hiện tượng tự nhiên em quan sát được trong tuần|Hiện tượng nào em muốn tìm hiểu thêm? Vì sao?
""",
    "timetable.csv": """class_name,weekday,period,subject
6A1,2,1,Toán
6A1,2,2,Ngữ văn
6A1,2,3,Khoa học tự nhiên
6A1,2,4,Ngữ văn
6A2,2,1,Ngữ văn
6A2,2,2,Toán
6A2,2,3,Toán
6A2,2,4,Khoa học tự nhiên
6A1,3,1,Ngữ văn
6A1,3,2,Toán
6A1,3,3,Toán
6A2,3,1,Toán
6A2,3,2,Ngữ văn
6A2,3,3,Khoa học tự nhiên
6A1,4,1,Toán
6A1,4,2,Khoa học tự nhiên
6A1,4,3,Ngữ văn
6A2,4,1,Khoa học tự nhiên
6A2,4,2,Toán
6A2,4,3,Ngữ văn
6A1,5,1,Ngữ văn
6A1,5,2,Ngữ văn
6A1,5,3,Toán
6A2,5,1,Toán
6A2,5,2,Khoa học tự nhiên
6A2,5,3,Toán
6A1,6,1,Khoa học tự nhiên
6A1,6,2,Toán
6A1,6,3,Ngữ văn
6A2,6,1,Ngữ văn
6A2,6,2,Toán
6A2,6,3,Ngữ văn
""",
}


def migrate(db):
    """Nâng cấp DB tạo từ phiên bản cũ: thêm cột còn thiếu, không đụng tới dữ liệu."""
    for table, col, decl in (("books", "edition", "TEXT"), ("lessons", "tasks", "TEXT")):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            print(f"  Nâng cấp DB: thêm cột {table}.{col}")


def init_db(seed=True):
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    migrate(db)
    if seed:
        # Bảng nào còn trống thì nạp dữ liệu mẫu (kể cả khi DB đã được tạo từ lần chạy trước)
        for table, name, fn in (("students", "students.csv", import_students),
                                ("books", "books.csv", import_books),
                                ("lessons", "lessons.csv", import_lessons),
                                ("timetable", "timetable.csv", import_timetable)):
            p = os.path.join(DATA_DIR, name)
            empty = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            if not empty:
                continue
            if os.path.exists(p):
                rows = read_csv_rows(p)
            else:
                rows = list(csv.DictReader(io.StringIO(SAMPLE_CSV[name])))
            print(f"  Nạp dữ liệu mẫu {name}: {fn(db, rows)} dòng")
    db.commit()
    db.close()


# ============================================================
# Đăng nhập giáo viên (PIN đơn giản – học sinh quét QR chỉ xem được, không mượn/trả được)
# ============================================================
def teacher_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("teacher"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("pin") == TEACHER_PIN:
            session["teacher"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Sai mã PIN", "error")
    return page("""
      <h1>Đăng nhập giáo viên</h1>
      <form method="post" class="card narrow">
        <label>Mã PIN<input name="pin" type="password" inputmode="numeric" autofocus required></label>
        <button>Đăng nhập</button>
      </form>""")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("dashboard"))


# ============================================================
# Nghiệp vụ
# ============================================================
def active_loan(db, book_id):
    return db.execute("""SELECT l.*, s.name, s.class_name FROM loans l JOIN students s ON s.id = l.student_id
                         WHERE l.book_id = ? AND l.returned_at IS NULL""", (book_id,)).fetchone()


def suggest_borrowers(db, book, limit=8):
    """Gợi ý người mượn tiếp theo cho một cuốn sách:
    1. cùng lớp đang giữ sách (nếu sách gắn với lớp), chưa có sách riêng, chưa cầm cuốn cùng môn nào;
    2. ưu tiên học sinh KHÔNG có thiết bị (không đọc được SGK điện tử ở nhà);
    3. rồi đến người mượn ít lần nhất, lâu chưa được mượn nhất."""
    return db.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM loans l JOIN books b ON b.id = l.book_id
                 WHERE l.student_id = s.id AND b.subject = :subject) AS times,
               (SELECT MAX(l.out_at) FROM loans l JOIN books b ON b.id = l.book_id
                 WHERE l.student_id = s.id AND b.subject = :subject) AS last_out
        FROM students s
        WHERE s.has_own_book = 0
          AND (:cls IS NULL OR s.class_name = :cls)
          AND NOT EXISTS (SELECT 1 FROM loans l JOIN books b ON b.id = l.book_id
                          WHERE l.student_id = s.id AND l.returned_at IS NULL AND b.subject = :subject)
        ORDER BY s.has_device ASC, times ASC, COALESCE(last_out, '') ASC, s.name
        LIMIT :limit""", {"subject": book["subject"], "cls": book["class_name"], "limit": limit}).fetchall()


def stats(db):
    total = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    own = db.execute("SELECT COUNT(*) FROM students WHERE has_own_book = 1").fetchone()[0]
    no_device = db.execute("SELECT COUNT(*) FROM students WHERE has_own_book = 0 AND has_device = 0").fetchone()[0]
    books = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    reference = db.execute(f"SELECT COUNT(*) FROM books b WHERE NOT {MATCH_SQL}",
                           {"ed": school_edition(db)}).fetchone()[0]
    out = db.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL").fetchone()[0]
    overdue = db.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL AND due_at < ?", (now(),)).fetchone()[0]
    # "Chưa được tiếp cận" = chưa có sách riêng, không có thiết bị, và CHƯA TỪNG được mượn sách về nhà
    unreached = db.execute("""SELECT COUNT(*) FROM students s WHERE s.has_own_book = 0 AND s.has_device = 0
                              AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.student_id = s.id)""").fetchone()[0]
    return dict(total=total, own=own, need=total - own, no_device=no_device, books=books, out=out,
                reference=reference, matching=books - reference,
                available=books - out, overdue=overdue, unreached=unreached,
                pct_own=round(100 * own / total) if total else 0)


# ============================================================
# Xoay vòng sách trên lớp theo thời khóa biểu
# ============================================================
def books_needed(n_students):
    """Số cuốn cần để cả lớp có sách trên lớp (2 em ngồi cùng bàn dùng chung 1 cuốn)."""
    return math.ceil(n_students / SHARE_PER_BOOK)


def split_proportional(total, needs):
    """Chia `total` cuốn cho các lớp theo tỷ lệ nhu cầu (phương pháp phần dư lớn nhất),
    không lớp nào nhận quá nhu cầu của mình."""
    want = sum(needs.values())
    if want <= total:
        return dict(needs)
    raw = {c: total * n / want for c, n in needs.items()}
    alloc = {c: int(v) for c, v in raw.items()}
    rest = total - sum(alloc.values())
    for c in sorted(needs, key=lambda c: (raw[c] - alloc[c], needs[c]), reverse=True)[:rest]:
        alloc[c] += 1
    return alloc


def plan_day(db, weekday, day=None):
    """Lập lịch chuyển sách trong một buổi học.

    Với mỗi (khối, môn): kho sách = các cuốn đúng bộ, không mất, không bị giữ quá hạn.
    Mỗi cuốn bắt đầu ở "lớp giữ" của nó (hoặc Thư viện). Đi qua các tiết theo thứ tự:
      1. Lớp nào học môn đó ở tiết này cần ceil(số em chưa có sách / 2) cuốn.
      2. Nếu không đủ, chia theo tỷ lệ nhu cầu.
      3. Sách đang ở sẵn lớp cần thì giữ nguyên; thiếu thì lấy từ nơi đang rảnh,
         ưu tiên nơi lâu nhất mới cần lại môn này (để tránh chuyển qua chuyển lại).
    Cuối buổi, sách được trả về lớp giữ ban đầu (để giữ quy trình mượn về nhà qua đêm).
    So sánh với cách cũ: mỗi lớp chỉ dùng sách của chính lớp mình."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    ed = school_edition(db)
    sessions = db.execute("SELECT class_name, period, subject FROM timetable WHERE weekday = ? "
                          "ORDER BY period, class_name", (weekday,)).fetchall()
    need_students = {r["class_name"]: r["n"] for r in db.execute(
        "SELECT class_name, COUNT(*) AS n FROM students WHERE has_own_book = 0 GROUP BY class_name")}
    # Sách sẵn sàng trong buổi: không mất, đúng bộ, và không bị giữ quá giờ trả của sáng hôm đó
    avail = db.execute(f"""
        SELECT b.id, b.subject, b.grade, COALESCE(b.class_name, 'Thư viện') AS home FROM books b
        WHERE b.condition != 'Mất' AND {MATCH_SQL}
          AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.book_id = b.id AND l.returned_at IS NULL
                          AND l.due_at > :start)""", {"ed": ed, "start": f"{day} 07:30"}).fetchall()
    stock = {}
    for b in avail:
        stock.setdefault((b["grade"], b["subject"]), Counter())[b["home"]] += 1

    periods = sorted({s["period"] for s in sessions})
    by_period = {p: [] for p in periods}
    for s in sessions:
        by_period[s["period"]].append(s)

    rows, moves = {p: [] for p in periods}, {p: [] for p in periods}
    with_rot = without_rot = total_need = 0
    holders = {k: Counter(v) for k, v in stock.items()}

    def next_use(key, place, after):
        for s in sessions:
            if s["period"] > after and s["class_name"] == place and \
               (class_grade(s["class_name"]), s["subject"]) == key:
                return s["period"]
        return 99

    for p in periods:
        groups = {}
        for s in by_period[p]:
            key = (class_grade(s["class_name"]), s["subject"])
            n = need_students.get(s["class_name"], 0)
            groups.setdefault(key, {})[s["class_name"]] = n
        for key, classes in groups.items():
            h = holders.get(key, Counter())
            supply = sum(h.values())
            needs = {c: books_needed(n) for c, n in classes.items()}
            alloc = split_proportional(supply, needs)
            # 1) giữ sách đang ở sẵn lớp cần; 2) gom phần dư từ nơi khác; 3) chuyển tới lớp thiếu
            free = []
            for place, cnt in h.items():
                extra = cnt - alloc.get(place, 0)
                if extra > 0:
                    free.append([next_use(key, place, p), place == "Thư viện", place, extra])
            free.sort(key=lambda x: (-x[0], not x[1]))
            for c in sorted(classes):
                lack = alloc[c] - min(h.get(c, 0), alloc[c])
                for src in free:
                    if lack == 0:
                        break
                    k = min(lack, src[3])
                    if k:
                        moves[p].append(dict(subject=key[1], grade=key[0], src=src[2], dst=c, n=k))
                        h[src[2]] -= k
                        h[c] += k
                        src[3] -= k
                        lack -= k
            for c, n in classes.items():
                own_class = stock.get(key, Counter()).get(c, 0)
                covered = min(n, alloc[c] * SHARE_PER_BOOK)
                covered_old = min(n, own_class * SHARE_PER_BOOK)
                with_rot += covered
                without_rot += covered_old
                total_need += n
                rows[p].append(dict(class_name=c, subject=key[1], grade=key[0], students=n,
                                    need=needs[c], got=alloc[c], covered=covered, covered_old=covered_old))
            holders[key] = +h
    # Cuối buổi: trả sách về lớp giữ
    end_moves = []
    for key, h in holders.items():
        home = stock.get(key, Counter())
        surplus = [[pl, h[pl] - home.get(pl, 0)] for pl in h if h[pl] > home.get(pl, 0)]
        for pl in sorted(home):
            lack = home[pl] - h.get(pl, 0)
            for src in surplus:
                if lack <= 0:
                    break
                k = min(lack, src[1])
                if k:
                    end_moves.append(dict(subject=key[1], grade=key[0], src=src[0], dst=pl, n=k))
                    src[1] -= k
                    lack -= k
    pct = lambda x: round(100 * x / total_need) if total_need else 0
    return dict(periods=periods, rows=rows, moves=moves, end_moves=end_moves,
                total_need=total_need, with_rot=with_rot, without_rot=without_rot,
                pct_with=pct(with_rot), pct_without=pct(without_rot),
                n_moves=sum(m["n"] for ms in moves.values() for m in ms))


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def qr_data_uri(text):
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ============================================================
# Giao diện chung
# ============================================================
LOGO_SVG = """<svg viewBox="0 0 40 40" width="{size}" height="{size}" aria-hidden="true">
  <rect width="40" height="40" rx="11" fill="#FFE45C"/>
  <path d="M8 16.5c4-1.8 8.2-1.6 12 1.2v14c-3.8-2.6-8-2.9-12-1.2z" fill="#1F2A7A"/>
  <path d="M32 16.5c-4-1.8-8.2-1.6-12 1.2v14c3.8-2.6 8-2.9 12-1.2z" fill="#3D4FC4"/>
  <path d="M12.5 11.5a10 10 0 0 1 15 0" fill="none" stroke="#1F2A7A" stroke-width="2.6" stroke-linecap="round"/>
  <path d="M28.6 7.6l-.6 4.6-4.5-.9" fill="none" stroke="#1F2A7A" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
FAVICON = "data:image/svg+xml;base64," + base64.b64encode(
    LOGO_SVG.format(size=40).replace('xmlns', '').replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    .encode()).decode()

# Icon nét mảnh 24x24 (vẽ tay, không phụ thuộc thư viện ngoài)
ICONS = {
    "home": '<path d="M4 11l8-6 8 6v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
    "lesson": '<path d="M3 6c3-1.5 6-1.5 9 .5 3-2 6-2 9-.5v12c-3-1.5-6-1.5-9 .5-3-2-6-2-9-.5z"/><path d="M12 6.5v12"/>',
    "books": '<rect x="4" y="4" width="4" height="16" rx="1"/><rect x="10" y="4" width="4" height="16" rx="1"/><path d="M16.5 5.2l3.4-.9 3 15.4-3.4.9z"/>',
    "students": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.8-3.4 3-5 5.5-5s4.7 1.6 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.2c2.4-.3 4.2 1.2 4.8 4.3"/>',
    "loans": '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "timetable": '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M9 10v10M14.5 10v10M8 3v4M16 3v4"/>',
    "rotate": '<path d="M5 9a7.5 7.5 0 0 1 13.5-2.5L20 8"/><path d="M20 4v4h-4"/><path d="M19 15a7.5 7.5 0 0 1-13.5 2.5L4 16"/><path d="M4 20v-4h4"/>',
    "qr": '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><path d="M14 14h2v2h-2zM18 18h2v2h-2zM14 18h2M18 14h2"/>',
    "upload": '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
    "logout": '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
    "login": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
}

# (endpoint, nhãn, icon, chỉ giáo viên, các endpoint khác cũng tính là "đang ở tab này")
MAIN_NAV = [
    ("dashboard", "Tổng quan", "home", False, ()),
    ("lessons", "Bài tuần này", "lesson", False, ("lessons_print",)),
    ("books", "Sách", "books", True, ("book_page",)),
    ("students", "Học sinh", "students", True, ()),
    ("loans", "Đang mượn", "loans", True, ()),
    ("timetable_page", "Thời khóa biểu", "timetable", True, ()),
    ("schedule", "Lịch chuyển sách", "rotate", True, ()),
]
TOOL_NAV = [
    ("labels", "In nhãn QR", "qr"),
    ("import_page", "Nhập CSV", "upload"),
    ("settings_page", "Cài đặt", "settings"),
]

LAYOUT = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sách Chung</title>
<link rel="icon" href="{{ favicon }}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
/* Mực tím trên vở ô li, đánh dấu bằng bút dạ quang */
:root{--bg:#F2F4FA;--card:#fff;--ink:#1A2155;--muted:#5A6283;--line:#DDE2F0;
      --accent:#2B3A9E;--accent-ink:#fff;--band:#1F2A7A;--band-2:#283591;--hl:#FFE45C;--hl-ink:#1A2155;
      --warn:#B42318;--warn-bg:#FCEAE8;--ok:#1F6B45;--ok-bg:#E3F2E9;--err-bg:#FCEAE8;--err:#B42318}
@media (prefers-color-scheme: dark){:root{--bg:#10142E;--card:#181D3D;--ink:#E8EBFA;--muted:#A3AAD0;--line:#2A3160;
      --accent:#8FA0FF;--accent-ink:#10142E;--band:#0B0F26;--band-2:#151B44;--warn:#FF9C8F;--warn-bg:#3A1C22;
      --ok:#7FD3A3;--ok-bg:#16302A;--err-bg:#3A1C22;--err:#FF9C8F}}
*{box-sizing:border-box}
body{margin:0;font:16px/1.55 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:var(--bg);color:var(--ink);font-feature-settings:"tnum" 1}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--hl);outline-offset:2px;border-radius:6px}

/* ---- Thanh điều hướng ---- */
.top{background:var(--band);color:#fff;
     background-image:linear-gradient(rgba(255,255,255,.055) 1px,transparent 1px),
                      linear-gradient(90deg,rgba(255,255,255,.055) 1px,transparent 1px);
     background-size:22px 22px}
.top-in{max-width:1080px;margin:0 auto;padding:0 16px}
.bar{display:flex;align-items:center;gap:12px;padding:14px 0 12px}
.brand{display:flex;align-items:center;gap:12px;color:#fff;text-decoration:none;margin-right:auto}
.brand svg{flex:none;filter:drop-shadow(0 2px 0 rgba(0,0,0,.25))}
.brand b{display:block;font-size:22px;font-weight:800;letter-spacing:-.02em;line-height:1.1}
.brand small{display:block;font-size:13px;color:rgba(255,255,255,.72)}
.tools{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.tool{display:inline-flex;align-items:center;gap:6px;padding:7px 10px;border-radius:8px;font-size:14px;
      color:rgba(255,255,255,.85);text-decoration:none}
.tool:hover{background:rgba(255,255,255,.1);color:#fff}
.tool.on{background:rgba(255,224,92,.16);color:var(--hl)}
.tool.cta{border:1.5px solid rgba(255,255,255,.35)}
.tabs{display:flex;flex-wrap:wrap;gap:6px;padding:0 0 14px}
@media (max-width:900px){.tabs{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:0 -16px;padding:0 16px 14px}}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:none;display:inline-flex;align-items:center;gap:8px;padding:9px 14px;border-radius:12px;
     font-size:15px;font-weight:600;color:#fff;text-decoration:none;background:var(--band-2);
     border:1px solid rgba(255,255,255,.08)}
.tab:hover{background:#34449F}
.tab.on{background:var(--hl);color:var(--hl-ink);border-color:var(--hl);box-shadow:0 3px 0 #C9A800}
.ico{width:19px;height:19px;flex:none;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.tool .ico{width:17px;height:17px}

/* ---- Nội dung ---- */
main{max-width:1080px;margin:0 auto;padding:28px 16px 56px}
h1{font-size:28px;line-height:1.2;font-weight:800;letter-spacing:-.02em;margin:0 0 18px}
h2{font-size:19px;font-weight:700;margin:30px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.narrow{max-width:440px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:30px;font-weight:800;line-height:1.1;letter-spacing:-.02em}
.stat span{color:var(--muted);font-size:14px}
.stat.warn{background:var(--warn-bg);border-color:transparent} .stat.warn b{color:var(--warn)}
.bar-p{height:12px;background:var(--line);border-radius:6px;overflow:hidden}
.bar-p i{display:block;height:100%;background:var(--accent)}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:15px;vertical-align:top}
tr:last-child td{border-bottom:0}
th{color:var(--muted);font-weight:600;font-size:13.5px;background:color-mix(in srgb,var(--line) 35%,var(--card))}
.scroll{overflow-x:auto}
label{display:block;margin-bottom:10px;font-size:14px;font-weight:500;color:var(--muted)}
input,select,textarea{display:block;width:100%;margin-top:5px;padding:10px 12px;font:inherit;color:var(--ink);
      background:var(--bg);border:1.5px solid var(--line);border-radius:10px}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);background:var(--card)}
input[type=checkbox]{display:inline;width:auto;margin:0 6px 0 0;accent-color:var(--accent)}
button,.btn{display:inline-block;padding:10px 18px;font:inherit;font-weight:700;border:1.5px solid var(--accent);border-radius:10px;
      background:var(--accent);color:var(--accent-ink);cursor:pointer;text-decoration:none}
button:hover,.btn:hover{filter:brightness(1.1)}
button[disabled]{opacity:.45;cursor:not-allowed}
.btn.ghost{background:transparent;color:var(--accent)}
.btn.ghost:hover{background:color-mix(in srgb,var(--accent) 8%,transparent);filter:none}
.big{width:100%;padding:16px;font-size:18px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end} .row>*{flex:1;min-width:140px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:13px;font-weight:600;background:var(--line)}
.pill.ok{background:var(--ok-bg);color:var(--ok)} .pill.warn{background:var(--warn-bg);color:var(--warn)}
.flash{padding:12px 16px;border-radius:10px;margin-bottom:14px;background:var(--ok-bg);color:var(--ok);font-weight:500}
.flash.error{background:var(--err-bg);color:var(--err)}
.muted{color:var(--muted)} .small{font-size:14px}
.labels{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.label{border:1px dashed #8890B5;border-radius:8px;padding:8px;text-align:center;background:#fff;color:#000;break-inside:avoid}
.label img{width:130px;height:130px} .label b{display:block;font-size:15px} .label span{font-size:12px}
.sheet{background:#fff;color:#000;border:1px solid var(--line);border-radius:8px;padding:24px 28px;max-width:800px}
.sheet h1{font-size:20px;text-align:center;margin:0 0 4px} .sheet h3{font-size:16px;margin:18px 0 6px}
.sheet .who{display:flex;gap:24px;margin:12px 0 4px} .sheet .who>div{display:flex;flex:1;gap:6px}
.sheet .who span{flex:1;border-bottom:1px dotted #000}
.sheet ol{margin:4px 0;padding-left:22px} .sheet .ln{border-bottom:1px dotted #777;height:26px}
.sheet .lesson{break-inside:avoid;border-top:1px solid #ccc;padding-top:6px;display:flex;gap:12px}
.sheet .lesson>div{flex:1} .sheet .lesson img{width:84px;height:84px}
.sheet .foot{font-size:12px;color:#444;margin-top:18px;border-top:1px solid #ccc;padding-top:6px}
@media (max-width:640px){.bar{flex-wrap:wrap}.tools{width:100%;justify-content:flex-start}.tool span{display:none}
      .tool{padding:8px}h1{font-size:24px}}
@media print{.top,.noprint{display:none!important} body{background:#fff} main{padding:0;max-width:none}
      .label{border-color:#999} .sheet{border:0;padding:0;max-width:none}}
</style></head><body>
{% macro icon(name) %}<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">{{ icons[name]|safe }}</svg>{% endmacro %}
<header class="top"><div class="top-in">
  <div class="bar">
    <a class="brand" href="{{ url_for('dashboard') }}">{{ logo|safe }}
      <span><b>Sách Chung</b><small>Mượn sách giáo khoa luân phiên</small></span></a>
    <nav class="tools" aria-label="Công cụ">
      {% if session.teacher %}
        {% for ep, text, ic in tool_nav %}
        <a class="tool {{ 'on' if request.endpoint == ep }}" href="{{ url_for(ep) }}" title="{{ text }}">{{ icon(ic) }}<span>{{ text }}</span></a>
        {% endfor %}
        <a class="tool" href="{{ url_for('logout') }}" title="Đăng xuất">{{ icon('logout') }}<span>Đăng xuất</span></a>
      {% else %}
        <a class="tool cta {{ 'on' if request.endpoint == 'login' }}" href="{{ url_for('login') }}">{{ icon('login') }}<span>Giáo viên đăng nhập</span></a>
      {% endif %}
    </nav>
  </div>
  <nav class="tabs" aria-label="Trang chính">
    {% for ep, text, ic, teacher_only, also in main_nav if session.teacher or not teacher_only %}
    {% set on = request.endpoint == ep or request.endpoint in also %}
    <a class="tab {{ 'on' if on }}" href="{{ url_for(ep) }}" {{ 'aria-current=page' if on }}>{{ icon(ic) }}{{ text }}</a>
    {% endfor %}
  </nav>
</div></header>
<main>
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
{% endwith %}
{{ body|safe }}
</main>
<script>var t=document.querySelector('.tab.on');if(t&&t.scrollIntoView)t.scrollIntoView({block:'nearest',inline:'center'});</script>
</body></html>"""


def page(body_tpl, **ctx):
    body = render_template_string(body_tpl, **ctx)
    return render_template_string(LAYOUT, body=body, logo=LOGO_SVG.format(size=44), favicon=FAVICON,
                                  icons=ICONS, main_nav=MAIN_NAV, tool_nav=TOOL_NAV)


# ============================================================
# Trang tổng quan
# ============================================================
@app.route("/")
def dashboard():
    db = get_db()
    st = stats(db)
    per_class = db.execute("""
        SELECT s.class_name,
               COUNT(*) AS total,
               SUM(s.has_own_book) AS own,
               SUM(CASE WHEN s.has_own_book = 0 AND s.has_device = 0 THEN 1 ELSE 0 END) AS no_device,
               (SELECT COUNT(*) FROM books b WHERE b.class_name = s.class_name) AS books,
               (SELECT COUNT(*) FROM loans l JOIN books b ON b.id = l.book_id
                 WHERE b.class_name = s.class_name AND l.returned_at IS NULL) AS out
        FROM students s GROUP BY s.class_name ORDER BY s.class_name""").fetchall()
    by_subject = db.execute("""
        SELECT b.subject, b.grade, COUNT(*) AS n,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM loans l WHERE l.book_id = b.id AND l.returned_at IS NULL)
                        THEN 1 ELSE 0 END) AS out
        FROM books b GROUP BY b.subject, b.grade ORDER BY b.grade, b.subject""").fetchall()
    return page("""
      <h1>Tổng quan</h1>
      {% if st.total and st.pct_own == 100 %}
        <div class="card" style="background:var(--ok-bg)"><b>🎉 Tất cả học sinh đã có sách chính thức.</b>
          Chương trình chuyển tiếp có thể kết thúc: thu hồi sách mượn về thư viện và
          <a href="{{ url_for('export_loans') }}">tải nhật ký mượn (CSV)</a> để lưu.</div>
      {% endif %}
      <div class="card">
        <div class="row" style="align-items:center">
          <div><b>{{ st.own }}/{{ st.total }}</b> học sinh đã có sách chính thức ({{ st.pct_own }}%)</div>
        </div>
        <div class="bar-p" style="margin-top:8px"><i style="width:{{ st.pct_own }}%"></i></div>
        <p class="small muted" style="margin:8px 0 0">Khi đạt 100%, chương trình chuyển tiếp kết thúc.</p>
      </div>
      <div class="grid">
        <div class="stat"><b>{{ st.need }}</b><span>học sinh đang chờ sách</span></div>
        <div class="stat"><b>{{ st.no_device }}</b><span>trong số đó không có thiết bị đọc SGK điện tử</span></div>
        <div class="stat {% if st.unreached %}warn{% endif %}"><b>{{ st.unreached }}</b><span>chưa từng được mượn sách về nhà</span></div>
        <div class="stat"><b>{{ st.matching }}</b><span>cuốn đúng bộ sách trường dạy</span></div>
        {% if st.reference %}<div class="stat"><b>{{ st.reference }}</b><span>cuốn khác bộ (chỉ tham khảo)</span></div>{% endif %}
        <div class="stat"><b>{{ st.out }}</b><span>đang được mượn</span></div>
        <div class="stat {% if st.overdue %}warn{% endif %}"><b>{{ st.overdue }}</b><span>quá hạn trả</span></div>
      </div>

      <h2>Theo lớp</h2>
      <div class="scroll"><table>
        <tr><th>Lớp</th><th>Sĩ số</th><th>Đã có sách</th><th>Không thiết bị</th><th>Sách của lớp</th><th>Đang mượn</th></tr>
        {% for c in per_class %}
        <tr><td><b>{{ c.class_name }}</b></td><td>{{ c.total }}</td><td>{{ c.own }}</td>
            <td>{{ c.no_device }}</td><td>{{ c.books }}</td><td>{{ c.out }}</td></tr>
        {% else %}<tr><td colspan="6" class="muted">Chưa có dữ liệu học sinh.</td></tr>{% endfor %}
      </table></div>

      <h2>Kho sách theo môn</h2>
      <div class="scroll"><table>
        <tr><th>Môn</th><th>Khối</th><th>Tổng</th><th>Đang mượn</th><th>Sẵn sàng</th></tr>
        {% for b in by_subject %}
        <tr><td>{{ b.subject }}</td><td>{{ b.grade }}</td><td>{{ b.n }}</td><td>{{ b.out }}</td><td>{{ b.n - b.out }}</td></tr>
        {% else %}<tr><td colspan="5" class="muted">Chưa có sách.</td></tr>{% endfor %}
      </table></div>

      <h2>Đọc SGK điện tử miễn phí</h2>
      <div class="card">Toàn bộ SGK được NXB Giáo dục Việt Nam cung cấp miễn phí tại
        <a href="{{ ebook }}" target="_blank" rel="noopener">{{ ebook }}</a>.
        Trên lớp, giáo viên mở trực tiếp trang này lên TV/máy chiếu; hệ thống không sao chép nội dung sách.</div>
    """, st=st, per_class=per_class, by_subject=by_subject, ebook=EBOOK_HOME)


# ============================================================
# Trang một cuốn sách (đích đến khi quét QR)
# ============================================================
@app.route("/b/<int:book_id>")
def book_page(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        abort(404)
    loan = active_loan(db, book_id)
    edition = school_edition(db)
    reference = is_reference(book, edition)
    suggestions = suggest_borrowers(db, book) if (session.get("teacher") and not loan and not reference) else []
    others = []
    if session.get("teacher") and not loan:
        others = db.execute("SELECT id, name, class_name FROM students WHERE has_own_book = 0 "
                            "ORDER BY class_name, name").fetchall()
    history = db.execute("""SELECT l.*, s.name, s.class_name FROM loans l JOIN students s ON s.id = l.student_id
                            WHERE l.book_id = ? ORDER BY l.out_at DESC LIMIT 10""", (book_id,)).fetchall()
    overdue = loan and loan["due_at"] < now()
    return page("""
      <h1>{{ b.subject }} {{ b.grade }} <span class="muted">· #{{ b.id }}</span></h1>
      <div class="card">
        <div class="row">
          <div><span class="muted small">Lớp giữ sách</span><br><b>{{ b.class_name or '—' }}</b></div>
          <div><span class="muted small">Tình trạng</span><br><b>{{ b.condition }}</b></div>
          <div><span class="muted small">Nguồn</span><br><b>{{ b.source }}</b></div>
          <div><span class="muted small">Bộ sách</span><br><b>{{ b.edition or edition or '—' }}</b>
            {% if reference %}<br><span class="pill warn">Tham khảo · khác bộ trường dạy</span>{% endif %}</div>
          <div><span class="muted small">Trạng thái</span><br>
            {% if loan %}<span class="pill {{ 'warn' if overdue else '' }}">Đang mượn{{ ' · QUÁ HẠN' if overdue }}</span>
            {% else %}<span class="pill ok">Sẵn sàng</span>{% endif %}</div>
        </div>
      </div>

      {% if loan %}
        <div class="card">
          <p style="margin-top:0">Đang ở chỗ <b>{{ loan.name }}</b> ({{ loan.class_name }})<br>
            <span class="small muted">Mượn lúc {{ loan.out_at }} · hạn trả {{ loan.due_at }}</span></p>
          {% if session.teacher %}
          <form method="post" action="{{ url_for('return_book', book_id=b.id) }}">
            <label>Tình trạng khi trả
              <select name="condition">
                {% for c in ['Tốt','Hơi cũ','Rách/hư cần sửa','Mất'] %}
                <option {{ 'selected' if c == b.condition }}>{{ c }}</option>{% endfor %}
              </select></label>
            <button class="big">✅ Xác nhận đã trả</button>
          </form>
          {% endif %}
        </div>
      {% elif session.teacher %}
        <div class="card">
          <h2 style="margin-top:0">Cho mượn</h2>
          {% if suggestions %}
          <p class="small muted">Gợi ý theo thứ tự ưu tiên: không có thiết bị → mượn ít lần nhất → lâu chưa được mượn.</p>
          {% for s in suggestions %}
            <form method="post" action="{{ url_for('borrow', book_id=b.id) }}" style="margin-bottom:8px">
              <input type="hidden" name="student_id" value="{{ s.id }}">
              <button class="big" style="text-align:left">{{ s.name }}
                <span style="font-weight:400;font-size:14px"> · {{ s.class_name }}
                {% if not s.has_device %}· 📵 không thiết bị{% endif %} · đã mượn {{ s.times }} lần</span></button>
            </form>
          {% endfor %}
          {% elif reference %}<p class="flash error">Cuốn này thuộc bộ <b>{{ b.edition }}</b>, khác bộ trường đang dạy
            (<b>{{ edition }}</b>) nên không được gợi ý tự động và không đưa vào lịch xoay vòng.
            Vẫn có thể cho mượn thủ công để tham khảo.</p>
          {% else %}<p class="muted">Không còn học sinh nào trong lớp cần mượn môn này.</p>{% endif %}
          <form method="post" action="{{ url_for('borrow', book_id=b.id) }}" style="margin-top:16px">
            <label>Hoặc chọn học sinh khác
              <select name="student_id">{% for s in others %}
                <option value="{{ s.id }}">{{ s.class_name }} – {{ s.name }}</option>{% endfor %}</select></label>
            <button class="btn ghost">Cho mượn</button>
          </form>
        </div>
      {% else %}
        <div class="card">Để mượn hoặc trả sách, hãy đưa cho giáo viên quét mã này
          (<a href="{{ url_for('login', next=request.path) }}">đăng nhập giáo viên</a>).<br>
          Trong lúc chờ, em có thể đọc SGK điện tử miễn phí tại
          <a href="{{ ebook }}" target="_blank" rel="noopener">{{ ebook }}</a>.</div>
      {% endif %}

      {% if history %}
      <h2>Lịch sử gần đây</h2>
      <div class="scroll"><table><tr><th>Học sinh</th><th>Mượn</th><th>Trả</th></tr>
        {% for h in history %}<tr><td>{{ h.name }} <span class="muted small">{{ h.class_name }}</span></td>
          <td>{{ h.out_at }}</td><td>{{ h.returned_at or '—' }}</td></tr>{% endfor %}
      </table></div>
      {% endif %}
    """, b=book, loan=loan, overdue=overdue, suggestions=suggestions, others=others,
                edition=edition, reference=reference,
                history=history, ebook=EBOOK_HOME)


@app.post("/b/<int:book_id>/borrow")
@teacher_required
def borrow(book_id):
    db = get_db()
    if active_loan(db, book_id):
        flash("Sách này đang được mượn, cần trả trước.", "error")
        return redirect(url_for("book_page", book_id=book_id))
    student_id = request.form.get("student_id", type=int)
    st = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not st:
        abort(400)
    due = (datetime.now() + timedelta(days=LOAN_DAYS)).replace(hour=7, minute=30).strftime("%Y-%m-%d %H:%M")
    db.execute("INSERT INTO loans(book_id, student_id, out_at, due_at) VALUES (?,?,?,?)",
               (book_id, student_id, now(), due))
    db.commit()
    flash(f"Đã cho {st['name']} mượn. Hạn trả: {due}.")
    return redirect(url_for("book_page", book_id=book_id))


@app.post("/b/<int:book_id>/return")
@teacher_required
def return_book(book_id):
    db = get_db()
    loan = active_loan(db, book_id)
    if loan:
        db.execute("UPDATE loans SET returned_at = ? WHERE id = ?", (now(), loan["id"]))
        cond = request.form.get("condition")
        if cond:
            db.execute("UPDATE books SET condition = ? WHERE id = ?", (cond, book_id))
        db.commit()
        flash(f"{loan['name']} đã trả sách.")
    return redirect(url_for("book_page", book_id=book_id))


# ============================================================
# Quản lý sách / học sinh / phiếu mượn
# ============================================================
@app.route("/books", methods=["GET", "POST"])
@teacher_required
def books():
    db = get_db()
    if request.method == "POST":
        f = request.form
        n = import_books(db, [{"subject": f["subject"], "grade": f["grade"], "class_name": f.get("class_name"),
                               "condition": f.get("condition"), "source": f.get("source"),
                               "quantity": f.get("quantity") or 1, "edition": f.get("edition")}])
        db.commit()
        flash(f"Đã thêm {n} cuốn. Nhớ in nhãn QR cho sách mới.")
        return redirect(url_for("books"))
    rows = db.execute("""SELECT b.*, s.name AS borrower, l.due_at FROM books b
                         LEFT JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
                         LEFT JOIN students s ON s.id = l.student_id
                         ORDER BY b.grade, b.subject, b.id""").fetchall()
    return page("""
      <h1>Kho sách chung</h1>
      <form method="post" class="card">
        <div class="row">
          <label>Môn<input name="subject" required placeholder="Toán"></label>
          <label>Khối<input name="grade" type="number" min="1" max="12" required></label>
          <label>Lớp giữ<input name="class_name" placeholder="6A1"></label>
          <label>Số lượng<input name="quantity" type="number" min="1" value="1"></label>
          <label>Nguồn<select name="source">
            <option>Thư viện</option><option>Phụ huynh tặng</option><option>Giáo viên</option><option>Trường khác</option>
          </select></label>
          <label>Bộ sách<input name="edition" list="editions" value="{{ edition }}"
            placeholder="để trống = bộ trường dạy"></label>
          <div><button>Thêm sách</button></div>
        </div>
      </form>
      <div class="scroll"><table>
        <tr><th>#</th><th>Môn</th><th>Khối</th><th>Lớp</th><th>Bộ sách</th><th>Tình trạng</th><th>Đang ở</th></tr>
        {% for b in rows %}
        <tr><td><a href="{{ url_for('book_page', book_id=b.id) }}">{{ b.id }}</a></td><td>{{ b.subject }}</td>
          <td>{{ b.grade }}</td><td>{{ b.class_name or '—' }}</td>
          <td>{{ b.edition or edition or '—' }}{% if b.edition and edition and b.edition != edition %}
              <span class="pill warn">tham khảo</span>{% endif %}</td><td>{{ b.condition }}</td>
          <td>{% if b.borrower %}{{ b.borrower }}
              {% if b.due_at < now %}<span class="pill warn">quá hạn</span>{% endif %}
              {% else %}<span class="pill ok">Sẵn sàng</span>{% endif %}</td></tr>
        {% endfor %}
      </table></div>
      <datalist id="editions">{% for e in editions %}<option value="{{ e }}">{% endfor %}</datalist>""",
                rows=rows, now=now(), edition=school_edition(db), editions=EDITION_SUGGESTIONS)


@app.route("/students", methods=["GET", "POST"])
@teacher_required
def students():
    db = get_db()
    if request.method == "POST":
        f = request.form
        if f.get("action") == "toggle":
            col = f["field"]
            if col not in ("has_device", "has_own_book"):
                abort(400)
            db.execute(f"UPDATE students SET {col} = 1 - {col} WHERE id = ?", (f["id"],))
        else:
            import_students(db, [{"name": f["name"], "class_name": f["class_name"],
                                  "has_device": f.get("has_device", 0), "has_own_book": 0}])
            flash("Đã thêm học sinh.")
        db.commit()
        return redirect(url_for("students", cls=request.args.get("cls", "")))
    cls = request.args.get("cls") or None
    classes = [r[0] for r in db.execute("SELECT DISTINCT class_name FROM students ORDER BY 1")]
    rows = db.execute("""SELECT s.*, (SELECT COUNT(*) FROM loans l WHERE l.student_id = s.id) AS times,
                                (SELECT GROUP_CONCAT(b.subject, ', ') FROM loans l JOIN books b ON b.id = l.book_id
                                  WHERE l.student_id = s.id AND l.returned_at IS NULL) AS holding
                         FROM students s WHERE (? IS NULL OR s.class_name = ?)
                         ORDER BY s.class_name, s.name""", (cls, cls)).fetchall()
    return page("""
      <h1>Học sinh</h1>
      <form method="post" class="card">
        <div class="row">
          <label>Họ tên<input name="name" required></label>
          <label>Lớp<input name="class_name" required placeholder="6A1"></label>
          <label style="flex:0 0 auto;padding-bottom:10px"><input type="checkbox" name="has_device" value="1">Có thiết bị</label>
          <div><button>Thêm</button></div>
        </div>
      </form>
      <p class="noprint">Lọc lớp:
        <a href="{{ url_for('students') }}">Tất cả</a>
        {% for c in classes %} · <a href="{{ url_for('students', cls=c) }}">{{ c }}</a>{% endfor %}</p>
      <p class="small muted">Bấm vào ô "Thiết bị" hoặc "Sách chính thức" để đổi trạng thái
        (vd khi học sinh đã nhận được sách chính thức).</p>
      <div class="scroll"><table>
        <tr><th>Họ tên</th><th>Lớp</th><th>Thiết bị</th><th>Sách chính thức</th><th>Đang giữ</th><th>Số lần mượn</th></tr>
        {% for s in rows %}
        <tr><td>{{ s.name }}</td><td>{{ s.class_name }}</td>
          {% for field, val in [('has_device', s.has_device), ('has_own_book', s.has_own_book)] %}
          <td><form method="post" style="margin:0">
            <input type="hidden" name="action" value="toggle"><input type="hidden" name="id" value="{{ s.id }}">
            <input type="hidden" name="field" value="{{ field }}">
            <button class="btn ghost" style="padding:2px 10px">{{ '✔ Có' if val else '✘ Chưa' }}</button>
          </form></td>{% endfor %}
          <td>{{ s.holding or '—' }}</td><td>{{ s.times }}</td></tr>
        {% endfor %}
      </table></div>""", rows=rows, classes=classes)


@app.route("/loans")
@teacher_required
def loans():
    rows = get_db().execute("""SELECT l.*, s.name, s.class_name, b.subject, b.grade FROM loans l
                               JOIN students s ON s.id = l.student_id JOIN books b ON b.id = l.book_id
                               WHERE l.returned_at IS NULL ORDER BY l.due_at""").fetchall()
    return page("""
      <h1>Sách đang được mượn</h1>
      <p><a class="btn ghost" href="{{ url_for('export_loans') }}">Tải toàn bộ nhật ký mượn (CSV)</a></p>
      <div class="scroll"><table>
        <tr><th>Sách</th><th>Học sinh</th><th>Mượn lúc</th><th>Hạn trả</th></tr>
        {% for l in rows %}
        <tr><td><a href="{{ url_for('book_page', book_id=l.book_id) }}">#{{ l.book_id }} {{ l.subject }} {{ l.grade }}</a></td>
          <td>{{ l.name }} <span class="muted small">{{ l.class_name }}</span></td><td>{{ l.out_at }}</td>
          <td>{{ l.due_at }} {% if l.due_at < now %}<span class="pill warn">quá hạn</span>{% endif %}</td></tr>
        {% else %}<tr><td colspan="4" class="muted">Không có sách nào đang được mượn.</td></tr>{% endfor %}
      </table></div>""", rows=rows, now=now())


@app.route("/export/loans.csv")
@teacher_required
def export_loans():
    rows = get_db().execute("""SELECT l.id, b.id AS book_id, b.subject, b.grade, s.name, s.class_name,
                                      l.out_at, l.due_at, l.returned_at
                               FROM loans l JOIN books b ON b.id = l.book_id JOIN students s ON s.id = l.student_id
                               ORDER BY l.out_at""").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["loan_id", "book_id", "subject", "grade", "student", "class", "out_at", "due_at", "returned_at"])
    for r in rows:
        w.writerow(list(r))
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=nhat_ky_muon_sach.csv"})


# ============================================================
# In nhãn QR
# ============================================================
@app.route("/labels")
@teacher_required
def labels():
    db = get_db()
    base = request.args.get("base") or request.host_url.rstrip("/")
    warn_local = "localhost" in base or "127.0.0.1" in base
    suggested = f"http://{lan_ip()}:{request.host.split(':')[-1] if ':' in request.host else 5000}"
    cls = request.args.get("cls") or None
    rows = db.execute("SELECT * FROM books WHERE (? IS NULL OR class_name = ?) ORDER BY class_name, subject, id",
                      (cls, cls)).fetchall()
    items = [dict(b=b, qr=qr_data_uri(f"{base}/b/{b['id']}")) for b in rows]
    return page("""
      <div class="noprint">
        <h1>In nhãn QR</h1>
        {% if warn_local %}
        <div class="flash error">Đang dùng địa chỉ <b>{{ base }}</b> – điện thoại sẽ KHÔNG mở được.
          Dùng địa chỉ mạng LAN: <a href="{{ url_for('labels', base=suggested, cls=cls) }}">{{ suggested }}</a>
          (điện thoại phải chung Wi-Fi với máy này).</div>
        {% endif %}
        <p>Mã QR trỏ tới <b>{{ base }}/b/&lt;số sách&gt;</b>. In ra, cắt và dán vào bìa trong của sách.
          Quét bằng camera điện thoại là mở được trang mượn/trả.</p>
        <p><button onclick="window.print()">🖨 In trang này</button></p>
      </div>
      <div class="labels">
        {% for it in items %}
        <div class="label"><img src="{{ it.qr }}" alt="QR sách {{ it.b.id }}">
          <b>{{ it.b.subject }} {{ it.b.grade }} · #{{ it.b.id }}</b>
          <span>Sách Chung · lớp {{ it.b.class_name or '—' }}</span></div>
        {% else %}<p class="muted">Chưa có sách.</p>{% endfor %}
      </div>""", items=items, base=base, warn_local=warn_local, suggested=suggested, cls=cls)


# ============================================================
# Bài tuần này (công khai cho học sinh)
# ============================================================
@app.route("/lessons", methods=["GET", "POST"])
def lessons():
    db = get_db()
    if request.method == "POST":
        if not session.get("teacher"):
            abort(403)
        f = request.form
        import_lessons(db, [dict(f)])
        db.commit()
        flash("Đã thêm bài học.")
        return redirect(url_for("lessons", grade=f.get("grade")))
    grades = [r[0] for r in db.execute("SELECT DISTINCT grade FROM lessons ORDER BY 1")]
    grade = request.args.get("grade", type=int) or (grades[0] if grades else None)
    weeks = [r[0] for r in db.execute("SELECT DISTINCT week FROM lessons WHERE grade = ? ORDER BY 1", (grade,))]
    week = request.args.get("week", type=int) or (weeks[-1] if weeks else None)
    rows = db.execute("SELECT * FROM lessons WHERE grade = ? AND week = ? ORDER BY subject",
                      (grade, week)).fetchall()
    return page("""
      <h1>Bài học tuần {{ week or '' }}{% if grade %} · Khối {{ grade }}{% endif %}</h1>
      <p>Khối: {% for gr in grades %}<a class="btn {{ '' if gr == grade else 'ghost' }}" style="padding:4px 12px"
         href="{{ url_for('lessons', grade=gr) }}">{{ gr }}</a> {% endfor %}
         &nbsp; Tuần: {% for w in weeks %}<a href="{{ url_for('lessons', grade=grade, week=w) }}">{{ w }}</a>
         {% if not loop.last %}·{% endif %} {% endfor %}</p>
      {% if rows %}<p><a class="btn ghost" href="{{ url_for('lessons_print', grade=grade, week=week) }}">
        🖨 In phiếu học tập tuần {{ week }} (cho học sinh không có thiết bị)</a></p>{% endif %}
      {% for l in rows %}
      <div class="card">
        <span class="pill">{{ l.subject }}</span>
        <h2 style="margin:8px 0">{{ l.title }}</h2>
        {% if l.note %}<p>{{ l.note }}</p>{% endif %}
        {% if l.tasks %}<ol>{% for t in l.tasks.split('\n') %}<li>{{ t }}</li>{% endfor %}</ol>{% endif %}
        <div class="row" style="flex:0">
          <a class="btn" href="{{ l.ebook_url or ebook }}" target="_blank" rel="noopener">📖 Đọc SGK điện tử (miễn phí)</a>
          {% if l.worksheet_url %}<a class="btn ghost" href="{{ l.worksheet_url }}" target="_blank" rel="noopener">📝 Phiếu học tập</a>{% endif %}
        </div>
      </div>
      {% else %}<p class="muted">Chưa có bài học nào.</p>{% endfor %}
      <p class="small muted">SGK điện tử do NXB Giáo dục Việt Nam cung cấp miễn phí. Phiếu học tập do giáo viên tự biên soạn.</p>

      {% if session.teacher %}
      <h2>Thêm bài học</h2>
      <form method="post" class="card">
        <div class="row">
          <label>Khối<input name="grade" type="number" min="1" max="12" value="{{ grade or '' }}" required></label>
          <label>Môn<input name="subject" required></label>
          <label>Tuần<input name="week" type="number" min="1" value="{{ week or 1 }}" required></label>
        </div>
        <label>Tên bài<input name="title" required></label>
        <label>Link SGK điện tử (để trống = trang chủ {{ ebook }})<input name="ebook_url" type="url"></label>
        <label>Link phiếu học tập do giáo viên soạn (Google Drive…)<input name="worksheet_url" type="url"></label>
        <label>Ghi chú cho học sinh<textarea name="note" rows="2"></textarea></label>
        <label>Nhiệm vụ / câu hỏi do giáo viên soạn (mỗi dòng một ý – sẽ in lên phiếu học tập)
          <textarea name="tasks" rows="3"></textarea></label>
        <button>Thêm bài</button>
      </form>
      {% endif %}""", rows=rows, grades=grades, grade=grade, weeks=weeks, week=week, ebook=EBOOK_HOME)


# ============================================================
# Nhập dữ liệu từ CSV
# ============================================================
@app.route("/import", methods=["GET", "POST"])
@teacher_required
def import_page():
    if request.method == "POST":
        kind = request.form.get("kind")
        f = request.files.get("file")
        fn = {"students": import_students, "books": import_books, "lessons": import_lessons,
              "timetable": import_timetable}.get(kind)
        if not f or not fn:
            flash("Chọn loại dữ liệu và file CSV.", "error")
        else:
            try:
                db = get_db()
                n = fn(db, read_csv_rows(f))
                db.commit()
                flash(f"Đã nhập {n} dòng.")
            except (KeyError, ValueError) as e:
                flash(f"File CSV sai định dạng (thiếu/sai cột {e}). Xem file mẫu trong thư mục data/.", "error")
        return redirect(url_for("import_page"))
    return page("""
      <h1>Nhập dữ liệu từ CSV</h1>
      <form method="post" enctype="multipart/form-data" class="card narrow">
        <label>Loại dữ liệu<select name="kind">
          <option value="students">Học sinh – cột: name, class_name, has_device, has_own_book</option>
          <option value="books">Sách – cột: subject, grade, class_name, quantity, condition, source, edition</option>
          <option value="lessons">Bài học – cột: grade, subject, week, title, ebook_url, worksheet_url, note, tasks</option>
          <option value="timetable">Thời khóa biểu – cột: class_name, weekday (2..7), period, subject</option>
        </select></label>
        <label>File CSV (UTF-8, có thể xuất từ Excel/Google Sheets)<input type="file" name="file" accept=".csv" required></label>
        <button>Nhập</button>
      </form>
      <p class="small muted">File mẫu nằm trong thư mục <code>data/</code>.
        Thời khóa biểu còn có thể <a href="{{ url_for('timetable_page') }}">nhập bằng ảnh chụp</a>.</p>""")



# ============================================================
# Cài đặt: bộ sách trường đang dạy
# ============================================================
@app.route("/settings", methods=["GET", "POST"])
@teacher_required
def settings_page():
    db = get_db()
    if request.method == "POST":
        set_setting(db, "edition", (request.form.get("edition") or "").strip())
        db.commit()
        flash("Đã lưu cài đặt.")
        return redirect(url_for("settings_page"))
    edition = school_edition(db)
    by_ed = db.execute("SELECT COALESCE(edition, '') AS ed, COUNT(*) AS n FROM books GROUP BY 1 ORDER BY 2 DESC").fetchall()
    return page("""
      <h1>Cài đặt</h1>
      <form method="post" class="card narrow">
        <label>Bộ sách giáo khoa trường đang dạy
          <input name="edition" list="editions" value="{{ edition }}" placeholder="Ví dụ: {{ editions[0] }}"></label>
        <datalist id="editions">{% for e in editions %}<option value="{{ e }}">{% endfor %}</datalist>
        <p class="small muted" style="margin-top:0">Sách khác bộ này được đánh dấu <b>tham khảo</b>: không được gợi ý
          tự động và không đưa vào lịch xoay vòng. Để trống nếu chưa muốn phân biệt.</p>
        <button>Lưu</button>
      </form>
      <h2>Kho sách theo bộ</h2>
      <div class="scroll"><table><tr><th>Bộ sách</th><th>Số cuốn</th><th></th></tr>
        {% for r in by_ed %}<tr><td>{{ r.ed or '(không ghi – tính theo bộ trường dạy)' }}</td><td>{{ r.n }}</td>
          <td>{% if r.ed and edition and r.ed != edition %}<span class="pill warn">tham khảo</span>
              {% else %}<span class="pill ok">đúng bộ</span>{% endif %}</td></tr>{% endfor %}
      </table></div>""", edition=edition, editions=EDITION_SUGGESTIONS, by_ed=by_ed)


# ============================================================
# Lịch chuyển sách trên lớp
# ============================================================
@app.route("/schedule")
@teacher_required
def schedule():
    db = get_db()
    today = datetime.now()
    wd_today = today.weekday() + 2 if today.weekday() < 6 else 2
    weekday = request.args.get("day", type=int) or wd_today
    if weekday not in WEEKDAYS:
        weekday = 2
    plan = plan_day(db, weekday)
    n_tt = db.execute("SELECT COUNT(*) FROM timetable").fetchone()[0]
    return page("""
      <h1>Lịch chuyển sách · {{ days[weekday] }}</h1>
      <p class="noprint">{% for d, name in days.items() %}<a class="btn {{ '' if d == weekday else 'ghost' }}"
         style="padding:4px 12px" href="{{ url_for('schedule', day=d) }}">{{ name }}</a> {% endfor %}
         &nbsp;<button onclick="window.print()" class="btn ghost" style="padding:4px 12px">🖨 In</button></p>
      {% if not n_tt %}
        <div class="card">Chưa có thời khóa biểu. <a href="{{ url_for('timetable_page') }}">Nhập thời khóa biểu</a>
          (bằng ảnh chụp hoặc file CSV).</div>
      {% elif not plan.periods %}
        <div class="card muted">Không có tiết học nào trong {{ days[weekday] }}.</div>
      {% else %}
      <div class="grid">
        <div class="stat"><b>{{ plan.pct_without }}%</b><span>lượt học sinh có sách trên lớp nếu mỗi lớp chỉ dùng sách của mình</span></div>
        <div class="stat"><b style="color:var(--accent)">{{ plan.pct_with }}%</b><span>khi xoay vòng sách giữa các lớp theo thời khóa biểu</span></div>
        <div class="stat"><b>{{ plan.n_moves }}</b><span>lượt chuyển sách trong buổi</span></div>
      </div>
      <p class="small muted">Trên lớp, {{ share }} học sinh ngồi cùng bàn dùng chung 1 cuốn. Học sinh chưa có sách được
        tính là cần sách. Chỉ dùng sách đúng bộ trường dạy, không tính sách mất hoặc đang bị giữ quá hạn.</p>
      {% for p in plan.periods %}
      <div class="card">
        <h2 style="margin-top:0">Tiết {{ p }}</h2>
        {% if plan.moves[p] %}
        <p style="margin:0 0 8px"><b>Trước tiết {{ p }}, chuyển:</b></p>
        <ul style="margin-top:0">{% for m in plan.moves[p] %}
          <li><b>{{ m.n }}</b> cuốn {{ m.subject }} {{ m.grade }}: {{ m.src }} → <b>{{ m.dst }}</b></li>{% endfor %}</ul>
        {% endif %}
        <div class="scroll"><table>
          <tr><th>Lớp</th><th>Môn</th><th>Em cần sách</th><th>Sách được dùng</th><th>Số em có sách</th></tr>
          {% for r in plan.rows[p] %}
          <tr><td><b>{{ r.class_name }}</b></td><td>{{ r.subject }}</td><td>{{ r.students }}</td>
            <td>{{ r.got }}/{{ r.need }} cuốn</td>
            <td>{{ r.covered }}/{{ r.students }}
              {% if r.covered < r.students %}<span class="pill warn">thiếu {{ r.students - r.covered }}</span>
              {% elif r.students %}<span class="pill ok">đủ</span>{% endif %}
              {% if r.covered > r.covered_old %}<span class="small muted"> (+{{ r.covered - r.covered_old }} nhờ xoay vòng)</span>{% endif %}
            </td></tr>{% endfor %}
        </table></div>
      </div>
      {% endfor %}
      {% if plan.end_moves %}
      <div class="card"><b>Cuối buổi, trả sách về lớp giữ</b> (để cho mượn về nhà qua đêm):
        <ul style="margin-bottom:0">{% for m in plan.end_moves %}
          <li>{{ m.n }} cuốn {{ m.subject }} {{ m.grade }}: {{ m.src }} → {{ m.dst }}</li>{% endfor %}</ul></div>
      {% endif %}
      {% endif %}
    """, plan=plan, weekday=weekday, days=WEEKDAYS, n_tt=n_tt, share=SHARE_PER_BOOK)


# ============================================================
# Thời khóa biểu: xem, nhập bằng ảnh (PaddleOCR-VL), xuất CSV
# ============================================================
def known_subjects(db):
    return [r[0] for r in db.execute("SELECT subject FROM books UNION SELECT subject FROM timetable "
                                     "UNION SELECT subject FROM lessons")]


def timetable_grid(rows):
    """[(weekday, period, subject)] → {period: {weekday: subject}}"""
    grid = {}
    for r in rows:
        grid.setdefault(r["period"], {})[r["weekday"]] = r["subject"]
    return grid


def timetable_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["class_name", "weekday", "period", "subject"])
    for r in rows:
        w.writerow([r["class_name"], r["weekday"], r["period"], r["subject"]])
    return "﻿" + buf.getvalue()   # BOM để Excel đọc đúng tiếng Việt


TT_GRID = """
  <div class="scroll"><table>
    <tr><th>Tiết</th>{% for d, name in days.items() %}<th>{{ name }}</th>{% endfor %}</tr>
    {% for p in periods %}<tr><td><b>{{ p }}</b></td>
      {% for d in days %}<td>{% if editable %}<input name="cell-{{ d }}-{{ p }}" value="{{ grid.get(p, {}).get(d, '') }}"
          list="subjects" style="margin:0;padding:6px;min-width:90px">{% else %}{{ grid.get(p, {}).get(d, '') }}{% endif %}</td>
      {% endfor %}</tr>{% endfor %}
  </table></div>"""


@app.route("/timetable")
@teacher_required
def timetable_page():
    db = get_db()
    classes = [r[0] for r in db.execute("SELECT DISTINCT class_name FROM timetable ORDER BY 1")]
    cls = request.args.get("cls") or (classes[0] if classes else None)
    rows = db.execute("SELECT * FROM timetable WHERE class_name = ?", (cls,)).fetchall()
    grid = timetable_grid(rows)
    periods = range(1, max(grid or [0]) + 1)
    return page("""
      <h1>Thời khóa biểu</h1>
      <form method="post" action="{{ url_for('timetable_scan') }}" enctype="multipart/form-data" class="card">
        <h2 style="margin-top:0">📷 Nhập bằng ảnh chụp</h2>
        <p class="small muted" style="margin-top:0">Chụp thẳng, đủ sáng, rõ toàn bộ bảng của <b>một lớp</b>.
          Hệ thống dùng PaddleOCR-VL đọc bảng, rồi cho bạn xem lại và sửa trước khi lưu.</p>
        {% if not ocr_ok %}<div class="flash error">{{ hint }}</div>{% endif %}
        <div class="row">
          <label>Ảnh thời khóa biểu<input type="file" name="image" accept="image/*" capture="environment" required></label>
          <label>Lớp (để trống = tự đọc từ ảnh)<input name="class_name" placeholder="6A1"></label>
          <div><button {{ 'disabled' if not ocr_ok }}>Đọc ảnh</button></div>
        </div>
      </form>
      <p>Hoặc <a href="{{ url_for('import_page') }}">nhập file CSV</a> ·
        <a href="{{ url_for('timetable_export') }}">Tải toàn bộ TKB (CSV)</a></p>
      {% if classes %}
        <p>Lớp: {% for c in classes %}<a class="btn {{ '' if c == cls else 'ghost' }}" style="padding:4px 12px"
          href="{{ url_for('timetable_page', cls=c) }}">{{ c }}</a> {% endfor %}</p>
        """ + TT_GRID + """
        <form method="post" action="{{ url_for('timetable_delete') }}" style="margin-top:10px">
          <input type="hidden" name="class_name" value="{{ cls }}">
          <button class="btn ghost" style="padding:4px 12px">Xóa TKB lớp {{ cls }}</button>
          <a class="btn ghost" style="padding:4px 12px" href="{{ url_for('timetable_export', cls=cls) }}">Tải CSV lớp {{ cls }}</a>
        </form>
      {% else %}<p class="muted">Chưa có thời khóa biểu.</p>{% endif %}
    """, classes=classes, cls=cls, grid=grid, periods=periods, days=WEEKDAYS, editable=False,
                ocr_ok=timetable_ocr.ocr_available(), hint=timetable_ocr.INSTALL_HINT)


@app.post("/timetable/scan")
@teacher_required
def timetable_scan():
    f = request.files.get("image")
    if not f or not f.filename:
        flash("Chọn ảnh thời khóa biểu.", "error")
        return redirect(url_for("timetable_page"))
    data = f.read()
    ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
    try:
        markdown = timetable_ocr.ocr_image(tmp.name)
    except Exception as e:   # chưa cài, hết VRAM, ảnh hỏng...
        flash(f"Không đọc được ảnh: {e}", "error")
        return redirect(url_for("timetable_page"))
    finally:
        os.unlink(tmp.name)
    db = get_db()
    cls, rows, warnings = timetable_ocr.parse_timetable(markdown, request.form.get("class_name"), known_subjects(db))
    mime = f.mimetype or "image/jpeg"
    return render_timetable_preview(cls, rows, warnings, markdown,
                                    "data:%s;base64,%s" % (mime, base64.b64encode(data).decode()))


def render_timetable_preview(cls, rows, warnings, markdown="", image=None):
    db = get_db()
    grid = timetable_grid(rows)
    periods = range(1, max(max(grid or [0]), 5) + 2)   # chừa thêm 1 hàng trống để bổ sung
    existing = db.execute("SELECT COUNT(*) FROM timetable WHERE class_name = ?", (cls,)).fetchone()[0] if cls else 0
    return page("""
      <h1>Kiểm tra thời khóa biểu vừa đọc</h1>
      {% for w in warnings %}<div class="flash error">{{ w }}</div>{% endfor %}
      <p class="small muted">Đọc được <b>{{ n }}</b> tiết có môn học. Sửa ô sai (OCR có thể nhầm), xóa trắng ô không học,
        rồi bấm Lưu. Chào cờ, Sinh hoạt lớp đã được bỏ qua vì không cần sách.</p>
      <form method="post" action="{{ url_for('timetable_save') }}">
        <div class="row">
          <label>Lớp<input name="class_name" value="{{ cls or '' }}" required placeholder="6A1"></label>
          <label style="flex:2;padding-bottom:10px"><input type="checkbox" name="replace" value="1" checked>
            Thay thế TKB cũ của lớp này{% if existing %} (đang có {{ existing }} tiết){% endif %}</label>
        </div>
        """ + TT_GRID + """
        <datalist id="subjects">{% for s in subjects %}<option value="{{ s }}">{% endfor %}</datalist>
        <p><button>💾 Lưu vào hệ thống và xuất CSV</button>
           <a class="btn ghost" href="{{ url_for('timetable_page') }}">Hủy</a></p>
      </form>
      {% if image %}<details class="card"><summary>Ảnh gốc</summary>
        <img src="{{ image }}" style="max-width:100%;margin-top:8px"></details>{% endif %}
      {% if markdown %}<details class="card"><summary>Kết quả OCR thô (PaddleOCR-VL)</summary>
        <pre style="white-space:pre-wrap;font-size:12px">{{ markdown }}</pre></details>{% endif %}
    """, cls=cls, grid=grid, periods=periods, days=WEEKDAYS, editable=True, warnings=warnings,
                n=len(rows), existing=existing, subjects=sorted(set(known_subjects(db))),
                markdown=markdown, image=image)


@app.post("/timetable/save")
@teacher_required
def timetable_save():
    db = get_db()
    cls = re.sub(r"\s+", "", request.form.get("class_name") or "").upper()
    if not cls:
        abort(400)
    rows = []
    for key, val in request.form.items():
        m = re.fullmatch(r"cell-(\d)-(\d{1,2})", key)
        subj = (val or "").strip()
        if m and subj:
            rows.append(dict(class_name=cls, weekday=int(m.group(1)), period=int(m.group(2)), subject=subj))
    rows.sort(key=lambda r: (r["weekday"], r["period"]))
    if not rows:
        flash("Thời khóa biểu trống, chưa lưu gì.", "error")
        return redirect(url_for("timetable_page"))
    if request.form.get("replace"):
        db.execute("DELETE FROM timetable WHERE class_name = ?", (cls,))
    n = import_timetable(db, rows)
    db.commit()
    os.makedirs(OCR_EXPORT_DIR, exist_ok=True)
    fname = f"TKB_{cls.replace('/', '-')}_{datetime.now():%Y%m%d_%H%M}.csv"
    with open(os.path.join(OCR_EXPORT_DIR, fname), "w", encoding="utf-8", newline="") as fh:
        fh.write(timetable_csv(rows))
    flash(f"Đã lưu {n} tiết cho lớp {cls}. File CSV: data/ocr/{fname}")
    return redirect(url_for("timetable_page", cls=cls))


@app.post("/timetable/delete")
@teacher_required
def timetable_delete():
    db = get_db()
    cls = request.form.get("class_name")
    db.execute("DELETE FROM timetable WHERE class_name = ?", (cls,))
    db.commit()
    flash(f"Đã xóa thời khóa biểu lớp {cls}.")
    return redirect(url_for("timetable_page"))


@app.route("/timetable.csv")
@teacher_required
def timetable_export():
    cls = request.args.get("cls")
    rows = get_db().execute("SELECT * FROM timetable WHERE (? IS NULL OR class_name = ?) "
                            "ORDER BY class_name, weekday, period", (cls, cls)).fetchall()
    name = f"TKB_{cls}.csv" if cls else "thoi_khoa_bieu.csv"
    return Response(timetable_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


# ============================================================
# Phiếu học tập tuần để in (cho học sinh không có thiết bị)
# ============================================================
@app.route("/lessons/print")
def lessons_print():
    db = get_db()
    grade = request.args.get("grade", type=int)
    week = request.args.get("week", type=int)
    rows = db.execute("SELECT * FROM lessons WHERE grade = ? AND week = ? ORDER BY subject", (grade, week)).fetchall()
    if not rows:
        abort(404)
    copies = sum(1 for s in db.execute("SELECT class_name FROM students WHERE has_own_book = 0 AND has_device = 0")
                 if class_grade(s["class_name"]) == grade)
    items = [dict(l=l, qr=qr_data_uri(l["ebook_url"] or EBOOK_HOME),
                  tasks=[t for t in (l["tasks"] or "").split("\n") if t.strip()]) for l in rows]
    return page("""
      <div class="noprint card">
        <b>Cần in {{ copies }} bản</b> – số học sinh khối {{ grade }} chưa có sách và không có thiết bị đọc SGK điện tử.
        <p class="small muted">Phiếu chỉ gồm tên bài và nhiệm vụ do giáo viên tự soạn, không sao chép nội dung SGK.
          Mã QR dẫn tới SGK điện tử miễn phí để em đọc khi mượn được điện thoại/máy tính hoặc tại thư viện.</p>
        <button onclick="window.print()">🖨 In phiếu</button>
        <a class="btn ghost" href="{{ url_for('lessons', grade=grade, week=week) }}">Quay lại</a>
      </div>
      <div class="sheet">
        <h1>PHIẾU HỌC TẬP TUẦN {{ week }} · KHỐI {{ grade }}</h1>
        <div class="who"><div>Họ tên: <span>&nbsp;</span></div><div style="flex:0 0 160px">Lớp: <span>&nbsp;</span></div></div>
        {% for it in items %}
        <div class="lesson">
          <div>
            <h3>{{ it.l.subject }}: {{ it.l.title }}</h3>
            {% if it.l.note %}<p style="margin:0 0 4px"><i>{{ it.l.note }}</i></p>{% endif %}
            {% if it.tasks %}
              <ol>{% for t in it.tasks %}<li>{{ t }}{% for _ in range(2) %}<div class="ln"></div>{% endfor %}</li>{% endfor %}</ol>
            {% else %}
              <p style="margin:0">Em ghi lại những điều đã học và câu hỏi còn thắc mắc:</p>
              {% for _ in range(4) %}<div class="ln"></div>{% endfor %}
            {% endif %}
          </div>
          <img src="{{ it.qr }}" alt="QR SGK điện tử">
        </div>
        {% endfor %}
        <div class="foot">Phiếu do giáo viên biên soạn, không sao chép nội dung sách giáo khoa.
          SGK điện tử miễn phí: {{ ebook }} · Sách Chung</div>
      </div>""", items=items, grade=grade, week=week, copies=copies, ebook=EBOOK_HOME)


@app.errorhandler(404)
def not_found(_e):
    return page("<h1>Không tìm thấy</h1><p>Mã QR hoặc đường dẫn không hợp lệ.</p>"), 404


# Khởi tạo DB ngay khi import để chạy được cả trên PythonAnywhere/Render (WSGI không gọi __main__)
init_db()

if __name__ == "__main__":
    ip = lan_ip()
    print("=" * 60)
    print("  Sách Chung đang chạy")
    print("  Trên máy này : http://localhost:5000")
    print(f"  Điện thoại   : http://{ip}:5000   (cùng Wi-Fi)")
    print(f"  PIN giáo viên: {TEACHER_PIN}")
    print("=" * 60)
    if os.environ.get("SACH_CHUNG_OPEN"):   # run.bat bật biến này để tự mở trình duyệt
        threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
