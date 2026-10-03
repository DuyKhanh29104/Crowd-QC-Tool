export default function InferenceSize({value,onChange}:{value:number;onChange:(size:number)=>void}) {
 const sizes=[320,640,960,1280,1600,1920];
 // Preserve valid sizes from older saved profiles without silently changing them.
 if(!sizes.includes(value)&&Number.isInteger(value)&&value>=320&&value<=1920&&value%32===0)sizes.push(value);
 return <label>Kích thước đầu vào YOLO (px)<select aria-label="Kích thước đầu vào YOLO" value={value} onChange={e=>onChange(Number(e.target.value))}>
 {sizes.sort((a,b)=>a-b).map(size=><option key={size} value={size}>{size} px</option>)}
 </select>
 <small>Cạnh dài tham chiếu toàn ảnh hoặc ROI crop theo cấu hình Advanced. Tăng kích thước giữ thêm chi tiết, tốn GPU hơn.</small></label>;
}
