import {number} from './shared';

const reasons:Record<string,string>={
 HEAD_CALIBRATION_REQUIRED:'Chưa có hiệu chuẩn tâm đầu',GROUND_CALIBRATION_REQUIRED:'Chưa có hiệu chuẩn mặt sàn',
 HEAD_REFERENCE_HEIGHT_REQUIRED:'Thiếu độ cao tâm đầu tham chiếu',CALIBRATION_PLANE_MISMATCH:'Hiệu chuẩn sai mặt phẳng',
 CALIBRATION_DISTANCE_UNVERIFIED:'Hiệu chuẩn chưa đạt kiểm tra khoảng cách',ROI_OUTSIDE_CALIBRATION:'ROI vượt vùng hiệu chuẩn',
 POINT_OUTSIDE_CALIBRATION:'Điểm đo vượt vùng hiệu chuẩn',SPEED_OR_TRACKING_DISABLED:'Đã tắt speed hoặc tracking',
 TRACK_ID_REQUIRED:'Chưa có track ID',DUPLICATE_TRACK_ID:'Track ID bị trùng trong frame',TRACK_HISTORY_REQUIRED:'Chưa có frame trước cùng track ID',
 PREVIOUS_POINT_OUTSIDE_ROI:'Điểm trước ở ngoài ROI',OUTSIDE_ROI:'Ngoài ROI',TRACK_TIME_GAP:'Khoảng thời gian quá dài hoặc không tăng',
 INVALID_PROJECTION:'Không chiếu được điểm đo',
};
export const motionReason=(value:string|null|undefined)=>value?(reasons[value]||value):'';
export function motionSummary(motion:any) {
 if(!motion)return 'Kết quả cũ; chạy phân tích lại để có chẩn đoán vận tốc.';
 if(!motion.metric_available)return `${motionReason(motion.reason)}. ${motion.enabled?'Nếu đủ lịch sử track, chỉ hiển thị chuyển động px/s.':'Chưa tính chuyển động.'}`;
 return motion.plane==='head'?`Ước lượng m/s trên mặt phẳng tâm đầu ở độ cao tham chiếu ${motion.reference_height_m} m; phụ thuộc chiều cao và tư thế.`:'Ước lượng m/s từ điểm giữa đáy box và hiệu chuẩn mặt sàn.';
}
export default function MotionDetails({row,motion,title='Vận tốc AI từng người / track',expanded=false}:{row:any;motion?:any;title?:string;expanded?:boolean}) {
 const objects=(row?.objects||[]).filter((o:any)=>o.inside!==false);
 return <details open={expanded||undefined}><summary>{title}</summary>
  {motion&&<p className="subtle">{motionSummary(motion)}</p>}
  <p className="subtle">Có m/s: {row?.speed_valid_count??objects.filter((o:any)=>o.speed!=null).length}/{objects.length}. Trung bình toàn ROI chỉ có khi đủ vận tốc của mọi người. px/s phụ thuộc vị trí trong ảnh và không dùng để phân loại đứng yên / tắc nghẽn.</p>
  {objects.length?<div style={{overflowX:'auto'}}><table><thead><tr><th>Track</th><th>m/s</th><th>px/s</th><th>Trạng thái</th></tr></thead><tbody>{objects.map((o:any,i:number)=><tr key={o.id||i}><td>{o.track_id??'Chưa có ID'}</td><td>{number(o.speed)}</td><td>{number(o.speed_px_s)}</td><td>{o.speed!=null?'Ước lượng':motionReason(o.speed_reason)||'Chưa đủ dữ liệu'}</td></tr>)}</tbody></table></div>:<p className="subtle">Không có box trong ROI tại frame này.</p>}
 </details>;
}
