*[English](README.md) | Tiếng Việt*

# turnstile-bypass-test

Tổng quan dự án kiểm thử phòng thủ: các công cụ giải Turnstile mã nguồn
mở có lấy được token hợp lệ cho trang tra cứu hóa đơn của chúng ta
không, và quan trọng hơn — **backend của chúng ta còn thực thi (enforce)
được điều gì sau khi token đó đã được chấp nhận hay không.**

**Chỉ chạy nhằm vào `tracuu.einvoice365.com` (hoặc hạ tầng khác do chúng
ta sở hữu) — không bao giờ nhằm vào site của bên thứ ba.**

## Bối cảnh mục tiêu

- **Trang:** FPT eInvoice — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **chế độ managed/invisible** — không
  hiện checkbox/puzzle, tự giải trong nền (~1-2 giây). Nút "Tra cứu" vẫn
  bị khóa (disabled) cho tới khi token sẵn sàng.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** input ẩn `name="cf-turnstile-response"`
- **Các endpoint tra cứu nghi vấn** (đọc được từ `portal.js`, cấu trúc
  request thật chưa được xác nhận): `/ptop/psearch`, `/ptopnew/psearch`,
  `/ptopnew/pcussearch`, `/api/inv/minutes/param`
- Không quan sát thấy lớp chống bot khác (không reCAPTCHA/hCaptcha/
  captcha ảnh, không có trang chặn toàn màn hình của Cloudflare).
  Turnstile là lớp duy nhất nhìn thấy được.

**Ý nghĩa:** vì widget chạy ẩn (invisible) và tự giải mà không cần
tương tác, bất kỳ solver nào load được trang và chờ callback đều lấy
được token — không cần giả lập click. Lớp phòng thủ thật sự phải nằm ở
backend (chống replay, rate limiting, verify lại với Cloudflare), không
nằm ở cách widget tương tác. Đây chính xác là điều mỗi nhánh dưới đây
đo lường.

## Các repo được tích hợp & so sánh

Việc này bắt đầu từ một khảo sát rộng hơn về các tool "Cloudflare
Turnstile bypass" trên GitHub. Hai repo được chọn để tích hợp thực tế,
vì chúng đại diện cho hai cách tiếp cận khác nhau:

| | nhánh [`ezsolver`](../../tree/ezsolver) | nhánh [`Turnstile-Solver`](../../tree/Turnstile-Solver) |
|---|---|---|
| **Repo gốc** | [ismoiloffS/EzSolver](https://github.com/ismoiloffS/EzSolver) | [Theyka/Turnstile-Solver](https://github.com/Theyka/Turnstile-Solver) |
| **Ngôn ngữ / engine** | Python + `nodriver` (CDP thuần, không qua Playwright) | Python + `patchright` (Playwright đã patch để giảm fingerprinting) |
| **Cách lấy token** | Load **trang thật** của chúng ta, chờ callback JS riêng của widget, click vào iframe nếu cần | Điều hướng tới **URL thật** (để kiểm tra domain-binding của sitekey vẫn vượt qua được) nhưng **chặn response và tự trả về HTML tối giản của riêng nó** — không bao giờ load trang thật của chúng ta |
| **Độ trung thực so với trang thật** | Cao — mọi script, DOM element, và logic phụ trên trang thật đều thực sự chạy | Thấp hơn — chỉ chứng minh được riêng sitekey có thể giải được một cách độc lập; không nói lên gì về JS thật của trang chúng ta |
| **License** | MIT | Không có file LICENSE rõ ràng trong repo — cần cẩn trọng nếu tái sử dụng ngoài phạm vi bài test này |
| **Yêu cầu hệ thống** | Chrome thật, Xvfb trên Linux không có GUI | Trình duyệt do `patchright` quản lý (chromium/chrome/msedge/camoufox) — không cần cài Chrome riêng |
| **Tốc độ / tài nguyên** | Có sẵn `service.py` giữ một Chrome luôn "nóng" (ít cold start hơn) — README của tác giả công bố ~2-2.2 giây/lần giải khi đã warm up | Mỗi lần gọi mở một Chrome mới qua `sync_solver` — chậm hơn, nhưng đơn giản hơn, không cần quản lý tiến trình nền |
| **Rủi ro khi tự host** | `service.py` bind vào `0.0.0.0` và **không có xác thực** — README tự cảnh báo chỉ nên chạy trong mạng tin cậy | Không có service mở cổng nào được dùng ở đây (ta gọi `sync_solver` trực tiếp như một hàm Python); API server riêng của repo (`main.py`/`api_solver.py`) cũng không có xác thực nếu dùng thay thế — không được thực thi trong bài test này |
| **Thực sự đo được gì** | Phòng thủ **end-to-end (toàn trình)** của trang thật — gần sát nhất với những gì một người dùng hoặc bot thật sẽ gặp phải | Phòng thủ của **riêng sitekey/domain-binding**, tách biệt khỏi logic frontend của chúng ta |
| **Các điểm đáng chú ý khi review mã** | Không có gì ngoài lời cảnh báo "dùng tùy rủi ro của bạn" thường thấy | Tương tự, không có gì đáng báo động, nhưng việc thiếu license là lý do cần cẩn trọng hơn nếu tái sử dụng ở nơi khác |

### Cách đọc hai kết quả cùng với nhau

- **Chạy `ezsolver` trước** — đây là kết quả gần sát nhất với một người
  dùng/bot thật nhằm vào đúng trang thật.
- **Chạy `Turnstile-Solver` sau** để xem riêng sitekey có dễ giải một
  cách độc lập không — nếu cả hai cuối cùng đều "được chấp nhận" bởi
  backend, đó là tín hiệu mạnh hơn cho thấy backend thực sự không có
  lớp phòng thủ thứ hai.
- Nếu một bên thành công và bên còn lại thất bại, đó cũng là một tín
  hiệu có giá trị riêng: nó cho biết lớp phòng thủ nằm ở JS thật của
  frontend hay nằm ở chính widget/domain-binding.

Các repo khác đã khảo sát nhưng chưa tích hợp: `odell0111/turnstile_solver`,
`Iruko233/Turnstile-Slip`, `Lucrehulk/cloudflare-turnstile-solver-2026`.
Có thể thêm nhánh cho bất kỳ repo nào trong số này sau, nếu cần.

## Cấu trúc của mỗi nhánh solver

```
<branch>/
├── <Solver>/          # repo gốc, vendor nguyên trạng
├── run_test.py        # harness: xác nhận quyền → lấy token → gửi lên backend → báo cáo accepted/rejected
├── .env.example        # sitekey/URL/endpoint đã điền sẵn các giá trị mà recon đã xác nhận
├── README.md / README.vi.md   # chi tiết kỹ thuật của solver đó (EN/VI)
└── SETUP.md / SETUP.vi.md     # hướng dẫn cài đặt & chạy từng bước, các lệnh có chú thích (EN/VI)
```

Chuyển sang nhánh bạn muốn và đọc `SETUP.md` ở đó để thực sự chạy nó —
`main` chỉ là tổng quan, không có gì ở đây chạy trực tiếp được.

## Đọc một kết quả (áp dụng cho cả hai nhánh)

| Lấy được token | Backend chấp nhận | Ý nghĩa |
|---|---|---|
| Thất bại | — | Solver này không lấy được token với cấu hình widget hiện tại. Không phải là bằng chứng chống lại mọi solver khác. |
| Thành công | Bị từ chối | Turnstile một mình không chặn được solver, nhưng backend có một lớp phòng thủ thứ hai thật. Đây là kết quả lành mạnh (healthy). |
| Thành công | Được chấp nhận | Backend coi "có token hợp lệ" là đủ. Cần bổ sung: verify lại ở phía server bằng `siteverify` kèm cơ chế chống dùng-lại (one-time-use enforcement), rate limiting theo từng IP/session, và phát hiện bất thường (anomaly detection) trên pattern của request. |
