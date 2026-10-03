# Crowd QC — thiết kế 5 màn hình và vận hành

Tài liệu sử dụng: [Hướng dẫn từ đầu](HUONG_DAN_SU_DUNG_TU_DAU.md).

## Phạm vi hiện hành

Theo bản thiết kế người dùng gửi ngày 28/09/2026: QC event từ CCTV là trung tâm; AI tạo candidate, người quyết định. Giữ upload video local, GPU, ROI toàn video, profile camera dùng lại, correction có lịch sử và xuất dữ liệu. Bỏ khỏi frontend các workspace train/model lifecycle, benchmark SUT, pooled camera, effort và các panel vận hành phức tạp. Module/backend cũ còn để tương thích dữ liệu/kiểm thử; không được nạp vào giao diện 5 bước.

## Skill áp dụng và hệ thiết kế

Đã tìm, cài và đọc:

- [Anthropic frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design): định hướng có chủ đích, ưu tiên đặc trưng công việc, kiểm lại bằng screenshot.
- [Vercel web-design-guidelines](https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines): nhãn form, focus, bàn phím, phản hồi bất đồng bộ, điều hướng, responsive. Quy tắc tham chiếu: [Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md).

Trước khi build đã chọn: nền workspace `#F5F7FA`, mặt làm việc `#FFFFFF`, chữ `#243247`, CTA xanh `#2C61CE`, cảnh báo amber `#AF721D`, xác nhận `#15735F`. Video dùng nền tối để vùng ảnh là tâm điểm. Font Segoe UI Variable/Segoe UI theo Windows, số tabular để so sánh count/thời gian. Thanh trái đánh số vì đây là quy trình tuần tự; không dùng animation trang trí, gradient hay dashboard số liệu không phục vụ QC.

## Sitemap và user flow

```text
App
├─ 1. Project & Video
├─ 2. ROI & Camera Setup
├─ 3. Run Analysis
├─ 4. Candidate Review
└─ 5. Dataset & Export
Settings / Hướng dẫn: dialog phụ, không thêm workflow thứ sáu

Camera mới: Create project → Upload → ROI + calibration tùy chọn
            → Save profile → Run analysis → Candidates → QC → Export
Camera có sẵn: Upload → Select matching profile → Run analysis → QC → Export
QC loop: Select candidate → Watch + evidence → Approve/Correct/Reject/Uncertain
         → Save revision → Auto next pending candidate
```

## Wireframe và component hierarchy

### 1. Project & Video

```text
[Sidebar 5 bước] [Breadcrumb / Project selector]
                [Tiêu đề]                    [Tạo project]
                [Project list | Video library                 ]
                [             | Upload video / folder / camera]
                [             | Video · Profile · Analysis/QC ]
                [             | Setup camera / Run analysis   ]
```

`App → Projects → ProjectList + UploadBar + VideoTable + ImportStatus`. Upload nhiều file tuần tự, index bằng worker, poll trạng thái; thông tin phân tích/QC lấy từ API. Bỏ video có confirmation và giữ source/lịch sử.

### 2. ROI & Camera Setup

```text
[Sidebar] [ROI | Calibration] [Camera profile / tên]
          [CCTV frame lớn  ] [ROI list / +        ]
          [Draw/Edit/Pan   ] [Tên + diện tích     ]
          [AB / BC / Test  ] [Lưu Camera Profile  ]
          [Bird-eye view   ] [Continue to Analysis]
```

`App → Camera → Canvas + CalibrationStage + RegionList + ProfileForm`. ROI nhiều vùng, có tọa độ pixel nguồn, không thêm thời gian. Phép chiếu 4 điểm trên hình chữ nhật thật; kiểm 2 điểm độc lập và Bird-eye view. Không có hiệu chuẩn hợp lệ thì speed UNKNOWN, count vẫn dùng được.

### 3. Run Analysis

```text
[Sidebar] [Video + ROI / detection / tracking] [Basic settings]
          [Count / speed / stationary       ] [Advanced      ]
          [Timeline clickable + charts      ] [Run / progress]
          [Open Candidate Review            ] [Cancel / retry]
```

`App → Analysis → Viewer + Timeline + Configuration + PipelineStages`. Cấu hình Basic có FPS/confidence/preset. Module chưa có model ghi rõ chưa có, không giả báo chạy. Detection/ByteTrack/speed/density chạy qua worker GPU; candidate generation theo các đoạn tín hiệu liên tiếp.

### 4. Candidate Review — màn hình trọng tâm

```text
[Sidebar] [Candidates  ] [Video chiếm ưu tiên ] [AI Evidence ]
          [Filter      ] [ROI/box/ID/speed   ] [Peak count  ]
          [Time/status ] [Play/frame/rate   ] [Why + notes ]
          [Add missed  ] [Timeline / jump   ] [Reviewer    ]
          [Approve] [Correct] [Reject] [Uncertain] / Save & Next
```

`App → Review → CandidateRail + Viewer/Canvas + EvidencePanel + DecisionBar`. Correct mở form cùng màn hình. Sửa box tại frame lấy mẫu, xóa FP/thêm người, chỉnh Track ID và đánh dấu đủ box riêng. AI prediction bất biến; QC revisions độc lập. Dùng expected_version chặn ghi đè khi xung đột. Review cấp event không tự cấp nhãn box/track. Không có candidate thì cho thêm đoạn bỏ sót bằng tay.

Phím tắt: Space phát/dừng; ←/→ frame; A approve; C correct; R reject; U uncertain; N next pending. Không bắt phím trong input/textarea/select. Sau lưu thành công mới chuyển next; lỗi giữ correction để sửa hoặc tải lại.

### 5. Dataset & Export

```text
[Sidebar] [Tổng reviewed / Normal / Crowded / Congested / Uncertain]
          [Ground Truth browser / AI → Human] [Scope / formats]
          [Corrections / verified / reviewer] [Create dataset ]
          [Hard-case tags + filters         ] [Jobs / download]
```

`App → Dataset → Summary + GroundTruthTable + HardCaseTags + ExportPanel`. Mở lại đúng analysis/candidate từ review record. Hard cases tổng hợp theo tag/correction; Reject được giữ làm event hard negative. Export là job nền, tạo ZIP có manifest và review lineage.

## Trạng thái và interaction

| Trạng thái | Cách hiển thị / xử lý |
|---|---|
| Loading | Khung thông báo khi tải workspace/màn hình; không hiển thị số DEMO giả làm dữ liệu thật. |
| Empty | CTA tạo project, chọn video, thiết lập camera hoặc chạy phân tích theo đầu vào còn thiếu. |
| Error | Thông báo inline/alert có hành động thử lại hoặc sửa dữ liệu; bản correction chưa lưu được giữ. |
| Processing | Progress upload/index/analysis/export, stage hiện hành, cancel theo loại job. |
| Completed | Video/timeline/candidates hoặc link tải dataset; không auto-approve đề xuất AI. |
| Unsaved | Cảnh báo trước chuyển màn hình/video/project hoặc đóng tab khi còn correction. |
| Conflict | Backend từ chối expected_version cũ; tải lại để đối chiếu, không force overwrite. |

Sidebar chỉ có 5 links, trạng thái màn hình phản ánh trong `?screen=`. Project giữ trong localStorage; media/profile/analysis lấy từ API. Chọn lại lần phân tích theo ROI để review lịch sử. Modal Settings có focus và khóa tương tác nền. Layout ưu tiên desktop 1440–1600 px; dưới 1250 px evidence xuống dưới video; màn hình nhỏ chuyển nav sang cuộn ngang, bảng cuộn trong container.

## Cấu trúc triển khai

```text
frontend/src/
  App.tsx             # Shell / state / navigation / lazy screens
  api.ts              # JSON API + streaming file upload progress
  Canvas.tsx          # ROI và sửa box trên tọa độ ảnh gốc
  qc/
    Projects.tsx      Camera.tsx      Analysis.tsx
    Review.tsx        Dataset.tsx
    Viewer.tsx        shared.tsx
  style.css           # Token và layout duy nhất của UI mới
backend/app/qc/
  api.py              # profiles, analysis, decisions, export endpoints
  analysis.py         # projection, metrics và event proposals
  worker.py           # isolated GPU analysis / export worker
```

Dùng React hiện có, không thêm framework. Các màn hình tải lazy; bundle chính khoảng 165 kB chưa gzip, Canvas tách riêng khoảng 301 kB. Không còn cảnh báo chunk >500 kB ở build mới.

Dùng WorkArtifact + WorkPointer hiện có để lưu profile/version/assignment, analysis, quyết định QC, candidate thủ công và manifest export; không cần sửa destructive schema. Profile và analysis đóng băng dữ liệu đầu vào. Kết quả quyết định giữ previous_review_id, reviewer, expected_version, nhãn AI, nhãn người, corrections và trạng thái verified.

Pipeline dùng YOLO11n GPU, ByteTrack khi bật tracking, box-bottom ROI filtering và homography để ước lượng tốc độ khi đủ kiểm tra khoảng cách/phạm vi calibration. Những số này vẫn là AI estimates, không phải phép đo GT tự động. FPS sampling dùng PTS đã index; không cắt im lặng tại giới hạn 300 frame cũ.

## Export và tính toàn vẹn

- JSON/CSV/QC report chứa nhãn AI và human riêng. HTML escape nội dung, CSV chặn chuỗi mở đầu công thức.
- MP4 clips cắt theo khoảng QC bằng FFmpeg; Frames có source frame IDs.
- Chỉ frame có box review đầy đủ và event verified mới vào `images/` và labels YOLO/COCO. Ảnh chưa đủ box ở `frames/`, không bị YOLO hiểu nhầm là negative.
- Box revisions mâu thuẫn trên cùng frame bị loại khỏi nhãn training, có lý do trong manifest. Ground Truth event không tự cấp verified tracking identity.
- Split ở trạng thái UNASSIGNED; người dùng chia source/session trước training bên ngoài. DEMO giữ loại nguồn DEMO.

## Vận hành và kiểm thử

Giữ `.venv-app`, `.venv-gpu`, model `weights/yolo11n.pt` và các lock. Kho mặc định `workspace_data/`, có thể đổi qua `CROWD_QC_DATA_ROOT`. Khóa GPU chung `.runtime/`; mọi worker dùng chung. `CROWD_QC_WORKER_PYTHON` đổi Python worker nếu đã cài đầy đủ dependencies. FFmpeg/ffprobe phải nằm trên PATH.

```powershell
.venv-app/Scripts/python.exe -m pytest tests -q
cd frontend
npm run build
npm run test:e2e
```

E2E cần Chrome, GPU và server chạy với kho TEMP mới (đặt `CROWD_QC_DATA_ROOT` trước khi chạy server); script tự tạo video sample DEMO trong TEMP, sau đó thêm project để kiểm workflow. Không dùng kho dữ liệu thật để chạy smoke. Screenshot, ZIP và kết quả kiểm thử mới đều nằm ngoài repo trong TEMP để không làm tăng file docs/evidence.

Kiểm chứng đợt thay đổi: 88 Python tests đạt; smoke trình duyệt chạy thực GPU → candidate → correction/Reject → ZIP gồm CSV/JSON/report/clip/frame/YOLO/COCO/tracking; reload giữ kết quả, 5 mục navigation, mobile không tràn trang. DEMO kiểm cơ chế, chưa nghiệm thu chất lượng CCTV/calibration/sự kiện thực tế.

## Dọn tài liệu và file cũ

Ngày 29/09/2026 đã kiểm tra đồ thị import từ `frontend/src/main.tsx` và xóa 12 file frontend không còn được dùng. Đồng thời dọn 21 tài liệu/báo cáo trong thư mục archive, kế hoạch triển khai cũ, manifest M0 rỗng, bản ghi dependency M0, 10 script demo/kiểm chứng cũ và 9 bài test trình duyệt của giao diện cũ. README và hai tài liệu hiện hành là đầu mối tra cứu. Giữ bộ test backend, test `five_screen.mjs`, script cài/chạy/vận hành, các dependency lock hiện hành và dữ liệu Mall.

Ngày 29/09/2026, theo yêu cầu xóa toàn bộ dữ liệu test, đã dừng server và xóa vật lý `local_data/`, `workspace_data/` cũ, `runs/`, `docs/evidence/`, `.pytest_cache/` cùng dữ liệu tạm Crowd QC. Kho `workspace_data/` mới dùng video Mall gốc (SHA256 `ba1e7fe38d8e3cf8d862fd92767182786feacbd1e6b67f7dba5afe89afdec593`), 640×480, 2.000 frame, 400 giây. ROI toàn khung có diện tích 100 m² giả định, evidence DEMO, tắt tốc độ vì chưa có hiệu chuẩn; không coi mật độ/nhãn AI là ground truth.


## Inference theo ROI có vùng đệm

Worker QC dùng `backend/app/qc/inference.py`: crop khung bao ROI cộng padding trái/phải 15% chiều rộng, trên 20% và dưới 10% chiều cao khung bao; làm tròn lên pixel và giới hạn trong nguồn. Không mask cứng theo đa giác để giữ cơ thể ở cả mép chéo/lõm. Không có lượt inference toàn frame phụ; vùng cắt có thể bằng frame nếu ROI cùng vùng đệm phủ hết ảnh.

ByteTrack nhận crop cố định và có thể dùng detection vùng đệm; sau tracking chỉ lưu người có điểm giữa đáy box thuộc ROI gốc (kể cả biên). Box được dịch về tọa độ video gốc, không cắt box theo ROI; count/density/speed vẫn dùng ROI và diện tích gốc. GT/export giữ hệ tọa độ nguồn. Padding cố định không bảo đảm đủ mọi cơ thể; mép video không có thêm pixel để khôi phục.

Tensor H×W làm tròn lên bội 32 theo tỷ lệ 640 / cạnh dài frame nguồn, không phóng mọi crop lên 640. CNN tính cả hình chữ nhật vùng cắt nên padding có thêm chi phí; mức tiết kiệm phụ thuộc ROI. Giải mã video vẫn cần frame gốc.

Artifact mới có `inference.mode=ROI_CROP_CONTEXT`, bounds ROI/crop, padding theo tỷ lệ và số pixel thực sau clipping, kích thước tensor, thời gian crop/predict/track. Giao diện phân biệt ba loại kết quả: context crop mới, strict masked crop cũ và toàn frame cũ. Chạy lại để áp dụng, không sửa lịch sử.

Kiểm chứng: 11 tests ROI/workflow đạt, bao gồm ảnh thực truyền vào model nhỏ hơn nguồn và có context, box vươn ngoài ROI nhưng chân trong vùng vẫn được giữ, người chỉ nằm trong padding bị loại, clipping ở mép video, tọa độ/speed và persistence/export. Số đo benchmark strict crop trước đây không đại diện cho chế độ có padding mới.


### Sửa thiếu detection và tốc độ đọc video

Không gọi `model.track()` để lấy danh sách người: wrapper này thay detection bằng các track được xác nhận. Worker gọi `predict()` một lần, dùng ByteTrack riêng để gắn ID theo chỉ số detection, giữ cả detection chưa có ID. Count vẫn theo ROI gốc, speed chỉ có khi ID và lịch sử hợp lệ. Không đổi YOLO11n, confidence hoặc kích thước tensor trong lần sửa này.

`qc/decoding.py` mở video một lần và giải mã tuần tự, lấy đúng PTS đã lập chỉ mục; không seek/mã hóa PNG/ghi cache cho từng mẫu. Có heartbeat, kiểm tra PTS/kích thước và đóng container khi hủy/lỗi. Artifact ghi riêng `decode_seconds`, `crop_predict_track_seconds`, `decode_mode` và `detection_policy` để truy vết.

So sánh trên video Mall hiện có, cùng 800 mẫu/PTS, ROI, confidence và GPU: lần trước sửa trong workspace mất 427,2 giây; lần kiểm tra mới trong kho TEMP mất 49,1 giây (không gồm import). Trong đó decode 10,5 giây, crop/detect/track 31,0 giây. Trung bình box trong ROI từ 2,91 lên 6,82/mẫu; đây là số detection, không chứng minh recall/precision do chưa có GT. Lần AUTO_LABEL cũ chỉ có 101 mẫu nên không dùng tổng thời gian của nó để so trực tiếp. Không ghi đè kết quả/GT của người dùng. 13 tests ROI/workflow đạt, smoke toàn bộ 800 mẫu trên CUDA đạt, frontend build đạt. Báo cáo đo ở TEMP `crowd_qc_sequential_result.json`.


### Review từng frame

`qc/FrameEditor.tsx` là chế độ mặc định của Review: tải frame theo ordinal, AI box chỉ lấy khi trùng frame ID, không lấy mẫu gần nhất. Canvas tách pan khỏi chọn/kéo box. Nút Lưu hoặc Lưu & frame tiếp xác nhận box và trạng thái frame, không cần reviewer/notes (API mặc định Local QC).

Mỗi frame được duyệt tạo/reuse candidate thủ công gắn frame_id, thời gian start=end theo PTS nguồn. API kiểm tra frame thuộc video, range và box; review có optimistic version, giữ lịch sử. Frame không thuộc mẫu inference được lưu metadata trong frame_samples để export không bỏ sót. Export YOLO/COCO dùng chính ảnh và nhãn frame đã xác nhận. Event clips bỏ qua review có thời lượng 0. Thao tác này không nội suy nhãn giữa các frame.


### Bố cục review và phát nhanh

Theo frontend-design: giữ màu nền #F5F7FA, panel #FFFFFF, ảnh trên #182536, thao tác chính #3265C7, box #F1C46B, ROI #6EDBC4; Segoe UI như ứng dụng hiện có. Ảnh là vùng chính, bảng chỉ số/công cụ 302px bên phải, transport dưới ảnh. Mobile xếp chỉ số dưới ảnh. CSS scoped frame-review để không đổi layout các chức năng khác.

FrameEditor dùng video nguồn khi phát nhanh (0,25–4×), không tải PNG từng frame trong lúc phát. Overlay AI gần nhất có timestamp riêng; pause hoặc B/V/H dừng và lấy frame theo PTS nguồn tại/sau thời điểm dừng. Chỉ ảnh chính xác được dùng để sửa/lưu. Lưu bị khóa khi playing/seeking/loading/busy. Bản sửa chưa lưu chặn phát; shortcut nhập liệu được giới hạn theo focus. Không tự lưu hay approve khi phát video.

Build đạt; browser smoke trong kho TEMP xác nhận hai cột/transport, playback 2×, B dừng vào sửa, Arrow chuyển frame, cảnh báo draft, Ctrl+Enter lưu và chuyển frame, mobile không tràn và không có lỗi JavaScript. Screenshot kiểm tra trong TEMP crowd_qc_review_layout.png và crowd_qc_review_mobile.png.


## Rà soát tối ưu dự án — 28/09/2026

Phạm vi: luồng upload/import, API/SQLite, inference/tracking, frontend/review, export, môi trường và kiểm thử. Đã kiểm tra các đường chạy chính và chạy toàn bộ bộ kiểm thử; không coi đây là chứng nhận chất lượng model hay kiểm toán bảo mật từng dòng mã. Benchmark dùng bản sao SQLite trong TEMP, thêm 150 review giả lập; video nguồn chỉ đọc, không ghi đè nhãn/project thật.

### Đã sửa trong đợt rà soát

| Khu vực | Vấn đề | Thay đổi và kiểm chứng |
|---|---|---|
| Workspace | Lấy từng review riêng, đọc cả review project khác, giải mã toàn bộ lịch sử frame/box chỉ để hiện danh sách video | JOIN review theo project; lấy metadata của analysis bằng JSON projection. 311 → 11 truy vấn; cùng response 123.963 byte. Trung vị đo tham khảo 199 → 36,5 ms. |
| API analysis | Truy vấn riêng pointer/artifact cho từng candidate | Gom JOIN một lần, 303 → 3 truy vấn. Toàn response vẫn 4.135.673 byte và khoảng 716 ms vì vẫn có JSON lớn; không tuyên bố tăng tốc toàn bộ tải lần đầu. |
| Lưu review | Tải lại toàn bộ rows sau mỗi lần lưu | `include_rows=false` trả candidates/review/version; frontend giữ rows bất biến, merge theo analysis ID. 4.135.673 → 150.080 byte cho phần analysis refresh (~96,4% ít hơn), khoảng 172 ms trong phép đo. Workspace vẫn refresh để cập nhật thống kê/dataset. |
| Theo dõi job | Poll 1,5 s tải cả project/workspace/history | Chỉ poll jobs, refresh workspace khi thấy job kết thúc; không chồng các request của cùng vòng polling. Browser kiểm chứng chỉ 2 lần workspace: mở trang và job hoàn tất. |
| Export | Hash video và nạp lại analysis theo từng review | Cache analysis/source trong phạm vi một export, hash đầy đủ một lần trước và một lần sau cho mỗi nguồn; kiểm stat giữa các review. Test 4 review cùng nguồn chỉ có 2 lần full hash. Giữ phát hiện source thay đổi và kiểm tra xung đột box. |

Đây là tối ưu I/O/query/network, không đổi model, confidence, ROI, FPS, ngưỡng event hoặc chính sách vận tốc. Đường inference hiện dùng decode tuần tự; dữ liệu review không bị tracker loại bỏ. GPU lease vẫn chỉ cho một worker GPU chạy cùng lúc.

### Phần đã kiểm tra và hướng tiếp theo

| Ưu tiên | Khu vực | Nhận xét / bước tiếp theo |
|---|---|---|
| Cao khi dữ liệu tăng | Lưu/tải analysis | Chuẩn hóa hoặc chia chunk rows theo analysis/frame, phân trang API và load cửa sổ đang xem. Hiện SQLite JSON chứa toàn bộ rows; kể cả compact refresh vẫn phải đọc một artifact lớn ở server. Cần migration, tương thích lịch sử và test seek/export. |
| Cao với nhiều frame xuất | Export ảnh/clip | Vẫn seek từng ảnh, tạo PNG rồi JPEG và chạy FFmpeg cho từng clip. Gom frame theo nguồn/PTS và decode tuần tự khi đủ dày; seek riêng tốt hơn nếu rất thưa. Cần benchmark mật độ chọn mẫu để chọn chiến lược, giữ đúng PTS/VFR. |
| Cao về chất lượng | Tracking/vận tốc | FPS lấy mẫu 2 có thể làm đứt track. Có thể tách FPS tracking khỏi FPS lưu/QC và đo độ bao phủ track. Cần video có GT, hiệu chuẩn đạt; không lấy người không có ID làm đứng yên. Không bật thay đổi FPS tự động trong đợt này. |
| Trung bình | GPU | Thử FP16, batch detection hoặc checkpoint mạnh hơn trên cùng đoạn CCTV/GT, báo cả thời gian và recall. Không tăng batch cho tracking mà làm sai thứ tự thời gian; không giảm chất lượng chỉ để tăng FPS. |
| Trung bình | Review frontend | FrameEditor/Viewer còn tìm mẫu gần nhất và candidate trong mảng; khi analysis rất lớn nên map theo ID, binary search theo time, virtualize danh sách. Native video đang dùng cho phát nhanh, PNG chỉ dùng khi dừng sửa. |
| Trung bình | Upload/import | Upload stream không giữ cả video trong RAM; import ghi index theo batch 1.000. Nhiều video có thể tạo nhiều subprocess import tranh CPU/ổ đĩa: nên hàng đợi giới hạn đồng thời, kèm hủy/progress. Hash/PTS là kiểm chứng dữ liệu nên không bỏ tùy tiện. |
| Trung bình | API jobs/dataset | API jobs gọi kiểm trạng thái riêng tối đa 25 lịch sử + active jobs. Workspace cũng vẫn trả tất cả review/profile/export. Khi lớn nên tách summary/pagination, batch đọc jobs và chỉ kiểm recovery cho job active. |
| Thấp | Cấu trúc code | Một số TSX dài, nhiều state/effect liên quan; nên tách hook lưu/review/playback và thêm bài kiểm browser có fixture riêng. Component frontend cũ đã xóa; API legacy còn để tương thích và được kiểm tra bằng bộ test backend. |
| Duy trì | Môi trường | `pip check` của .venv-app và .venv-gpu đều đạt; frontend build chia chunk, chunk lớn nhất Canvas khoảng 301 KB. Không nâng dependency/model đồng loạt trong đợt tối ưu. |

Kiểm chứng: 96 tests toàn bộ đạt trước khi thêm 2 regression tests; 10 tests QC workflow (gồm 2 test mới) đạt sau đó. Frontend build đạt. Browser smoke xác nhận compact refresh giữ rows/nhãn, chuyển frame sau lưu, polling chỉ refresh workspace khi hoàn tất và không lỗi JavaScript. Có một cảnh báo deprecation từ Starlette/AnyIO, chưa phải lỗi chức năng. Benchmark chi tiết: TEMP `crowd_qc_audit.json`; dữ liệu đo giả lập không trộn vào workspace chính. Thời gian là số đo cục bộ, phụ thuộc tải máy; query count và response size là chỉ số ổn định hơn.


## Chọn detector toàn thân / đầu — 29/09/2026

Settings.detection_target = person | head, mặc định person để tương thích lịch sử. `qc/models.py` chọn checkpoint cố định; head config ở `configs/head_model.json` giữ nguồn, revision Hugging Face, SHA-256, lớp và giấy phép. File tải đã đối chiếu SHA-256 với LFS oid của revision c2cb4f015cf7bf07b7baeef5bef7075c1aa5148a. Worker kiểm hash và names[0] cùng task detect, không fallback sang model khác hoặc tạo box đầu từ box người. Artifact lưu detector id/target/hash; mỗi object có label_type. Review từ chối label_type không khớp run.

Model đầu: YOLOv8n HollywoodHeads, nguồn https://huggingface.co/abhiWanKenobi/yolov8n_head_detection, CC-BY-NC-4.0. Không chạy script từ repo ngoài; dùng checkpoint qua Ultralytics đã cài. Head dùng tâm box cho membership. Từ 30/09/2026, speed/stationary m/s của head dùng `head_calibration` riêng trên mặt phẳng tâm đầu; không dùng hiệu chuẩn sàn. Person dùng `calibration` mặt sàn. Cả hai dùng bộ đo chung tại `qc/motion.py`, có px/s khi thiếu hiệu chuẩn và API `/api/qc/analysis/{id}/motion-preview` cho box đã chỉnh. Chi tiết ở [Đo vận tốc ROI](DO_VAN_TOC_ROI.md).

Analysis/Viewer/FrameEditor/lịch sử chạy đọc loại nhãn từ artifact, không từ dropdown cấu hình đang sửa. Dataset hiển thị loại box; JSON/CSV ghi detection_target, manifest ghi detection_targets. YOLO/COCO chỉ nhận cùng một loại nhãn mỗi lần export, class 0 đặt tên person hoặc head đúng target. Chọn kết quả cùng loại bằng Custom selection. Hash/GT/revision vẫn giữ nguyên.

Kiểm chứng: 100 pytest tests đạt, frontend build đạt; hai worker CUDA trên cùng đoạn Mall 3 giây, 6 mẫu mỗi loại đều tạo box thật và metadata đúng. Test browser xác nhận dropdown, đổi history, giữ nhãn run cũ khi đổi cấu hình và lưu correction head. Test export xác nhận YAML/COCO tên head, chặn trộn hai loại, kiểm ROI bằng tâm đầu và không sinh tốc độ sàn giả. Đây là kiểm chứng chức năng, không phải benchmark recall/precision CCTV.
