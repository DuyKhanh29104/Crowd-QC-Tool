import {api,Frame} from '../api';

export type BufferedFrame={frame:Frame;image:HTMLImageElement};

// Scoped to one editor/media. Keep decoded pixels bounded, including 4K sources.
export function createFrameBuffer(mediaId:string,frameCount:number) {
 const metadata=new Map<number,Frame>(),cache=new Map<number,BufferedFrame>();
 const pending=new Map<number,Promise<BufferedFrame>>();
 const pendingMetadata=new Map<number,Promise<Frame>>();
 const controllers=new Map<AbortController,number|undefined>();
 const stats={cancelledRequests:0,cacheHits:0,loads:0};
 const frameAt=(ordinal:number,signal?:AbortSignal):Promise<Frame>=>{
  const hit=metadata.get(ordinal);if(hit)return Promise.resolve(hit);
  const running=pendingMetadata.get(ordinal);if(running&&!signal)return running;
  const request=api(`/media/${mediaId}/frames?offset=${ordinal}&limit=1`,undefined,signal).then(response=>{
   signal?.throwIfAborted();
   const frame=response.items[0] as Frame;
   if(!frame||frame.ordinal!==ordinal)throw new Error('Không tìm thấy frame nguồn.');
   metadata.set(ordinal,frame);
   while(metadata.size>64)metadata.delete(metadata.keys().next().value!);
   return frame;
  });
  pendingMetadata.set(ordinal,request);void request.finally(()=>{if(pendingMetadata.get(ordinal)===request)pendingMetadata.delete(ordinal);}).catch(()=>{});
  return request;
 };
 const storageName=`review-source-frames-v1-${mediaId}`;
 const disk=()=>window.caches.open(storageName);
 let generation=0;
 const cancelWarmup=(keepOrdinal?:number)=>{
  generation++;
  for(const [controller,ordinal] of controllers){
   if(keepOrdinal!==undefined&&ordinal===keepOrdinal)continue;
   controller.abort();controllers.delete(controller);stats.cancelledRequests++;
  }
  for(const ordinal of pending.keys())if(ordinal!==keepOrdinal)pending.delete(ordinal);
  for(const ordinal of pendingMetadata.keys())if(ordinal!==keepOrdinal)pendingMetadata.delete(ordinal);
 };
 const load=(ordinal:number):Promise<BufferedFrame>=>{
  const hit=cache.get(ordinal);
  if(hit){stats.cacheHits++;cache.delete(ordinal);cache.set(ordinal,hit);return Promise.resolve(hit);}
  const running=pending.get(ordinal);if(running)return running;
  const controller=new AbortController();controllers.set(controller,ordinal);stats.loads++;
  const signal=controller.signal;
  const request=(async()=>{
   const frame=await frameAt(ordinal,signal);
   let objectUrl:string|undefined;
   try{
    const stored=await (await disk()).match(frame.image_url);
    if(stored)objectUrl=URL.createObjectURL(await stored.blob());
   }catch{/* Browsers without cache storage still support normal frame review. */}
   const image=new Image();
   try{
    signal.throwIfAborted();
    if(!objectUrl){
     const response=await fetch(frame.image_url,{signal});
     if(!response.ok)throw new Error('FRAME_FETCH_FAILED');
     objectUrl=URL.createObjectURL(await response.blob());
    }
    signal.throwIfAborted();image.src=objectUrl;await image.decode();signal.throwIfAborted();
   }catch(e){if(signal.aborted)throw e;throw new Error('Không tải được ảnh nguồn. Hãy thử lại frame này.');}
   finally{if(objectUrl)URL.revokeObjectURL(objectUrl);}
   const result={frame,image};cache.set(ordinal,result);
   let bytes=Array.from(cache.values()).reduce((sum,v)=>sum+v.image.naturalWidth*v.image.naturalHeight*4,0);
   while(cache.size>1&&(cache.size>12||bytes>64*1024*1024)){
    const key=cache.keys().next().value!;const old=cache.get(key)!;
    bytes-=old.image.naturalWidth*old.image.naturalHeight*4;cache.delete(key);
   }
   return result;
  })();
  pending.set(ordinal,request);void request.finally(()=>{controllers.delete(controller);if(pending.get(ordinal)===request)pending.delete(ordinal);}).catch(()=>{});
  return request;
 };
 const warm=async(ordinal:number,direction=1,stride=1)=>{
  const token=++generation;
  const controller=new AbortController();controllers.set(controller,undefined);
  try{
   const response=await api(`/media/${mediaId}/frames?offset=${Math.max(0,ordinal-2*stride)}&limit=${4*stride+1}`,undefined,controller.signal);
   if(token!==generation)return;
   for(const frame of response.items as Frame[])metadata.set(frame.ordinal,frame);
   while(metadata.size>64)metadata.delete(metadata.keys().next().value!);
   // One background decode at a time; navigation takes priority over this queue.
   for(const delta of [direction*stride,2*direction*stride,-direction*stride]){
    if(token!==generation)return;
    const next=ordinal+delta;
    if(next>=0&&next<frameCount&&metadata.has(next))await load(next);
   }
  }catch{/* A speculative failure must not interrupt review. Foreground loads retry. */}
  finally{controllers.delete(controller);}
 };
 const stepDelay=async(from:number,delta:number)=>{
  const to=Math.max(0,Math.min(frameCount-1,from+delta));
  const [a,b]=await Promise.all([frameAt(from),frameAt(to)]);
  return Math.abs(b.relative_seconds-a.relative_seconds)*1000/2;
 };
 return {load,warm,cancelWarmup,stepDelay,stats};
}
