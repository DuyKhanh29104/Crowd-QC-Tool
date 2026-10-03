import {createRoot} from 'react-dom/client';
import FrameEditor from '../../src/qc/FrameEditor';
import {Media} from '../../src/api';
import {defaults} from '../../src/qc/shared';
import '../../src/style.css';
const media={id:'video',name:'BBox regression',width:640,height:360,frame_count:12,duration_seconds:1.2} as Media;
const box=(id:string,x:number)=>({id,box:[x,20,x+20,60],track_id:id,label_type:'person'});
const analysis={id:'run',roi:{polygon:[[0,0],[640,0],[640,360],[0,360]]},settings:{...defaults,sampling_fps:2},
 rows:[{frame_id:'frame0',ordinal:0,time:0,objects:[box('ai0',10)]},{frame_id:'frame5',ordinal:5,time:.5,objects:[box('ai5',30)]},{frame_id:'frame10',ordinal:10,time:1,objects:[]}],
 candidates:[{id:'event',review:{id:'saved-event',created_at:'2026-09-30T10:00:00Z',payload:{label:'NORMAL',frame_edits:{frame2:[box('manual2',50)],frame5:[]}}}}]};
createRoot(document.getElementById('root')!).render(<main style={{padding:16}}><FrameEditor media={media} analysis={analysis} reload={async()=>{}} onDirty={()=>{}}/></main>);
