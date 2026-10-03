# Hướng dẫn Crowd QC — 5 màn hình

## Mở ứng dụng

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run.ps1
```

Mở **http://127.0.0.1:8765**. Sau khi sửa frontend: thêm `-Build`. Máy mới cần Python 3.12, Node/npm, FFmpeg/ffprobe; cài bằng `scripts/setup.ps1 -GPU`. Máy hiện tại đã có `.venv-app`, `.venv-gpu` và weights gốc.

## 1. Project & Video

- Bấm **Tạo project**, nhập tên. Chọn project ở danh sách hoặc thanh trên.
- Chọn đúng loại nguồn CCTV thực tế / mô phỏng, nhập mã camera.
- **Chọn video** hỗ trợ một hoặc nhiều file. **Chọn thư mục** lấy các file video trong thư mục đã chọn. Ứng dụng tải theo thứ tự, hiện tiến độ và lập chỉ mục.
- Video được sao chép vào kho ứng dụng; file gốc trên máy không bị thay đổi.
- Danh sách hiển thị thời lượng, độ phân giải, FPS trung bình xấp xỉ, Camera Profile và tiến độ QC.
- Nếu đã có profile đúng camera/góc nhìn/độ phân giải, chọn ở cột Camera Profile rồi **Phân tích**. Không dùng profile DEMO cho video thật.
- Nếu chưa có, chọn **Setup camera**. Nút × bỏ video khỏi danh sách nhưng giữ file và lịch sử để kết quả QC vẫn truy vết được; thao tác có xác nhận.

## 2. ROI & Camera Setup

### ROI

Vẽ ít nhất 3 đỉnh hoặc chọn **Toàn khung hình**. Nhập **tên ROI và diện tích m²**. ROI luôn áp dụng suốt video, không nhập thời gian. Nút + tạo nhiều ROI; có thể đổi tên, sửa đỉnh hoặc xóa ROI khỏi cấu hình đang sửa. Diện tích là số người dùng khai báo, không được suy từ pixel.

### Hiệu chuẩn (khi cần tốc độ)

Chọn tab nhỏ **Hiệu chuẩn mặt sàn** ngay trong màn hình này:

1. Click 4 mốc A → B → C → D theo thứ tự quanh một tứ giác lồi trên mặt sàn. Có thể nhập tọa độ pixel bằng bàn phím trong phần mở rộng.
2. Mặc định **Dạng vùng hiệu chuẩn** là **Tứ giác · góc A vuông · 4 cạnh**: AB vuông góc AD **ngoài thực tế**, nhập AB, BC, CD, DA theo mét; không cần đo đường chéo. Góc trên ảnh có thể không nhìn vuông do phối cảnh. Hệ thống từ chối số đo không tạo được tứ giác lồi hợp lệ. Nếu vùng thực là hình chữ nhật, chọn **Hình chữ nhật · 2 cạnh** để chỉ nhập AB, BC. Profile tứ giác sáu số đo cũ vẫn được hỗ trợ.
3. Bấm **Kiểm tra 2 điểm**, click hai điểm có khoảng cách thực đã đo, nhập khoảng cách mong đợi.
4. Bấm **Kiểm tra hiệu chuẩn** để xem khoảng cách ước lượng và sai lệch. Sau khi lưu profile có thể xem Bird-eye view.
5. Tốc độ chỉ được ước lượng khi phép kiểm khoảng cách đạt, ROI nằm trong vùng hiệu chuẩn và có lịch sử track đủ liên tiếp. Không có hiệu chuẩn vẫn phân tích count/mật độ; tốc độ hiển thị chưa xác định.

Đặt tên profile rồi **Lưu Camera Profile → Continue to Analysis**. Profile giữ ROI, diện tích, phép chiếu, độ phân giải và các thông số phân tích mặc định. Lưu sửa đổi tạo một bản profile mới; kết quả đã chạy giữ cấu hình cũ.

## 3. Run Analysis

Chọn **Loại detection** trước khi bấm Run analysis:

- **Toàn thân · YOLO11n**: bbox người, kiểm ROI bằng điểm giữa đáy box; có thể đo vận tốc nếu đủ điều kiện hiệu chuẩn/tracking.
- **Đầu người · YOLOv8n Head**: bbox đầu từ model chuyên detect đầu; kiểm ROI bằng tâm đầu. Chọn ROI phù hợp vị trí đầu trong ảnh. Đo m/s qua tab **Hiệu chuẩn tâm đầu** riêng, với bốn mốc ở cùng độ cao tâm đầu tham chiếu và phép kiểm khoảng cách. Không áp dụng hiệu chuẩn mặt sàn trực tiếp cho box đầu.

Từ 30/09/2026, cả hai loại box đều có chuyển động px/s khi đủ track, và m/s khi hiệu chuẩn đúng mặt phẳng đạt kiểm tra. Trong **Nhãn từng frame**, mở **Đo vận tốc từ box đã chỉnh** để chọn frame mốc, gán cùng Track ID ở frame sau và tính/tải phép đo JSON. Xem [hướng dẫn đo vận tốc ROI](DO_VAN_TOC_ROI.md) để thiết lập, hiểu điều kiện và giới hạn đo.

Model đầu được tải sẵn tại `weights/yolov8n_head_detector.pt`, nguồn [abhiWanKenobi/yolov8n_head_detection](https://huggingface.co/abhiWanKenobi/yolov8n_head_detection), huấn luyện HollywoodHeads, giấy phép **CC BY-NC 4.0** (nghiên cứu/phi thương mại). Chưa có đánh giá GT trên camera của bạn, cần QC kết quả.

Loại model được lưu cùng lần chạy. Đổi dropdown chỉ đổi cấu hình lần chạy mới; box đang xem vẫn theo model đã tạo kết quả. Chọn **Lần phân tích** trên thanh trên để mở lại bbox đầu hoặc toàn thân. Review tự đổi nhãn công cụ thành **Thêm đầu / Thêm người**. Các kết quả cũ không có trường loại model được hiểu là toàn thân.

Mặc định **High Recall**, 2 FPS, confidence 0,15. Chọn ROI cần phân tích; có thể chạy lại riêng cho ROI khác. Danh sách **Lần phân tích** trên thanh đầu trang cho phép mở lại các lần chạy trước.

- Sampling FPS: số frame lấy mẫu mỗi giây, không thay tốc độ phát video.
- Confidence: ngưỡng detection. Preset Balanced / High Precision tăng ngưỡng; Custom cho phép điều chỉnh.
- **Advanced**: tracking, speed/stationary, ngưỡng mật độ, tốc độ, tỷ lệ đứng yên, thời gian tắc nghẽn tối thiểu. Detection/ROI filtering/density là phần cốt lõi luôn chạy. Crowd Counter chưa có model; Head Detection là lựa chọn riêng ở đầu cấu hình.

**Kích thước đầu vào YOLO (px):** chỉnh trong **ROI & Camera Setup → Camera Profile** để lưu mặc định cho camera, hoặc trong **Run Analysis → Cấu hình phân tích** để thay riêng lần chạy. Mặc định 640; hỗ trợ 320–1920, bội số 32, với gợi ý 640 / 960 / 1280 / 1600 / 1920. Áp dụng cho cả toàn thân và đầu. Đây là cạnh dài tham chiếu của toàn ảnh: ảnh cắt ROI được thu theo cùng tỷ lệ và làm tròn lên bội số 32, nên tensor thực tế có thể nhỏ hơn giá trị đã chọn. Tăng kích thước giữ thêm chi tiết nhưng tốn GPU/thời gian hơn; không tự thay model. Kích thước được lưu cùng mỗi lần phân tích. Thay đổi không tính lại kết quả cũ; cần chạy Analysis lại.

Model nhận ảnh cắt theo khung bao ROI cộng vùng đệm: 15% chiều rộng mỗi bên, 20% chiều cao phía trên và 10% phía dưới, giới hạn trong video. Giữ nguyên ảnh trong vùng cắt để thấy cơ thể sát biên (kể cả biên chéo của ROI); chỉ đếm người có điểm giữa đáy box trong ROI gốc. Box có thể vươn ngoài ROI, nhưng người chỉ đứng trong vùng đệm không được tính. Diện tích ROI không thay đổi. Tensor vẫn giảm theo kích thước vùng cắt; ROI lớn gần toàn ảnh sẽ ít tiết kiệm thời gian. Vùng đệm không khôi phục phần cơ thể ngoài mép video và không bảo đảm bắt đủ mọi người. Giải mã video vẫn đọc frame gốc. Chạy lại Analysis để áp dụng; kết quả cũ được giữ nguyên.

Detection đạt ngưỡng confidence luôn được giữ để QC, kể cả khi chưa có track ID. Tracking chỉ bổ sung ID; tốc độ chưa xác định nếu chưa đủ lịch sử. Phân tích đọc video tuần tự theo đúng các PTS đã chọn, không tạo PNG cho từng mẫu.

Bấm **Run analysis**. Pipeline xử lý toàn video trên GPU, có trạng thái, phần trăm và hủy/chạy lại. Sau khi hoàn tất, xem video, overlay, count/speed/stationary và timeline. Click timeline để nhảy tới đoạn cần xem.

Box/track trên video là gợi ý của frame lấy mẫu gần nhất; có timestamp riêng. Speed dùng điểm đáy box và homography, vẫn là ước lượng AI. Video có codec không được trình duyệt hỗ trợ sẽ chuyển sang xem chuỗi frame và ghi rõ chế độ này.

## 4. Candidate Review

Mặc định mở **Nhãn từng frame**, không cần candidate và không cần nhập tên/ghi chú.

1. Dùng **Frame trước / Frame sau**, phím ← / → hoặc nhập **Số frame** để chọn đúng frame gốc.
2. Chọn box để kéo, kéo góc dưới phải để đổi kích thước, hoặc nhập X1/Y1/X2/Y2. Bấm **Thêm người** rồi click hai góc; **Xóa box** hoặc Delete để bỏ box sai.
3. Frame chưa được AI lấy mẫu bắt đầu chưa có box, có thông báo rõ. Không sao chép nhãn từ frame gần nhất.
4. Kiểm tra **Trạng thái frame**, rồi bấm **Lưu frame** hoặc **Lưu & frame tiếp**. Một lần bấm xác nhận box của frame, kể cả frame rỗng. Chọn UNCERTAIN khi chưa chắc để chưa đưa vào nhãn đã duyệt.
5. Nhãn lưu theo đúng ID/PTS frame, có revision và kiểm tra xung đột. Chuyển frame khi chưa lưu có cảnh báo. Các frame ngoài mẫu AI cũng xuất được YOLO/COCO sau khi duyệt.

AI chỉ detect trong ROI cộng vùng đệm. Khi xác nhận toàn frame để xuất YOLO/COCO, kiểm tra và bổ sung cả người khác trong ảnh nếu có. Không coi box thiếu ở ngoài ROI là ảnh âm tính đã được xác nhận tự động.

Chọn **Duyệt sự kiện** khi cần xem candidate, bằng chứng và nhãn NORMAL/CROWDED/CONGESTED. **Approve / Reject / Uncertain** lưu ngay bằng một nút, không yêu cầu nhập tên; ghi chú và hard-case tag là tùy chọn. **Correct** mở chỉnh nhãn sự kiện, count/thời gian/box rồi bấm **Lưu correction**. Việc duyệt sự kiện không tự duyệt mọi box.

### Xem nhanh và phím tắt

Khung xem có chiều cao gọn hơn (khoảng 52% chiều cao màn hình, tối đa 520 px trên desktop); video/ảnh được căn giữa và giữ tỷ lệ. **Mẫu AI trước / Mẫu AI sau** chuyển tới đúng frame đã phân tích. Với sampling thấp hơn FPS nguồn, các frame xen giữa chưa có bbox AI; UI phân biệt trường hợp này với frame đã chạy AI nhưng không phát hiện đối tượng. Nhãn QC đã lưu trong review sự kiện cũng được đọc lại ở review từng frame, kể cả nhãn rỗng. Không sao chép box từ frame gần nhất sang frame chưa phân tích.

Khung video ở Run Analysis và Review bám theo khi cuộn. Thanh zoom nằm nổi trên ảnh nên phát/dừng không đổi kích thước vùng xem. Cuộn chuột thường để cuộn trang; **Ctrl + cuộn** (hoặc Command + cuộn) và nút ± để zoom, **Vừa khung** để đưa ảnh về kích thước vừa màn hình. Phím tắt và tải trước được gom vào mục mở rộng bên dưới video.

**Giữ tiến/lùi cố định 2×:** giữ phím ← / → hoặc giữ nút **Frame trước / Frame sau**. Nhấn một lần chuyển một frame ngay; khi giữ, nhịp chuyển tiếp bằng một nửa khoảng cách timestamp giữa hai frame theo hướng tua. Ví dụ video 25 FPS có nhịp 20 ms, 30 FPS khoảng 16,7 ms; video FPS biến thiên dùng timestamp thực. Không có tùy chọn chỉnh nhịp tua. Nếu frame chưa sẵn sàng thì chờ, không cộng dồn các bước tua hoặc tăng tốc bù. Thả nút/phím hoặc chuyển sang cửa sổ khác sẽ dừng lặp. Trong khoảng chờ, hệ thống tải trước theo hướng đang tua; giữ Shift dùng bước 10 frame và khoảng thời gian của chính bước đó. Tốc độ tua cố định 2× độc lập với lựa chọn tốc độ phát video. Không cần tải toàn bộ video để dùng luồng này. Máy giải mã chậm có thể không đạt đủ 2×.


Khi chuyển từng frame, ảnh và box hiện tại được giữ cho đến khi ảnh mới giải mã xong, rồi đổi cùng nhau. Ứng dụng tải trước hai frame theo hướng đang tua và một frame ở phía ngược lại và giữ bộ đệm ảnh đã giải mã (tối đa 12 ảnh, mục tiêu 64 MiB; một ảnh lớn vẫn được giữ). Tua lại các frame gần đó dùng bộ đệm. Nếu tải lâu, một chỉ báo nhỏ ghi rõ frame đang chờ và frame đang hiển thị; sửa/lưu/đo vận tốc tạm khóa để tránh nhầm frame. Tải lỗi có nút **Thử tải lại frame**. Nhảy xa vẫn có thể cần chờ giải mã nguồn.

Frame ở cột trái; chỉ số, công cụ sửa và nút lưu ở cột phải. Thanh phát/chuyển frame nằm dưới ảnh. Bấm Phát hoặc Space để xem video gốc, chọn tốc độ 0,25× / 0,5× / 1× / 1,5× / 2× / 4×. Khi phát, overlay và chỉ số là mẫu AI gần nhất, có ghi thời điểm. Khi dừng, tool chọn frame nguồn đầu tiên tại hoặc sau thời điểm dừng để sửa chính xác. Video không tự duyệt/lưu nhãn.

| Phím trong Nhãn từng frame | Thao tác |
|---|---|
| Space | Phát / dừng |
| ← / → | Frame trước / sau |
| Shift + ← / → | Lùi / tiến 10 frame |
| [ / ] | Giảm / tăng tốc độ phát |
| V | Chọn / sửa box; dừng phát nếu đang chạy |
| B | Thêm người; dừng phát để chỉnh frame |
| H | Di chuyển ảnh |
| Delete | Xóa box được chọn |
| S hoặc Ctrl+S | Lưu frame |
| Ctrl+Enter | Lưu và chuyển frame tiếp |

Các phím đơn không can thiệp khi đang nhập ô form; Ctrl+S và Ctrl+Enter vẫn lưu. Phím lưu bị khóa trong lúc phát/tải frame/lưu để tránh gán nhãn nhầm. Có bản sửa chưa lưu thì cần lưu trước khi phát. Nếu codec không phát được trong trình duyệt, dùng các nút/phím chuyển từng frame.

Trong **Duyệt sự kiện**: A=Approve, C=Correct, R=Reject, U=Uncertain, N=candidate tiếp. Các phím này không dùng để duyệt hàng loạt frame trong chế độ phát.

## 5. Dataset & Export

Xem bảng AI → Human, thời gian, correction, người QC và trạng thái verified. Click tên video để trở lại đúng lần phân tích/candidate. Các nhóm hard case lấy từ tag QC; Reject tự vào False Candidate.

Chọn **All verified / Only corrected / Only hard cases / Custom selection**, đặt tên và chọn định dạng:

- JSON, CSV và báo cáo QC HTML.
- Event Clips / Video Segments: clip MP4 theo khoảng đã được QC lưu; hai lựa chọn dùng cùng clip sự kiện, không nhân đôi file.
- Frames: ảnh thuộc các khoảng QC; ảnh chưa đủ box nằm riêng trong `frames/`.
- YOLO / COCO: xuất riêng dataset toàn thân (`person`) và đầu (`head`), dùng Custom selection khi project có cả hai. Không trộn hai loại vào một lớp. Chỉ frame có xác nhận đủ box và event verified; ảnh nằm trong `images/`, nhãn trong `labels/` hoặc `annotations.coco.json`.
- Tracking Data: phân biệt AI samples và human frame corrections; không tự gắn cờ đã kiểm identity toàn track.

**Create training dataset** tạo ZIP bằng job nền, có tiến độ và link tải. Có manifest giữ review IDs, định dạng, nguồn và exclusions. Các frame có box review mâu thuẫn không được trộn thành nhãn train. Split vẫn là UNASSIGNED: chia theo nguồn/session trước khi training ở công cụ bên ngoài.

Tool không còn màn hình train trực tiếp, chọn/rollback model, benchmark SUT hay đo công trong workflow chính.

## Lưu trữ, lỗi và giới hạn

- Kho đang dùng: `workspace_data/`, hoặc `CROWD_QC_DATA_ROOT`. Khóa GPU chung ở `.runtime/`. Log của job ở `<data_root>/job_logs`.
- Nguồn và checkpoint phải giữ đúng nội dung để hash truy vết hợp lệ.
- Dữ liệu mô phỏng chỉ kiểm chức năng. Candidate/ước lượng tốc độ không thay thế GT đã kiểm bằng CCTV thật.
- **Dữ liệu thử nghiệm đã được làm mới ngày 29/09/2026**: đã xóa `local_data/`, kho `workspace_data/` cũ, `runs/`, `docs/evidence/`, cache pytest và các dữ liệu tạm Crowd QC. Kho mới dùng video gốc `mall_dataset_reconstructed_5fps.mp4`. ROI toàn khung có diện tích **100 m² giả định để test**, không dùng mật độ này làm kết quả thực tế; chưa có hiệu chuẩn vận tốc. Mã nguồn, môi trường và weights được giữ lại. Các component frontend cũ không thuộc đợt dọn dữ liệu này.

Tài liệu kỹ thuật, sitemap và wireframe: [Kỹ thuật và vận hành](KY_THUAT_VA_VAN_HANH.md).
