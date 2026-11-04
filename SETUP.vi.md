*[English](SETUP.md) | Tiếng Việt*

# Cài đặt & chạy — nhánh ezsolver

Hướng dẫn từng bước. Chạy việc này trên máy của bạn hoặc một VM do bạn
kiểm soát — **không** chạy trong một session cloud sandbox vốn không có
Chrome thật và không có quyền truy cập mạng tới site mục tiêu.

## 0. Điều kiện tiên quyết

- Python 3.8 hoặc mới hơn
- Đã cài Google Chrome (trình duyệt thật, không chỉ là Chromium) — tải
  tại https://www.google.com/chrome/ nếu bạn chưa có
- Linux **không** có desktop đồ họa: cài thêm Xvfb (màn hình ảo), ví dụ
  `sudo apt install xvfb` trên Debian/Ubuntu
- Máy này có quyền truy cập mạng tới `tracuu.einvoice365.com` và tới
  `challenges.cloudflare.com`

## 1. Lấy mã nguồn

```bash
# Clone repo nếu máy bạn chưa có sẵn
git clone https://github.com/Le-atuan/turnstile-bypass-test
cd turnstile-bypass-test

# Chuyển sang đúng nhánh này — mỗi solver nằm trên một nhánh riêng
git checkout ezsolver
```

## 2. Tạo một môi trường Python độc lập

```bash
# Một venv giúp giữ dependency của project này tách biệt khỏi
# Python của hệ thống
python3 -m venv venv

# Kích hoạt nó — dấu nhắc (prompt) của shell bạn sẽ hiện (venv)
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd/PowerShell)
```

## 3. Cài đặt dependency

```bash
# Cài nodriver (browser automation), requests (HTTP client),
# và python-dotenv (đọc file .env ở bước dưới)
pip install -r requirements.txt
```

## 4. Cấu hình mục tiêu

```bash
# Copy từ file mẫu — .env đã được đưa vào .gitignore nên các thay đổi
# cục bộ của bạn (và bất kỳ thông tin endpoint thật nào bạn điền vào)
# sẽ không bao giờ bị commit
cp .env.example .env
```

Mở `.env` bằng một editor và kiểm tra/điền:

- `TARGET_SITEURL` — đã được set sẵn thành `https://tracuu.einvoice365.com/`.
- `TARGET_SITEKEY` — đã được set sẵn thành sitekey thật tìm được trong
  quá trình recon (`0x4AAAAAACA6lZiDGKBtjbxg`). Cứ giữ nguyên trừ khi
  widget thay đổi.
- `VERIFY_ENDPOINT` — đây là một endpoint **nghi vấn** (`/ptop/psearch`)
  được đoán ra từ việc đọc `portal.js`. **Hãy xác nhận lại nó có đúng
  không** trước khi tin vào kết quả "backend chấp nhận":
  1. Mở trang thật trên Chrome, bấm F12 (DevTools), vào tab
     **Network**, và tick chọn "Preserve log".
  2. Điền một MST + mã tra cứu có vẻ hợp lệ và bấm "Tra cứu".
  3. Tìm request thật vừa được gửi đi, rồi copy đúng URL, method, và
     tên các field của nó vào `.env` (`VERIFY_ENDPOINT`,
     `VERIFY_METHOD`, `VERIFY_EXTRA_FIELDS`).
  4. Dùng một **mã hóa đơn test đã biết trước**, không bao giờ dùng dữ
     liệu khách hàng thật, cho bước này.
- Để trống `VERIFY_ENDPOINT` nếu bạn chỉ muốn kiểm tra xem có lấy được
  token hay không, mà không chạm vào endpoint tra cứu.

## 5. Chạy test

```bash
python run_test.py
```

Điều gì sẽ xảy ra:
1. Script in ra URL mục tiêu và **yêu cầu bạn gõ `y` để xác nhận bạn
   được phép test nó** — chưa có gì chạm tới mạng trước khi bạn xác
   nhận.
2. Nó khởi chạy Chrome (bạn sẽ thấy một cửa sổ mở ra, trừ khi bạn đã
   sửa code để chạy ở chế độ headless) và điều khiển nó đi tới đúng
   trang mục tiêu thật.
3. Nó chờ EzSolver lấy được một token Turnstile ra từ trang.
4. Nếu `VERIFY_ENDPOINT` đã được set, nó gửi token (cùng với bất kỳ
   field nào khác) tới endpoint đó và in ra mã trạng thái HTTP cùng một
   đoạn trích của response.
5. Nó in ra một bản tóm tắt bằng ngôn ngữ dễ hiểu về ý nghĩa của kết
   quả — xem bảng trong `README.md` / README của nhánh `main`.

## 6. Dọn dẹp

```bash
# Thoát khỏi venv khi đã xong việc
deactivate
```

## Xử lý sự cố

- **"Chrome not found"** — set `CHROME_PATH` thành đường dẫn đầy đủ tới
  file thực thi Chrome của bạn trước khi chạy, ví dụ:
  ```bash
  export CHROME_PATH="/usr/bin/google-chrome-stable"   # Linux
  python run_test.py
  ```
- **Timeout / không có token sau một thời gian chờ dài** — hệ thống
  chấm điểm rủi ro (risk scoring) của Turnstile trên trang mục tiêu có
  thể đang gắn cờ chính trình duyệt automation (thường gặp ở các cấu
  hình headless, hoặc nếu `headless=True` được set mà không có một user
  agent thực tế). Xem mục "Kết quả thực sự cho chúng ta biết điều gì"
  trong `README.md` trước khi vội cho rằng đây là lớp phòng thủ của
  backend bạn đang hoạt động.
- **Linux: "Xvfb not found"** — `sudo apt install xvfb` (Debian/Ubuntu)
  hoặc lệnh tương đương với distro của bạn.
