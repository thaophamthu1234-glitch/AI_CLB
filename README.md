# 📚 Sách Chung

Đây là công cụ giúp nhà trường điều phối việc **mượn sách giáo khoa luân phiên** trong thời gian chờ sách chính thức về đủ.

- **Không sao chép SGK.** Học sinh đọc nội dung qua SGK điện tử miễn phí chính thức của NXB Giáo dục Việt Nam (https://taphuan.nxbgd.vn). Hệ thống chỉ quản lý sách giấy đang có sẵn: sách thư viện, sách phụ huynh tặng, sách giáo viên, sách điều chuyển từ trường khác.
- **Ưu tiên học sinh không có thiết bị.** Đây là những em không thể đọc bản điện tử ở nhà.
- **Chi phí 0 đồng** cho phụ huynh. Nhà trường chỉ cần một máy tính và giấy in nhãn.
- **Là giải pháp chuyển tiếp:** khi 100% học sinh đã có sách chính thức, dashboard báo kết thúc để thu sách về và xuất nhật ký.

## Chạy thử

**Windows:** nhấp đúp `run.bat`. Lần đầu nó tự tạo môi trường Python, cài thư viện và mở trình duyệt.
**macOS/Linux:** `./run.sh`. Hoặc chạy tay:

```bash
pip install -r requirements.txt
python app.py
```

- Trên máy tính: http://localhost:5000
- Trên điện thoại (phải cùng Wi-Fi): dùng địa chỉ `http://<IP>:5000` được in ra trong terminal khi khởi động.
- Mã PIN giáo viên mặc định là `1234`. Có thể đổi bằng biến môi trường `SACH_CHUNG_PIN`.

Ở lần chạy đầu tiên, dữ liệu mẫu trong `data/` được nạp tự động. DB cũ được tự nâng cấp, không mất dữ liệu. Muốn làm lại từ đầu thì xóa file `sach_chung.db`.

### Nhập thời khóa biểu bằng ảnh (tùy chọn, dùng PaddleOCR-VL)

1. Cài PaddlePaddle (bản GPU nếu có card NVIDIA) theo hướng dẫn tại https://www.paddleocr.ai.
2. `pip install -r requirements-ocr.txt`
3. Vào *Thời khóa biểu → Nhập bằng ảnh chụp*. Lần đọc đầu tiên sẽ tải model nên chậm.

Nếu đã chạy sẵn PaddleOCR-VL qua vLLM, đặt `SACH_CHUNG_OCR_BACKEND=vllm-server` và `SACH_CHUNG_OCR_SERVER=http://127.0.0.1:8118/v1`.
Không cài OCR thì app vẫn chạy bình thường, thời khóa biểu nhập bằng CSV.

### Đưa lên mạng (để ban giám khảo bấm link là dùng được)

- **PythonAnywhere (miễn phí):** upload thư mục, tạo Web app *Manual configuration*, trong file WSGI ghi
  `import sys; sys.path.insert(0, "/home/<user>/sach-chung"); from app import app as application`, chạy `pip install -r requirements.txt` trong console.
- **Render:** Build command `pip install -r requirements.txt gunicorn`, Start command `gunicorn app:app`.
  Gói miễn phí không giữ file khi khởi động lại, nên dữ liệu sẽ quay về dữ liệu mẫu (phù hợp để demo).

Khi đưa lên mạng, hãy đặt `SACH_CHUNG_PIN` và `SACH_CHUNG_SECRET` khác mặc định.

## Quy trình sử dụng

1. **Kiểm kê:** nhập danh sách học sinh và sách bằng CSV (trang *Nhập CSV*) hoặc nhập tay. Với mỗi học sinh, ghi rõ em đó có thiết bị không và đã có sách chính thức chưa. Vào *Cài đặt* chọn bộ sách trường đang dạy: sách khác bộ (vd phụ huynh tặng sách bộ cũ) được đánh dấu *tham khảo*.
2. **Thời khóa biểu:** chụp ảnh TKB từng lớp (hoặc nhập CSV). Trang *Lịch chuyển sách* cho biết trước mỗi tiết cần chuyển bao nhiêu cuốn từ lớp nào sang lớp nào, để một bộ sách phục vụ nhiều lớp trong cùng buổi.
3. **In nhãn QR:** mở trang *In nhãn QR*, in ra, cắt và dán vào bìa trong của từng cuốn sách. Nhớ in bằng địa chỉ LAN, không dùng `localhost`.
4. **Mượn/trả:** giáo viên dùng camera điện thoại quét mã QR trên sách. Màn hình hiện danh sách học sinh được gợi ý theo thứ tự ưu tiên; chạm vào tên là xong. Khi học sinh trả sách, quét lại mã và bấm "Đã trả".
5. **Trên lớp:** giáo viên mở SGK điện tử lên TV hoặc máy chiếu. Trang *Bài tuần này* (công khai, không cần đăng nhập) có link tới SGK điện tử và phiếu học tập do giáo viên tự soạn.
6. **Học sinh không có thiết bị:** ở *Bài tuần này* bấm *In phiếu học tập* để in phiếu A4 gồm tên bài, nhiệm vụ do giáo viên soạn, dòng kẻ để trả lời và mã QR tới SGK điện tử. Hệ thống tính sẵn số bản cần in.
7. **Kết thúc:** khi học sinh nhận được sách chính thức, giáo viên bấm "✔ Có" ở cột *Sách chính thức*. Khi đạt 100%, tải nhật ký mượn (CSV) và thu sách về thư viện.

## Thuật toán gợi ý người mượn

Ứng viên là học sinh **cùng lớp giữ sách**, **chưa có sách chính thức** và **không đang giữ cuốn nào cùng môn**. Danh sách được sắp xếp theo thứ tự:

1. không có thiết bị → có thiết bị
2. số lần đã mượn môn này: ít → nhiều
3. lần mượn gần nhất: lâu nhất → gần nhất

Sách khác bộ trường đang dạy không được gợi ý tự động (vẫn có thể cho mượn thủ công để tham khảo).

## Thuật toán xoay vòng sách trên lớp

Với mỗi khối và môn, kho gồm các cuốn đúng bộ, không mất, không bị giữ quá hạn; mỗi cuốn bắt đầu ở lớp giữ của nó. Đi lần lượt từng tiết:

1. Lớp học môn đó ở tiết này cần ⌈số em chưa có sách / 2⌉ cuốn (2 em cùng bàn dùng chung 1 cuốn, hằng số `SHARE_PER_BOOK`).
2. Không đủ sách thì chia theo tỷ lệ nhu cầu (phương pháp phần dư lớn nhất).
3. Sách đang ở sẵn lớp cần thì giữ nguyên; còn thiếu thì lấy từ nơi đang rảnh, ưu tiên nơi lâu nhất mới cần lại môn đó để tránh chuyển qua chuyển lại.
4. Cuối buổi trả sách về lớp giữ để tiếp tục cho mượn về nhà qua đêm.

Trang lịch so sánh tỷ lệ học sinh có sách trên lớp khi xoay vòng với khi mỗi lớp chỉ dùng sách của mình. Với dữ liệu mẫu, tỷ lệ này tăng từ khoảng 61–73% lên 70–91% tùy ngày.

Thời hạn mượn mặc định là qua đêm, trả lúc 7:30 sáng hôm sau. Có thể đổi bằng hằng số `LOAN_DAYS` trong `app.py`.

## Định dạng CSV

| File | Cột |
|---|---|
| `students.csv` | `name, class_name, has_device, has_own_book` (giá trị 0/1) |
| `books.csv` | `subject, grade, class_name, quantity, condition, source` |
| `books.csv` (tùy chọn) | thêm cột `edition` (bộ sách) |
| `lessons.csv` | `grade, subject, week, title, ebook_url, worksheet_url, note, tasks` (`tasks` ngăn cách bằng `\|`) |
| `timetable.csv` | `class_name, weekday, period, subject` (`weekday` là 2..7) |

Có thể soạn trong Excel hoặc Google Sheets rồi lưu dưới dạng **CSV UTF-8**.

## Cấu trúc

```
sach-chung/
├── app.py            # toàn bộ ứng dụng (Flask + SQLite)
├── timetable_ocr.py  # đọc ảnh thời khóa biểu bằng PaddleOCR-VL → bảng → CSV
├── run.bat / run.sh  # chạy một chạm
├── requirements.txt
├── requirements-ocr.txt  # tùy chọn, cho tính năng đọc ảnh
├── data/             # dữ liệu mẫu (hư cấu); data/ocr/ chứa CSV xuất từ ảnh TKB
└── sach_chung.db     # tự tạo khi chạy lần đầu
```

## Quyền riêng tư

Hệ thống chỉ lưu họ tên, lớp và hai cờ có/không (thiết bị, sách chính thức). Không lưu số điện thoại hay địa chỉ. Dữ liệu nằm trên máy của trường và không gửi đi đâu.
