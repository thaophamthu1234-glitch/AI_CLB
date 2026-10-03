"""Sinh dữ liệu giả lập 1 trường THCS lớn, ghi thẳng vào sach_chung.db của app."""
import os, random, sqlite3, sys
from datetime import datetime, timedelta

def generate(app_dir, classes_per_grade, students_per_class=40, days=20, seed=1):
    random.seed(seed)
    sys.path.insert(0, app_dir)
    import app as A
    if os.path.exists(A.DB_PATH):
        os.remove(A.DB_PATH)
    A.init_db(seed=False)
    db = sqlite3.connect(A.DB_PATH)
    ED = "Kết nối tri thức với cuộc sống"
    for k, v in (("edition", ED), ("setup_seen", "1"), ("qr_done", "1"), ("no_seed", "1")):
        A.set_setting(db, k, v)

    ho = "Nguyễn Trần Lê Phạm Hoàng Huỳnh Phan Vũ Võ Đặng Bùi Đỗ Hồ Ngô Dương Lý".split()
    dem = "Văn Thị Minh Ngọc Gia Bảo Đức Thu Quốc Thảo Hải Khánh Tuấn Mỹ".split()
    ten = "An Bình Châu Dũng Giang Hà Huy Khoa Lan Linh Minh Nam Ngân Phong Quân Tâm Thảo Trang Vy Yến".split()
    subjects = {"Toán": 4, "Ngữ văn": 4, "Tiếng Anh": 3, "Khoa học tự nhiên": 4,
                "Lịch sử và Địa lý": 3, "Giáo dục công dân": 1, "Tin học": 1, "Công nghệ": 1}
    classes = [f"{g}A{i}" for g in (6, 7, 8, 9) for i in range(1, classes_per_grade + 1)]

    students = []
    for c in classes:
        for _ in range(students_per_class):
            students.append({"name": f"{random.choice(ho)} {random.choice(dem)} {random.choice(ten)}",
                             "class_name": c, "has_device": int(random.random() < 0.5),
                             "has_own_book": int(random.random() < 0.4)})
    A.import_students(db, students)

    books = []
    for c in classes:
        g = int(c[0])
        for s in subjects:
            books.append({"subject": s, "grade": g, "class_name": c, "quantity": random.randint(3, 10),
                          "source": random.choice(["Thư viện", "Phụ huynh tặng", "Giáo viên", "Trường khác"])})
            if random.random() < 0.2:   # sách bộ cũ phụ huynh tặng -> sách tham khảo
                books.append({"subject": s, "grade": g, "class_name": c, "quantity": 2, "edition": "Cánh diều"})
    A.import_books(db, books)

    tt = []
    pool = [s for s, w in subjects.items() for _ in range(w)] + ["Hoạt động trải nghiệm"] * 4 + ["Giáo dục thể chất"] * 2 + ["Nghệ thuật"] * 3
    for c in classes:
        p = pool[:30] if len(pool) >= 30 else pool + random.choices(pool, k=30 - len(pool))
        random.shuffle(p)
        for i, s in enumerate(p):
            tt.append({"class_name": c, "weekday": 2 + i // 5, "period": 1 + i % 5, "subject": s})
    A.import_timetable(db, tt)

    lessons = [{"grade": g, "subject": s, "week": w, "title": f"{s} {g} – Bài tuần {w}",
                "tasks": "Đọc bài trên SGK điện tử|Trả lời 3 câu hỏi cuối bài"}
               for g in (6, 7, 8, 9) for s in subjects for w in range(1, 11)]
    A.import_lessons(db, lessons)

    # Lịch sử mượn về nhà qua đêm trong `days` ngày học gần nhất
    need = {}
    for sid, c in db.execute("SELECT id, class_name FROM students WHERE has_own_book = 0"):
        need.setdefault(c, []).append(sid)
    book_rows = db.execute("SELECT id, class_name FROM books").fetchall()
    start = datetime.now() - timedelta(days=days)
    loans = []
    for d in range(days):
        day = start + timedelta(days=d)
        last = d == days - 1
        for bid, c in book_rows:
            if random.random() < 0.7 and need.get(c):
                sid = random.choice(need[c])
                out = day.replace(hour=16, minute=30)
                due = (day + timedelta(days=1)).replace(hour=7, minute=30)
                ret = None if last else (due - timedelta(minutes=random.randint(0, 40))).strftime("%Y-%m-%d %H:%M")
                if last and random.random() < 0.05:   # vài cuốn quá hạn từ hôm trước
                    pass
                loans.append((bid, sid, out.strftime("%Y-%m-%d %H:%M"), due.strftime("%Y-%m-%d %H:%M"), ret))
    db.executemany("INSERT INTO loans(book_id, student_id, out_at, due_at, returned_at) VALUES (?,?,?,?,?)", loans)
    db.commit()
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("students", "books", "timetable", "lessons", "loans")}
    db.close()
    return counts
