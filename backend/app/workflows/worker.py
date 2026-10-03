"""GPU jobs: training, paired helper evaluation and explicit ByteTrack proposals."""
import gc
import io
import json
from pathlib import Path
import sys
import time
import copy
import subprocess
from sqlalchemy import update
from backend.app.db.models import Job, Media, ROIVersion, now
from backend.app.db.session import make_engine,sessions
from backend.app.media.lease import WorkerLease
from backend.app.jobs.profiles import gpu_lease,runtime_versions,json_hash
from backend.app.media.service import digest,frame_image,verify_source
from backend.app.jobs.worker import Cancelled,selected_frames
from backend.app.workflows.store import artifact,get,dataset,model_path

def run(root,job_id):
    root=Path(root);engine=make_engine(root);Session=sessions(engine);lease=WorkerLease(root,job_id);gpu=gpu_lease()
    if not lease.acquire():return
    started=time.perf_counter()
    def checkpoint(progress=None,result=None):
        with Session.begin() as db:
            job=db.get(Job,job_id)
            if job.cancel_requested or job.state not in ('QUEUED','RUNNING'):raise Cancelled()
            job.heartbeat=now()
            if progress is not None:job.progress=int(progress)
            if result is not None:job.result=result
    try:
        with Session() as db:
            job=db.get(Job,job_id)
            if not job or job.state!='QUEUED':return
            payload=copy.deepcopy(job.payload);kind=job.kind;project_id=job.project_id
        while not gpu.acquire():checkpoint();time.sleep(.25)
        checkpoint()
        with Session.begin() as db:
            claimed=db.execute(update(Job).where(Job.id==job_id,Job.state=='QUEUED',Job.cancel_requested==False).values(state='RUNNING',heartbeat=now()))
            if claimed.rowcount!=1:raise Cancelled()
        import torch
        from ultralytics import YOLO
        from PIL import Image
        if not torch.cuda.is_available():raise ValueError('CUDA_REQUIRED')
        torch.cuda.reset_peak_memory_stats()
        output=root/'learning_runs'/job_id;output.mkdir(parents=True,exist_ok=True)
        runtime=runtime_versions()
        try:
            runtime['nvidia_driver']=subprocess.check_output(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],text=True,timeout=10,creationflags=0x08000000 if sys.platform=='win32' else 0).strip()
        except (OSError,subprocess.SubprocessError):runtime['nvidia_driver']='UNAVAILABLE'
        runtime['cuda']=torch.version.cuda;runtime['device']=torch.cuda.get_device_name(0)
        (output/'run_manifest.json').write_text(json.dumps({'job_id':job_id,'kind':kind,'payload':payload,'runtime':runtime,'cuda':torch.version.cuda,'device':torch.cuda.get_device_name(0)},indent=2),encoding='utf-8')
        if kind in ('TRAIN','HELPER_EVAL'):
            with Session() as db:
                snap,folder=dataset(db,root,payload['dataset_id'])
                if snap.manifest_hash!=payload['dataset_hash']:raise ValueError('DATASET_HASH_CHANGED')
                models=[copy.deepcopy(get(db,id,'MODEL').payload) for id in ([payload['parent_model_id']] if kind=='TRAIN' else [payload['incumbent_id'],payload['candidate_id']])]
                for m in models:m['weights_path']=str(model_path(db,m))
            for m in models:
                if digest(m['weights_path'])!=m['weights_sha256']:raise ValueError('WEIGHTS_HASH_MISMATCH')
            if kind=='TRAIN':
                p=payload['profile'];epochs=[]
                model=YOLO(payload['resume_path'] if payload.get('resume_path') else models[0]['weights_path'])
                if payload.get('resume_path') and digest(payload['resume_path'])!=payload['resume_sha256']:raise ValueError('RESUME_CHECKPOINT_CHANGED')
                def batch(trainer):checkpoint(min(94,100*(trainer.epoch/max(p['epochs'],1))))
                def epoch(trainer):
                    checkpoint()
                    if trainer.epoch>=p['epochs']:return  # Final best-checkpoint validation is not another training epoch.
                    values={k:float(v) for k,v in trainer.metrics.items()}
                    epochs.append({'epoch':trainer.epoch+1,'metrics':values})
                    checkpoint(min(95,100*(trainer.epoch+1)/p['epochs']),{'epochs':epochs,'elapsed_seconds':time.perf_counter()-started,'last_checkpoint':str(trainer.last),'last_sha256':digest(trainer.last) if trainer.last.is_file() else None})
                model.add_callback('on_train_batch_end',batch);model.add_callback('on_fit_epoch_end',epoch)
                args={**p,'data':str(folder/'data.yaml'),'device':0,'workers':0,'project':str(output),'name':'train','exist_ok':True,'save':True,'plots':False,'verbose':False,'resume':bool(payload.get('resume_path'))}
                model.train(**args)
                checkpoint(96)
                weights=Path(model.trainer.best if model.trainer.best.is_file() else model.trainer.last)
                parent=models[0];profile=copy.deepcopy(parent['profile']);profile['weights_path']=str(weights)
                profile['config']['class_mapping']={'0':'person'}
                model_payload={'baseline':False,'task':'person_detection','class_schema':'person','architecture':'YOLO11n','weights_path':str(weights),'weights_sha256':digest(weights),
                    'profile':profile,'parent_model_id':payload['parent_model_id'],'train_job_id':job_id,'dataset_id':snap.id,'dataset_hash':snap.manifest_hash,
                    'evidence_type':'DEMO' if snap.manifest['purpose']=='DEMO_SMOKE' else 'REAL','runtime':runtime,'license':parent.get('license'),'state':'CANDIDATE'}
                result={'epochs':epochs,'elapsed_seconds':time.perf_counter()-started,'peak_vram_bytes':torch.cuda.max_memory_allocated(),'runtime':runtime,'last_checkpoint':str(model.trainer.last),
                    'last_sha256':digest(model.trainer.last),'best_checkpoint':str(weights),'auto_activated':False}
                publish_kind='MODEL';publish_payload=model_payload
            else:
                results=[]
                val_entries=[e for e in snap.manifest['entries'] if e['split']=='validation']
                with Session() as db:
                    roi_polygons={e['revision']['roi_version_id']:copy.deepcopy(db.get(ROIVersion,e['revision']['roi_version_id']).polygon_source_px) for e in val_entries}
                for model_index,m in enumerate(models):
                    checkpoint(model_index*45)
                    if 'prediction' in locals():del prediction
                    if 'model' in locals():del model
                    gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats()
                    model=YOLO(m['weights_path'])
                    metrics=model.val(data=str(folder/'data.yaml'),split='val',imgsz=payload['imgsz'],batch=1,device=0,workers=0,plots=False,verbose=False,project=str(output),name=f'eval_{model_index}',conf=.001,iou=.7,classes=[0])
                    frames=[]
                    for i,e in enumerate(val_entries):
                        checkpoint(model_index*45+int(40*i/max(len(val_entries),1)))
                        # Same operating point and image inputs for both models; warm-up excluded.
                        image=str(folder/e['image_path']);model.predict(image,imgsz=payload['imgsz'],device=0,conf=payload['confidence'],iou=.7,classes=[0],verbose=False)
                        torch.cuda.synchronize();t=time.perf_counter()
                        prediction=model.predict(image,imgsz=payload['imgsz'],device=0,conf=payload['confidence'],iou=.7,classes=[0],verbose=False)[0]
                        torch.cuda.synchronize();elapsed=time.perf_counter()-t
                        boxes=prediction.boxes.xyxy.cpu().tolist();scores=prediction.boxes.conf.cpu().tolist()
                        from backend.app.workflows.helper import match_boxes,helper_count
                        matches=match_boxes(e['boxes'],boxes,scores)
                        frames.append({'image_path':e['image_path'],'width':e['width'],'height':e['height'],'camera_id':e['camera_id'],'condition':e['condition'],'gt_boxes':e['boxes'],'boxes':boxes,'scores':scores,'latency_seconds':elapsed,'helper_count':helper_count(e,boxes,roi_polygons[e['revision']['roi_version_id']]),**matches})
                    results.append({'model_id':[payload['incumbent_id'],payload['candidate_id']][model_index],'map50':float(metrics.box.map50),'map50_95':float(metrics.box.map),'precision':float(metrics.box.mp),'recall':float(metrics.box.mr),'frames':frames,'peak_vram_bytes':torch.cuda.max_memory_allocated()})
                from backend.app.workflows.helper import gate,diagnostic_slices
                from backend.app.workflows.benchmark import numeric
                for r in results:
                    r['diagnostics']=diagnostic_slices(r['frames']);r['helper_count_metrics']=numeric([f['helper_count']['error'] for f in r['frames'] if f['helper_count']['error'] is not None])
                publish_kind='HELPER_EVALUATION';publish_payload={'dataset_id':snap.id,'dataset_hash':snap.manifest_hash,'incumbent_id':payload['incumbent_id'],'candidate_id':payload['candidate_id'],'results':results,
                    'gate':gate(results,snap.manifest,payload['gate']),'profile':payload,'runtime':runtime,'review_time':'NOT_MEASURED','count_membership':'PER_FRAME_ELIGIBILITY_AND_ESTIMATED_BOX_BOTTOM_PROXY','evidence_type':'DEMO' if snap.manifest['purpose']=='DEMO_SMOKE' else 'REAL',
                    'peak_vram_bytes':torch.cuda.max_memory_allocated(),'elapsed_seconds':time.perf_counter()-started}
                result={'gate':publish_payload['gate'],'elapsed_seconds':publish_payload['elapsed_seconds']}
        elif kind=='AUTO_TRACK':
            with Session() as db:
                media=db.get(Media,payload['media_id']);frames=selected_frames(db,media,payload)
            verify_source(media,full=True)
            if digest(payload['profile']['weights_path'])!=payload['weights_sha256']:raise ValueError('WEIGHTS_HASH_MISMATCH')
            from ultralytics.utils import ROOT as ULTRA_ROOT
            tracker_path=ULTRA_ROOT/'cfg/trackers/bytetrack.yaml'
            tracker_text=tracker_path.read_text()
            model=YOLO(payload['profile']['weights_path']);tracks={};rows=[]
            for i,frame in enumerate(frames):
                checkpoint(int(95*i/max(len(frames),1)))
                image=Image.open(io.BytesIO(frame_image(media,frame,root/'worker_frame_cache',64*1024*1024))).convert('RGB')
                r=model.track(image,persist=True,tracker='bytetrack.yaml',device=0,imgsz=640,classes=[0],conf=.1,iou=.7,verbose=False)[0]
                points=[]
                if r.boxes.id is not None:
                    for box,id,score in zip(r.boxes.xyxy.cpu().tolist(),r.boxes.id.cpu().tolist(),r.boxes.conf.cpu().tolist()):
                        tid=f'{job_id}:{int(id)}';anchor=[(box[0]+box[2])/2,box[3]]
                        point={'frame_id':frame.id,'pts':frame.pts,'time':frame.relative_seconds,'anchor':anchor,'box':box,'score':score,'verified':False,'observed':False,'identity_verified':False,'anchor_origin':'ESTIMATED_BOX_BOTTOM'}
                        tracks.setdefault(tid,[]).append(point);points.append({'track_id':tid,**point})
                rows.append({'frame_id':frame.id,'pts':frame.pts,'time':frame.relative_seconds,'points':points})
            verify_source(media,full=True)
            publish_kind='TRACKS';publish_payload={'media_id':media.id,'source_sha256':media.sha256,'profile':payload,'tracker':'bytetrack.yaml','tracker_config':tracker_text,'tracker_sha256':digest(tracker_path),'runtime':runtime,'reset_policy':'FRESH_MODEL_PER_JOB','tracks':tracks,'frames':rows,'reviewed':False,'evidence_type':media.evidence_type,'actor':payload['actor'],
                'track_flags':{tid:['ANCHORS_ESTIMATED','IDENTITY_UNREVIEWED']+(['SHORT_TRACK'] if ps[-1]['time']-ps[0]['time']<1 else [])+(['OBSERVATION_GAP'] if any(b['time']-a['time']>.25 for a,b in zip(ps,ps[1:])) else []) for tid,ps in tracks.items()},'flags':['ANCHORS_ESTIMATED','IDENTITY_UNREVIEWED'],'elapsed_seconds':time.perf_counter()-started}
            result={'tracks':len(tracks),'frames':len(rows)}
        else:raise ValueError('UNSUPPORTED_JOB_KIND')
        checkpoint(98)
        with Session.begin() as db:
            j=db.get(Job,job_id)
            if j.cancel_requested or j.state!='RUNNING':raise Cancelled()
            record=artifact(db,publish_kind,publish_payload,project_id,payload.get('actor','local_operator'))
            j.state='SUCCEEDED';j.progress=100;j.heartbeat=now();j.result={**result,'artifact_id':record.id}
    except Exception as exc:
        with Session.begin() as db:
            job=db.get(Job,job_id)
            if job and job.state in ('QUEUED','RUNNING'):
                cancelled=isinstance(exc,Cancelled) or job.cancel_requested;job.state='CANCELLED' if cancelled else 'FAILED'
                job.error=None if cancelled else {'code':'CUDA_OOM' if 'out of memory' in str(exc).lower() else getattr(exc,'code','WORKFLOW_FAILED'),'message':str(exc),'retryable':True};job.heartbeat=now()
    finally:
        if 'model' in locals():del model
        gc.collect()
        if 'torch' in sys.modules and sys.modules['torch'].cuda.is_available():sys.modules['torch'].cuda.empty_cache()
        gpu.release();lease.release();engine.dispose()

if __name__=='__main__':run(sys.argv[1],sys.argv[2])
