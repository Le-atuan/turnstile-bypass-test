*[English](README.md) | Tiếng Việt*

# Nhánh ezsolver — chi tiết kỹ thuật

Tích hợp [`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver)
(MIT) nhằm vào trang tra cứu `tracuu.einvoice365.com` của chúng ta. Xem
README ở [nhánh `main`](../../tree/main) để có tổng quan và bảng so sánh
với nhánh `Turnstile-Solver`. File này đi sâu vào cách EzSolver hoạt động
bên trong, và ý nghĩa của điều đó khi đọc kết quả test.

Lệnh cài đặt/chạy nằm ở [`SETUP.md`](SETUP.md) (EN) /
[`SETUP.vi.md`](SETUP.vi.md) (VI).

## Cách EzSolver lấy token — đọc code

EzSolver có 2 điểm vào, dùng chung logic lõi trong `EzSolver/solver.py`:

- `solver.py` — CLI độc lập, mở Chrome mới cho mỗi lần chạy.
- `service.py` — giữ 1 Chrome sống xuyên suốt nhiều request qua một HTTP
  server nhỏ tự viết (`POST /solve`), để giảm chi phí khởi động lại.

`run_test.py` của chúng ta gọi trực tiếp `solver.solve()` (không cần qua
HTTP vì chỉ test 1 lần). Trình tự bên trong `solve_on_page()`:

1. **Điều hướng tới đúng URL thật** — `await page.get(siteurl)`. Khác với
   nhánh `Turnstile-Solver`, EzSolver **không** chặn hay thay response của
   trang. HTML, CSS, JS thật của chúng ta tải và chạy đúng như trình
   duyệt của người dùng thật.
2. **Hook `Element.prototype.attachShadow`** trước khi script của
   Turnstile chạy, ép mọi shadow root mà Cloudflare tạo ra thành
   `mode: 'open'` thay vì `'closed'`. Turnstile render iframe của nó
   trong shadow DOM đóng chính vì để script của trang khó chui vào; hook
   này vô hiệu hóa lớp cách ly đó để script automation đọc được vị trí
   iframe và trạng thái nội bộ widget.
3. **Chèn và tự gọi render widget** vào một div định vị cố định
   (`#_ts_box`), dùng `turnstile.render()` với `sitekey` thật của chúng
   ta, thay vì dựa vào auto-render có sẵn của trang. Tùy chọn `callback`
   và `error-callback` lưu kết quả vào `window._tsToken` /
   `window._tsError` để phía Python polling đọc.
4. **Polling trạng thái mỗi 0.5s** qua `page.evaluate()`, kiểm tra theo
   thứ tự: callback lỗi, callback token, rồi giá trị input ẩn
   `cf-turnstile-response` làm phương án dự phòng.
5. **Nếu sau ~5s chưa có token hay lỗi**, xác định hình chữ nhật trên màn
   hình của iframe Cloudflare (qua shadow root đã mở, hoặc dự phòng bằng
   tìm `<iframe src*="challenges.cloudflare.com">`) và **giả lập một cú
   click giống người**: di chuột 2 bước có jitter ngẫu nhiên
   (`random.uniform(-3, 3)` trên mỗi tọa độ, có `sleep` xen giữa), rồi
   click. Bước này chỉ có ý nghĩa với widget **hiện checkbox** — trên
   trang mục tiêu, widget chạy managed/invisible nên lẽ ra phải giải xong
   từ bước 4, không cần chạy tới logic click này.
6. **Mã lỗi được dịch sang lý do dễ hiểu** (`110100` sitekey sai,
   `110200` domain không được ủy quyền, `110600` hết giờ challenge, …) —
   giúp phân biệt "Turnstile chủ động từ chối trình duyệt này" với "chỉ
   đơn giản là hết giờ."

## Kết quả thực sự nói lên điều gì, với cơ chế trên

- **Vì bước 1 load đúng trang thật, không sửa gì**, một token thành công
  ở đây có nghĩa: *một trình duyệt được điều khiển bằng script, không có
  người thật, chạy đúng JS thật của trang, đã khiến Turnstile chấp
  thuận.* Đây là mức gần nhất một bài test tự động có thể đạt để mô
  phỏng "điều gì sẽ xảy ra nếu một bot nhằm trình duyệt vào đúng trang
  tra cứu của chúng ta ngay bây giờ."
- **Kỹ thuật hook shadow-DOM (bước 2) đáng ghi nhớ**, không riêng gì tool
  này: shadow root `closed` không phải là ranh giới bảo mật chống lại
  chính trình duyệt mà công cụ automation đang điều khiển, vì truy cập ở
  tầng CDP/Playwright luôn có thể override `attachShadow` trước khi bất
  kỳ script nào khác chạy. Đừng dựa vào shadow DOM để chống bot — nó chỉ
  nâng rào với người sao chép thủ thuật JS của ai đó mà không tự điều
  khiển trình duyệt, không nâng rào với bất kỳ ai trực tiếp lái trình
  duyệt — chính là tình huống của mọi tool solver.
- **Việc gọi `turnstile.render()` tường minh với sitekey thật nghĩa là
  domain-binding được kiểm tra đúng như với người dùng thật** — không
  phải một bài test trên trang giả/khác domain như kỹ thuật ở nhánh
  `Turnstile-Solver`.
- Một **thất bại** ở đây, trong khi chế độ managed/invisible không cần
  click, gợi ý rằng chính hệ thống chấm điểm rủi ro của Cloudflare đã
  gắn cờ trình duyệt automation (fingerprint headless, thiếu tín hiệu
  người dùng thật, …) chứ không phải do lỗi timing của logic giả lập
  click — cần lưu ý điều này trước khi kết luận cấu hình widget/backend
  của chúng ta là nguyên nhân chặn.

## Yêu cầu riêng của solver này

- **Chrome thật**, không chỉ là build Chromium chỉ dùng cho automation
  headless — `nodriver` điều khiển trực tiếp qua DevTools Protocol
  (không qua lớp Playwright), và README gốc khuyến nghị dùng đúng binary
  Chrome.
- Linux không có màn hình: `Xvfb` (code tự khởi động trên `:99` nếu
  `DISPLAY` chưa được set).
- Biến môi trường `CHROME_PATH` nếu Chrome không nằm ở các vị trí được
  tự dò (xem `EzSolver/solver.py:_find_chrome()`).

## Điểm gồ ghề trong mã gốc (cần biết trước khi phụ thuộc vào nó)

- HTTP server trong `EzSolver/service.py` lắng nghe `0.0.0.0` và
  **không có xác thực hay rate limit** — README gốc tự cảnh báo điều
  này. `run_test.py` của chúng ta không dùng `service.py` (gọi thẳng
  `solver.solve()` trong cùng tiến trình), nên không ảnh hưởng tới test
  của ta, nhưng cần biết nếu sau này tái sử dụng repo để chạy service
  thường trú.
- `clientsend.py` hardcode `SERVICE_URL = "http://127.0.0.1:8191/solve"`
  — không liên quan vì ta bỏ qua nó, ghi lại cho đầy đủ.
