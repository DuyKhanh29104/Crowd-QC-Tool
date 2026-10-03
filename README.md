# Crowd QC

Công cụ QC video CCTV theo đúng 5 bước:

**Project & Video → ROI & Camera Setup → Run Analysis → Candidate Review → Dataset & Export**

AI tìm candidate. QC approve, correct, reject hoặc giữ uncertain. Correction và hard negatives được lưu để tạo Ground Truth và dataset cho training bên ngoài.

## Chạy ứng dụng

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run.ps1 -Build
```

Mở **http://127.0.0.1:8765**. Các lần sau có thể bỏ `-Build`. Máy mới cài qua `scripts/setup.ps1 -GPU`.

## Hai tài liệu hiện hành

- [Hướng dẫn sử dụng](docs/HUONG_DAN_SU_DUNG_TU_DAU.md): thao tác 5 màn hình, phím tắt, profile và export.
- [Thiết kế và vận hành](docs/KY_THUAT_VA_VAN_HANH.md): sitemap, flow, wireframe, component hierarchy, trạng thái, skill UI/UX, kiến trúc và kiểm thử.

Upload nhiều video hoặc cả thư mục. ROI chỉ cần tên, diện tích và vùng vẽ, luôn áp dụng suốt video. Camera Profile dùng lại ROI/hiệu chuẩn theo camera và độ phân giải. Profile DEMO không dùng cho CCTV thật.

Kho hiện hành: `workspace_data/` hoặc `CROWD_QC_DATA_ROOT`. Video Mall, kết quả phân tích, model và môi trường GPU được giữ lại. Tài liệu lưu trữ, kế hoạch triển khai cũ, component giao diện đã ngừng dùng và script demo cũ đã được dọn; sử dụng hai tài liệu hiện hành ở trên.
