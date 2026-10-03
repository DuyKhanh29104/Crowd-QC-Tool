import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {api,Media} from '../../src/api';
import Camera from '../../src/qc/Camera';
import Analysis from '../../src/qc/Analysis';
import FrameEditor from '../../src/qc/FrameEditor';
import {defaults} from '../../src/qc/shared';
import '../../src/style.css';

const media={id:'video',name:'Motion fixture',camera_id:'test',width:640,height:360,frame_count:30,duration_seconds:3} as Media;
const roi={id:'roi',name:'ROI',area_m2:20,polygon:[[0,0],[640,0],[640,360],[0,360]]};
const initial={id:'profile',payload:{name:'Two planes',rois:[roi],settings:defaults,calibration:{plane:'ground',points:roi.polygon,width_m:6.4,height_m:3.6,test:{points:[[50,50],[150,50]],expected_m:1},distance_verified:true}}};
const analysis={id:'run',roi,settings:{...defaults,detection_target:'head'},candidates:[],rows:[0,5].map(i=>({frame_id:'frame'+i,ordinal:i,time:i/10,count:1,density:.05,label:'NORMAL',objects:[{id:'box',box:[10+4*i,20,30+4*i,60],track_id:'one',label_type:'head'}]}))};
const noDirty=()=>{};
function Harness() {
 const [screen,setScreen]=useState('camera'),[profile,setProfile]=useState<any>(initial);
 return <main style={{padding:24}}><div className="canvas-tools">{['camera','analysis','review'].map(s=><button key={s} onClick={()=>setScreen(s)}>Test {s}</button>)}</div>
 {screen==='camera'?<Camera media={media} profile={profile} onDirty={noDirty} onSaved={async()=>setProfile(await api('/test/profile'))}/>:
 screen==='analysis'?<Analysis media={media} profile={profile} analysis={null} jobs={[]} reload={async()=>{}} onReview={()=>{}} onSetup={()=>{}}/>:
 <FrameEditor media={media} analysis={analysis} onDirty={noDirty} reload={async()=>{}}/>}</main>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
