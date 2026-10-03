"""Kiểm tra tải: python tools/bench.py . 15   (15 lớp/khối = 2400 HS). CẢNH BÁO: GHI ĐÈ sach_chung.db – chạy trên bản sao thư mục."""
import os, sys, time, statistics, importlib, re
B = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, B)
app_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(B, "..")); cpg = int(sys.argv[2]) if len(sys.argv) > 2 else 6
import gen
t0 = time.time(); counts = gen.generate(app_dir, cpg); tgen = time.time() - t0
import app as A
c = A.app.test_client()
with c.session_transaction() as s:
    s["teacher"] = True
db = A.sqlite3.connect(A.DB_PATH)
bid = db.execute("SELECT id FROM books WHERE id NOT IN (SELECT book_id FROM loans WHERE returned_at IS NULL) LIMIT 1").fetchone()[0]
bid_out = db.execute("SELECT book_id FROM loans WHERE returned_at IS NULL LIMIT 1").fetchone()[0]
cls = db.execute("SELECT class_name FROM students LIMIT 1").fetchone()[0]
db.close()
routes = ["/today", f"/today?cls={cls}", "/today?cls=", "/labels?cls=*", "/overview", "/schedule", "/students", f"/students?cls={cls}", "/books", "/loans",
          f"/b/{bid}", f"/b/{bid_out}", "/scan", "/lessons", "/lessons/print?grade=6&week=1", "/timetable", "/export/loans.csv",
          "/labels", "/setup/2"]
print(f"\n=== {len(set(r[0] for r in []))}{counts}  (sinh dữ liệu {tgen:.1f}s)")
print(f"{'Trang':28s} {'lần1(ms)':>9s} {'tb3(ms)':>9s} {'KB':>8s}  status")
for r in routes:
    ts = []
    for i in range(3):
        t = time.perf_counter(); resp = c.get(r); ts.append((time.perf_counter() - t) * 1000)
        if ts[0] > 8000: break
    print(f"{r:28s} {ts[0]:9.0f} {statistics.mean(ts):9.0f} {len(resp.data)/1024:8.0f}  {resp.status_code}")
# luồng mượn/trả
t = time.perf_counter(); page = c.get(f"/b/{bid}").data.decode()
sid = re.search(r'name="student_id" value="(\d+)"', page)
r1 = c.post(f"/b/{bid}/borrow", data={"student_id": sid.group(1)}) if sid else None
r2 = c.post(f"/b/{bid}/return", data={"condition": "Tốt"})
print(f"{'quét→mượn→trả':28s} {(time.perf_counter()-t)*1000:9.0f}            {r1.status_code if r1 else 'no-sugg'} {r2.status_code}")
