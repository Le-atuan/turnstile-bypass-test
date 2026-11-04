*[English](README.md) | Tiếng Việt*

# turnstile-bypass-test

Kiểm thử phòng thủ: các công cụ giải Turnstile mã nguồn mở có lấy được
token hợp lệ cho trang tra cứu hóa đơn của chúng ta không, và quan trọng
hơn — **backend còn thực thi gì sau khi token đã được chấp nhận không?**

**Chỉ chạy nhằm vào `tracuu.einvoice365.com` (hoặc hạ tầng ta sở hữu) —
không nhằm vào site bên thứ ba.**

## Bối cảnh mục tiêu

- **Trang:** FPT eInvoice — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **chế độ managed/invisible** — không
  checkbox/puzzle, tự giải trong nền (~1-2s). Nút "Tra cứu" bị khóa tới
  khi token sẵn sàng.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** input ẩn `name="cf-turnstile-response"`
- **Endpoint tra cứu nghi vấn** (từ `portal.js`, chưa xác nhận):
  `/ptop/psearch`, `/ptopnew/psearch`, `/ptopnew/pcussearch`,
  `/api/inv/minutes/param`
- **Framework giao diện:** Webix (nút là `<div>` có view-id ổn định; chỉ
  2 ô nhập là `<input>` thật).
- Không thấy lớp chống bot nào khác (không reCAPTCHA/hCaptcha/captcha
  ảnh, không trang chặn toàn màn hình của Cloudflare).

**Ý nghĩa:** widget ẩn tự giải không cần tương tác, nên bất kỳ solver nào
load trang và chờ là có token. Lớp phòng thủ thật phải nằm ở backend
(chống replay, rate limit, verify lại với Cloudflare) — đó là thứ dự án
này đo.

## Solver được tích hợp: `ezsolver`

[Nhánh `ezsolver`](../../tree/ezsolver) tích hợp
[`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver) (MIT,
dựng trên `nodriver` + Chrome thật). Đây là tool duy nhất trong nhóm
khảo sát lái được **trang thật** — load trang, điền form thật, bấm nút
thật, vào màn kết quả. Hai script:

- `run_test.py` — lấy token, rồi gửi thẳng HTTP POST tới endpoint backend
  ("backend có chấp nhận token mà không cần phiên trình duyệt không?").
- `run_ui_test.py` — điền form thật + bấm "Tra cứu" như người dùng ("site
  có phát hiện bot thao tác trên UI không?").

## Các solver khác đã khảo sát (không tích hợp)

| Repo | Vì sao không |
|---|---|
| Theyka/Turnstile-Solver | Trả về trang giả chỉ có widget — không load trang thật, chỉ lấy được token, không lái UI được. |
| odell0111/turnstile_solver | Cùng kỹ thuật trang giả (đã đọc code xác nhận) — chỉ lấy token. Code chỉn chu nhất nhóm nhưng không tương tác UI thật. |
| Iruko233/Turnstile-Slip | Node.js, trả về cookie `cf_clearance` — khác ngôn ngữ, tập trung token/cookie. |
| Lucrehulk/cloudflare-turnstile-solver-2026 | Rust + extension, Windows GUI — không phải trình điều khiển trình duyệt gọn. |

Tóm lại: chỉ tool dựng trên thư viện browser-automation chung (`nodriver`,
Playwright) mà thực sự load trang thật mới test được UI. EzSolver hợp;
số còn lại chỉ lấy token.

## Đọc kết quả

| Token | Backend | Nghĩa là |
|---|---|---|
| Thất bại | — | Solver không lấy được token với cấu hình widget hiện tại. |
| Lấy được | Từ chối | Turnstile một mình không chặn được, nhưng backend có lớp thứ hai thật. Tốt. |
| Lấy được | Chấp nhận | Backend coi "có token hợp lệ" là đủ. Cần thêm: verify lại `siteverify` phía server (chống dùng lại), rate limit theo IP/session, phát hiện bất thường. |

Xem [nhánh `ezsolver`](../../tree/ezsolver) để biết cách cài, sơ đồ luồng
đầy đủ, và cách đọc từng kết quả.
