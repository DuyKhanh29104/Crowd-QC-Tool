import {useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {api,Media,ReviewObject} from '../api';
import Canvas from '../Canvas';
import {createFrameBuffer,BufferedFrame} from './frameBuffer';
import {useFrameHold} from './useFrameHold';
import {indexFrameAnnotations} from './frameAnnotations';
import {nearestSample} from './samples';
import MotionDetails from './MotionDetails';
import MotionReview from './MotionReview';
import {useDirty,number,boxLabel,targetOf} from './shared';

export default function FrameEditor({media,analysis,reload,onDirty,initialIndex=0}:{media:Media;analysis:any;initialIndex?:number;reload:()=>Promise<void>;onDirty:(v:boolean)=>void}){
 const [index,setIndex]=useState(initialIndex),[display,setDisplay]=useState<BufferedFrame>(),[objects,setObjects]=useState<any[]>([]),[selected,setSelected]=useState(''),[mode,setMode]=useState<'select'|'add'|'pan'>('select'),[label,setLabel]=useState('NORMAL');
 const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[ready,setReady]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [retry,setRetry]=useState(0);
 const [scrubTime,setScrubTime]=useState<number>();
 const scrubTimer=useRef<number>(),seekController=useRef<AbortController>();
 const travel=useRef({direction:1,stride:1});
 const displayedAt=useRef(0);
 useLayoutEffect(()=>{displayedAt.current=performance.now();},[display]);
 useLayoutEffect(()=>{setDisplay(undefined);setReady(false);},[media.id]);
 const frame=display?.frame;
 const buffer=useMemo(()=>createFrameBuffer(media.id,media.frame_count),[media.id,media.frame_count]);
 const current=ready&&frame?.ordinal===index&&scrubTime===undefined;
 const head=targetOf(analysis)==='head';
 const video=useRef<HTMLVideoElement>(null),playStart=useRef(0);
 const [playing,setPlaying]=useState(false),[rate,setRate]=useState(1),[playTime,setPlayTime]=useState(0),[seeking,setSeeking]=useState(false);
 const rates=[.25,.5,1,1.5,2,4];
 const candidate=analysis.candidates.find((c:any)=>c.frame_id===frame?.id);
 const annotations=useMemo(()=>indexFrameAnnotations(analysis),[analysis.rows,analysis.candidates]);
 const sample=frame?annotations.samples.get(frame.id):undefined;
 const correction=frame?annotations.edits.get(frame.id):undefined;
 const nearby=useMemo(()=>{const rows=analysis.rows as any[];return {previous:[...rows].reverse().find(r=>r.ordinal<index),next:rows.find(r=>r.ordinal>index)};},[analysis.rows,index]);
 useDirty(dirty||busy||seeking,onDirty);
 useEffect(()=>{if(video.current)video.current.playbackRate=rate;},[rate,playing]);
 useEffect(()=>()=>buffer.cancelWarmup(),[buffer]);
 useEffect(()=>{let live=true;buffer.cancelWarmup(index);setReady(false);setError('');
  buffer.load(index).then(result=>{if(!live)return;setDisplay(result);setReady(true);void buffer.warm(index,travel.current.direction,travel.current.stride);})
   .catch(e=>{if(live&&e.name!=='AbortError')setError(e.message);});
  return()=>{live=false;};
 },[buffer,index,retry]);
 useLayoutEffect(()=>{if(!frame)return;const items=correction?.objects??sample?.objects??[];setObjects(items);setSelected(items[0]?.id||'');setLabel(correction?.label||sample?.label||'NORMAL');setDirty(false);},[analysis.id,frame?.id,correction?.reviewId]);
 const go=(next:number)=>{if(busy||seeking||next<0||next>=media.frame_count)return false;if(dirty&&!confirm('Frame có thay đổi chưa lưu. Bỏ thay đổi và chuyển frame?'))return false;video.current?.pause();setPlaying(false);clearTimeout(scrubTimer.current);setScrubTime(undefined);setDirty(false);setIndex(next);return true;};
 const stopAt=async(timestamp=video.current?.currentTime??playTime,delta=0)=>{
  if(busy)return;
  seekController.current?.abort();const controller=new AbortController();seekController.current=controller;
  video.current?.pause();setPlaying(false);setSeeking(true);setError('');
  try{
   const r=await api(`/media/${media.id}/frames?timestamp=${Math.max(0,timestamp)}&limit=1`,undefined,controller.signal);
   if(controller.signal.aborted)return;
   const i=r.items[0]?.ordinal??media.frame_count-1;setIndex(Math.max(0,Math.min(media.frame_count-1,i+delta)));setRetry(v=>v+1);
  }catch(e){if(!controller.signal.aborted)setError((e as Error).message);}
  finally{if(seekController.current===controller){seekController.current=undefined;setSeeking(false);setScrubTime(undefined);}}
 };
 const scrub=(timestamp:number,flush=false)=>{
  clearTimeout(scrubTimer.current);seekController.current?.abort();buffer.cancelWarmup();
  seekController.current=undefined;setSeeking(false);
  video.current?.pause();setPlaying(false);setScrubTime(timestamp);
  if(flush)void stopAt(timestamp);
  else scrubTimer.current=window.setTimeout(()=>void stopAt(timestamp),100);
 };
 useEffect(()=>()=>{clearTimeout(scrubTimer.current);seekController.current?.abort();},[buffer]);
 const step=(delta:number,repeated=false):boolean|null=>{
  if(busy||seeking)return null;
  if(repeated&&dirty)return false;
  if(playing){void stopAt(undefined,delta);return true;}
  if(!current)return error?false:null;
  const next=Math.max(0,Math.min(media.frame_count-1,index+delta));
  if(next===index)return false;
  travel.current={direction:Math.sign(delta),stride:Math.abs(delta)};
  return go(next);
 };
 const hold=useFrameHold(step,delta=>buffer.stepDelay(index,delta),setError,()=>displayedAt.current);
 useEffect(()=>()=>hold.stop(),[media.id]);
 const holdButton=(delta:number)=>({
  onPointerDown:(e:React.PointerEvent<HTMLButtonElement>)=>{if(e.button!==0)return;e.currentTarget.setPointerCapture(e.pointerId);hold.start('pointer',delta);},
  onPointerUp:()=>hold.stop('pointer'),onPointerCancel:()=>hold.stop('pointer'),onLostPointerCapture:()=>hold.stop('pointer'),
  onClick:(e:React.MouseEvent<HTMLButtonElement>)=>{if(e.detail===0)step(delta);},
 });
 const togglePlay=()=>{if(busy||seeking||!frame)return;if(playing){void stopAt();return;}if(!current)return;if(dirty){setError('Lưu nhãn đang sửa trước khi phát video.');return;}playStart.current=frame.relative_seconds;setPlayTime(frame.relative_seconds);setError('');setPlaying(true);};
 const tool=async(value:'select'|'add'|'pan')=>{if(playing)await stopAt();setMode(value);};
 const change=(items:any[])=>{setObjects(items);setDirty(true);};
 const save=async(next:boolean)=>{if(!frame||busy||!current||playing||seeking)return;setBusy(true);setError('');try{
  const c=candidate||await api(`/qc/analysis/${analysis.id}/candidates`,{frame_id:frame.id,start:frame.relative_seconds,end:frame.relative_seconds});
  await api(`/qc/analysis/${analysis.id}/candidates/${c.id}/decision`,{decision:'CORRECT',label,start:frame.relative_seconds,end:frame.relative_seconds,frame_edits:{[frame.id]:objects},boxes_verified:[frame.id],expected_version:candidate?.version||0});
  setDirty(false);setNotice(`Đã lưu nhãn frame ${index+1}.`);await reload();if(next&&index<media.frame_count-1)setIndex(index+1);
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 useEffect(()=>{const key=(e:KeyboardEvent)=>{
  if(busy||seeking||e.altKey)return;
  if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='s'||e.key==='Enter')){e.preventDefault();if(!e.repeat)void save(e.key==='Enter');return;}
  if((e.target as HTMLElement).closest('input,textarea,select,[contenteditable]')||e.ctrlKey||e.metaKey)return;
  const k=e.key.toLowerCase();
  if([' ','arrowleft','arrowright','b','v','h','delete','s','[',']'].includes(k))e.preventDefault();
  if(k==='arrowleft'||k==='arrowright'){if(!e.repeat)hold.start(k,(k==='arrowleft'?-1:1)*(e.shiftKey?10:1));return;}
  hold.stop();
  if(e.repeat)return;
  if(k===' ')togglePlay();if(k==='b')void tool('add');if(k==='v')void tool('select');if(k==='h')void tool('pan');
  if(k==='s')void save(false);
  if(k==='delete'&&selected&&!playing&&current){change(objects.filter(o=>o.id!==selected));setSelected('');}
  if(k==='['||k===']')setRate(rates[Math.max(0,Math.min(rates.length-1,rates.indexOf(rate)+(k===']'?1:-1)))]);
 };window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);});
 const chosen=objects.find(o=>o.id===selected);
 const shownSample=playing?nearestSample<any>(analysis.rows,playTime):sample;
 const shownObjects=playing?(shownSample?.objects||[]):objects;
 const status=playing?'Đang phát':seeking||!current?'Đang lấy frame':dirty?'Chưa lưu':correction?'Đã lưu QC':sample?'Nhãn AI':'Chưa chạy AI';
 return <section className="frame-review" aria-label="Review từng frame">
 {error&&<div className="alert" role="alert">{error}{!current&&<button onClick={()=>setRetry(v=>v+1)}>Thử tải lại frame</button>}</div>}{notice&&<div className="notice" role="status">{notice}</div>}
 <div className="frame-review-grid">
 <div className="frame-workspace">
 <div className="frame-player-sticky"><div className="frame-screen-heading"><span>{media.name}</span><span>{media.width} × {media.height}</span></div>
 <div className="frame-visual" aria-busy={!current&&!playing} data-displayed-frame={frame?.ordinal}>
 {!current&&frame&&!playing&&<span className="frame-buffer-status" role="status">Đang tải frame {index+1} · đang hiển thị frame {frame.ordinal+1}</span>}
 {playing?<div className="frame-playback"><video ref={video} src={`/api/qc/media/${media.id}/video`} muted playsInline onLoadedMetadata={()=>{if(video.current){video.current.currentTime=playStart.current;video.current.playbackRate=rate;void video.current.play().catch(e=>{setPlaying(false);setError(e.message);});}}} onTimeUpdate={()=>setPlayTime(video.current?.currentTime||0)} onEnded={()=>void stopAt(media.duration_seconds)} onError={()=>{setPlaying(false);setError('Trình duyệt không phát được codec này. Bạn vẫn có thể kiểm từng frame bằng phím mũi tên.');}}/>
 <svg viewBox={`0 0 ${media.width} ${media.height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true"><polygon points={analysis.roi.polygon.map((p:number[])=>p.join(',')).join(' ')} fill="#44b59c18" stroke="#6edbc4" strokeWidth="2"/>{shownObjects.map((o:any)=><rect key={o.id} x={o.box[0]} y={o.box[1]} width={o.box[2]-o.box[0]} height={o.box[3]-o.box[1]} fill="none" stroke="#f1c46b" strokeWidth="2"/>)}</svg><span className="playback-caption">AI · {shownSample?.time.toFixed(2)??'—'} s</span></div>
 :frame?<Canvas compact url={frame.image_url} preparedImage={display?.image} width={media.width} height={media.height} points={analysis.roi.polygon} mode="pan" onChange={()=>{}} onImageReady={setReady} reviewMode={mode} reviewDisabled={busy||seeking||!current} selectedObject={selected} onSelectObject={setSelected} reviewObjects={objects.map((o:any):ReviewObject=>({id:o.id,box:o.box,marker:[(o.box[0]+o.box[2])/2,(o.box[1]+o.box[3])/2],anchor:null,anchor_verified:false}))} onEditObject={o=>change(objects.map(old=>old.id===o.id?{...old,box:o.box}:old))} onAddObject={box=>{if(box){const item={id:crypto.randomUUID(),box,track_id:null,score:null,label_type:targetOf(analysis)};change([...objects,item]);setSelected(item.id);setMode('select');}}}/>:<div className="frame-placeholder" role="status">Đang tải frame…</div>}
 </div>
 <div className="frame-transport">
 <input className="frame-scrubber" aria-label="Vị trí video" type="range" min="0" max={media.duration_seconds} step="0.01" value={scrubTime??(playing?playTime:frame?.relative_seconds||0)} disabled={busy||dirty} onChange={e=>scrub(Number(e.target.value))} onPointerUp={e=>scrub(Number(e.currentTarget.value),true)}/>
 <div className="frame-transport-row"><div className="frame-play-controls"><button disabled={busy||seeking||!frame} {...holdButton(-1)} aria-label="Frame trước" title="Frame trước (←)">‹</button><button className="primary" disabled={busy||seeking||!frame} onClick={togglePlay} aria-label={playing?'Tạm dừng':'Phát video'} title="Space">{playing?'Ⅱ':'▶'}</button><button disabled={busy||seeking||!frame} {...holdButton(1)} aria-label="Frame sau" title="Frame sau (→)">›</button><span className="frame-clock">{(playing?playTime:frame?.relative_seconds||0).toFixed(3)} <small>/ {media.duration_seconds.toFixed(1)} s</small></span></div>
 <div className="frame-jump"><label>Frame<input aria-label="Số frame" type="number" min={1} max={media.frame_count} disabled={playing||busy||seeking} value={index+1} onChange={e=>{const n=Number(e.target.value);if(Number.isInteger(n))go(n-1);}}/></label><span>/ {media.frame_count}</span><label>Tốc độ<select aria-label="Tốc độ review" value={rate} onChange={e=>setRate(Number(e.target.value))}>{rates.map(r=><option key={r} value={r}>{r}×</option>)}</select></label></div></div>
 </div>
 </div>
 <details className="review-help"><summary>Phím tắt</summary>
 <div className="frame-key-strip" title="Giữ tiến/lùi: 2× thời gian video. Ctrl + cuộn để zoom."><span><kbd>Space</kbd> Phát / dừng</span><span><kbd>←</kbd><kbd>→</kbd> 1 frame</span><span><kbd>Shift + ← / →</kbd> 10 frame</span><span><kbd>[</kbd><kbd>]</kbd> Tốc độ</span></div></details>
 </div>
 <aside className="frame-inspector" aria-label="Chỉ số và công cụ">
 <div className="frame-inspector-title"><h2>{boxLabel(analysis)}</h2><span className={'frame-status '+(dirty?'unsaved':'')}>{status}</span></div>
 <div className="frame-stats"><div><span>{playing?'Box AI gần nhất':'Box trên frame'}</span><strong>{shownObjects.length}</strong></div><div><span>Mật độ AI / m²</span><strong>{number(shownSample?.density)}</strong></div><div><span>Tốc độ AI / m/s</span><strong>{number(shownSample?.speed)}</strong></div><div><span>Đứng yên AI</span><strong>{number(shownSample?.stationary==null?null:shownSample.stationary*100,'%')}</strong></div></div>
 <p className="frame-source-note">{playing?'Overlay AI theo mẫu gần nhất; dừng để sửa đúng frame.':correction?'Đang hiển thị box QC đã lưu trên đúng frame này.':sample?(objects.length?'Box AI trên đúng frame.':'AI đã chạy trên frame này và không phát hiện đối tượng trong ROI.'):`Frame này chưa được AI phân tích (${analysis.settings.sampling_fps} FPS). Có thể tự gán box hoặc chuyển tới mẫu AI.`}</p>
 {!playing&&<div className="frame-sample-nav"><button disabled={busy||seeking||!nearby.previous} onClick={()=>go(nearby.previous.ordinal)}>‹ Mẫu AI trước</button><button disabled={busy||seeking||!nearby.next} onClick={()=>go(nearby.next.ordinal)}>Mẫu AI sau ›</button></div>}
 <div className="frame-tool-grid"><button className={mode==='select'&&!playing?'active':''} disabled={busy||seeking} onClick={()=>void tool('select')}>Chọn / sửa box <kbd>V</kbd></button><button className={mode==='add'&&!playing?'active':''} disabled={busy||seeking} onClick={()=>void tool('add')}>{head?'Thêm đầu':'Thêm người'} <kbd>B</kbd></button><button disabled={busy||seeking} onClick={()=>void tool('pan')}>Di chuyển ảnh <kbd>H</kbd></button><button disabled={!chosen||!current||busy||playing||seeking} onClick={()=>{change(objects.filter(o=>o.id!==selected));setSelected('');}}>Xóa box <kbd>Del</kbd></button></div>
 <MotionDetails row={shownSample} motion={analysis.motion}/>
 <MotionReview key={analysis.id} analysis={analysis} frame={frame} objects={objects} disabled={busy||playing||seeking||!current}/>
 <fieldset disabled={busy||playing||seeking||!current} className="frame-edit-fields">
 {chosen?<div className="box-fields">{[0,1,2,3].map(axis=><label key={axis}>{['X1','Y1','X2','Y2'][axis]}<input type="number" value={Math.round(chosen.box[axis]*100)/100} onChange={e=>change(objects.map(o=>{if(o.id!==selected)return o;const box=[...o.box];box[axis]=Number(e.target.value);return {...o,box};}))}/></label>)}<label className="frame-track-field">Track ID<input placeholder="Chưa có" value={chosen.track_id||''} onChange={e=>change(objects.map(o=>o.id===selected?{...o,track_id:e.target.value||null}:o))}/></label></div>:<p className="subtle">Chọn một box trên ảnh để sửa tọa độ.</p>}
 <label>Trạng thái frame<select value={label} onChange={e=>{setLabel(e.target.value);setDirty(true);}}>{['NORMAL','CROWDED','CONGESTED','UNCERTAIN'].map(l=><option key={l}>{l}</option>)}</select></label>
 <div className="frame-save-actions"><button onClick={()=>void save(false)}>Lưu frame <kbd>S</kbd></button><button className="primary" onClick={()=>void save(true)}>{busy?'Đang lưu…':'Lưu & frame tiếp'} <kbd>Ctrl ↵</kbd></button></div>
 </fieldset>
 </aside></div></section>;
}
