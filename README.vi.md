*[English](README.md) | Tiếng Việt*

# Nhánh ezsolver — chi tiết kỹ thuật

Tích hợp [`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver)
(MIT) nhằm vào trang tra cứu `tracuu.einvoice365.com` của chúng ta. Xem
README ở [nhánh `main`](../../tree/main) để có tổng quan dự án và bảng so
sánh với nhánh `Turnstile-Solver`. File này đi sâu hơn vào cách EzSolver
hoạt động bên trong, và điều đó có ý nghĩa gì khi đọc kết quả test cho
đúng.

Lệnh cài đặt/chạy xem tại [`SETUP.md`](SETUP.md) /
[`SETUP.vi.md`](SETUP.vi.md).

## Hai bài test riêng biệt trong nhánh này

- **`run_test.py`** — lấy token qua `solve_on_page()` của chính EzSolver,
  sau đó (tùy chọn) gửi thẳng một HTTP POST chứa token đó tới endpoint
  backend đoán được. Xem phần đọc code và lưu ý quan trọng về việc xóa
  DOM ở dưới.
- **`run_ui_test.py`** — một bài test khác, sát thực tế hơn: load đúng
  trang thật **không sửa đổi gì**, chờ widget Turnstile của chính trang
  tự giải xong trong nền, rồi gõ tay tax code + lookup code thật vào
  đúng 2 ô form thật "MST bên bán" / "Mã tra cứu hóa đơn" với độ trễ
  giống người thật, và bấm đúng nút "Tra cứu" thật — để trả lời câu hỏi
  "web có detect được bot đang thao tác trên giao diện thật hay không",
  chứ không chỉ "có lấy được token hay không". Script này chủ động
  không dùng `solve_on_page()` — xem lý do ở mục dưới.

## Cách EzSolver lấy được token — đọc qua mã nguồn

EzSolver cung cấp hai điểm vào (entry point) cùng dùng chung phần logic
lõi trong `EzSolver/solver.py`:

- `solver.py` — CLI độc lập, mở một Chrome mới cho mỗi lần chạy.
- `service.py` — giữ một Chrome instance sống xuyên suốt nhiều request
  thông qua một HTTP server nhỏ tự xây dựng (`POST /solve`), để cắt
  giảm chi phí cold-start.

`run_test.py` của chúng ta gọi trực tiếp `solver.solve()` (không cần
thêm một bước gọi HTTP cho chỉ một lần test). Trình tự bên trong
`solve_on_page()`:

1. **Điều hướng tới URL mục tiêu thật** — `await page.get(siteurl)`.
   Khác với nhánh `Turnstile-Solver`, EzSolver **không** chặn response
   mạng — HTML thật được tải về ban đầu. **Nhưng xem phần đính chính ở
   dưới: bước ngay sau đó lại xóa sạch nó.**
2. **Hook `Element.prototype.attachShadow`** trước khi script của chính
   Turnstile chạy, buộc mọi shadow root mà Cloudflare tạo ra phải ở
   `mode: 'open'` thay vì `'closed'`. Turnstile render iframe của nó
   trong một shadow DOM đóng (closed) với mục đích cụ thể là để script
   của trang khó có thể chui vào; hook này vô hiệu hóa lớp cách ly đó
   để script automation có thể đọc được vị trí của iframe và trạng thái
   nội bộ của widget.
3. **Inject và tự tay render widget** vào một div có vị trí cố định nhỏ
   (`#_ts_box`), dùng `turnstile.render()` với `sitekey` thật của chúng
   ta, thay vì dựa vào auto-render có sẵn của trang. Các tùy chọn
   `callback` và `error-callback` lưu kết quả vào `window._tsToken` /
   `window._tsError` để phía Python có thể polling đọc.
4. **Polling trạng thái mỗi 0.5 giây** qua `page.evaluate()`, kiểm tra
   theo thứ tự: callback lỗi, callback token, và cuối cùng là giá trị
   của input ẩn `cf-turnstile-response` như một phương án dự phòng.
5. **Nếu sau khoảng 5 giây vẫn chưa có token hoặc lỗi nào xuất hiện**,
   xác định hình chữ nhật trên màn hình (on-screen rectangle) của
   iframe Cloudflare (thông qua shadow root đã mở, hoặc dự phòng bằng
   cách tìm một `<iframe src*="challenges.cloudflare.com">` thông
   thường) và **giả lập một cú click giống người thật**: di chuột theo
   hai bước với độ rung ngẫu nhiên (`random.uniform(-3, 3)` trên mỗi
   tọa độ, có các khoảng `sleep` xen giữa lúc di chuột và lúc click),
   sau đó mới click. Bước này chỉ có ý nghĩa với một widget dạng
   checkbox *hiển thị ra ngoài* — còn ở trang mục tiêu của chúng ta,
   widget chạy ở chế độ managed/invisible nên về lý thuyết phải được
   giải xong ngay từ bước 4, không bao giờ cần chạm tới logic click
   này.
6. **Các mã lỗi được map sang các lý do dễ đọc cho con người**
   (`110100` sitekey không hợp lệ, `110200` domain không được ủy quyền,
   `110600` challenge hết thời gian chờ, v.v.) — hữu ích để phân biệt
   giữa trường hợp "Turnstile chủ động từ chối trình duyệt này" và
   "chỉ đơn giản là hết giờ."

## Đính chính: `solve_on_page()` xóa sạch DOM của trang thật

Đọc kỹ khối code hook ở bước 2, nó bắt đầu bằng
`window.stop(); document.body.innerHTML = '';` — **trước khi** cài hook
shadow-DOM và inject div widget của riêng nó. Điều này có nghĩa là
EzSolver **không** giữ nguyên form, script, hay bất kỳ nội dung DOM nào
khác của trang thật một khi quá trình giải bắt đầu — nó xóa sạch
`<body>` ngay sau khi điều hướng, giống hệt cách trang giả ở nhánh
`Turnstile-Solver` làm, chỉ khác là xóa muộn hơn một bước (sau một lần
điều hướng thật, thay vì qua route interception).

**Ảnh hưởng thực tế:** `run_test.py` (dùng `solve_on_page()`) vẫn là
một bài test có ý nghĩa — sitekey thật, domain thật, và bất kỳ script
nào đã chạy xong trước khi bị xóa đều được thực thi đúng — nhưng nó
**không** phải là bài test cho form thật hay luồng submit thật, vì form
đó không còn tồn tại trong DOM vào thời điểm token trả về. Để test đúng
form và nút bấm thật, hãy dùng `run_ui_test.py` (xem ở trên), vốn không
bao giờ xóa gì cả.

## Kết quả thực sự cho chúng ta biết điều gì, với cơ chế này

- **Một token thành công qua `run_test.py` có nghĩa là**: *một trình
  duyệt được điều khiển bằng script, không có con người thật, đã khiến
  Turnstile chấp thuận trên đúng domain thật của chúng ta* — nhưng
  không nói lên gì về việc luồng form/nút bấm thật của chúng ta có qua
  được mà không bị phát hiện hay không. Đó là mục đích của
  `run_ui_test.py`.
- **Kỹ thuật hook shadow-DOM (bước 2) là một điểm đáng lưu ý**, không
  chỉ riêng với tool này: shadow root ở chế độ `closed` không phải là
  một ranh giới bảo mật (security boundary) chống lại chính trình duyệt
  mà công cụ automation đang điều khiển, vì quyền truy cập ở tầng
  CDP/Playwright luôn có thể override `attachShadow` trước khi bất kỳ
  script nào khác chạy. Đừng dựa vào tính đóng gói (encapsulation) của
  shadow DOM như một cơ chế chống bot — nó chỉ nâng rào đối với những
  người sao chép thủ thuật JS của người khác mà không tự điều khiển
  trình duyệt, chứ không nâng rào đối với bất kỳ ai trực tiếp lái trình
  duyệt — mà đó chính xác là tình huống của mọi tool solver.
- **Việc gọi `turnstile.render()` một cách tường minh với sitekey thật
  đồng nghĩa với việc domain-binding được kiểm tra đúng như với một
  người dùng thật** — đây không phải là một bài test trên trang
  giả/trang ngoài (synthetic/foreign-page) như kỹ thuật ở nhánh
  `Turnstile-Solver`.
- Một **thất bại** ở đây, trong khi chế độ managed/invisible không yêu
  cầu click, sẽ gợi ý rằng chính hệ thống chấm điểm rủi ro (risk
  scoring) của Cloudflare đã gắn cờ trình duyệt automation (fingerprint
  của chế độ headless, thiếu các tín hiệu của người dùng thật, v.v.)
  chứ không phải là vấn đề về thời gian của logic giả lập click — cần
  lưu ý điều này trước khi kết luận rằng cấu hình backend/widget của
  chính chúng ta là nguyên nhân gây ra việc bị chặn.

## Yêu cầu riêng của solver này

- **Chrome thật (real Google Chrome)**, không chỉ là một bản build
  Chromium chỉ dùng cho automation headless — `nodriver` điều khiển nó
  trực tiếp qua DevTools Protocol (không qua lớp Playwright), và README
  gốc khuyến nghị dùng đúng binary Chrome chính thức.
- Linux không có màn hình: `Xvfb` (mã nguồn tự khởi động nó trên `:99`
  nếu `DISPLAY` chưa được set từ trước).
- Biến môi trường `CHROME_PATH` nếu Chrome không nằm ở một trong các vị
  trí được tự động dò tìm (xem `EzSolver/solver.py:_find_chrome()`).

## Những điểm chưa hoàn thiện trong mã nguồn gốc (cần review trước khi phụ thuộc vào nó)

- HTTP server trong `EzSolver/service.py` lắng nghe (bind) ở `0.0.0.0`
  và **không có xác thực hay rate limiting** — README gốc tự cảnh báo
  về điều này. `run_test.py` của chúng ta hoàn toàn không dùng
  `service.py` (nó gọi `solver.solve()` ngay trong cùng tiến trình),
  nên điều này không ảnh hưởng tới bài test của ta, nhưng vẫn cần lưu
  tâm nếu sau này repo này được tái sử dụng để chạy như một service
  thường trú.
- `clientsend.py` hardcode `SERVICE_URL = "http://127.0.0.1:8191/solve"`
  — không liên quan tới chúng ta vì ta không đi qua nó, ghi chú lại ở
  đây chỉ để đầy đủ thông tin.

## `run_ui_test.py` — bài test UI trên form thật

Được viết riêng cho project này, không thuộc EzSolver gốc. Nó chỉ tái
sử dụng các hàm khởi chạy trình duyệt của EzSolver (`start_browser`,
`_find_chrome`, `_start_xvfb_if_needed`) và không bao giờ gọi
`solve_on_page()`, nên DOM của trang thật không bao giờ bị xóa:

1. Load trang thật, không sửa đổi gì.
2. Polling input ẩn `cf-turnstile-response` thật (do chính script của
   trang quản lý — chế độ managed/invisible nghĩa là không cần click)
   cho tới khi nó có giá trị.
3. Gõ một tax code và lookup code thật vào đúng các ô
   `input[placeholder="MST bên bán"]` / `input[placeholder="Mã tra cứu hóa đơn"]`,
   gõ từng ký tự một với độ trễ ngẫu nhiên (`random.uniform(0.05, 0.18)`
   giây) để tránh kiểu gõ tức thời/máy móc.
4. Click nút thật được tìm theo text hiển thị, `"Tra cứu"`.
5. Lấy và in ra nội dung text của trang sau khi submit.

Cấu hình qua `.env`: `TEST_CASES`, một list JSON gồm các object
`{"tax_code": "...", "lookup_code": "..."}` — mỗi object là một hóa đơn
thật, đã phát hành, được chính chủ sở hữu site cho phép rõ ràng để dùng
cho bài test này (xem comment trong `.env.example`). Mỗi phần tử chạy
đúng 1 lần, theo thứ tự, mỗi lần nhằm vào một hóa đơn thực sự khác
nhau — khác với việc lặp lại cùng một lượt tra cứu, nên bản thân nó
không test được rate-limit trên các request giống hệt nhau, nhưng nó
test được xem site có xử lý nhất quán qua nhiều lượt tra cứu khác nhau
do bot thực hiện trong cùng một session hay không.

**Chưa được chạy thử end-to-end trong session này** — được viết dựa
theo API đã công bố của nodriver, nhưng môi trường cloud sandbox này
không có Chrome thật và không có đường mạng tới mục tiêu, nên các tên
hàm cụ thể (`page.select`, `page.find`, `element.send_keys`) cần được
xác nhận lại với phiên bản nodriver bạn đã cài, ở lần chạy đầu tiên trên
máy bạn.
