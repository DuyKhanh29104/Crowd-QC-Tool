import copy
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid
from typing import Literal
from fastapi import APIRouter,Query
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,text,func
from backend.app.db.models import Project,Media,Frame,Job,WorkArtifact,WorkPointer
from backend.app.errors import DomainError
from backend.app.workflows.store import artifact,get,serialize
from backend.app.media.service import verify_source,frame_image
from backend.app.roi.geometry import validate_polygon
from backend.app.jobs.profiles import ROOT
from .analysis import calibration,metrics
from .motion import motion_context,track_history
from .calibration_geometry import bird_eye_layout
from .models import detector_spec,detection_target

class Input(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
class Settings(Input):
    detection_target:Literal['person','head']='person'
    preset:Literal['High Recall','Balanced','High Precision','Custom']='High Recall'
    sampling_fps:float=Field(default=2,ge=.1,le=15)
    imgsz:int=Field(default=640,ge=320,le=1920,multiple_of=32)
    confidence:float=Field(default=.15,ge=.01,le=.95)
    iou:float=Field(default=.7,gt=0,le=1)
    max_det:int=Field(default=300,ge=1,le=5000)
    roi_scale_mode:Literal['source','crop']='source'
    roi_margin_side:float=Field(default=.15,ge=0,le=.5)
    roi_margin_top:float=Field(default=.20,ge=0,le=.5)
    roi_margin_bottom:float=Field(default=.10,ge=0,le=.5)
    person_model_path:str|None=Field(default=None,min_length=1,max_length=1024)
    track_buffer_seconds:float|None=Field(default=None,ge=.1,le=10)
    track_use_low_conf:bool=False
    track_high_thresh:float=Field(default=.25,gt=0,le=1)
    track_low_thresh:float=Field(default=.1,ge=0,lt=1)
    new_track_thresh:float=Field(default=.25,gt=0,le=1)
    match_thresh:float=Field(default=.8,gt=0,le=1)
    density_threshold:float=Field(default=1,gt=0)
    stationary_speed:float=Field(default=.15,gt=0)
    slow_speed:float=Field(default=.3,gt=0)
    stationary_ratio:float=Field(default=.6,ge=0,le=1)
    min_duration:float=Field(default=20,ge=0,le=600)
    tracking:bool=True
    speed:bool=True
    @model_validator(mode='after')
    def tracker_thresholds(self):
        if not self.track_low_thresh < self.track_high_thresh <= self.new_track_thresh:
            raise ValueError('Cần track_low_thresh < track_high_thresh <= new_track_thresh.')
        return self
class Region(Input):
    id:str=Field(default_factory=lambda:uuid.uuid4().hex)
    name:str=Field(min_length=1,max_length=120)
    area_m2:float=Field(gt=0)
    polygon:list[tuple[float,float]]
class Distance(Input):
    points:list[tuple[float,float]]=Field(min_length=2,max_length=2)
    expected_m:float=Field(gt=0)
class SideLengths(Input):
    ab:float=Field(gt=0)
    bc:float=Field(gt=0)
    cd:float=Field(gt=0)
    da:float=Field(gt=0)
class QuadLengths(SideLengths):
    ac:float=Field(gt=0)
    bd:float=Field(gt=0)
class Calibration(Input):
    points:list[tuple[float,float]]=Field(min_length=4,max_length=4)
    width_m:float|None=Field(default=None,gt=0)
    height_m:float|None=Field(default=None,gt=0)
    shape:Literal['rectangle','right_angle','quadrilateral']='rectangle'
    lengths_m:SideLengths|QuadLengths|None=None
    test:Distance|None=None
    plane:Literal['ground','head']='ground'
    reference_height_m:float|None=Field(default=None,gt=0)
    @model_validator(mode='after')
    def reference_plane(self):
        if self.shape=='rectangle' and (self.width_m is None or self.height_m is None):
            raise ValueError('Hình chữ nhật cần AB và BC (m).')
        if self.shape=='right_angle' and (self.lengths_m is None or isinstance(self.lengths_m,QuadLengths)):
            raise ValueError('Góc A vuông: chỉ nhập bốn cạnh AB, BC, CD, DA (m), không nhập đường chéo.')
        if self.shape=='quadrilateral' and not isinstance(self.lengths_m,QuadLengths):
            raise ValueError('Tứ giác cần bốn cạnh AB, BC, CD, DA và hai đường chéo AC, BD (m).')
        if self.plane=='head' and self.reference_height_m is None:
            raise ValueError('Cần nhập độ cao tâm đầu tham chiếu (m).')
        return self
class HeadCalibration(Calibration):
    plane:Literal['head']='head'
    reference_height_m:float=Field(gt=0)
class Profile(Input):
    name:str=Field(min_length=1,max_length=120)
    media_id:str
    rois:list[Region]=Field(min_length=1,max_length=20)
    calibration:Calibration|None=None
    head_calibration:HeadCalibration|None=None
    settings:Settings=Field(default_factory=Settings)
    @model_validator(mode='after')
    def ground_plane(self):
        if self.calibration and self.calibration.plane!='ground':
            raise ValueError('Lưu hiệu chuẩn tâm đầu vào head_calibration, không dùng làm hiệu chuẩn mặt sàn.')
        return self
class ApplyProfile(Input):profile_id:str
class Analyze(Input):
    profile_id:str
    settings:Settings=Field(default_factory=Settings)
    roi_id:str
class Decision(Input):
    decision:Literal['APPROVE','CORRECT','REJECT','UNCERTAIN']
    label:Literal['NORMAL','CROWDED','CONGESTED','UNCERTAIN']
    start:float=Field(ge=0)
    end:float=Field(ge=0)
    count:int|None=Field(default=None,ge=0)
    reviewer:str=Field(default='Local QC',max_length=120)
    notes:str=Field(default='',max_length=4000)
    tags:list[Literal['Occlusion','Very Dense Crowd','Low Light','Small Person','Tracking Error','False Candidate']]=[]
    frame_edits:dict[str,list[dict]]=Field(default_factory=dict)
    boxes_verified:list[str]=[]
    expected_version:int=Field(default=0,ge=0)
    @model_validator(mode='after')
    def scope(self):
        if self.end<self.start:raise ValueError('Kết thúc phải sau thời điểm bắt đầu.')
        return self
class Manual(Input):
    start:float=Field(ge=0)
    end:float=Field(ge=0)
    frame_id:str|None=None
class MotionBox(Input):
    id:str=Field(min_length=1,max_length=120)
    box:tuple[float,float,float,float]
    track_id:str|None=Field(default=None,min_length=1,max_length=120)
    label_type:Literal['person','head']
class MotionFrame(Input):
    frame_id:str
    objects:list[MotionBox]=Field(max_length=10000)
class MotionPreview(Input):
    frames:list[MotionFrame]=Field(min_length=2,max_length=2)
class Export(Input):
    scope:Literal['verified','corrected','hard','custom']='verified'
    review_ids:list[str]=[]
    formats:list[Literal['JSON','CSV','QC Report','Frames','Event Clips','Video Segments','YOLO','COCO','Tracking Data']]=['JSON','CSV']
    name:str=Field(default='QC dataset',min_length=1,max_length=120)


def register(app,root,Session,require,processes):
    router=APIRouter(prefix='/api/qc')
    def latest(db,key):
        p=db.get(WorkPointer,key)
        return (get(db,p.artifact_id),p.version) if p else (None,0)
    def put(db,key,row,version):
        p=db.get(WorkPointer,key)
        if p:p.artifact_id=row.id;p.version=version
        else:db.add(WorkPointer(key=key,artifact_id=row.id,version=version))
    def records(db,kind,project=None):
        q=select(WorkArtifact).where(WorkArtifact.kind==kind)
        if project:q=q.where(WorkArtifact.project_id==project)
        return list(db.scalars(q.order_by(WorkArtifact.created_at.desc())))
    def reviews(db,project):
        query=select(WorkArtifact,WorkPointer.version).join(WorkPointer,WorkPointer.artifact_id==WorkArtifact.id).where(WorkPointer.key.like('qc_review:%'),WorkArtifact.project_id==project)
        return [dict(serialize(row),version=version) for row,version in db.execute(query)]
    def analysis_rows(db,project):
        # List metadata only: do not deserialize every historical frame/box array.
        return db.execute(select(WorkArtifact.id,WorkArtifact.created_at,
            func.json_extract(WorkArtifact.payload,'$.media_id').label('media_id'),
            func.json_extract(WorkArtifact.payload,'$.roi.name').label('roi_name'),
            func.coalesce(func.json_extract(WorkArtifact.payload,'$.detector.target'),func.json_extract(WorkArtifact.payload,'$.settings.detection_target'),'person').label('detection_target'),
            func.json_array_length(WorkArtifact.payload,'$.candidates').label('candidates'))
            .where(WorkArtifact.kind=='QC_ANALYSIS',WorkArtifact.project_id==project)
            .order_by(WorkArtifact.created_at.desc())).all()
    def launch(db,media,kind,payload):
        job=Job(id=uuid.uuid4().hex,project_id=media.id if isinstance(media,Project) else media.project_id,kind=kind,state='QUEUED',progress=0,payload=payload)
        db.add(job);db.flush();return job.id
    def start(job_id,gpu=True):
        python=Path(os.environ.get('CROWD_QC_WORKER_PYTHON',ROOT/'.venv-gpu/Scripts/python.exe')) if gpu else Path(sys.executable)
        logs=root/'job_logs';logs.mkdir(exist_ok=True)
        try:
            with (logs/(job_id+'.log')).open('ab') as log:
                processes[job_id]=subprocess.Popen([str(python),'-m','backend.app.qc.worker',str(root),job_id],cwd=ROOT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,stdout=log,stderr=log)
        except OSError as exc:
            with Session.begin() as db:
                j=db.get(Job,job_id);j.state='FAILED';j.error={'message':str(exc)}

    @router.get('/projects/{id}/workspace')
    def workspace(id:str):
        with Session() as db:
            require(db,Project,id)
            hidden={r.payload['media_id'] for r in records(db,'QC_REMOVED_MEDIA',id)}
            media=[serialize(m) for m in db.scalars(select(Media).where(Media.project_id==id)) if m.id not in hidden]
            analysis=analysis_rows(db,id);r=reviews(db,id);manual=records(db,'QC_MANUAL_CANDIDATE',id)
            for m in media:
                applied,_=latest(db,'qc_camera:'+m['id']);m['profile_id']=applied.payload['profile_id'] if applied else None
                runs=[a for a in analysis if a.media_id==m['id']]
                m['analysis_id']=runs[0].id if runs else None
                m['analysis_runs']=[{'id':a.id,'roi_name':a.roi_name,'created_at':a.created_at,'detection_target':a.detection_target} for a in runs]
                m['candidates']=((runs[0].candidates or 0)+sum(x.payload['analysis_id']==runs[0].id for x in manual)) if runs else 0
                m['reviewed']=sum(x['payload']['analysis_id']==m['analysis_id'] and x['payload']['decision']!='UNCERTAIN' for x in r)
            return {'media':media,'profiles':[serialize(p) for p in records(db,'QC_CAMERA_PROFILE')],
                    'reviews':r,'exports':[serialize(p) for p in records(db,'QC_EXPORT',id)]}

    @router.post('/profiles',status_code=201)
    def save_profile(value:Profile):
        with Session.begin() as db:
            media=require(db,Media,value.media_id)
            for roi in value.rois:validate_polygon(roi.polygon,media.width,media.height)
            calibrations={}
            for key in ('calibration','head_calibration'):
                value_cal=getattr(value,key)
                calibrations[key]=None
                if value_cal is None:continue
                data=value_cal.model_dump()
                for x,y in data['points']+(data['test']['points'] if data['test'] else []):
                    if not(0<=x<=media.width and 0<=y<=media.height):raise DomainError('INVALID_POINTS','Điểm hiệu chuẩn phải nằm trong ảnh.',422)
                try:calibrations[key]=calibration(**data)
                except (ValueError,ZeroDivisionError) as exc:raise DomainError('INVALID_CALIBRATION',str(exc),422)
            payload={**value.model_dump(),**calibrations,'width':media.width,'height':media.height,'evidence_type':media.evidence_type}
            row=artifact(db,'QC_CAMERA_PROFILE',payload,media.project_id)
            link=artifact(db,'QC_CAMERA_ASSIGNMENT',{'media_id':media.id,'profile_id':row.id},media.project_id)
            _,v=latest(db,'qc_camera:'+media.id);put(db,'qc_camera:'+media.id,link,v+1)
            return serialize(row)

    @router.post('/calibration-preview')
    def preview(value:Calibration):
        try:return calibration(**value.model_dump())
        except (ValueError,ZeroDivisionError) as exc:raise DomainError('INVALID_CALIBRATION',str(exc),422)

    @router.post('/media/{id}/profile')
    def apply(id:str,value:ApplyProfile):
        with Session.begin() as db:
            media=require(db,Media,id);profile=get(db,value.profile_id,'QC_CAMERA_PROFILE')
            if (media.width,media.height)!=(profile.payload['width'],profile.payload['height']):raise DomainError('RESOLUTION_MISMATCH','Video và camera profile phải cùng độ phân giải.',422)
            if media.evidence_type!=profile.payload['evidence_type']:raise DomainError('EVIDENCE_MISMATCH','Không dùng profile mô phỏng cho video thực tế.',422)
            row=artifact(db,'QC_CAMERA_ASSIGNMENT',{'media_id':id,'profile_id':profile.id},media.project_id)
            _,v=latest(db,'qc_camera:'+id);put(db,'qc_camera:'+id,row,v+1)
            return {'profile_id':profile.id}

    @router.post('/media/{id}/remove')
    def remove(id:str):
        with Session.begin() as db:
            media=require(db,Media,id)
            if any(j.state in ('RUNNING','QUEUED') and j.payload.get('media_id')==id for j in db.scalars(select(Job).where(Job.project_id==media.project_id))):raise DomainError('JOB_ACTIVE','Chờ phân tích hoàn tất hoặc hủy trước khi bỏ video.',409)
            artifact(db,'QC_REMOVED_MEDIA',{'media_id':id},media.project_id)
            return {'removed_from_project':True,'source_preserved':True}

    @router.post('/media/{id}/analyze',status_code=202)
    def analyze(id:str,value:Analyze):
        with Session.begin() as db:
            media=require(db,Media,id);p=get(db,value.profile_id,'QC_CAMERA_PROFILE');verify_source(media,full=True)
            if (media.width,media.height,media.evidence_type)!=(p.payload['width'],p.payload['height'],p.payload['evidence_type']):raise DomainError('PROFILE_MISMATCH','Profile không khớp video.',422)
            roi=next((r for r in p.payload['rois'] if r['id']==value.roi_id),None)
            if not roi:raise DomainError('ROI_REQUIRED','Chọn ROI của profile.',422)
            spec=detector_spec(value.settings.detection_target,value.settings.person_model_path)
            if not (ROOT/spec['weights']).is_file():raise DomainError('MODEL_MISSING','Chưa có checkpoint cho chế độ đã chọn.',422)
            job=launch(db,media,'QC_ANALYSIS',{'media_id':id,'profile_id':p.id,'camera':p.payload,'roi':roi,'settings':value.settings.model_dump(),'detector':spec})
        start(job);return {'job_id':job}

    @router.get('/analysis/{id}')
    def analysis(id:str,include_rows:bool=True):
        with Session() as db:
            row=get(db,id,'QC_ANALYSIS')
            data=copy.deepcopy(row.payload) if include_rows else {'candidates':copy.deepcopy(row.payload['candidates'])}
            manual=[r.payload['candidate'] for r in records(db,'QC_MANUAL_CANDIDATE',row.project_id) if r.payload['analysis_id']==id]
            data['candidates']+=manual
            prefix='qc_review:'+id+':'
            joined=db.execute(select(WorkPointer.key,WorkArtifact,WorkPointer.version).join(WorkArtifact,WorkPointer.artifact_id==WorkArtifact.id).where(WorkPointer.key.startswith(prefix,autoescape=True)))
            current={key[len(prefix):]:(review,version) for key,review,version in joined}
            for c in data['candidates']:
                review,v=current.get(c['id'],(None,0))
                c.update(review=serialize(review) if review else None,version=v,qc_status=review.payload['decision'] if review else 'PENDING')
            return {'id':id,**data}

    @router.post('/analysis/{id}/candidates',status_code=201)
    def manual_candidate(id:str,value:Manual):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            run=get(db,id,'QC_ANALYSIS');media=require(db,Media,run.payload['media_id'])
            if not value.start<=value.end<=media.duration_seconds:raise DomainError('INVALID_RANGE','Khoảng thời gian vượt video.',422)
            if value.frame_id:
                frame=require(db,Frame,value.frame_id)
                if frame.media_id!=media.id or value.start!=frame.relative_seconds or value.end!=frame.relative_seconds:raise DomainError('INVALID_FRAME','Frame không khớp video hoặc thời điểm.',422)
                for r in records(db,'QC_MANUAL_CANDIDATE',run.project_id):
                    if r.payload['analysis_id']==id and r.payload['candidate'].get('frame_id')==value.frame_id:return r.payload['candidate']
            c={'id':uuid.uuid4().hex,'start':value.start,'end':value.end,'label':'UNCERTAIN','peak_count':None,'avg_speed':None,'stationary':None,'reasons':['QC thêm đoạn cần kiểm để tìm sự kiện AI bỏ sót.'],'manual':True}
            if value.frame_id:c['frame_id']=value.frame_id
            artifact(db,'QC_MANUAL_CANDIDATE',{'analysis_id':id,'candidate':c},run.project_id);return c

    @router.post('/analysis/{id}/motion-preview')
    def preview_motion(id:str,value:MotionPreview):
        with Session() as db:
            run=get(db,id,'QC_ANALYSIS');media=require(db,Media,run.payload['media_id'])
            # Manually supplied identities can be compared even when AI tracking was off.
            settings={**run.payload['settings'],'detection_target':detection_target(run.payload),'tracking':True,'speed':True}
            context=motion_context(run.payload.get('camera',{}),run.payload['roi'],settings)
            rows=[];previous={};last=None
            for sample in value.frames:
                frame=require(db,Frame,sample.frame_id)
                if frame.media_id!=media.id:raise DomainError('INVALID_FRAME','Frame phải thuộc video của lần phân tích.',422)
                if last is not None and frame.relative_seconds<=last:raise DomainError('INVALID_TIME_ORDER','Frame mốc phải nằm trước frame cần đo.',422)
                objects=[o.model_dump() for o in sample.objects]
                if len({o['id'] for o in objects})!=len(objects):raise DomainError('INVALID_OBJECT','ID box phải duy nhất trong frame.',422)
                for obj in objects:
                    a,b,c,d=obj['box']
                    if not 0<=a<c<=media.width or not 0<=b<d<=media.height:raise DomainError('INVALID_BOX','Box phải có kích thước dương và nằm trong ảnh.',422)
                    if obj['label_type']!=settings['detection_target']:raise DomainError('LABEL_TYPE_MISMATCH','Loại box phải khớp lần phân tích.',422)
                row=metrics(objects,run.payload['roi'],settings,previous,frame.relative_seconds,motion=context)
                rows.append({**row,'frame_id':frame.id,'pts':frame.pts,'objects':objects,
                             'time_base_num':frame.time_base_num,'time_base_den':frame.time_base_den})
                previous=track_history(objects,frame.relative_seconds);last=frame.relative_seconds
            return {'status':'MANUAL_PAIR_ESTIMATE_NOT_GT','analysis_id':id,'motion':context,'rows':rows,
                    'media_id':media.id,'source_sha256':media.sha256,'evidence_type':media.evidence_type,
                    'profile_id':run.payload.get('profile_id'),'delta_seconds':rows[1]['time']-rows[0]['time'],
                    'identity_verified':False,'estimator':'ENDPOINT_DISPLACEMENT_OVER_SOURCE_PTS',
                    'max_gap_seconds':2.1/settings['sampling_fps']}

    @router.post('/analysis/{id}/candidates/{candidate_id}/decision',status_code=201)
    def decision(id:str,candidate_id:str,value:Decision):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            run=get(db,id,'QC_ANALYSIS');media=require(db,Media,run.payload['media_id'])
            candidates=run.payload['candidates']+[r.payload['candidate'] for r in records(db,'QC_MANUAL_CANDIDATE',run.project_id) if r.payload['analysis_id']==id]
            candidate=next((c for c in candidates if c['id']==candidate_id),None)
            if not candidate:raise DomainError('NOT_FOUND','Candidate không tồn tại.',404)
            key='qc_review:'+id+':'+candidate_id;old,version=latest(db,key)
            if value.expected_version!=version:raise DomainError('REVIEW_CONFLICT','Kết quả đã được sửa ở phiên khác. Tải lại để đối chiếu.',409)
            if value.end>media.duration_seconds:raise DomainError('INVALID_RANGE','Khoảng sự kiện vượt thời lượng video.',422)
            frames={r['frame_id']:r for r in run.payload['rows']}
            extra_frames=[]
            for fid in (set(value.frame_edits)|set(value.boxes_verified))-frames.keys():
                frame=db.get(Frame,fid)
                if frame and frame.media_id==media.id:
                    sample={'frame_id':fid,'time':frame.relative_seconds,'ordinal':frame.ordinal,'pts':frame.pts,'objects':[]}
                    frames[fid]=sample;extra_frames.append(sample)
            if any(f not in frames for f in set(value.frame_edits)|set(value.boxes_verified)):raise DomainError('INVALID_FRAME','Chỉ sửa frame thuộc video đang kiểm.',422)
            for frame_id,objects in value.frame_edits.items():
                if not value.start<=frames[frame_id]['time']<=value.end:raise DomainError('OUTSIDE_EVENT','Frame sửa phải thuộc khoảng sự kiện.',422)
                ids=[]
                for o in objects:
                    if o.get('label_type',detection_target(run.payload))!=detection_target(run.payload):raise DomainError('LABEL_TYPE_MISMATCH','Loại box phải khớp model của lần phân tích.',422)
                    o['label_type']=detection_target(run.payload)
                    box=o.get('box');track=o.get('track_id');ids.append(o.get('id'))
                    if not box or len(box)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in box) or not(0<=box[0]<box[2]<=media.width and 0<=box[1]<box[3]<=media.height):raise DomainError('INVALID_BOX','Box phải nằm trong ảnh và có kích thước dương.',422)
                    if track is not None and (not isinstance(track,str) or len(track)>120):raise DomainError('INVALID_TRACK','Track ID không hợp lệ.',422)
                    # Client boxes may carry old AI measurements after coordinates/IDs change.
                    # Persist geometry, not stale motion estimates masquerading as corrections.
                    for field in ('anchor','inside','speed','velocity_mps','speed_px_s','velocity_px_s','speed_reason'):
                        o.pop(field,None)
                if len(ids)!=len(set(ids)) or any(not isinstance(i,str) or not i for i in ids):raise DomainError('INVALID_OBJECT','ID người phải duy nhất trong frame.',422)
            if any(not value.start<=frames[f]['time']<=value.end for f in value.boxes_verified):raise DomainError('OUTSIDE_EVENT','Frame xác nhận phải thuộc sự kiện.',422)
            if value.decision=='APPROVE' and (value.start!=candidate['start'] or value.end!=candidate['end'] or value.count is not None or value.frame_edits):
                raise DomainError('CORRECTION_REQUIRED','Có correction; hãy lưu bằng Correct.',422)
            label='NORMAL' if value.decision=='REJECT' else 'UNCERTAIN' if value.decision=='UNCERTAIN' else value.label
            if value.decision=='APPROVE':label=candidate['label']
            if label=='UNCERTAIN' and value.decision not in ('UNCERTAIN','CORRECT'):raise DomainError('LABEL_REQUIRED','Candidate chưa rõ cần Correct hoặc Uncertain.',422)
            payload={**value.model_dump(),'label':label,'analysis_id':id,'candidate_id':candidate_id,'media_id':media.id,'ai':candidate,'verified':label!='UNCERTAIN' and value.decision!='UNCERTAIN',
                     'detection_target':detection_target(run.payload),'frame_samples':extra_frames,'hard_negative':value.decision=='REJECT','evidence_type':media.evidence_type,'previous_review_id':old.id if old else None}
            row=artifact(db,'QC_DECISION',payload,run.project_id,value.reviewer);put(db,key,row,version+1)
            return {**serialize(row),'version':version+1}

    @router.get('/media/{id}/video')
    def video(id:str):
        with Session() as db:
            media=require(db,Media,id);path=verify_source(media)
            return FileResponse(path,filename=media.name,content_disposition_type='inline')

    @router.get('/media/{id}/bird-eye/{profile_id}')
    def bird_eye(id:str,profile_id:str,plane:Literal['ground','head']='ground'):
        from PIL import Image,ImageDraw
        from .analysis import solve
        with Session() as db:
            media=require(db,Media,id);p=get(db,profile_id,'QC_CAMERA_PROFILE').payload
            c=p.get('head_calibration' if plane=='head' else 'calibration')
            if not c:raise DomainError('NO_CALIBRATION','Chưa có hiệu chuẩn cho mặt phẳng này.',422)
            frame=db.scalar(select(Frame).where(Frame.media_id==id).order_by(Frame.ordinal).limit(1))
            image=Image.open(io.BytesIO(frame_image(media,frame,root/'frame_cache',256*1024*1024)))
            (w,h),world_pixels=bird_eye_layout(c)
            # PIL expects inverse coefficients: output pixels to source pixels.
            a=[];b=[]
            for (x,y),(u,v) in zip(world_pixels,c['points']):
                a.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);b.extend([u,v])
            result=image.transform((w,h),Image.Transform.PERSPECTIVE,solve(a,b),Image.Resampling.BICUBIC)
            if c.get('shape') in ('quadrilateral','right_angle'):
                mask=Image.new('L',(w,h),0);ImageDraw.Draw(mask).polygon([tuple(p) for p in world_pixels],fill=255)
                supported=Image.new(result.mode,(w,h),0);supported.paste(result,mask=mask);result=supported
            out=io.BytesIO();result.save(out,format='PNG');return Response(out.getvalue(),media_type='image/png')

    @router.post('/projects/{id}/exports',status_code=202)
    def export(id:str,value:Export):
        with Session.begin() as db:
            project=require(db,Project,id);items=reviews(db,id)
            selected=[]
            for row in items:
                p=row['payload']
                keep=p['verified'] if value.scope=='verified' else p['decision']=='CORRECT' if value.scope=='corrected' else (p['decision'] in ('CORRECT','REJECT','UNCERTAIN') or bool(p['tags'])) if value.scope=='hard' else row['id'] in value.review_ids
                if keep:selected.append(row['id'])
            if not selected:raise DomainError('EMPTY_DATASET','Chưa có kết quả QC phù hợp bộ lọc.',422)
            if not value.formats:raise DomainError('FORMAT_REQUIRED','Chọn ít nhất một định dạng.',422)
            targets={r['payload'].get('detection_target','person') for r in items if r['id'] in selected}
            if len(targets)>1 and any(f in value.formats for f in ('YOLO','COCO')):raise DomainError('MIXED_LABEL_TYPES','Xuất bbox người và bbox đầu thành hai dataset riêng; dùng Custom selection để chọn cùng loại.',422)
            if any(f in value.formats for f in ('YOLO','COCO')) and not any(r['payload']['boxes_verified'] and r['payload']['verified'] for r in items if r['id'] in selected):raise DomainError('BOX_REVIEW_REQUIRED','YOLO/COCO cần ít nhất một frame đã xác nhận kiểm đủ box; duyệt event chưa đủ.',422)
            job=launch(db,project,'QC_EXPORT',{'project_id':id,**value.model_dump(),'review_ids':selected})
        start(job,False);return {'job_id':job}

    @router.get('/exports/{id}/download')
    def download(id:str):
        with Session() as db:
            row=get(db,id,'QC_EXPORT');path=root/'qc_exports'/(row.id+'.zip')
            if not path.is_file():raise DomainError('EXPORT_MISSING','File xuất không còn. Tạo lại dataset.',404)
            return FileResponse(path,filename='crowd-qc-'+row.id[:8]+'.zip')
    app.include_router(router)
