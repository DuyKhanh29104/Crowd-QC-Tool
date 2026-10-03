"""Five-screen QC analysis/export jobs, isolated from the API process."""
import copy
from contextlib import closing, contextmanager
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from sqlalchemy import select,update
from backend.app.db.models import Job,Media,Frame,now
from backend.app.db.session import make_engine,sessions
from backend.app.media.lease import WorkerLease
from backend.app.media.service import verify_source,frame_image,digest
from backend.app.jobs.profiles import gpu_lease,ROOT,runtime_versions
from backend.app.jobs.worker import selected_frames,Cancelled
from backend.app.workflows.store import artifact,get
from .analysis import metrics,candidates
from .motion import motion_context,track_history
from .inference import ROIInput
from .decoding import sampled_images
from .models import detector_spec,detector_path,detection_target


def review_samples(run, review):
    return sorted(run['rows']+review.get('frame_samples',[]),key=lambda r:r['time'])


@contextmanager
def staged_archive(path):
    """Publish a complete ZIP only; remove staging data on failure/cancellation."""
    partial=path.with_suffix('.partial')
    try:
        with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as archive:
            yield archive
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def export_clip(media, start, end, path, checkpoint, timeout=600):
    # stream_index is the absolute container index, not the video-only index.
    command=['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(media.source_path),
             '-t',str(end-start),'-map',f'0:{media.stream_index}','-an','-c:v','libx264',
             '-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
    # A file avoids stderr pipe deadlocks while waiting and checking cancellation.
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=errors,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        started=time.monotonic()
        try:
            while True:
                checkpoint()
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
                try:
                    code=process.wait(timeout=min(1,remaining))
                except subprocess.TimeoutExpired:
                    continue
                if code:
                    errors.seek(0,io.SEEK_END)
                    errors.seek(max(0,errors.tell()-4000))
                    raise subprocess.CalledProcessError(code,command,stderr=errors.read())
                return
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def export_dataset(db,root,payload,checkpoint):
    from PIL import Image
    reviews=[get(db,id,'QC_DECISION') for id in payload['review_ids']]
    runs={};sources={}
    for review in reviews:
        p=review.payload
        if p['analysis_id'] not in runs:runs[p['analysis_id']]=get(db,p['analysis_id'],'QC_ANALYSIS').payload
        if p['media_id'] not in sources:
            media=db.get(Media,p['media_id']);verify_source(media,True);sources[media.id]=media
    targets={detection_target(run) for run in runs.values()}
    if len(targets)>1 and any(f in payload['formats'] for f in ('YOLO','COCO')):raise ValueError('MIXED_LABEL_TYPES')
    class_name=next(iter(targets)) if len(targets)==1 else 'mixed'
    id=uuid.uuid4().hex;folder=root/'qc_exports';folder.mkdir(exist_ok=True)
    path=folder/(id+'.zip');formats=payload['formats']
    rows=[];coco={'images':[],'annotations':[],'categories':[{'id':0,'name':class_name}]};excluded=[];exported=set();box_versions={}
    for review in reviews:
        p=review.payload;run=runs[p['analysis_id']]
        if not p['verified']:continue
        for sample in review_samples(run,p):
            if sample['frame_id'] in p['boxes_verified']:
                boxes=p['frame_edits'].get(sample['frame_id'],sample['objects'])
                box_versions.setdefault(sample['frame_id'],set()).add(json.dumps(sorted([o['box'] for o in boxes]),sort_keys=True))
    with staged_archive(path) as z:
        for index,review in enumerate(reviews):
            checkpoint(int(90*index/max(1,len(reviews))))
            p=review.payload;run=runs[p['analysis_id']];media=sources[p['media_id']];verify_source(media)
            rows.append({'review_id':review.id,'candidate_id':p['candidate_id'],'video':media.name,'start':p['start'],'end':p['end'],'ai_label':p['ai']['label'],'human_label':p['label'],'ai_count':p['ai']['peak_count'],'human_count':p['count'],
                         'decision':p['decision'],'detection_target':detection_target(run),'verified':p['verified'],'hard_negative':p['hard_negative'],'tags':p['tags'],'notes':p['notes'],'reviewer':p['reviewer'],'evidence_type':p['evidence_type'],'corrections':p['frame_edits']})
            samples=[r for r in review_samples(run,p) if p['start']<=r['time']<=p['end']]
            if 'Tracking Data' in formats:z.writestr(f'tracking/{review.id}.json',json.dumps({'ai_samples':samples,'human_frame_corrections':p['frame_edits'],'human_box_verified_frames':p['boxes_verified'],'track_identity_verified':False,'motion':run.get('motion'),'speed_units':{'speed':'m/s','velocity_mps':'[vx, vy] m/s in calibration axes','speed_px_s':'px/s','velocity_px_s':'[vx, vy] px/s in image axes'}},ensure_ascii=False))
            if any(f in formats for f in ('Event Clips','Video Segments')):
                if p['end']<=p['start']:excluded.append({'review':review.id,'reason':'ZERO_DURATION_CLIP'})
                else:
                    clip=folder/(id+'_'+review.id+'.mp4')
                    try:
                        export_clip(media,p['start'],p['end'],clip,checkpoint)
                        z.write(clip,f'clips/{review.id}.mp4')
                    finally:clip.unlink(missing_ok=True)
            if any(f in formats for f in ('Frames','YOLO','COCO')):
                for sample in samples:
                    checkpoint()
                    frame_id=sample['frame_id'];qualified=p['verified'] and frame_id in p['boxes_verified'] and len(box_versions.get(frame_id,set()))==1
                    # One source frame once per dataset; conflicting revisions are never merged.
                    export_key=('train' if qualified else 'preview',frame_id)
                    if export_key in exported:continue
                    if 'Frames' not in formats and not qualified:continue
                    frame=db.get(Frame,frame_id);data=frame_image(media,frame,root/'worker_frame_cache',64*1024*1024)
                    image=Image.open(io.BytesIO(data));out=io.BytesIO();image.convert('RGB').save(out,'JPEG',quality=95)
                    image_name=f"{'images' if qualified else 'frames'}/{frame_id}.jpg";z.writestr(image_name,out.getvalue());exported.add(export_key)
                    if qualified:
                        objects=p['frame_edits'].get(frame_id,sample['objects']);labels=[];image_id=len(coco['images'])+1
                        coco['images'].append({'id':image_id,'file_name':image_name,'width':media.width,'height':media.height,'review_id':review.id,'source_frame_id':frame_id})
                        for obj in objects:
                            a,b,c,d=obj['box'];labels.append(f'0 {(a+c)/2/media.width:.8f} {(b+d)/2/media.height:.8f} {(c-a)/media.width:.8f} {(d-b)/media.height:.8f}')
                            coco['annotations'].append({'id':len(coco['annotations'])+1,'image_id':image_id,'category_id':0,'bbox':[a,b,c-a,d-b],'area':(c-a)*(d-b),'iscrowd':0})
                        if 'YOLO' in formats:z.writestr(f'labels/{frame_id}.txt','\n'.join(labels)+('\n' if labels else ''))
                    else:excluded.append({'frame':frame_id,'reason':'CONFLICTING_BOX_REVIEWS' if len(box_versions.get(frame_id,set()))>1 else 'BOXES_NOT_HUMAN_VERIFIED','training_eligible':False})
        if 'JSON' in formats:z.writestr('events.json',json.dumps(rows,ensure_ascii=False,indent=2))
        if 'CSV' in formats:
            out=io.StringIO();fields=[k for k in rows[0] if k!='corrections'];writer=csv.DictWriter(out,fields,extrasaction='ignore');writer.writeheader()
            for row in rows:
                safe={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()}
                writer.writerow(safe)
            z.writestr('events.csv','\ufeff'+out.getvalue())
        if 'COCO' in formats:z.writestr('annotations.coco.json',json.dumps(coco,ensure_ascii=False))
        if 'YOLO' in formats:z.writestr('data.yaml',f'path: .\ntrain: images\nnames:\n  0: {class_name}\n')
        if 'QC Report' in formats:
            from html import escape
            body=''.join('<tr>'+''.join('<td>'+escape(str(row[k]))+'</td>' for k in ('video','start','end','ai_label','human_label','decision','reviewer'))+'</tr>' for row in rows)
            z.writestr('qc-report.html','<!doctype html><meta charset="utf-8"><title>QC report</title><style>body{font:14px system-ui;margin:32px}td,th{padding:12px;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style><h1>'+escape(payload['name'])+'</h1><p>Human QC events. Event approval does not approve all person boxes or track identity.</p><table><tr><th>Video</th><th>Start</th><th>End</th><th>AI</th><th>Human</th><th>Decision</th><th>Reviewer</th></tr>'+body+'</table>')
        manifest={'name':payload['name'],'detection_targets':sorted(targets),'scope':payload['scope'],'formats':formats,'review_ids':payload['review_ids'],'reviews':len(rows),'frames':len(exported),'verified_box_frames':len(coco['images']),'excluded':excluded,'split_policy':'UNASSIGNED: split by source/session before training','created_at':now(),'event_approval_is_not_box_or_track_approval':True}
        z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        for media in sources.values():verify_source(media,True)
        checkpoint(98)
    return id,manifest


def run(root,job_id):
    root=Path(root);engine=make_engine(root);Session=sessions(engine);lease=WorkerLease(root,job_id);gpu=gpu_lease()
    if not lease.acquire():
        engine.dispose()
        return
    def checkpoint(progress=None,stage=None):
        with Session.begin() as db:
            j=db.get(Job,job_id)
            if j.cancel_requested or j.state not in ('RUNNING','QUEUED'):raise Cancelled()
            j.heartbeat=now()
            if progress is not None:j.progress=int(progress)
            if stage:j.result={'stage':stage}
    try:
        with Session() as db:
            j=db.get(Job,job_id);payload=copy.deepcopy(j.payload);project_id=j.project_id;kind=j.kind
        if kind=='QC_ANALYSIS':
            while not gpu.acquire():checkpoint(stage='Chờ GPU');time.sleep(.25)
        with Session.begin() as db:
            changed=db.execute(update(Job).where(Job.id==job_id,Job.state=='QUEUED',Job.cancel_requested==False).values(state='RUNNING',heartbeat=now()))
            if changed.rowcount!=1:raise Cancelled()
        if kind=='QC_EXPORT':
            with Session() as db:id,manifest=export_dataset(db,root,payload,checkpoint)
            with Session.begin() as db:
                checkpoint(98)
                a=artifact(db,'QC_EXPORT',{**manifest,'zip_sha256':digest(root/'qc_exports'/(id+'.zip'))},project_id)
                (root/'qc_exports'/(id+'.zip')).rename(root/'qc_exports'/(a.id+'.zip'))
                j=db.get(Job,job_id);j.state='SUCCEEDED';j.progress=100;j.result={'artifact_id':a.id};j.heartbeat=now()
            return
        import torch
        from ultralytics import YOLO
        if not torch.cuda.is_available():raise ValueError('CUDA_UNAVAILABLE: không tự chuyển sang CPU.')
        with Session() as db:
            media=db.get(Media,payload['media_id']);verify_source(media,True)
            settings=payload['settings'];frames=selected_frames(db,media,{'every_seconds':1/settings['sampling_fps'],'start_seconds':0,'end_seconds':media.duration_seconds,'max_frames':media.frame_count+1})
        target=settings.get('detection_target','person');spec=payload.get('detector') or detector_spec(target,settings.get('person_model_path'))
        weights,checksum=detector_path(spec);model=YOLO(str(weights))
        if model.names.get(0)!=target or model.task!='detect':raise ValueError('MODEL_CLASS_MISMATCH')
        payload['detector']={**spec,'sha256':checksum}
        rows=[];previous={};motion=motion_context(payload['camera'],payload['roi'],settings)
        roi_input=ROIInput(media.width,media.height,payload['roi']['polygon'],base_size=settings.get('imgsz',640),target=target,settings=settings);inference_seconds=0.;decode_seconds=0.
        with closing(sampled_images(media,frames,checkpoint)) as images:
            for index,(frame,image,decode_time) in enumerate(images):
                decode_seconds+=decode_time
                checkpoint(2+int(92*index/max(1,len(frames))),f'Phát hiện & theo dõi: {frame.relative_seconds:.1f} / {media.duration_seconds:.1f} giây')
                started=time.perf_counter()
                objects=roi_input.infer(model,image,settings)
                inference_seconds+=time.perf_counter()-started
                row=metrics(objects,payload['roi'],settings,previous,frame.relative_seconds,motion=motion)
                row.update(frame_id=frame.id,ordinal=frame.ordinal,pts=frame.pts,objects=objects);rows.append(row)
                previous=track_history(objects,frame.relative_seconds)
        checkpoint(96,'Tạo candidates')
        events=candidates(rows,settings,media.duration_seconds)
        result={**payload,'rows':rows,'candidates':events,'duration':media.duration_seconds,'source_sha256':media.sha256,'evidence_type':media.evidence_type,
                'inference':{**roi_input.metadata(),'crop_predict_track_seconds':inference_seconds,'decode_seconds':decode_seconds,'decode_mode':'SEQUENTIAL_PTS_NO_PNG_CACHE','sample_count':len(rows)},
                'runtime':runtime_versions(),'device':str(next(model.model.parameters()).device),'speed_available':motion['metric_available'],
                'motion':motion,'measurements':'AI_ESTIMATES_NOT_GT','completed_at':now()}
        verify_source(media,True)
        with Session.begin() as db:
            j=db.get(Job,job_id)
            if j.cancel_requested:raise Cancelled()
            a=artifact(db,'QC_ANALYSIS',result,project_id);j.state='SUCCEEDED';j.progress=100;j.heartbeat=now();j.result={'artifact_id':a.id,'candidates':len(events),'frames':len(rows)}
    except Exception as exc:
        with Session.begin() as db:
            j=db.get(Job,job_id)
            if j and j.state in ('QUEUED','RUNNING'):
                j.state='CANCELLED' if isinstance(exc,Cancelled) or j.cancel_requested else 'FAILED';j.error=None if j.state=='CANCELLED' else {'message':str(exc),'retryable':True};j.heartbeat=now()
    finally:gpu.release();lease.release();engine.dispose()

if __name__=='__main__':run(sys.argv[1],sys.argv[2])
