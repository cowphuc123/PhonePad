# PhonePad – Biến điện thoại thành tay cầm chơi game cho máy tính

Cài app PhonePad lên điện thoại Android, mở **PhonePad.exe** trên máy tính Windows: điện thoại thành
tay cầm / bàn phím ảo qua Wi‑Fi, độ trễ thấp, và có thể xem luôn hình máy tính trên điện thoại.

## Tải về

Vào trang **[Releases](https://github.com/cowphuc123/PhonePad/releases/latest)**, có 2 phần:

| File | Dùng cho |
|---|---|
| `PhonePad-PC-v….exe` | **Máy tính Windows 10/11** – một file, không cần cài Python. |
| `PhonePad-Android-v….apk` | **Điện thoại Android 8+** – mở file trên điện thoại để cài (cho phép *cài ứng dụng không rõ nguồn*). |

## Chạy

1. Máy tính: mở **`PhonePad-PC-v….exe`**. Cửa sổ PhonePad hiện IP của máy và các điện thoại đang kết nối.
   Lần đầu Windows hỏi tường lửa → chọn **Cho phép** ở *Mạng riêng tư (Private)*.
   (Windows SmartScreen có thể cảnh báo vì exe chưa ký số: bấm *More info → Run anyway*.)
2. Điện thoại (cùng Wi‑Fi): mở app **PhonePad** → app tự tìm máy tính. Không thấy thì nhập IP hiện trên cửa sổ PhonePad.
3. Đóng cửa sổ PhonePad (hoặc bấm **Tắt PhonePad**) là tắt hẳn.

Máy tính **chỉ nhận kết nối từ app PhonePad**; mở địa chỉ máy tính bằng trình duyệt sẽ bị từ chối.

## Cập nhật

Cả hai bản tự kiểm tra trang Releases trên GitHub (từ bản 0.3):
- **Máy tính:** có bản mới thì cửa sổ PhonePad hiện nút **⬆ Cập nhật lên v…** (góc dưới). Bấm → tải exe mới,
  kiểm tra mã SHA‑256, tự thay file exe đang dùng và mở lại. Bố cục, ffmpeg giữ nguyên.
- **Điện thoại:** màn hình đầu của app hiện nút **⬆ Cập nhật lên bản …**. Bấm → tải APK, Android hỏi xác nhận cài.
  Lần đầu Android yêu cầu bật *Cho phép từ nguồn này* cho PhonePad.
- Nên cập nhật cả hai cùng lúc (máy tính và app cùng số phiên bản).
- Bản 0.2 trở về trước chưa có nút này: tải tay bản mới ở trang Releases một lần.

## Hai kiểu điều khiển

| Hồ sơ mẫu | Máy tính nhận được |
|---|---|
| **Tay cầm Xbox** | Tay cầm Xbox 360 ảo (game nhận như tay cầm thật, có cần analog). Cần driver ViGEmBus, xem bên dưới. |
| **Bàn phím WASD** | Phím bàn phím và chuột thật (chạy với mọi game, không cần cài thêm). |

### Tự tạo bố cục
Trong app bấm **✎ Sửa**:
- **+ Nút** – nút tròn / vuông / chữ nhật, gán **bất kỳ phím nào** (chữ, số, F1–F12, Shift, Space, chuột trái/phải, nút Xbox…). Chọn kiểu *Giữ* hoặc *Bật/tắt*.
- **+ Joystick** – cần di chuyển: 4 hướng gán 4 phím tuỳ ý (W A S D, T F G H, mũi tên…), kéo chéo sẽ bấm 2 phím cùng lúc. Hoặc chọn chế độ *cần analog trái/phải* của tay cầm Xbox.
- **+ D‑pad** – thập tự 4 hướng, gán phím tuỳ ý.
- Kéo để di chuyển, thanh trượt để đổi kích thước, **⚙ Hồ sơ** để tạo / sao chép / đổi tên / xoá bố cục.
- **✓ Xong** để lưu và quay lại chơi.

Bố cục lưu trên máy tính (`%LOCALAPPDATA%\PhonePad\layouts.json`, nút **Mở thư mục dữ liệu** trên cửa sổ),
nên điện thoại nào kết nối cũng dùng được.

## Tay cầm Xbox ảo

Cần driver **ViGEmBus**. Nếu cửa sổ PhonePad báo *chưa có driver ViGEmBus*, bấm **Cài driver**
(bộ cài đi kèm exe, Windows sẽ hỏi quyền Administrator), cài xong tắt và mở lại PhonePad.
Mỗi điện thoại kết nối = 1 tay cầm riêng → chơi được nhiều người.

## Xem hình máy tính trên điện thoại

Trong app bấm **📺** để bật/tắt **hình máy tính** làm nền phía sau các nút (người chơi 2 không cần nhìn màn hình máy tính).
- Lần đầu: trên cửa sổ PhonePad bấm **Tải ffmpeg (~170 MB)** (chỉ một lần).
- Máy tính chụp màn hình và nén bằng card NVIDIA (NVENC), không có thì Intel Quick Sync; gửi UDP; điện thoại giải mã phần cứng.
  Đo trên máy thử: nén ~23 ms, 60 fps. Thanh trên app hiện fps và độ trễ hình ước tính.
- Game chia đôi màn hình: trong **✎ Sửa → ⚙ Hồ sơ → Hình máy tính** chọn *Nửa trái/phải/trên/dưới*
  cho từng hồ sơ. Chất lượng 720p/1080p chọn trong menu tên hồ sơ.
- Mất gói Wi‑Fi thì hình có thể nhoè thoáng qua, tự lành trong tối đa 2 giây.

## Để độ trễ thấp nhất

App gửi trạng thái nút qua **UDP** ngay khi chạm (mất gói không làm kẹt phím vì gói sau mang đủ trạng thái),
bật chế độ **Wi‑Fi độ trễ thấp** của Android. Thời gian xử lý trên máy tính ≈ 0,1 ms; độ trễ thực tế gần như chỉ còn là của mạng.

Mẹo thêm:
- Dùng **Wi‑Fi 5 GHz**, máy tính cắm dây LAN vào router → thường 3–10 ms.
- **Chia sẻ mạng qua USB (USB tethering)**: cắm cáp, bật *Chia sẻ Internet qua USB* trên điện thoại → thường 1–2 ms và ổn định nhất.

## Lưu ý
- Nếu game chạy bằng quyền **Administrator**, hãy chạy PhonePad bằng quyền Administrator (chuột phải → *Run as administrator*) thì phím mới vào game.
- Ai cùng mạng Wi‑Fi có app PhonePad cũng điều khiển được — chỉ dùng trong mạng nhà bạn.
- Đổi cổng: `PhonePad.exe --port 9000 --udp-port 9001`.

## Chạy từ mã nguồn / tự build

```
pip install aiohttp vgamepad psutil
python server.py              # cửa sổ PhonePad; --console để chỉ in ra console
```
`start.bat` làm việc tương tự. Khi chạy từ mã nguồn, bố cục và ffmpeg nằm ngay trong thư mục dự án.

Build lại cả exe và APK (cần `pip install pyinstaller pillow` và Android SDK):
```
powershell -ExecutionPolicy Bypass -File tools\make_release.ps1
```
Kết quả nằm trong `dist\`. Chỉ build APK: `cd android && gradlew.bat assembleDebug`.
