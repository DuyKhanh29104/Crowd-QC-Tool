/** Optional experiments preserve saved profiles and the current detector defaults. */
export default function InferenceOptions({settings,update}:{settings:any;update:(key:string,value:any)=>void}) {
 return <>
  <label>Scale ảnh cắt ROI<select value={settings.roi_scale_mode??'source'} onChange={e=>update('roi_scale_mode',e.target.value)}>
   <option value="source">Giữ tỷ lệ theo toàn ảnh (hiện tại)</option>
   <option value="crop">Theo cạnh dài ROI crop · thử nghiệm người nhỏ</option>
  </select></label>
  <small>Scale theo crop tăng chi tiết và chi phí GPU. Kiểm tra box trên video trước khi đổi profile.</small>
  {([['iou','NMS IoU',.7,0.01,1,.01],['max_det','Số detection tối đa',300,1,5000,1],
    ['roi_margin_side','Đệm hai bên ROI (tỷ lệ)',.15,0,.5,.01],
    ['roi_margin_top','Đệm phía trên ROI (tỷ lệ)',.2,0,.5,.01],
    ['roi_margin_bottom','Đệm phía dưới ROI (tỷ lệ)',.1,0,.5,.01],
    ['track_high_thresh','Ngưỡng tracking cao',.25,.01,1,.01],
    ['track_low_thresh','Ngưỡng tracking thấp',.1,0,.99,.01],
    ['new_track_thresh','Ngưỡng tạo track mới',.25,.01,1,.01],
    ['match_thresh','Ngưỡng ghép track',.8,.01,1,.01]] as const).map(([key,label,fallback,min,max,step])=>
   <label key={key}>{label}<input type="number" min={min} max={max} step={step} value={settings[key]??fallback} onChange={e=>update(key,Number(e.target.value))}/></label>)}
  <label>Giữ track mất tạm thời (giây)<input type="number" min=".1" max="10" step=".1" placeholder="Mặc định: 30 frame lấy mẫu" value={settings.track_buffer_seconds??''} onChange={e=>update('track_buffer_seconds',e.target.value===''?null:Number(e.target.value))}/></label>
  <label><input type="checkbox" checked={settings.track_use_low_conf??false} onChange={e=>update('track_use_low_conf',e.target.checked)}/>Dùng detection dưới confidence để duy trì track</label>
  <small>Detection dưới confidence không được thêm vào count hay nhãn QC.</small>
 </>;
}
