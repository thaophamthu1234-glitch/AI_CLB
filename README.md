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

Ở lần chạy đầu tiên, dữ liệu mẫu trong `data/` được nạp tự động. DB cũ được tự nâng cấp, không mất dữ liệu. Muốn bắt đầu với dữ liệu thật, bấm *Xóa dữ liệu mẫu* trong *Thiết lập ban đầu*.

### Nhập thời khóa biểu bằng ảnh (tùy chọn, dùng PaddleOCR-VL)

1. Cài PaddlePaddle (bản GPU nếu có card NVIDIA) theo hướng dẫn tại https://www.paddleocr.ai.
2. `pip install -r requirements-ocr.txt`
3. Vào *Thời khóa biểu → Nhập bằng ảnh chụp*. Lần đọc đầu tiên sẽ tải model nên chậm.

**Đã cài PaddleOCR-VL ở môi trường Python khác** (vd môi trường của dự án khác) thì app không thấy được, vì `run.bat` chạy bằng `.venv` riêng của app. Có hai cách:

- Chạy app bằng Python đã có PaddleOCR: tạo file `python.txt` cạnh `run.bat`, chỉ ghi đường dẫn tới `python.exe` đó (vd `C:\Users\PC\Documents\Du_an_khac\.venv\Scripts\python.exe`, không có dấu ngoặc kép). Lần chạy sau `run.bat` sẽ tự cài thêm Flask và qrcode vào môi trường đó.
- Hoặc cài PaddleOCR vào `.venv` của app: `uv pip install --python .venv\Scripts\python.exe -r requirements-ocr.txt` (cần cài PaddlePaddle bản GPU vào cùng môi trường).

Trang *Thời khóa biểu* ghi rõ app đang chạy bằng Python nào và vì sao chưa dùng được OCR.

Nếu đã chạy sẵn PaddleOCR-VL qua vLLM, đặt `SACH_CHUNG_OCR_BACKEND=vllm-server` và `SACH_CHUNG_OCR_SERVER=http://127.0.0.1:8118/v1`.
Không cài OCR thì app vẫn chạy bình thường, thời khóa biểu nhập bằng CSV.

### Bản .exe cho Windows (không cần cài Python)

Người dùng chỉ cần tải `SachChung-windows.zip` ở mục **Releases** của repo, giải nén và nhấp đúp `SachChung.exe`.
Dữ liệu lưu trong `sach_chung.db` cạnh file .exe.

Để tự đóng gói: nhấp đúp `build_exe.bat` trên Windows. Kết quả nằm ở `dist\SachChung\` và `SachChung-windows.zip`.
Bản .exe không kèm PaddleOCR; tính năng đọc ảnh TKB dùng **máy chủ OCR** bên dưới.

### Máy chủ OCR: cho máy không có GPU dùng PaddleOCR-VL của máy khác

```
Máy trường (SachChung.exe) ── ảnh TKB + mã bí mật ──► ngrok ──► ocr_server.py (máy có GPU)
                           ◄────────── bảng đọc được ─────────
```

Trên máy có GPU và đã cài PaddleOCR-VL:

1. Đăng ký tài khoản miễn phí tại https://ngrok.com, tải `ngrok.exe` đặt vào thư mục này
   và chạy một lần `ngrok config add-authtoken <authtoken trong trang ngrok>`.
2. Trong trang ngrok, mục **Domains**, nhận một tên miền tĩnh miễn phí (vd `ten-ban.ngrok-free.app`)
   và ghi tên miền đó vào file `ngrok_domain.txt`.
3. Nhấp đúp `ocr_server.bat`. Cửa sổ hiện **mã bí mật (token)**; cửa sổ thứ hai là đường hầm ngrok.
   Lần đầu chạy, `ocr_server_config.json` được tạo để lưu mã bí mật (không đẩy lên GitHub).

Trên máy dùng app: *Quản lý → Cài đặt → Máy chủ OCR*, điền `https://ten-ban.ngrok-free.app` và mã bí mật,
bấm *Lưu và kiểm tra kết nối*.

Máy chủ chỉ nhận yêu cầu có đúng mã bí mật, đọc từng ảnh một, không lưu ảnh lại.
Khi máy chủ tắt, app báo rõ và giáo viên vẫn nhập TKB bằng file CSV.
Dùng trong cùng Wi-Fi mà không cần ngrok: đặt `"host": "0.0.0.0"` trong `ocr_server_config.json`
và điền `http://<IP máy có GPU>:8765` vào app.

### Đưa lên mạng (để ban giám khảo bấm link là dùng được)

- **PythonAnywhere (miễn phí):** upload thư mục, tạo Web app *Manual configuration*, trong file WSGI ghi
  `import sys; sys.path.insert(0, "/home/<user>/sach-chung"); from app import app as application`, chạy `pip install -r requirements.txt` trong console.
- **Render:** Build command `pip install -r requirements.txt gunicorn`, Start command `gunicorn app:app`.
  Gói miễn phí không giữ file khi khởi động lại, nên dữ liệu sẽ quay về dữ liệu mẫu (phù hợp để demo).

Khi đưa lên mạng, hãy đặt `SACH_CHUNG_PIN` và `SACH_CHUNG_SECRET` khác mặc định.

## Quy trình sử dụng

### Lần đầu: Thiết lập ban đầu (khoảng 15 phút)

Giáo viên đăng nhập lần đầu sẽ được đưa vào trình hướng dẫn 5 bước. Bước nào chưa sẵn sàng có thể bỏ qua; trang *Hôm nay* sẽ nhắc các bước còn thiếu.

1. **Chọn bộ sách** trường đang dạy. Sách khác bộ (vd sách bộ cũ phụ huynh tặng) được đánh dấu *tham khảo*.
2. **Nhập học sinh:** file CSV (có file mẫu để tải) hoặc gõ từng em. Mỗi em chỉ cần họ tên, lớp, có thiết bị không, đã có sách chính thức chưa.
3. **Nhập sách** đang có trong trường: thư viện, phụ huynh tặng, giáo viên, trường khác.
4. **Thời khóa biểu:** chụp ảnh TKB từng lớp (hệ thống tự đọc bằng PaddleOCR-VL) hoặc nhập CSV.
5. **In nhãn QR**, cắt và dán vào bìa trong của từng cuốn. Nhớ mở app bằng địa chỉ LAN, không dùng `localhost`.

Hệ thống có sẵn dữ liệu mẫu để chạy thử. Khi nhập dữ liệu thật, bấm *Xóa dữ liệu mẫu* ở đầu trình hướng dẫn.

### Hằng ngày: trang Hôm nay

Sau khi đăng nhập, giáo viên vào thẳng trang *Hôm nay*. Phần ứng với giờ hiện tại được đánh dấu *Bây giờ*:

- **Đầu giờ:** sách cần thu về (hạn 7:30, quá hạn tô đỏ). Bấm *Đã trả* ngay trên danh sách.
- **Trong giờ:** tiết đang học và việc cần làm trong giờ ra chơi (chuyển bao nhiêu cuốn từ lớp nào sang lớp nào). Giờ bắt đầu các tiết chỉnh trong *Cài đặt*.
- **Cuối giờ:** sách còn ở trường kèm học sinh được gợi ý mượn về nhà. Bấm *Cho mượn* là xong; mỗi em chỉ được gợi ý một lần để sách chia đều.

Trên điện thoại có nút **Quét sách** cố định cuối màn hình: dùng camera quét mã QR trên sách (hoặc gõ số sách) để mở trang mượn/trả.

### Thanh điều hướng

- Giáo viên: **Hôm nay · Quét sách · Quản lý** (học sinh, kho sách, thời khóa biểu, lịch chuyển sách, in nhãn, nhập dữ liệu, bài tuần này, cài đặt) **· Báo cáo** (tổng quan, đang mượn, tải nhật ký).
- Học sinh và phụ huynh không cần đăng nhập, chỉ thấy trang **Bài tuần này**: link SGK điện tử miễn phí và phiếu học tập in được cho em không có thiết bị.

Cạnh các khái niệm mới (*sách tham khảo*, *xoay vòng*, *chưa từng được mượn*, *quá hạn*) có dấu **?**, bấm vào để xem giải thích.

### Kết thúc chương trình

Khi 100% học sinh có sách chính thức, trang *Hôm nay* hiện nút *Kết thúc chương trình* gồm 3 bước: danh sách sách cần thu về, tải nhật ký mượn (CSV), đóng chương trình. Sau khi đóng, hệ thống không cho mượn thêm nhưng vẫn giữ dữ liệu và có thể mở lại.

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
├── build_exe.bat     # đóng gói SachChung.exe
├── ocr_server.py/.bat# máy chủ OCR cho máy khác gọi tới (qua ngrok)
├── assets/           # icon, hướng dẫn đi kèm bản .exe
├── requirements.txt
├── requirements-ocr.txt  # tùy chọn, cho tính năng đọc ảnh
├── data/             # dữ liệu mẫu (hư cấu); data/ocr/ chứa CSV xuất từ ảnh TKB
└── sach_chung.db     # tự tạo khi chạy lần đầu
```

## Quyền riêng tư

Hệ thống chỉ lưu họ tên, lớp và hai cờ có/không (thiết bị, sách chính thức). Không lưu số điện thoại hay địa chỉ. Dữ liệu nằm trên máy của trường và không gửi đi đâu.
