*[English](README.md) | Tiếng Việt*

# turnstile-bypass-test

Tổng quan dự án kiểm thử phòng thủ: liệu các công cụ giải Turnstile
mã nguồn mở có lấy được token hợp lệ cho trang tra cứu hóa đơn của chúng
ta không, và quan trọng hơn — **backend của chúng ta còn chặn được gì sau
khi token đã được chấp nhận hay không.**

**Chỉ chạy nhằm vào `tracuu.einvoice365.com` (hoặc hạ tầng khác do chúng
ta sở hữu) — không nhằm vào site của bên thứ ba.**

## Mục tiêu & bối cảnh kỹ thuật của trang

- **Trang:** FPT eInvoice — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **chế độ managed/invisible** — không
  hiện checkbox/puzzle, tự giải trong nền (~1-2s). Nút "Tra cứu" bị khóa
  cho tới khi token sẵn sàng.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** input ẩn `name="cf-turnstile-response"`
- **Endpoint tra cứu nghi vấn** (đọc từ `portal.js`, chưa xác nhận cấu
  trúc request thật): `/ptop/psearch`, `/ptopnew/psearch`,
  `/ptopnew/pcussearch`, `/api/inv/minutes/param`
- Không phát hiện lớp chống bot khác (không reCAPTCHA/hCaptcha/captcha
  ảnh, không trang chặn toàn màn hình của Cloudflare). Turnstile là lớp
  duy nhất nhìn thấy được.

**Ý nghĩa:** vì widget chạy ẩn và tự giải không cần tương tác, bất kỳ
solver nào load được trang và chờ callback đều lấy được token — không cần
giả lập click. Vậy lớp phòng thủ thật phải nằm ở backend (chống replay,
rate limit, verify lại với Cloudflare), không nằm ở cách widget tương tác.
Đây chính là điều mỗi nhánh dưới đây đo lường.

## Repo được tích hợp & so sánh

Repo gốc nằm trong một khảo sát rộng hơn các tool "Cloudflare Turnstile
bypass" trên GitHub. Tạm chọn 2 repo để tích hợp thực tế, vì đại diện cho
hai cách tiếp cận khác nhau:

| | [`ezsolver`](../../tree/ezsolver) branch | [`Turnstile-Solver`](../../tree/Turnstile-Solver) branch |
|---|---|---|
| **Repo gốc** | [ismoiloffS/EzSolver](https://github.com/ismoiloffS/EzSolver) | [Theyka/Turnstile-Solver](https://github.com/Theyka/Turnstile-Solver) |
| **Ngôn ngữ / engine** | Python + `nodriver` (CDP thuần, không qua Playwright) | Python + `patchright` (Playwright đã patch để né fingerprint) |
| **Cách lấy token** | Load **đúng trang thật** của mình, chờ widget tự giải qua callback JS, click thêm vào iframe nếu cần | Điều hướng tới **URL thật** (để domain-binding của sitekey hợp lệ) nhưng **chặn response và tự chèn HTML giả** chỉ chứa widget — không load trang thật |
| **Độ sát với thực tế trang** | Cao — JS, DOM, mọi logic phụ trên trang thật đều chạy | Thấp hơn — chỉ kiểm tra sitekey giải được độc lập, không phản ánh JS thật của trang |
| **License** | MIT | Không thấy LICENSE rõ ràng trong repo — cần cân nhắc nếu dùng lại ngoài phạm vi test |
| **Yêu cầu hệ thống** | Chrome thật, Xvfb (Linux không GUI) | Trình duyệt do `patchright` quản lý (chromium/chrome/msedge/camoufox), không cần Chrome cài sẵn riêng |
| **Tốc độ / tài nguyên** | Có bản `service.py` giữ 1 Chrome sống lâu (ít cold-start) — theo README tác giả: ~2-2.2s/lần sau khi warm up | Mỗi lần gọi mở Chrome mới qua `sync_solver` — chậm hơn, nhưng đơn giản, không cần giữ tiến trình nền |
| **Bảo mật khi tự host** | `service.py` lắng nghe `0.0.0.0`, **không xác thực** — README tự cảnh báo chỉ nên chạy trong mạng tin cậy | Không có service mặc định chạy mở cổng (ta chỉ gọi hàm Python trực tiếp qua `sync_solver`); nếu dùng `main.py`/`api_solver.py` (API server có sẵn trong repo) thì cũng không có xác thực — chưa kiểm tra kỹ |
| **Phù hợp để đo gì** | Đo khả năng phòng thủ **thực tế** của toàn bộ trang (đúng những gì người dùng/bot thật sẽ gặp) | Đo khả năng phòng thủ của **riêng sitekey/domain-binding**, tách biệt khỏi logic frontend |
| **Rủi ro khi review mã** | Không thấy flag đáng ngại ngoài cảnh báo "không chịu trách nhiệm" quen thuộc của tác giả | Tương tự — không thấy obfuscation/binary lạ, nhưng do thiếu license nên cẩn trọng hơn nếu định tái sử dụng |

### Khuyến nghị đọc kết quả

- **Chạy `ezsolver` trước** — đây là phép test gần với thực tế người dùng/bot thật nhất.
- **Chạy `Turnstile-Solver` sau** để biết riêng sitekey có bị giải độc lập dễ không — nếu cả hai đều "accepted" bởi backend thì kết luận chắc hơn là backend thực sự không có lớp chặn thứ hai.
- Nếu `ezsolver` thất bại nhưng `Turnstile-Solver` thành công (hoặc ngược lại), đó là tín hiệu quý: cho biết lớp phòng thủ nằm ở phía frontend (JS thật trên trang) hay ở chính widget/domain-binding.

Các repo khảo sát nhưng chưa tích hợp: `odell0111/turnstile_solver`,
`Iruko233/Turnstile-Slip`, `Lucrehulk/cloudflare-turnstile-solver-2026`.
Có thể thêm nhánh cho chúng sau nếu cần.

## Cấu trúc mỗi nhánh solver

```
<branch>/
├── <Solver>/          # vendor nguyên trạng repo gốc
├── run_test.py        # harness: xin xác nhận quyền → lấy token → gửi backend → báo accepted/rejected
├── .env.example        # sitekey/URL/endpoint đã điền sẵn giá trị thật đã khảo sát
├── README.md           # chi tiết kỹ thuật riêng của solver đó
└── SETUP.md            # hướng dẫn cài & chạy từng bước, có comment giải thích mỗi lệnh
```

Sang nhánh tương ứng và đọc `SETUP.md` ở đó để chạy thử — `main` chỉ là
trang tổng quan, không có code để chạy trực tiếp.

## Đọc kết quả chung (áp dụng cho cả 2 nhánh)

| Lấy token | Backend chấp nhận? | Ý nghĩa |
|---|---|---|
| Thất bại | — | Solver này không lấy được token với cấu hình widget hiện tại. Không chứng minh được với mọi solver khác. |
| Thành công | Bị từ chối | Turnstile không chặn được solver, nhưng backend có lớp phòng thủ thứ hai thật. Đây là kết quả tốt. |
| Thành công | Được chấp nhận | Backend coi "có token hợp lệ" là đủ. Cần thêm: verify lại token với API `siteverify` của Cloudflare (chặn tái sử dụng), rate limit theo IP/session, và phát hiện bất thường trên pattern request. |
