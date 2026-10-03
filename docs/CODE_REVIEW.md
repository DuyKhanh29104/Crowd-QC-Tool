# Kết quả rà soát và cải tiến — 30/09/2026

Đã sửa trực tiếp các điểm ảnh hưởng đến review frame, lấy mẫu video, cache ảnh và xuất dataset. Đợt này tập trung vào luồng xử lý chính của backend và các component review ở frontend.

| Mức ưu tiên | Phát hiện | Thay đổi và tác dụng |
| --- | --- | --- |
| Cao | Export dùng `0:v:{stream_index}` dù metadata lưu chỉ số stream tuyệt đối. File có audio đứng trước video có thể không xuất được clip. | Dùng `0:{stream_index}`. Kiểm tra bằng file MKV có audio ở stream 0, video ở stream 1 và giải mã clip MP4 đầu ra. |
| Cao | Viewer phụ thuộc `Math.floor(seconds * 10)`, khiến chuyển frame trong cùng khoảng 0,1 giây không cập nhật frame hiện tại. | Theo dõi timestamp chính xác. Kiểm thử Chrome ở 30 FPS xác nhận tiến liên tiếp frame 0 → 1 → 2 → 3 và lùi về frame 2. |
| Trung bình | Khóa cache chỉ có hiệu lực trong một process; worker khác vẫn có thể xóa PNG giữa lúc kiểm tra, đọc hoặc cập nhật thời gian truy cập. `touch()` có thể tạo lại file rỗng. | Đọc trực tiếp và xử lý cache miss; dùng `os.utime()` không tạo file mới; chịu được file bị xóa trong lúc quét và bỏ qua file tạm bị Windows khóa khi thu hồi cache. |
| Trung bình | Export chỉ kiểm tra hủy giữa các review, còn FFmpeg chặn luồng tối đa 600 giây. Lỗi export có thể để lại ZIP `.partial`. | Kiểm tra hủy giữa các frame và khi chờ FFmpeg; terminate/kill và thu hồi process khi cần; context manager dọn staging ZIP khi lỗi hoặc hủy. Chỉ công bố ZIP sau khi ghi và xác minh nguồn xong. |
| Hiệu năng | Lấy mẫu tạo ORM object cho cả những frame sẽ bị loại. | Stream các cột metadata, chỉ tải đầy đủ frame đã chọn theo lô 500. Giữ tính toán timestamp bằng `Fraction` và thứ tự frame. |
| Hiệu năng | Mỗi lần cập nhật overlay đều quét toàn bộ mẫu để tìm thời điểm gần nhất; quét trajectory cả khi lớp này tắt. | Dùng tìm kiếm nhị phân chung cho Viewer, Review và FrameEditor: tìm mẫu gần nhất từ O(n) xuống O(log n). Chỉ lấy cửa sổ trajectory khi bật hiển thị. |
| Hiệu năng | Thu hồi cache gọi `stat()` nhiều lần và luôn sắp xếp tất cả file. | Lấy metadata một lần cho mỗi file; chỉ sắp xếp khi tổng dung lượng vượt ngân sách. |

Các file triển khai chính:

- `backend/app/jobs/worker.py`: lấy mẫu frame.
- `backend/app/media/service.py`: cache ảnh.
- `backend/app/qc/worker.py`: export, quản lý FFmpeg, dọn ZIP staging; giải phóng engine khi không lấy được worker lease.
- `frontend/src/qc/samples.ts`: hàm tìm mẫu và cửa sổ thời gian.
- `frontend/src/qc/Viewer.tsx`, `Review.tsx`, `FrameEditor.tsx`: sử dụng hàm tìm mẫu; sửa chuyển frame.

**Kết quả đo hiệu năng**

Benchmark riêng hàm `selected_frames`, SQLite trong RAM, 60.000 frame ở 30 FPS, lấy mẫu mỗi 0,5 giây và trả về 4.000 frame. Mỗi phiên bản chạy một lượt làm nóng và ba lượt đo; dùng trung vị. Hai phiên bản trả về cùng danh sách ID và cùng thứ tự.

| Phiên bản | Thời gian trung vị |
| --- | ---: |
| Trước sửa | 0,612 giây |
| Sau sửa | 0,324 giây |

Nhanh hơn khoảng **1,89 lần**, giảm khoảng **47%** thời gian ở bước lấy mẫu trong phép đo này. Đây không phải phép đo tốc độ suy luận GPU hay toàn pipeline; lợi ích phụ thuộc tỷ lệ frame được chọn.

**Kiểm chứng**

- Trước sửa: 100 kiểm thử backend đạt.
- Sau sửa: **114 kiểm thử backend đạt**; pytest báo một warning, không có test thất bại.
- **3 kiểm thử thuật toán frontend đạt**, gồm timestamp không đều, biên, trường hợp cách đều và giới hạn số lần đọc timestamp trên 100.000 mẫu.
- **1 kiểm thử trình duyệt Chrome đạt** cho chuyển frame ở 30 FPS, với API giả lập và không có lỗi JavaScript.
- **TypeScript và production build đạt**.
- Backend bổ sung kiểm thử cache bị xóa trước/sau đọc, cache đang bị khóa, lấy mẫu VFR và nhiều lô, xuất clip có audio trước video, dọn staging khi lỗi/hủy, và thu hồi FFmpeg khi lỗi/timeout/hủy.

Lệnh chạy lại từ thư mục dự án:

```powershell
.\.venv-app\Scripts\python.exe -m pytest tests -q
npm --prefix frontend run test:unit
npm --prefix frontend run test:viewer
npm --prefix frontend run build
```

`test:viewer` dùng Chrome qua Playwright và tự mở server thử nghiệm trên cổng ngẫu nhiên. Không ghi vào dữ liệu ứng dụng đang chạy. Chưa chạy lại toàn bộ workflow E2E GPU; phiên ứng dụng hiện có ở cổng 8765 được giữ nguyên.

**Các điểm còn nên cải thiện ở đợt tiếp theo**

- `qc/api.py` vẫn đọc artifact JSON chứa toàn bộ kết quả và tạo bảng frame từ mọi dòng khi lưu quyết định. Với video dài, nên đo RAM/độ trễ thực tế rồi cân nhắc tách dữ liệu theo frame và phân trang API.
- Các component Review và FrameEditor còn dùng nhiều `any` và JSX dài. Tách kiểu dữ liệu API và component sẽ giúp phát hiện lỗi sớm hơn, nhưng cần thực hiện theo từng phần để tránh ảnh hưởng nghiệp vụ QC.
- Cache vẫn là cơ chế best effort giữa nhiều process; giới hạn dung lượng có thể tạm vượt khi Windows đang giữ file mở. Tìm kiếm nhị phân dựa trên điều kiện mẫu được sắp tăng dần theo timestamp, phù hợp với luồng phân tích hiện tại.

Bản sao các file gốc được lưu ở `.runtime/code-review-20260930-024528/`; kết quả benchmark ở `.runtime/code-review-benchmark.json`. Không thay đổi schema database hoặc trọng số model.
