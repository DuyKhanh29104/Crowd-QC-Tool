import {useEffect,useRef} from 'react';

// Ignore OS repeat. Source timestamps determine 2x pacing, including VFR and reverse steps.
export function useFrameHold(advance:(delta:number,repeated:boolean)=>boolean|null,
 delay:(delta:number)=>Promise<number>,onError:(message:string)=>void,displayedAt:()=>number=()=>0) {
 const latest=useRef({advance,delay,onError,displayedAt});latest.current={advance,delay,onError,displayedAt};
 const timer=useRef<number>(),owner=useRef<string>();
 const generation=useRef(0);
 const stop=(key?:string)=>{
  if(key&&owner.current!==key)return;
  generation.current++;clearTimeout(timer.current);timer.current=undefined;owner.current=undefined;
 };
 const start=(key:string,delta:number)=>{
  if(owner.current===key)return;
  stop();owner.current=key;const token=generation.current;
  if(latest.current.advance(delta,false)===false){stop();return;}
  let advancedAt=performance.now();
  const plan=async()=>{
   try{
    const wait=await latest.current.delay(delta);
    if(token!==generation.current)return;
    const tick=()=>{
     if(token!==generation.current)return;
     // A slow decode must not shorten the time the next visible frame is held.
     const remaining=Math.max(advancedAt,latest.current.displayedAt())+wait-performance.now();
     if(remaining>0){timer.current=window.setTimeout(tick,remaining);return;}
     const result=latest.current.advance(delta,true);
     if(result===false){stop();return;}
     if(result===null){timer.current=window.setTimeout(tick,8);return;}
     // No catch-up burst after a slow decode. Read the next frame after React commits.
     advancedAt=performance.now();timer.current=window.setTimeout(()=>void plan(),0);
    };
    tick();
   }catch(e){if(token===generation.current){stop();latest.current.onError((e as Error).message);}}
  };
  timer.current=window.setTimeout(()=>void plan(),0);
 };
 useEffect(()=>{
  const blur=()=>stop();
  const visibility=()=>{if(document.hidden)stop();};
  const keyup=(e:KeyboardEvent)=>stop(e.key.toLowerCase());
  window.addEventListener('blur',blur);window.addEventListener('keyup',keyup);
  document.addEventListener('visibilitychange',visibility);
  return()=>{stop();window.removeEventListener('blur',blur);window.removeEventListener('keyup',keyup);document.removeEventListener('visibilitychange',visibility);};
 },[]);
 return {start,stop};
}
