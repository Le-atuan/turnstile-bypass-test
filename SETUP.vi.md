*[English](SETUP.md) | Tiếng Việt*

# Cài đặt & chạy — nhánh ezsolver

Hướng dẫn từng bước. Chạy ở máy bạn hoặc một VM bạn kiểm soát — **không**
chạy trong session cloud sandbox vì nơi đó không có Chrome thật và không
có mạng tới site mục tiêu.

## 0. Yêu cầu trước khi bắt đầu

- Python 3.8 hoặc mới hơn
- Đã cài Google Chrome (trình duyệt thật, không chỉ Chromium) — tải tại
  https://www.google.com/chrome/ nếu chưa có
- Linux **không** có desktop đồ họa: cài thêm Xvfb (màn hình giả lập),
  ví dụ `sudo apt install xvfb` trên Debian/Ubuntu
- Máy này có mạng truy cập được `tracuu.einvoice365.com` và
  `challenges.cloudflare.com`

## 1. Lấy code

```bash
# Clone repo nếu máy bạn chưa có
git clone https://github.com/Le-atuan/turnstile-bypass-test
cd turnstile-bypass-test

# Chuyển sang đúng nhánh này — mỗi solver nằm ở 1 nhánh riêng
git checkout ezsolver
```

## 2. Tạo môi trường Python riêng

```bash
# venv giúp tách dependency của project này khỏi Python hệ thống
python3 -m venv venv

# Kích hoạt — dấu nhắc shell của bạn sẽ hiện (venv)
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd/PowerShell)
```

## 3. Cài dependency

```bash
# Cài nodriver (điều khiển trình duyệt), requests (gọi HTTP),
# và python-dotenv (đọc file .env ở bước dưới)
pip install -r requirements.txt
```

## 4. Cấu hình mục tiêu

```bash
# Copy từ file mẫu — .env đã được .gitignore loại ra nên các thay đổi
# riêng của bạn (và endpoint thật nếu điền vào) không bao giờ bị commit
cp .env.example .env
```

Mở `.env` bằng editor và kiểm tra/điền:

- `TARGET_SITEURL` — đã sẵn `https://tracuu.einvoice365.com/`.
- `TARGET_SITEKEY` — đã sẵn sitekey thật khảo sát được
  (`0x4AAAAAACA6lZiDGKBtjbxg`). Giữ nguyên trừ khi widget đổi.
- `VERIFY_ENDPOINT` — đây là endpoint **suy luận**(`/ptop/psearch`) từ
  việc đọc `portal.js`. **Hãy xác nhận lại cho đúng** trước khi tin vào
  kết quả "backend chấp nhận":
  1. Mở trang thật trên Chrome, bấm F12 (DevTools), vào tab **Network**,
     tick "Preserve log".
  2. Điền một MST + mã tra cứu có vẻ hợp lệ, bấm "Tra cứu".
  3. Tìm request thật vừa được gửi, copy đúng URL, method, tên các field
     vào `.env` (`VERIFY_ENDPOINT`, `VERIFY_METHOD`, `VERIFY_EXTRA_FIELDS`).
  4. Dùng **mã hóa đơn test đã biết trước**, không dùng dữ liệu khách
     hàng thật, cho bước này.
- Để trống `VERIFY_ENDPOINT` nếu bạn chỉ muốn test xem có lấy được token
  hay không, không chạm vào endpoint tra cứu.

## 5. Chạy test

```bash
python run_test.py
```

Điều sẽ xảy ra:
1. Script in ra URL mục tiêu và **yêu cầu bạn gõ `y` để xác nhận bạn có
   quyền test nó** — chưa chạm mạng trước khi bạn xác nhận.
2. Nó mở Chrome (bạn sẽ thấy 1 cửa sổ hiện ra, trừ khi bạn tự sửa code
   để chạy headless) và điều hướng tới đúng trang mục tiêu.
3. Nó chờ EzSolver lấy được token Turnstile từ trang.
4. Nếu `VERIFY_ENDPOINT` đã điền, nó gửi token (cùng các field khác) lên
   endpoint đó và in ra mã HTTP + một đoạn trích response.
5. Nó in ra một bản tóm tắt dễ hiểu về ý nghĩa kết quả — xem bảng trong
   `README.vi.md` hoặc README ở nhánh `main`.

## 6. Dọn dẹp

```bash
# Thoát venv khi xong việc
deactivate
```

## Xử lý lỗi thường gặp

- **"Chrome not found"** — set biến `CHROME_PATH` tới đường dẫn đầy đủ
  của Chrome trước khi chạy, ví dụ:
  ```bash
  export CHROME_PATH="/usr/bin/google-chrome-stable"   # Linux
  python run_test.py
  ```
- **Timeout / chờ lâu không có token** — hệ thống chấm điểm rủi ro của
  Turnstile trên trang mục tiêu có thể đang gắn cờ chính trình duyệt
  automation (thường gặp khi chạy headless, hoặc đặt `headless=True` mà
  không có user agent thực tế). Xem mục "Kết quả thực sự nói lên điều
  gì" trong `README.vi.md` trước khi vội kết luận đây là do phòng thủ ở
  backend của bạn.
- **Linux: "Xvfb not found"** — `sudo apt install xvfb` (Debian/Ubuntu)
  hoặc lệnh tương đương với distro bạn dùng.
