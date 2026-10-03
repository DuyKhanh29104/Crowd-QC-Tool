import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import Viewer from '../../src/qc/Viewer';
import type {Media} from '../../src/api';

const media = {id:'video',name:'30 FPS fixture',width:640,height:360,frame_count:30,duration_seconds:1} as Media;
function Harness() {
  const [seconds,setSeconds] = useState(0);
  const [ordinal,setOrdinal] = useState(-1);
  return <><output data-testid="ordinal">{ordinal}</output><Viewer media={media} seconds={seconds} onSeek={setSeconds} onFrame={frame=>setOrdinal(frame.ordinal)}/></>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
