*[English](README.md) | Tiếng Việt*

# Nhánh ezsolver

Tích hợp [`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver)
(MIT) để test trang tra cứu `tracuu.einvoice365.com` của chúng ta. Xem
[nhánh `main`](../../tree/main) để có tổng quan. Hướng dẫn cài/chạy:
[`SETUP.md`](SETUP.md) / [`SETUP.vi.md`](SETUP.vi.md).

## Backend giải Turnstile

Chỉ `undetected-chromedriver` (`SOLVER_BACKEND=uc`,
`EzSolver/solver_uc.py`) vượt được Turnstile của site này — đây là
default cho 2 script bên dưới. `nodriver` và `playwright-stealth` đã
thử và bị chặn.

## Các bài test

| Script | Làm gì | Trả lời câu hỏi |
|---|---|---|
| `run_test.py` | Lấy token qua EzSolver (`uc`), rồi gửi thẳng HTTP POST tới `POST /api/search` (endpoint/payload reverse-engineer bằng `capture_request.py`). Chạy ngầm, bước gửi HTTP không cần trình duyệt hiện. Hỗ trợ `MAX_WORKERS>1` để chạy song song nhiều bộ hồ sơ, mỗi bộ 1 browser riêng. | "Backend có chấp nhận token mà không cần phiên trình duyệt thật, và có chặn request đồng thời không?" |
| `capture_request.py` | Chạy 1 lần: lái UI thật bằng `uc`, hook `fetch`/`XMLHttpRequest`, dump đúng request trang gửi vào `captured_request.json`. Dùng để tìm ra endpoint/payload cho `run_test.py`. | — |

`run_test.py` lặp qua các entry trong `test_cases.json` (hoặc `TEST_CASES`
trong `.env` nếu file đó không tồn tại).

## Các lệnh chạy

```bash
cp .env.example .env            # cài 1 lần: set TARGET_SITEURL/SITEKEY
pip install -r requirements.txt
```

### Bài test chính — bypass qua raw-HTTP chạy ngầm + tải XML (ưu tiên, không cần UI)

```bash
python run_test.py
```

Giải Turnstile, gửi thẳng token tới `POST /api/search`, và lưu file XML
hóa đơn thật vào `logs/xmls/` cho mỗi bộ test — đây là lệnh nên chạy mặc
định, không hỏi xác nhận. Bộ test lấy từ `test_cases.json` nếu file đó
tồn tại, không thì lấy từ `TEST_CASES` trong `.env` — dù nguồn nào cũng
phải là 1 mảng JSON các object dạng `{"info": {"tax_code": "...",
"lookup_code": "..."}}`; cặp nào đã có trong `logs/downloaded.json` sẽ bị
skip. Dùng file khác (ví dụ batch 1000 bộ) bằng:

```bash
python run_test.py --cases /đường/dẫn/tới/file_của_bạn.json
```

(thay `/đường/dẫn/tới/file_của_bạn.json` bằng đường dẫn thật tới file của bạn)

### Tương tự nhưng chạy N bộ cùng lúc (để dò xem có bị rate-limit không)

```bash
MAX_WORKERS=4 python run_test.py
```

Chạy đồng thời tối đa 4 bộ hồ sơ, mỗi bộ 1 browser riêng, để xem backend
có chặn/đánh dấu các request đồng thời giống nhau không.

### Chạy 1 lần: capture đúng request mà trang thật gửi

```bash
python capture_request.py
```

Đã chạy 1 lần để reverse-engineer endpoint/payload cho `run_test.py`;
chỉ chạy lại nếu frontend của site thay đổi.

## `run_test.py` hoạt động thế nào

```mermaid
flowchart TD
    A[Với mỗi entry {info: {tax_code, lookup_code}}<br/>từ file --cases hoặc TEST_CASES trong .env] --> B{Cặp đã có trong<br/>logs/downloaded.json?}
    B -->|có| S[skipped — kết quả cuối, không mở browser]
    B -->|chưa| C[Mở Chrome qua backend uc,<br/>giải Turnstile → lấy token]
    C -->|lỗi| E1[error]
    C -->|có token| D[POST token + tax_code + lookup_code<br/>tới VERIFY_ENDPOINT]
    D -->|lỗi| E1
    D --> F{Response text khớp<br/>NOT_FOUND_KEYWORDS?}
    F -->|có| N[not_found — kết quả cuối]
    F -->|không| G{HTTP 2xx?}
    G -->|không| R[rejected — kết quả cuối]
    G -->|có| H{Response JSON có field<br/>'xml' khác rỗng?}
    H -->|không| R
    H -->|có| I[Lưu XML vào logs/xmls/,<br/>ghi cặp vào downloaded.json]
    I --> ACC[accepted — kết quả cuối]
    E1 --> J{Còn lượt thử<br/>trong MAX_ATTEMPTS?}
    J -->|còn| C
    J -->|hết| EF[error — kết quả cuối]
```

Các bộ test khác nhau chạy song song khi `MAX_WORKERS>1` (mỗi bộ 1 browser
riêng, qua `ThreadPoolExecutor`); chỉ `error` mới retry — `skipped`,
`not_found`, `rejected`, `accepted` luôn là kết quả cuối.

## Kết quả & thử lại

Chỉ **lỗi kỹ thuật** mới retry (tối đa `MAX_ATTEMPTS`) — câu trả lời
thật từ site luôn là kết quả cuối.

| Kết quả | Nghĩa là | Retry? |
|---|---|---|
| `accepted` | HTTP 2xx và đã lưu được file XML | Không |
| `rejected` | Backend trả non-2xx | Không |
| `not_found` | Response khớp `NOT_FOUND_KEYWORDS` (hợp lệ: "không có bản ghi") | Không |
| `error` | Lấy token hoặc gửi HTTP request thất bại | **Có** |
| `skipped` | XML đã được tải ở lần chạy trước | Không |

## Chạy song song nhiều bộ

`run_test.py` với `MAX_WORKERS>1` trong `.env` chạy đồng thời nhiều bộ
hồ sơ, mỗi bộ 1 browser riêng — dùng để kiểm tra backend có chặn/đánh
dấu các request đồng thời giống nhau từ 1 máy không (tại thời điểm test
này, chưa chặn).

`NOT_FOUND_KEYWORDS` là danh sách **đoán, chưa xác nhận** — chỉnh lại khi
gặp phản hồi "không tìm thấy" thật.

## Logging

Mỗi lần chạy ghi `logs/<script>_<timestamp>.log` (chi tiết đầy đủ +
traceback) bên cạnh output console ngắn gọn. Khi một bước lỗi, còn lưu
thêm screenshot + HTML trang vào `logs/`. File XML tải được qua
`run_test.py` nằm ở `logs/xmls/`. **`logs/` đã gitignore** — nó có thể
chứa dữ liệu hóa đơn thật.

## Yêu cầu

Chrome thật (không chỉ Chromium — `undetected-chromedriver` điều khiển);
Xvfb trên Linux không GUI; `CHROME_PATH` nếu Chrome không tự dò được.
