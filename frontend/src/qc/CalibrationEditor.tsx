import {useEffect,useRef,useState} from 'react';
import {api,Media,Point} from '../api';

type QuadSide='ab'|'bc'|'cd'|'da'|'ac'|'bd';
export type CalibrationInput = {
  plane:'ground'|'head'; points:Point[]; width_m:number; height_m:number;
  shape:'rectangle'|'right_angle'|'quadrilateral';lengths_m:Partial<Record<QuadSide,number>>|null;
  reference_height_m:number|null; test:{points:Point[];expected_m:number}|null;
};
export function calibrationInput(value:any, plane:'ground'|'head'):CalibrationInput {
  return {plane,points:value?.points||[],width_m:value?.width_m||8,height_m:value?.height_m||5,
    shape:value?.shape||(value?'rectangle':'right_angle'),lengths_m:value?.lengths_m||null,
    reference_height_m:value?.reference_height_m??null,test:value?.test||null};
}

export default function CalibrationEditor({media,frame,value,onChange,savedId,hasSaved}:{
  media:Media;frame:any;value:CalibrationInput;onChange:(v:CalibrationInput)=>void;savedId:string;hasSaved:boolean;
}) {
  const [testing,setTesting]=useState(false),[preview,setPreview]=useState<any>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const head=value.plane==='head',quad=value.shape!=='rectangle',right=value.shape==='right_angle',points=value.points,testPoints=value.test?.points||[];
  const revision=useRef(0);
  useEffect(()=>{revision.current++;setPreview(undefined);setError('');},[value]);
  useEffect(()=>()=>{revision.current++;},[]);
  const update=(patch:Partial<CalibrationInput>)=>onChange({...value,...patch});
  const selectShape=(shape:CalibrationInput['shape'])=>{
    const lengths:Partial<Record<QuadSide,number>>={};
    for(const side of ['ab','bc','cd','da'] as QuadSide[])if(value.lengths_m?.[side]!=null)lengths[side]=value.lengths_m[side];
    update({shape,...(shape==='right_angle'?{lengths_m:lengths}: {})});
  };
  const check=async()=>{const version=revision.current;setBusy(true);setError('');try {const checked=await api('/qc/calibration-preview',value);if(version===revision.current)setPreview(checked);}catch(e){if(version===revision.current)setError((e as Error).message);}finally{setBusy(false);}};
  const pointInput=(index:number,axis:number,nextValue:number,test=false)=>{
    const next=(test?testPoints:points).map(p=>[...p] as Point);
    while(next.length<=index)next.push([0,0]);next[index][axis]=nextValue;
    update(test?{test:{points:next,expected_m:value.test?.expected_m||1}}:{points:next});
  };
  return <section aria-label={head?'Hiệu chuẩn tâm đầu':'Hiệu chuẩn mặt sàn'}>
    <p className="subtle">{head?'Dùng 4 mốc trên một mặt phẳng ngang ở cùng độ cao tâm đầu đã đo. Có thể dùng người tham chiếu đứng lần lượt tại A, B, C, D có khoảng cách thực đã biết; chọn đúng tâm đầu tại mỗi vị trí. Camera phải cố định.':'Chọn 4 mốc A → B → C → D trên cùng mặt sàn, theo thứ tự quanh biên một tứ giác lồi. Điểm giữa đáy box toàn thân sẽ được chiếu về mặt phẳng này.'}</p>
    <label>Dạng vùng hiệu chuẩn<select aria-label="Dạng vùng hiệu chuẩn" value={value.shape} onChange={e=>selectShape(e.target.value as CalibrationInput['shape'])}><option value="right_angle">Tứ giác · góc A vuông · 4 cạnh</option><option value="rectangle">Hình chữ nhật · 2 cạnh</option>{value.shape==='quadrilateral'&&<option value="quadrilateral">Profile cũ · 4 cạnh + 2 đường chéo</option>}</select></label>
    {right&&<p className="notice">Chọn A tại góc vuông ngoài thực tế: AB ⟂ AD. Góc A trong ảnh có thể không vuông do phối cảnh. B, C, D đi tiếp theo thứ tự quanh tứ giác lồi; ba góc còn lại không cần vuông.</p>}
    {head&&<label>Độ cao tâm đầu tham chiếu (m)<input type="number" min="0.01" step="any" value={value.reference_height_m??''} placeholder="Nhập số đo thực" onChange={e=>update({reference_height_m:e.target.value===''?null:Number(e.target.value)})}/></label>}
    <div className="canvas-tools"><button className={!testing?'active':''} onClick={()=>setTesting(false)}>4 điểm A → B → C → D</button><button className={testing?'active':''} onClick={()=>setTesting(true)}>Kiểm tra 2 điểm</button><button onClick={()=>update(testing?{test:null}:{points:[]})}>Vẽ lại điểm</button><button onClick={()=>update({points:[],test:null})}>Bỏ hiệu chuẩn này</button></div>
    {frame&&<div className="calibration-stage" style={{aspectRatio:`${media.width}/${media.height}`}}><img src={frame.image_url} width={media.width} height={media.height} alt={head?'Chọn các điểm trên mặt phẳng tâm đầu':'Chọn các điểm trên mặt sàn'}/><svg viewBox={`0 0 ${media.width} ${media.height}`} onClick={e=>{
      const rect=e.currentTarget.getBoundingClientRect();const p:Point=[(e.clientX-rect.left)/rect.width*media.width,(e.clientY-rect.top)/rect.height*media.height];
      if(testing)update({test:{points:testPoints.length<2?[...testPoints,p]:[p],expected_m:value.test?.expected_m||1}});
      else update({points:points.length<4?[...points,p]:[p]});
    }}><polygon points={points.map(p=>p.join(',')).join(' ')} stroke="#7daeff" fill="#4189e82a" strokeWidth="2"/>{points.map((p,i)=><g key={i}><circle cx={p[0]} cy={p[1]} r="6" fill="#2867d4"/><text x={p[0]+10} y={p[1]} fill="white" fontSize="18">{right&&i===0?'A · 90° thực':'ABCD'[i]}</text></g>)}{testPoints.map((p,i)=><circle key={'test'+i} cx={p[0]} cy={p[1]} r="5" fill="#ffc364"/>)}</svg></div>}
    <p className="subtle">{right?'Chỉ đo bốn cạnh AB, BC, CD, DA. Hai đường chéo được tính từ bốn cạnh và giả thiết góc A = 90°, không cần nhập.':quad?'Profile cũ dùng bốn cạnh và hai đường chéo. Chỉ đổi sang góc A vuông khi đã xác nhận AB ⟂ AD ngoài thực tế.':'AB và BC là hai cạnh vuông góc tính bằng mét.'} ROI đo tốc độ cần nằm trong tứ giác A–B–C–D. Hai điểm kiểm độc lập phải ở cùng mặt phẳng, nằm trong tứ giác và không trùng bốn góc.</p>
    <div className="field-grid">{quad?((right?['ab','bc','cd','da']:['ab','bc','cd','da','ac','bd']) as QuadSide[]).map(side=><label key={side}>{side.toUpperCase()} (m)<input type="number" min="0.001" step="any" value={value.lengths_m?.[side]??''} onChange={e=>{const lengths={...value.lengths_m};if(e.target.value==='')delete lengths[side];else lengths[side]=Number(e.target.value);update({lengths_m:lengths});}}/></label>):<><label>AB (m)<input type="number" min="0.01" step="any" value={value.width_m} onChange={e=>update({width_m:Number(e.target.value)})}/></label><label>BC (m)<input type="number" min="0.01" step="any" value={value.height_m} onChange={e=>update({height_m:Number(e.target.value)})}/></label></>}<label>Khoảng cách kiểm tra (m)<input type="number" min="0.01" step="any" value={value.test?.expected_m??1} onChange={e=>update({test:{points:testPoints,expected_m:Number(e.target.value)}})}/></label></div>
    <details><summary>Nhập tọa độ điểm bằng bàn phím</summary>{['A','B','C','D'].map((label,i)=><div className="field-grid" key={label}>{[0,1].map(axis=><label key={axis}>{label} {axis?'Y':'X'} (px)<input type="number" value={points[i]?.[axis]??''} min="0" max={axis?media.height:media.width} onChange={e=>pointInput(i,axis,Number(e.target.value))}/></label>)}</div>)}{['Kiểm 1','Kiểm 2'].map((label,i)=><div className="field-grid" key={label}>{[0,1].map(axis=><label key={axis}>{label} {axis?'Y':'X'} (px)<input type="number" value={testPoints[i]?.[axis]??''} min="0" max={axis?media.height:media.width} onChange={e=>pointInput(i,axis,Number(e.target.value),true)}/></label>)}</div>)}</details>
    <button disabled={busy||points.length!==4} onClick={()=>void check()}>Kiểm tra hiệu chuẩn</button>
    {error&&<p className="alert" role="alert">{error}</p>}
    {preview&&<div className="notice">{preview.distance_m!=null?`Ước lượng ${preview.distance_m.toFixed(2)} m · Sai lệch ${preview.error_percent.toFixed(1)}% · ${preview.distance_verified?'Đạt kiểm tra khoảng cách':'Cần kiểm tra lại điểm / số đo'}`:'Đã tính phép chiếu. Chọn 2 điểm kiểm độc lập để bật ước lượng tốc độ.'}</div>}
    {preview?.measurement_check&&<p className="subtle">Số đo tứ giác nhất quán: BD suy ra {preview.measurement_check.derived_m.toFixed(3)} m, BD đã đo {preview.measurement_check.measured_m.toFixed(3)} m, sai lệch {preview.measurement_check.error_m.toFixed(3)} m. Đây là kiểm tra hình học; vẫn cần hai điểm kiểm độc lập để bật m/s.</p>}
    {preview?.construction&&<p className="subtle">Đã dựng tứ giác với góc A = 90° theo khai báo. AC tính được {preview.construction.derived_diagonals_m.ac.toFixed(3)} m; BD tính được {preview.construction.derived_diagonals_m.bd.toFixed(3)} m. Đây là số suy ra, không phải số đo kiểm độc lập.</p>}
    {head&&<p className="subtle">Kết quả m/s là xấp xỉ cho người có tâm đầu gần độ cao tham chiếu; người cao/thấp khác, cúi đầu hoặc lên cầu thang có thể sai lệch. Độ cao được lưu để mô tả mặt phẳng, không tự biến hiệu chuẩn mặt sàn thành hiệu chuẩn đầu.</p>}
    {savedId&&hasSaved&&<figure className="bird-eye"><img src={`/api/qc/media/${media.id}/bird-eye/${savedId}?plane=${value.plane}`} width="600" height="375" alt="Bird-eye view từ camera profile đã lưu"/><figcaption>Bird-eye view {head?'mặt phẳng tâm đầu':'mặt sàn'} của profile đã lưu</figcaption></figure>}
  </section>;
}
