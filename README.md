# PhonePad – Biến điện thoại thành tay cầm chơi game cho máy tính

Điện thoại mở một trang web (không cần cài app), kết nối với máy tính qua Wi‑Fi bằng WebSocket.
Mỗi lần chạm được gửi đi ngay lập tức → máy tính bấm phím / nút tay cầm ảo.

## Tải về

Vào trang **[Releases](https://github.com/cowphuc123/PhonePad/releases/latest)**, có 2 phần:

| File | Dùng cho |
|---|---|
| `PhonePad-PC-v….zip` | **Máy tính Windows** – giải nén ra một thư mục rồi làm theo mục *Chạy* bên dưới. |
| `PhonePad-Android-v….apk` | **Điện thoại Android** – app độ trễ thấp, xem được hình máy tính (tuỳ chọn; không cài app thì dùng bản web). |

## Chạy

1. Máy tính cần Python 3.8+ ([python.org](https://www.python.org/downloads/), khi cài nhớ tick *Add python.exe to PATH*).
2. Bấm đúp **`start.bat`** (tự cài `aiohttp` nếu thiếu).
3. Trình duyệt máy tính mở trang có **mã QR** → quét bằng điện thoại (điện thoại và máy tính dùng chung Wi‑Fi).
4. Lần đầu Windows hỏi tường lửa cho Python → chọn **Cho phép** ở *Mạng riêng tư (Private)*.
5. Trên điện thoại bấm **⛶** để toàn màn hình và xoay ngang.

## Hai kiểu điều khiển

| Hồ sơ mẫu | Máy tính nhận được |
|---|---|
| **Tay cầm Xbox** | Tay cầm Xbox 360 ảo (game nhận như tay cầm thật, có cần analog). Cần cài thêm, xem bên dưới. |
| **Bàn phím WASD** | Phím bàn phím và chuột thật (chạy với mọi game, không cần cài thêm). |

### Tự tạo bố cục
Bấm **✎ Sửa**:
- **+ Nút** – nút tròn / vuông / chữ nhật, gán **bất kỳ phím nào** (chữ, số, F1–F12, Shift, Space, chuột trái/phải, nút Xbox…). Chọn kiểu *Giữ* hoặc *Bật/tắt*.
- **+ Joystick** – cần di chuyển: 4 hướng gán 4 phím tuỳ ý (W A S D, T F G H, mũi tên…), kéo chéo sẽ bấm 2 phím cùng lúc. Hoặc chọn chế độ *cần analog trái/phải* của tay cầm Xbox.
- **+ D‑pad** – thập tự 4 hướng, gán phím tuỳ ý.
- Kéo để di chuyển, thanh trượt để đổi kích thước, **⚙ Hồ sơ** để tạo / sao chép / đổi tên / xoá bố cục.

Bố cục lưu trong `layouts.json` trên máy tính, nên điện thoại nào kết nối cũng dùng được.

## Bật tay cầm Xbox ảo (tuỳ chọn)

```
pip install vgamepad
```
Lệnh này sẽ mở trình cài driver **ViGEmBus** – bấm cài đặt, rồi chạy lại `start.bat`.
Mỗi điện thoại kết nối = 1 tay cầm riêng → chơi được nhiều người.

## App Android (độ trễ thấp hơn bản web)

App gửi nút bấm qua **UDP** (không bị TCP chặn chờ khi Wi‑Fi rớt gói), bật chế độ **Wi‑Fi độ trễ thấp** của Android và nhận cảm ứng trực tiếp không qua trình duyệt. Bố cục dùng chung với bản web.

1. Tải `PhonePad-Android-v….apk` ở trang [Releases](https://github.com/cowphuc123/PhonePad/releases/latest) bằng điện thoại rồi mở để cài (cho phép *cài ứng dụng không rõ nguồn*; hoặc `adb install`). Tự build thì file nằm ở `android\app\build\outputs\apk\debug\app-debug.apk`.
2. Máy tính chạy `start.bat`. Mở app → app tự tìm máy tính (UDP cổng 8766). Không thấy thì nhập IP hiện trên trang mã QR.
3. **✎ Sửa** mở trình sửa bố cục ngay trong app; bấm **✓ Xong** để lưu và quay lại chơi.
4. **📺** bật/tắt **hình máy tính** làm nền phía sau các nút (người chơi 2 không cần nhìn màn hình máy tính).
   - Máy tính chụp màn hình và nén bằng card NVIDIA (NVENC), gửi UDP; điện thoại giải mã phần cứng.
     Đo trên máy này: nén ~23 ms, 60 fps. Thanh trên app hiện fps và độ trễ hình ước tính.
   - Cần ffmpeg (một lần): chuột phải `tools\fetch_ffmpeg.ps1` → *Run with PowerShell*.
   - Game chia đôi màn hình: trong **✎ Sửa → ⚙ Hồ sơ → Hình máy tính** chọn *Nửa trái/phải/trên/dưới*
     cho từng hồ sơ. Chất lượng 720p/1080p chọn trong menu tên hồ sơ.
   - Mất gói Wi‑Fi thì hình có thể nhoè thoáng qua, tự lành trong tối đa 2 giây.

Build lại APK (máy đã cài Android SDK):
```
cd android
gradlew.bat assembleDebug
```
iPhone: dùng bản web như cũ.

## Để độ trễ thấp nhất

Phần mềm đã: gửi ngay khi chạm (không chờ), tắt thuật toán Nagle (TCP_NODELAY), không nén, gói tin vài byte, ping đều để Wi‑Fi điện thoại không ngủ. Thời gian xử lý trên máy tính ≈ 0,1 ms; độ trễ thực tế gần như chỉ còn là của mạng (số **ms** hiển thị trên điện thoại là thời gian khứ hồi).

Mẹo thêm:
- Dùng **Wi‑Fi 5 GHz**, máy tính cắm dây LAN vào router → thường 3–10 ms.
- **Chia sẻ mạng qua USB (USB tethering)**: cắm cáp, bật *Chia sẻ Internet qua USB* trên điện thoại, máy tính sẽ hiện thêm một địa chỉ IP trên trang QR → thường 1–2 ms và ổn định nhất.
- Hoặc Android + adb: `adb reverse tcp:8765 tcp:8765`, rồi trên điện thoại mở `http://localhost:8765`.

## Lưu ý
- Nếu game chạy bằng quyền **Administrator**, hãy chạy `start.bat` bằng quyền Administrator thì phím mới vào game.
- Ai cùng mạng Wi‑Fi cũng mở được trang này — chỉ dùng trong mạng nhà bạn.
- Đổi cổng: `start.bat --port 9000`.
