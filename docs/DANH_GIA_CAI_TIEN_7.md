# Đánh giá cải tiến trên 7.mp4 — 30/09/2026

Đã áp dụng các cải tiến review có lợi ích đo được. Giữ YOLO11n, mặc định detection và ByteTrack; bổ sung cấu hình thử nghiệm, không tự đổi profile hay dữ liệu QC đã lưu.

## Kiến trúc và quyết định

Backend FastAPI + SQLite/SQLAlchemy; worker Python/CUDA riêng. Frontend React/Vite, Konva cho chỉnh box và SVG trên video native. Frame dùng PTS nguồn, không suy thời gian từ số thứ tự. ROI, kết quả AI và correction được lưu riêng trong artifact JSON. Xuất YOLO/COCO đã yêu cầu xác nhận box của người QC.

| Đề xuất | Đánh giá và thay đổi |
| --- | --- |
| Crop chữ nhật + margin, giữ context, lọc polygon bằng bottom-center | Đã có đúng trong code. Giữ nguyên, cho cấu hình margin. Không warp/mask ảnh inference. |
| Phóng crop, tăng imgsz | Có triển vọng phát hiện người nhỏ; thêm `roi_scale_mode=crop`. Giữ `source` mặc định vì chưa có GT. |
| Giảm confidence / tăng IoU / max_det | Confidence đã là 0,15. Bổ sung cấu hình IoU/max_det; giữ 0,7/300 mặc định. |
| YOLO11s | Không có checkpoint s trong dự án; không tải/đổi model. API nhận `person_model_path` để so sánh khi có checkpoint. |
| ByteTrack + low-confidence association | Giữ ByteTrack. Thêm tùy chọn và test tracker thật. Chưa bật mặc định vì chưa đo được ID switches bằng GT. |
| Giữ track 1–3 giây | Thêm `track_buffer_seconds`, đổi thành số frame theo sampling FPS. Thử 2 giây tạo nhiều ID hơn trên video này; giữ mặc định cũ 30 frame. |
| ROI tracking margin | Đã track tất cả detection trong crop context trước khi lọc polygon đếm; giữ nguyên. |
| Head–person fusion, HEAD_ONLY temporal recovery, reconnect/ReID mới | Chưa triển khai vào pipeline. Chưa có GT/identity để chứng minh tốt hơn; đầu không xác định được vị trí chân trong ROI mặt sàn. Không suy body box/ground anchor từ head rồi đưa vào count hoặc GT. Hai chế độ person/head vẫn được giữ. |
| Giữ viewport, native video, overlay riêng, cache LRU, prefetch theo hướng | Đã có. Giữ cách hoạt động; cache RAM vẫn giới hạn 12 ảnh/64 MiB. |
| Hủy request cũ, debounce scrub | Đã sửa: AbortController cho metadata/ảnh, kiểm tra stale, debounce 100 ms và lấy ngay khi thả slider. Giữ tải đang chạy nếu chính là frame đích mới. |
| Decoder sống lâu + seek keyframe | Đã sửa: pool tối đa 2 reader; đi tới gần dùng iterator, seek xa/ngược về keyframe trước PTS. Đóng reader khi lỗi, eviction, shutdown. |
| Nén ảnh review | PNG `compress_level=1`; vẫn lossless, so pixel bằng decoder cũ. Đổi CPU lấy kích thước PNG lớn hơn; giới hạn cache byte giữ nguyên. |
| Proxy / thumbnail / tiled inference | Chưa thêm vì chưa chứng minh lợi ích sau tối ưu hiện tại; tránh thêm pipeline, mapping và lưu trữ chưa cần thiết. |
| QC → GT → export / active learning | Đã có correction, hard negative, xác nhận box, YOLO/COCO. Giữ schema và các điều kiện xuất hiện tại. Chưa thêm loại lỗi/fusion state hoặc training tự động. |

Trên Windows, reader dùng handle chỉ đọc cho phép chia sẻ rename/delete để không khóa chức năng di chuyển nguồn. Các cờ được đối chiếu với [CreateFileW của Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew). Việc kiểm hash nguồn vẫn chạy trước khi dùng cache/decoder.

## Benchmark thực tế

Nguồn `7.mp4`: 1440×1080, 700 frame, 25,926 giây. SHA-256: `94a57e9e66c994fce2ca13718869b9d7100efa29cb1cc818f894ef2d898d56e6`. Dùng polygon đã lưu của chính video này; crop context `[440,438,1440,1080]`. GPU: NVIDIA GeForce RTX 3050 Laptop GPU.

Đo hàm backend trả PNG; không bao gồm HTTP, browser decode hoặc toàn bộ độ trễ người dùng nhìn thấy. Cache miss nghĩa là chưa có PNG, không phải xóa cache hệ điều hành. Có 15 lần đi frame gần, 8 lần jump xa và 15 cache hit mỗi bản. Pixel RGB của tất cả frame đo được so với bản cũ, kết quả giống nhau.

| Review, trung vị | Trước | Sau | Kết luận |
| --- | ---: | ---: | --- |
| Frame gần, cache miss | 225,80 ms | 65,00 ms | Giảm 71,2% |
| Jump xa, cache miss | 281,59 ms | 155,21 ms | Giảm 44,9% |
| Cache hit | 0,95 ms | 1,15 ms | Không có cải thiện; chênh khoảng 0,2 ms |

Inference lấy 130 frame theo lưới thời gian 5 Hz trải toàn clip, cùng YOLO11n/confidence 0,15. Mỗi tensor shape được warm up. Các hàng là một lượt đo; timing có nhiễu tải máy, không phải kết luận thống kê về tốc độ GPU.

| Cấu hình | Box qua 130 frame | Count TB/frame | Số ID khác nhau | Median inference + tracking |
| --- | ---: | ---: | ---: | ---: |
| Code cũ, source 640 | 1.865 | 14,35 | 83 | 23,90 ms |
| Code mới, mặc định tương thích | 1.865 | 14,35 | 83 | 25,11 ms |
| Crop 640 | 2.343 | 18,02 | 91 | 35,46 ms |
| Crop 960 | 2.833 | 21,79 | 109 | 41,91 ms |
| Crop 1280 | 2.879 | 22,15 | 109 | 56,67 ms |
| Source 640 + low-confidence association | 1.865 | 14,35 | 82 | 27,18 ms |
| Source 640 + buffer 2 giây | 1.865 | 14,35 | 86 | 24,48 ms |

Đã assert toàn bộ box, confidence và Track ID của mặc định mới giống bản cũ trên 130 frame, không chỉ so tổng count. Inference mặc định chưa có lợi ích tốc độ; phần thay đổi này nhằm cấu hình và đo thử, không phải tăng tốc.

Crop 960 tăng số box khoảng 52%, nhưng **không được diễn giải là recall tăng 52%**. Không có frame person/identity GT đã duyệt cho video này. Recall, precision, FN, FP, ID switches và fragmentation vì vậy chưa tính; số ID khác nhau không thay thế ID switches. Chưa đo CPU%, RAM hay benchmark YOLO11s. JSON có thêm FPS inference, p95, GPU peak memory và số quan sát track được ByteTrack phục hồi.

## Cấu hình mới

Trong **Run Analysis → Advanced**, có scale ROI, IoU, max_det, margin ba hướng, ngưỡng tracker, thời gian giữ track và lựa chọn low-confidence association. `imgsz` vẫn dùng trường cũ (640/960/1280 đều hỗ trợ), `confidence` vẫn dùng tên cũ.

| Trường | Mặc định |
| --- | --- |
| `roi_scale_mode` | `source` — giữ tỷ lệ toàn ảnh |
| `roi_margin_side`, `roi_margin_top`, `roi_margin_bottom` | 0,15 / 0,20 / 0,10 |
| `iou`, `max_det` | 0,7 / 300 |
| `track_buffer_seconds` | null — giữ 30 frame của cấu hình cũ |
| `track_use_low_conf` | false |
| `track_high_thresh`, `track_low_thresh`, `new_track_thresh`, `match_thresh` | 0,25 / 0,10 / 0,25 / 0,8 |
| `person_model_path` | null — YOLO11n hiện tại; chỉ cấu hình qua API |

Metadata của lần phân tích lưu margin, tensor size, tracker config và thời gian buffer thực tế. Detection dưới confidence chỉ có thể hỗ trợ tracker khi bật tùy chọn; không trở thành box QC hay tăng count. Không cần migration database.

## File và kiểm chứng

Các thay đổi chính: `backend/app/qc/{api,inference,models,worker}.py`, `backend/app/media/{service,decoder,shared_file}.py`, `backend/app/main.py`; `frontend/src/api.ts`, `qc/{frameBuffer,FrameEditor,useFrameHold,InferenceOptions,InferenceSize,Analysis}`. Không đổi trọng số hoặc sửa video, profile và annotation hiện có.

182 test backend qua; các test bổ sung gồm coordinate transform/crop scale, low-confidence không tăng count, validation cấu hình và vòng đời decoder. Ba test ByteTrack thật qua trong môi trường GPU: phục hồi ID sau miss, duy trì track qua low-confidence, thời gian buffer theo sampling FPS. Test browser gồm stale transfer thực sự bị abort, viewport không trống, latest scrub target, cache, annotation, nhịp giữ phím VFR, motion và layout. TypeScript/production build qua. Có một warning deprecation Starlette/AnyIO hiện có.

Lệnh chạy lại từ thư mục dự án:

```powershell
.\.venv-app\Scripts\python.exe -m pytest tests -q
.\.venv-gpu\Scripts\python.exe -m unittest discover -s tests/runtime -v
node --test --test-concurrency=1 frontend/tests/*.test.mjs
npm --prefix frontend run build
.\.venv-gpu\Scripts\python.exe scripts/benchmark_qc_video.py
```

Chạy browser test tuần tự để các Vite test server không tranh cache dependency chung. Bộ runtime được skip trong môi trường app không có Ultralytics và chạy riêng bằng môi trường GPU.

Benchmark dùng bản gốc tại `.runtime/improvement-baseline/`, chỉ đọc dữ liệu ứng dụng. Kết quả tổng hợp: [evidence/benchmark-7.json](evidence/benchmark-7.json); predictions từng cấu hình tại `.runtime/benchmark-7-*-predictions.json`. Muốn áp dụng backend mới vào phiên đang chạy, khởi động lại bằng `scripts/run.ps1 -Build`.

Bước tiếp theo có giá trị nhất là QC một tập frame `7.mp4` gồm cảnh che khuất/người nhỏ, xác nhận identity giữa frame, rồi mới quyết định đổi default sang crop 960 hoặc đưa fusion vào count. Các thay đổi chưa có bằng chứng tốt hơn đã được giữ nguyên như yêu cầu.
