import {useEffect,useRef,useState} from 'react';
import {api,Frame} from '../api';
import MotionDetails,{motionSummary} from './MotionDetails';

export default function MotionReview({analysis,frame,objects,disabled}:{analysis:any;frame?:Frame;objects:any[];disabled:boolean}) {
 const [reference,setReference]=useState<{frame:Frame;objects:any[]}>(),[result,setResult]=useState<any>(),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const revision=useRef(0);
 useEffect(()=>{revision.current++;setResult(undefined);setError('');},[frame?.id,objects,reference,analysis.id]);
 useEffect(()=>()=>{revision.current++;},[]);
 const target=analysis.detector?.target||analysis.settings.detection_target||'person';
 const snapshot=(items:any[])=>items.map(o=>({id:o.id,box:[...o.box],track_id:o.track_id||null,label_type:target}));
 const measure=async()=>{
  if(!frame||!reference)return;
  const version=revision.current;setBusy(true);setError('');
  try {const value=await api(`/qc/analysis/${analysis.id}/motion-preview`,{frames:[{frame_id:reference.frame.id,objects:reference.objects},{frame_id:frame.id,objects:snapshot(objects)}]});if(version===revision.current)setResult(value);}
  catch(e){if(version===revision.current)setError((e as Error).message);}finally{setBusy(false);}
 };
 const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`motion-${analysis.id}-${frame?.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 return <details><summary>Đo vận tốc từ box đã chỉnh</summary>
  <p className="subtle">Gán cùng Track ID cho cùng một người ở hai frame. Chọn frame mốc trước, sau đó chuyển đến frame cần đo. Mỗi ID chỉ xuất hiện một lần trong mỗi frame.</p>
  <button disabled={disabled||busy||!frame} onClick={()=>{if(frame)setReference({frame,objects:snapshot(objects)});}}>Đặt frame hiện tại làm mốc</button>
  {reference&&<><p className="subtle">Mốc: frame {reference.frame.ordinal+1}, {reference.frame.relative_seconds.toFixed(3)} s. Bản sao box tại lúc đặt mốc; nếu sửa lại hãy đặt lại mốc. Khoảng cách tối đa giữa hai frame: {(2.1/analysis.settings.sampling_fps).toFixed(2)} s.</p><button disabled={disabled||busy||!frame||frame.relative_seconds<=reference.frame.relative_seconds} onClick={()=>void measure()}>{busy?'Đang tính…':'Tính vận tốc với frame mốc'}</button></>}
  {error&&<p role="alert">{error}</p>}
  {result&&<><p className="subtle">{motionSummary(result.motion)}</p><MotionDetails row={result.rows[1]} title="Kết quả từ box đã chỉnh" expanded/><button onClick={download}>Tải phép đo JSON</button><p className="subtle">Phép đo thử dùng box hiện tại; chưa xác nhận identity toàn track, chưa phải GT và không ghi đè vận tốc AI. Lưu frame riêng để giữ nhãn đã chỉnh.</p></>}
 </details>;
}
