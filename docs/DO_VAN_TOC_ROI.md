# Đo vận tốc người trong ROI — toàn thân và box đầu

Cập nhật 30/09/2026. Hai loại box đều có luồng đo tự động qua tracking và luồng so sánh hai frame từ nhãn đã chỉnh.

## Kết quả rà soát

Trước thay đổi, toàn thân đã dùng điểm giữa đáy box và homography mặt sàn để tính tốc độ. Box đầu bị tắt speed tại backend và UI. Việc sửa tọa độ box không tính lại vận tốc; object gửi từ trình duyệt còn có thể mang các trường vận tốc AI cũ.

Hiện tại, backend dùng chung bộ đo chuyển động, chọn đúng mặt phẳng theo loại box. Khi lưu correction, các trường vận tốc/anchor suy ra từ box cũ được loại bỏ để không bị hiểu nhầm là phép đo từ box đã sửa. Kết quả AI gốc được giữ để đối chiếu.

| Loại nhãn | Điểm theo dõi | Hiệu chuẩn m/s |
| --- | --- | --- |
| Toàn thân (`person`) | Giữa đáy box | Tứ giác lồi có góc A vuông hoặc hình chữ nhật trên mặt sàn |
| Đầu (`head`) | Tâm box đầu | Tứ giác lồi có góc A vuông hoặc hình chữ nhật trên mặt phẳng ngang tại độ cao tâm đầu tham chiếu |

Homography áp dụng cho các điểm trên mặt phẳng được hiệu chuẩn. Do đó không lấy tâm đầu nhân trực tiếp với homography mặt sàn. Cơ sở hình học: [OpenCV — homography và cấu trúc phẳng](https://docs.opencv.org/4.5.1/d9/dab/tutorial_homography.html).

## Thiết lập để đo tự động

1. Vào **ROI & Camera Setup**, tạo ROI phù hợp điểm theo dõi: vị trí chân cho toàn thân, vị trí tâm đầu cho nhãn đầu. Có thể lưu hai ROI riêng.
2. Với toàn thân, mở **Hiệu chuẩn mặt sàn**, chọn A → B → C → D theo thứ tự quanh vùng thực. Mặc định **Dạng vùng hiệu chuẩn** là **Tứ giác · góc A vuông · 4 cạnh**: AB phải vuông góc AD ngoài thực tế, nhập AB, BC, CD, DA theo mét. Không cần đo đường chéo. Nếu vùng thực là hình chữ nhật, có thể chọn **Hình chữ nhật · 2 cạnh** và chỉ nhập AB, BC.
3. Với box đầu, mở **Hiệu chuẩn tâm đầu**, chọn dạng vùng và nhập số đo tương tự, cùng **Độ cao tâm đầu tham chiếu (m)**. Bốn mốc phải nằm trên một mặt phẳng ngang ở cùng độ cao tâm đầu đã đo. Có thể cho người tham chiếu đứng thẳng lần lượt tại A, B, C, D đã đo trên sàn, rồi chọn tâm đầu tương ứng ở từng frame bằng ô **Frame hiệu chuẩn**. Camera và nền tọa độ phải cố định; chiều cao tâm đầu cần nhất quán.
4. Chọn thêm hai điểm kiểm độc lập trong tứ giác, không trùng bốn góc, ở đúng mặt phẳng đang đo. Nhập khoảng cách thực. **Kiểm tra hiệu chuẩn** chỉ đạt nếu sai số không vượt `max(0,05 m; 2% khoảng cách kiểm)`.
5. **Lưu Camera Profile**. Profile lưu riêng `calibration` và `head_calibration`; chiều cao tham chiếu chỉ mô tả mặt phẳng đầu, không tự chuyển phép chiếu sàn thành phép chiếu đầu.
6. Vào **Run Analysis**, chọn đúng loại detection, ROI, bật **Person tracking** và **Speed & stationary estimation**, rồi chạy lại. Kết quả cũ giữ nguyên snapshot cấu hình; đổi profile không tính lại kết quả cũ.
7. Mở **Vận tốc AI từng người / track** để xem m/s, px/s và lý do thiếu dữ liệu. Overlay ưu tiên m/s; nếu chưa có hiệu chuẩn phù hợp nhưng có track thì hiển thị px/s.

Toàn bộ ROI đo m/s phải nằm trong tứ giác hiệu chuẩn. Nếu không đạt, hệ thống báo lý do và chỉ đo px/s khi đủ lịch sử, thay vì ngoại suy ra ngoài vùng đã hiệu chuẩn. Thiếu số đo thực vẫn chạy count/mật độ/tracking được.

### Tứ giác có một góc vuông tại A

Luồng mới yêu cầu **AB ⟂ AD ngoài thực tế**, với A → B → C → D đi quanh một tứ giác lồi. Góc A trong ảnh có thể không nhìn vuông do phối cảnh. Hệ thống dùng khai báo của người đo; không thể xác nhận góc thực bằng cách kiểm tra góc trên ảnh.

Chỉ cần **bốn cạnh AB, BC, CD, DA**. Bộ dựng đặt A=(0,0), B=(AB,0), D=(0,DA), suy ra BD theo định lý Pythagoras, rồi tìm C là giao hai đường tròn tâm B, D với bán kính BC, CD. Chọn C ở phía đối diện A qua BD và từ chối số đo không tạo được tứ giác lồi, không suy biến. Không yêu cầu các góc B, C, D vuông.

Ví dụ: A=(0,0), B=(5,0), C=(6,3), D=(0,4) m. Nhập AB=5, BC≈3,1623, CD≈6,0828, DA=4 m. AC≈6,7082 và BD≈6,4031 m được tính tự động, không phải số đo kiểm độc lập.

Profile mới lưu `shape=right_angle`, bốn `lengths_m`, `world_points` và `construction` ghi rõ góc vuông do người dùng khai báo. Bốn cạnh là số đo để dựng hình; vẫn cần **một khoảng cách kiểm bằng hai điểm độc lập** trước khi bật m/s. Các đường chéo suy ra không thay thế bước kiểm này.

Với tứ giác, `width_m`/`height_m` trong kết quả là kích thước khung bao tọa độ thực. Bird-eye view giữ đúng hình dạng và cùng tỷ lệ mét trên cả hai trục; phần ngoài tứ giác được che. Luồng này dùng chung cho hiệu chuẩn sàn và mặt phẳng tâm đầu.

Profile hình chữ nhật cũ và profile tứ giác sáu số đo cũ vẫn được đọc theo đúng mô hình đã lưu, không tự đổi giả thiết hình học. Khi sửa profile tứ giác cũ, sáu ô số đo vẫn xuất hiện; chuyển sang góc A vuông sẽ bỏ hai đường chéo nhập tay và cần kiểm tra lại.

## Đo từ box tự gán hoặc đã sửa

Trong **Candidate Review → Nhãn từng frame**:

1. Chọn một frame, sửa/thêm box và gán Track ID cho người cần đo. Cùng một người phải có cùng ID ở hai frame; trong một frame mỗi ID chỉ có một box.
2. Mở **Đo vận tốc từ box đã chỉnh**, bấm **Đặt frame hiện tại làm mốc**. Đây là bản sao box tại thời điểm bấm; sửa frame mốc sau đó thì cần đặt lại mốc.
3. Lưu nhãn nếu muốn giữ thay đổi, rồi chuyển đến frame sau, sửa box và gán cùng Track ID.
4. Bấm **Tính vận tốc với frame mốc**. API dùng ID/PTS frame đã được lập chỉ mục, không nhận timestamp do trình duyệt tự khai.
5. Xem bảng **Kết quả từ box đã chỉnh** và **Tải phép đo JSON**. Bản JSON có hai snapshot box, nguồn, frame/PTS/timebase, mặt phẳng, phép chiếu, độ cao tham chiếu, khoảng thời gian và kết quả.

Đây là phép đo thử (`MANUAL_PAIR_ESTIMATE_NOT_GT`): không ghi đè vận tốc AI, không tự xác nhận identity toàn track và không được tự đưa vào GT. Lưu frame vẫn là thao tác riêng. Kết quả thử được tải thành JSON; không tự lưu thành artifact phép đo trong database. Không có tracker AI vẫn có thể dùng hai frame với Track ID gán thủ công.

## Công thức và điều kiện

Với cùng một track tại hai thời điểm nguồn `t0 < t1`:

```text
person: p = ((x1+x2)/2, y2)
head:   p = ((x1+x2)/2, (y1+y2)/2)

P = project(H_of_correct_plane, p)
velocity_mps = (P1 - P0) / (t1 - t0)
speed        = norm(velocity_mps)

velocity_px_s = (p1 - p0) / (t1 - t0)
speed_px_s    = norm(velocity_px_s)
```

Vector m/s dùng trục X theo A→B, trục Y vuông góc với AB và hướng về phía C, D. Với tứ giác góc A vuông và hình chữ nhật, Y trùng hướng A→D; với profile tứ giác sáu số đo cũ thì không nhất thiết. Vector px/s dùng trục ảnh X sang phải, Y xuống dưới. Thời gian lấy từ PTS/timebase nguồn, không lấy từ tốc độ phát hay giả định FPS cố định.

- Cả hai điểm phải ở trong ROI. Điểm đầu mới xuất hiện, ID đổi, ID trùng hoặc điểm trước ngoài ROI không tạo ra vận tốc bằng 0.
- Giữ chính sách khoảng cách thời gian tối đa `2,1 / sampling_fps` giây (mặc định 2 FPS: 1,05 giây), kể cả khi so sánh nhãn thủ công. Chọn hai frame đủ gần và có thứ tự thời gian tăng.
- Tốc độ toàn nhóm và tỷ lệ đứng yên chỉ có khi mọi người được đếm trong ROI đều có m/s. Trạng thái thiếu của một người không bị loại khỏi mẫu số để làm đẹp số trung bình.
- px/s không dùng với ngưỡng m/s để suy đứng yên/tắc nghẽn. Có thể xem chuyển động ảnh khi chưa hiệu chuẩn, nhưng không so trực tiếp tốc độ người gần/xa camera.
- `Tracking Data` export giữ các mẫu AI, vector/tốc độ với đơn vị và metadata phép đo riêng với correction của con người.

## Giới hạn cần biết

Tốc độ từ box đầu là xấp xỉ theo mặt phẳng độ cao tham chiếu. Người có chiều cao khác, cúi đầu, chuyển động đầu, bậc thang, sàn không phẳng hoặc camera di chuyển làm sai lệch phép đo. Điểm giữa đáy box toàn thân cũng chỉ là ước lượng điểm tiếp xúc sàn; box bị che khuất/cắt chân có thể gây lỗi. Homography không tự xử lý méo ống kính lớn.

Bộ đo hiện dùng độ dịch chuyển giữa hai điểm, chưa làm trơn theo cửa sổ thời gian. Với quỹ đạo cong, đó không phải tổng chiều dài đường đi; nhiễu box hoặc tracker đổi identity vẫn có thể làm tốc độ tăng giả. Kiểm tra bằng quãng đường/thời gian thực và identity trên CCTV của camera trước khi dùng làm số liệu đánh giá. Luồng GT có kiểm identity và track ở `workflows/motion.py` vẫn độc lập, không được tự động đánh dấu đạt bởi kết quả QC này.

## Kiểm chứng triển khai

- **164 kiểm thử backend đạt** (một warning, không có lỗi); kiểm thử luồng Chrome và TypeScript/production build đạt. Hiệu chuẩn góc A vuông có kiểm thử dựng hình từ bốn cạnh, ảnh phối cảnh không vuông, số đo không hợp lệ, lưu profile và đo vận tốc cho cả toàn thân lẫn đầu. Các kiểm thử profile hình chữ nhật và tứ giác sáu số đo cũ vẫn được giữ. Luồng Chrome kiểm tra mặc định mới, nhập bốn cạnh, lưu profile và đo chuyển động; TypeScript/production build cũng được kiểm tra.
- Kiểm thử số học cho cả hai mặt phẳng: dịch chuyển 0,3 m và 0,4 m trong 0,5 s cho vector `[0,6; 0,8] m/s` và độ lớn 1 m/s.
- Kiểm thử API, worker, export: head chọn phép chiếu khác ground; giữ frame/PTS, không ghi đè AI, chặn sai loại box/thời gian/toạ độ; chặn vận tốc thiếu/ID trùng/khoảng mất dấu; bỏ số đo AI cũ trong correction.
- Kiểm thử Chrome: nhập hiệu chuẩn đầu, giữ hiệu chuẩn sàn, bật speed cho head, so sánh hai frame đã chỉnh và tải JSON.
- Hai model thật đã chạy trên `cuda:0` trong kho DEMO riêng: 15 mẫu/mỗi model; toàn thân có tối đa 4 người và 56 số đo m/s, đầu có tối đa 3 đầu và 42 số đo m/s. Video lặp lại ảnh đứng yên nên tốc độ bằng 0. Đây là smoke test luồng GPU, không phải đo độ chính xác vận tốc người đi trên CCTV.
- Bằng chứng chạy GPU: `.runtime/motion-gpu-report.json`; ảnh kiểm UI: `.runtime/motion-review.png`.

Lệnh kiểm tra:

```powershell
.\.venv-app\Scripts\python.exe -m pytest tests -q
npm --prefix frontend run test:motion
npm --prefix frontend run test:viewer
npm --prefix frontend run test:unit
npm --prefix frontend run build
```

Khởi động lại backend đang chạy để nạp API mới, sau đó tải lại giao diện. Không cần migration database hoặc thay model; dữ liệu hiệu chuẩn mới nằm trong payload Camera Profile.
