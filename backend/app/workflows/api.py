import copy
import json
import os
from pathlib import Path
import subprocess
import uuid
from typing import Literal
from fastapi import APIRouter
from fastapi.responses import Response,FileResponse
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,text
from backend.app.db.models import Job,Media,Project,GTSnapshot,WorkArtifact,WorkPointer
from backend.app.jobs.profiles import ROOT,profile,json_hash
from backend.app.media.service import digest,verify_source
from backend.app.errors import DomainError
from backend.app.gt.observations import classify_speed,possible_levels,possible_classes,metric
from fractions import Fraction
from .store import artifact,get,serialize,dataset,baseline,active_model,model_path,disposition
from .benchmark import parse_sut,evaluate
from .motion import fit_calibration,population_speed,draft_motion

class Input(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
class TrainProfile(Input):
    epochs:int=Field(default=1,ge=1,le=100)
    imgsz:int=Field(default=640,ge=320,le=640,multiple_of=32)
    batch:Literal[1]=1
    amp:Literal[False]=False
    seed:int=42
    deterministic:Literal[True]=True
    optimizer:Literal['SGD']='SGD'
    lr0:float=Field(default=.001,gt=0,le=.1)
    mosaic:float=Field(default=0,ge=0,le=1)
    mixup:Literal[0]=0
    fliplr:float=Field(default=0,ge=0,le=1)
    hsv_h:Literal[0]=0
    hsv_s:Literal[0]=0
    hsv_v:Literal[0]=0
    translate:Literal[0]=0
    scale:Literal[0]=0
    degrees:Literal[0]=0
    close_mosaic:Literal[0]=0
class Train(Input):
    dataset_id:str
    parent_model_id:str
    profile:TrainProfile=Field(default_factory=TrainProfile)
    actor:str=Field(min_length=1,max_length=120)
class Gate(Input):
    min_images:int=Field(default=20,ge=1)
    min_images_per_slice:int=Field(default=5,ge=1)
    min_map_gain:float|None=None
    max_slice_recall_drop:float|None=Field(default=None,ge=0,le=1)
    max_latency_seconds:float|None=Field(default=None,gt=0)
    max_vram_bytes:int|None=Field(default=None,gt=0)
class Compare(Input):
    dataset_id:str
    incumbent_id:str
    candidate_id:str
    imgsz:int=Field(default=640,ge=320,le=640,multiple_of=32)
    confidence:float=Field(default=.25,gt=0,lt=1)
    gate:Gate=Field(default_factory=Gate)
    actor:str=Field(min_length=1,max_length=120)
class Activate(Input):
    model_id:str
    evaluation_id:str|None=None
    namespace:Literal['DEMO','REAL']='DEMO'
    expected_version:int=Field(ge=0)
    rollback:bool=False
    reason:str=Field(min_length=1,max_length=2000)
    actor:str=Field(min_length=1,max_length=120)
class SUTConfig(Input):
    format:Literal['JSON','CSV']='JSON'
    delimiter:str=Field(default=',',min_length=1,max_length=1)
    decimal:Literal['.',',']='.'
    mapping:dict[str,str]
    units:dict[str,str]
    semantics:dict[str,Literal['INSTANT','PAST_WINDOW','UNKNOWN']]
    time_origin:Literal['MEDIA_START','SOURCE_START','WALL_CLOCK']='MEDIA_START'
    media_zero_iso:str|None=None
    offset_seconds:float=0
    offset_evidence:str=Field(min_length=1,max_length=2000)
    camera_value:str
    roi_value:str
    evidence_type:Literal['DEMO','REAL']='DEMO'
    raw_precision_confirmed:bool=False
    rules_hash:str|None=None
    @model_validator(mode='after')
    def check_origin(self):
        if self.time_origin=='WALL_CLOCK' and not self.media_zero_iso:raise ValueError('WALL_CLOCK requires media_zero_iso with timezone')
        if not self.mapping.get('timestamp'):raise ValueError('Explicit timestamp mapping is required')
        return self
class SUTInput(Input):
    media_id:str
    roi_version_id:str
    raw_text:str=Field(max_length=10000000)
    config:SUTConfig
    actor:str=Field(min_length=1,max_length=120)
class Evaluation(Input):
    gt_snapshot_id:str
    sut_id:str
    tolerance_seconds:float=Field(default=0,ge=0,le=5)
    condition:str=Field(default='UNSPECIFIED',min_length=1,max_length=120)
    actor:str=Field(min_length=1,max_length=120)
class Pool(Input):
    evaluation_ids:list[str]=Field(min_length=1,max_length=100)
    actor:str=Field(min_length=1,max_length=120)
class Trial(Input):
    pair_id:str=Field(min_length=1,max_length=120)
    camera:str=Field(min_length=1,max_length=120)
    condition:str=Field(min_length=1,max_length=120)
    split:Literal['development','validation','locked_test']='validation'
    order:Literal['MANUAL_FIRST','AI_FIRST']
    manual_active_seconds:float=Field(ge=0)
    assisted_active_seconds:float=Field(ge=0)
    manual_total_seconds:float=Field(ge=0)
    assisted_total_seconds:float=Field(ge=0)
    manual_quality:float=Field(ge=0,le=1)
    assisted_quality:float=Field(ge=0,le=1)
    independent_quality_checked:bool
    edits:int=Field(ge=0)
    unchanged_objects:int=Field(ge=0)
    objects:int=Field(ge=0)
    @model_validator(mode='after')
    def validate_totals(self):
        if self.manual_total_seconds<self.manual_active_seconds or self.assisted_total_seconds<self.assisted_active_seconds:raise ValueError('TOTAL_TIME_MUST_INCLUDE_ACTIVE_TIME')
        if self.unchanged_objects>self.objects:raise ValueError('UNCHANGED_EXCEEDS_POPULATION')
        return self
class EffortStudy(Input):
    rows:list[Trial]=Field(min_length=1,max_length=10000)
    evidence_type:Literal['DEMO','REAL']='DEMO'
    protocol_evidence:str=Field(min_length=1,max_length=4000)
    max_quality_drop:float=Field(default=0,ge=0,le=1)
    actor:str=Field(min_length=1,max_length=120)
class Control(Input):
    pixel:tuple[float,float]
    world:tuple[float,float]
    role:Literal['FIT','HOLDOUT']
    region:Literal['NEAR','MID','FAR']
class Length(Input):
    a:tuple[float,float]
    b:tuple[float,float]
    meters:float=Field(gt=0)
    region:Literal['NEAR','MID','FAR']
class Calibration(Input):
    media_id:str
    roi_version_id:str
    points:list[Control]=Field(min_length=6,max_length=100)
    lengths:list[Length]=Field(max_length=100)
    repeatability_mps:float|None=Field(default=None,ge=0)
    evidence:str=Field(min_length=1,max_length=4000)
    approve:bool=False
    actor:str=Field(min_length=1,max_length=120)
class Track(Input):
    media_id:str
    start_seconds:float=Field(default=0,ge=0)
    end_seconds:float=Field(gt=0)
    every_seconds:float=Field(default=.1,ge=.025,le=1)
    max_frames:int=Field(default=300,ge=2,le=1000)
    actor:str=Field(min_length=1,max_length=120)
class TrackPoint(Input):
    frame_id:str
    pts:int
    time:float=Field(ge=0)
    anchor:tuple[float,float]
    box:tuple[float,float,float,float]|None=None
    score:float|None=None
    verified:bool=False
    observed:bool=False
    identity_verified:bool=False
    anchor_origin:str='HUMAN'
class TrackReview(Input):
    expected_revision_id:str
    tracks:dict[str,list[TrackPoint]]
    reviewed:bool=False
    viewed_complete_window:bool=False
    start_seconds:float=Field(ge=0)
    end_seconds:float=Field(gt=0)
    notes:str=Field(min_length=1,max_length=4000)
    actor:str=Field(min_length=1,max_length=120)
class MotionHints(Input):
    max_gap_seconds:float=Field(default=.25,gt=0,le=.5)
    boundary_pixels:float=Field(default=10,ge=0,le=100)
    max_speed_mps:float=Field(default=5,gt=0,le=100)
    jitter_displacement_m:float=Field(default=.05,ge=0,le=1)
    jitter_path_m:float=Field(default=.1,gt=0,le=10)
class MotionPreview(Input):
    calibration_id:str
    tracks:dict[str,list[TrackPoint]]
    start_seconds:float=Field(ge=0)
    end_seconds:float=Field(gt=0)
    config:MotionHints=Field(default_factory=MotionHints)
class SpeedSnapshot(Input):
    gt_snapshot_id:str
    calibration_id:str
    track_revision_id:str
    window_seconds:Literal[1]=1
    max_gap_seconds:float=Field(default=.25,gt=0,le=.5)
    speed_uncertainty_mps:float|None=Field(default=None,ge=0,le=100)
    uncertainty_evidence:str|None=Field(default=None,max_length=4000)
    actor:str=Field(min_length=1,max_length=120)
    @model_validator(mode='after')
    def bound_requires_evidence(self):
        if self.speed_uncertainty_mps is not None and not self.uncertainty_evidence:raise ValueError('Measured uncertainty bound requires evidence')
        return self
class BackupInput(Input):
    include_sources:bool=False
class ModelDecision(Input):
    state:Literal['REJECTED','ARCHIVED','CANDIDATE']
    reason:str=Field(min_length=1,max_length=2000)
    actor:str=Field(min_length=1,max_length=120)

def register(app,root,Session,require,processes):
    router=APIRouter(prefix='/api/workflows')
    def launch(id):
        folder=root/'job_logs';folder.mkdir(parents=True,exist_ok=True)
        python=Path(os.environ.get('CROWD_QC_WORKER_PYTHON',ROOT/'.venv-gpu/Scripts/python.exe'))
        try:
            with (folder/f'{id}.log').open('ab') as log:processes[id]=subprocess.Popen([str(python),'-m','backend.app.workflows.worker',str(root),id],cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,stdout=log,stderr=log)
        except OSError as exc:
            with Session.begin() as db:
                job=db.get(Job,id);job.state='FAILED';job.error={'code':'WORKER_START_FAILED','message':str(exc)}
    def enqueue(db,kind,project,payload):
        row=Job(id=uuid.uuid4().hex,project_id=project,kind=kind,state='QUEUED',progress=0,payload=payload);db.add(row);db.flush();return row.id

    @router.get('/projects/{id}/artifacts')
    def artifacts(id:str,kind:str|None=None):
        with Session() as db:
            q=select(WorkArtifact).where(WorkArtifact.project_id==id)
            if kind:q=q.where(WorkArtifact.kind==kind)
            return [serialize(r) for r in db.scalars(q.order_by(WorkArtifact.created_at.desc()).limit(500))]
    @router.get('/artifacts/{id}')
    def detail(id:str):
        with Session() as db:return serialize(get(db,id))
    @router.get('/learning')
    def learning():
        with Session.begin() as db:
            default=baseline(db)
            active_ids={active_model(db,ns).id for ns in ('DEMO','REAL')};models=[]
            comparisons=list(db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='HELPER_EVALUATION').order_by(WorkArtifact.created_at.desc())))
            for m in db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='MODEL').order_by(WorkArtifact.created_at.desc())):
                ev=next((e for e in comparisons if e.payload['candidate_id']==m.id),None)
                state='ACTIVE' if m.id in active_ids else disposition(db,m.id) or ('ELIGIBLE' if ev and ev.payload['gate']['verdict']=='ELIGIBLE' else 'CANDIDATE')
                models.append({**serialize(m),'registry_state':state})
            return {'models':models,
                'active':{ns:{'model_id':active_model(db,ns).id,'version':db.get(WorkPointer,'active_model:'+ns).version if db.get(WorkPointer,'active_model:'+ns) else 0} for ns in ('DEMO','REAL')},'baseline_id':default.id}
    @router.post('/models/{id}/decision',status_code=201)
    def model_decision(id:str,value:ModelDecision):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'));model=get(db,id,'MODEL')
            if id in {active_model(db,ns).id for ns in ('DEMO','REAL')}:raise DomainError('ACTIVE_MODEL_PROTECTED','Switch active model before rejecting/archiving.',409)
            return serialize(artifact(db,'MODEL_DECISION',{'model_id':id,**value.model_dump()},model.project_id,value.actor))
    @router.get('/jobs/{id}/log')
    def log(id:str):
        with Session() as db:require(db,Job,id)
        path=root/'job_logs'/f'{id}.log'
        if not path.exists():return {'text':''}
        with path.open('rb') as f:f.seek(max(0,path.stat().st_size-40000));data=f.read()
        return {'text':data.decode('utf-8',errors='replace')}

    @router.post('/train',status_code=202)
    def train(value:Train):
        with Session.begin() as db:
            snap,_=dataset(db,root,value.dataset_id);parent=get(db,value.parent_model_id,'MODEL')
            if disposition(db,parent.id)=='ARCHIVED':raise DomainError('MODEL_ARCHIVED','Restore candidate status before using this parent.',409)
            if not all(snap.manifest['summary']['splits'].get(k) for k in ('train','validation')):raise DomainError('TRAIN_VALIDATION_REQUIRED','Cần train và validation ở group tách biệt.',409)
            if snap.manifest['purpose']!='DEMO_SMOKE' and parent.payload['evidence_type']=='DEMO':raise DomainError('DEMO_PARENT_BLOCKED','Không dùng checkpoint DEMO làm parent dữ liệu thật.',409)
            payload={**value.model_dump(),'dataset_hash':snap.manifest_hash,'parent_weights_hash':parent.payload['weights_sha256']}
            id=enqueue(db,'TRAIN',snap.project_id,payload)
        launch(id);return {'job_id':id}
    @router.post('/compare',status_code=202)
    def compare(value:Compare):
        with Session.begin() as db:
            snap,_=dataset(db,root,value.dataset_id)
            if not snap.manifest['summary']['splits'].get('validation'):raise DomainError('VALIDATION_REQUIRED','Không dùng locked test thay validation.',409)
            for id in (value.incumbent_id,value.candidate_id):get(db,id,'MODEL')
            id=enqueue(db,'HELPER_EVAL',snap.project_id,{**value.model_dump(),'dataset_hash':snap.manifest_hash})
        launch(id);return {'job_id':id}
    @router.post('/jobs/{id}/retry',status_code=202)
    def retry(id:str,resume:bool=False):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'));job=require(db,Job,id)
            if job.kind not in ('TRAIN','HELPER_EVAL','AUTO_TRACK') or job.state not in ('FAILED','CANCELLED','INTERRUPTED'):raise DomainError('RETRY_NOT_ALLOWED','Job chưa ở trạng thái có thể retry.',409)
            payload=copy.deepcopy(job.payload)
            if resume:
                path=root/'learning_runs'/id/'train/weights/last.pt'
                if job.kind!='TRAIN' or not path.is_file():raise DomainError('NO_RESUMABLE_CHECKPOINT','Chưa có checkpoint đầy đủ; tạo run mới.',409)
                dataset(db,root,payload['dataset_id']);payload.update(resume_path=str(path),resume_sha256=digest(path))
                job.payload=payload;job.state='QUEUED';job.cancel_requested=False;job.error=None;job.heartbeat=None;new_id=id
            else:
                payload.pop('resume_path',None);payload.pop('resume_sha256',None);payload['retry_of']=id;new_id=enqueue(db,job.kind,job.project_id,payload)
        launch(new_id);return {'job_id':new_id,'resume':resume}
    @router.post('/activate')
    def activate(value:Activate):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'));target=get(db,value.model_id,'MODEL');old=active_model(db,value.namespace);key='active_model:'+value.namespace;pointer=db.get(WorkPointer,key)
            if (pointer.version if pointer else 0)!=value.expected_version:raise DomainError('VERSION_CONFLICT','Active model đã đổi.',409)
            if disposition(db,target.id) in ('REJECTED','ARCHIVED'):raise DomainError('MODEL_NOT_CANDIDATE','Restore candidate status before activation.',409)
            if value.namespace=='REAL' and target.payload['evidence_type']=='DEMO':raise DomainError('DEMO_ACTIVATION_BLOCKED','Model DEMO chỉ kích hoạt cho video DEMO.',409)
            if digest(model_path(db,target.payload))!=target.payload['weights_sha256']:raise DomainError('WEIGHTS_HASH_MISMATCH','Checkpoint không khớp hash.',409)
            if value.rollback:
                history=list(db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='ACTIVATION')))
                if not any(r.payload['namespace']==value.namespace and value.model_id in (r.payload['from_model_id'],r.payload['to_model_id']) for r in history):raise DomainError('NO_ROLLBACK_HISTORY','Model chưa có trong lịch sử active.',409)
            else:
                ev=get(db,value.evaluation_id,'HELPER_EVALUATION') if value.evaluation_id else None
                if not ev or ev.payload['candidate_id']!=target.id or ev.payload['incumbent_id']!=old.id:raise DomainError('COMPARISON_REQUIRED','Cần evaluation cùng incumbent hiện tại và candidate.',409)
                if value.namespace=='REAL' and ev.payload['gate']['verdict']!='ELIGIBLE':raise DomainError('GATE_NOT_PASSED','Candidate chưa đủ bằng chứng qua gate.',409)
                if value.namespace=='DEMO' and ev.payload['evidence_type']!='DEMO':raise DomainError('NAMESPACE_MISMATCH','Evaluation khác phạm vi DEMO.',409)
                dataset(db,root,ev.payload['dataset_id'])
            next_version=(pointer.version if pointer else 0)+1
            if pointer:pointer.artifact_id=target.id;pointer.version=next_version
            else:db.add(WorkPointer(key=key,artifact_id=target.id,version=next_version))
            row=artifact(db,'ACTIVATION',{**value.model_dump(),'from_model_id':old.id,'to_model_id':target.id,'version':next_version,'demo_only':value.namespace=='DEMO'},target.project_id,value.actor)
            return serialize(row)

    @router.post('/sut',status_code=201)
    def import_sut(value:SUTInput):
        from backend.app.db.models import ROIVersion
        with Session.begin() as db:
            media=require(db,Media,value.media_id);roi=require(db,ROIVersion,value.roi_version_id)
            if roi.media_id!=media.id or media.evidence_type=='DEMO' and value.config.evidence_type!='DEMO':raise DomainError('SCOPE_MISMATCH','ROI/media/evidence không khớp.',422)
            try:payload=parse_sut(value.raw_text,value.config.model_dump(),media)
            except (ValueError,TypeError,KeyError) as e:raise DomainError('INVALID_SUT_FORMAT',str(e),422)
            payload.update(media_id=media.id,roi_version_id=roi.id,source_sha256=media.sha256,actor=value.actor)
            return serialize(artifact(db,'SUT',payload,media.project_id,value.actor))
    def gt_snapshot(db,id):
        row=db.get(GTSnapshot,id)
        if row:return row.manifest,row.media_id,row.manifest_hash
        row=get(db,id,'SPEED_SNAPSHOT');return row.payload,row.payload['media_id'],row.content_hash
    @router.post('/evaluate',status_code=201)
    def eval_sut(value:Evaluation):
        with Session.begin() as db:
            gt,media_id,checksum=gt_snapshot(db,value.gt_snapshot_id);sut=get(db,value.sut_id,'SUT')
            if sut.payload['media_id']!=media_id:raise DomainError('MEDIA_MISMATCH','GT và SUT khác media.',409)
            entries=[e for e in gt['entries'] if e['revision']['roi_version_id']==sut.payload['roi_version_id']]
            if not entries:raise DomainError('ROI_MISMATCH','Không có GT cùng ROI version.',409)
            config={'tolerance_seconds':value.tolerance_seconds,'rules_hash':json_hash(entries[0]['revision']['rules'])}
            report=evaluate(entries,sut.payload,config);media=require(db,Media,media_id)
            report.update(gt_snapshot_id=value.gt_snapshot_id,gt_hash=checksum,sut_id=sut.id,sut_hash=sut.content_hash,config=config,
                evidence_type='DEMO' if media.evidence_type=='DEMO' or sut.payload['config']['evidence_type']=='DEMO' else 'REFERENCE_COMPARISON',
                scope={'camera_id':media.camera_id,'roi_version_id':sut.payload['roi_version_id'],'condition':value.condition},integration_status='REAL_SUT_CONTRACT_NOT_VERIFIED')
            from backend.app.db.models import ROIVersion
            roi=require(db,ROIVersion,sut.payload['roi_version_id']);source=db.get(Media,media.parent_media_id) if media.parent_media_id else media
            report['sample_keys']=[[source.sha256,media.stream_index,round(e['frame']['relative_seconds']+float(media.source_offset_seconds),9),json_hash(roi.polygon_source_px)] for e in entries]
            report['aggregation_protocol']={'rules_hash':config['rules_hash'],'semantics':sut.payload['config']['semantics'],'tolerance_seconds':value.tolerance_seconds,'evaluator_version':report['evaluator_version'],'speed_estimator':{k:v for k,v in gt.get('estimator',{}).items() if k in ('window_seconds','max_gap_seconds','speed_uncertainty_mps')},'estimator_hash':gt.get('estimator_hash')}
            return serialize(artifact(db,'EVALUATION',report,media.project_id,value.actor))

    @router.post('/projects/{id}/aggregate',status_code=201)
    def pool(id:str,value:Pool):
        from .aggregate import aggregate
        with Session.begin() as db:
            require(db,Project,id);records=[get(db,key,'EVALUATION') for key in value.evaluation_ids]
            if any(r.project_id!=id for r in records):raise DomainError('PROJECT_MISMATCH','Reports must belong to this project.',409)
            if any('aggregation_protocol' not in r.payload for r in records):raise DomainError('REEVALUATE_REQUIRED','Re-run historical reports to attach aggregation protocol.',409)
            try:payload=aggregate([serialize(r) for r in records])
            except ValueError as e:raise DomainError('POOL_BLOCKED',str(e),409)
            return serialize(artifact(db,'POOLED_EVALUATION',payload,id,value.actor))

    @router.post('/projects/{id}/effort-studies',status_code=201)
    def effort_study(id:str,value:EffortStudy):
        from .effort import summarize_trials
        with Session.begin() as db:
            require(db,Project,id)
            if value.evidence_type=='REAL' and not db.scalar(select(Media.id).where(Media.project_id==id,Media.evidence_type=='REAL').limit(1)):raise DomainError('REAL_MEDIA_REQUIRED','Do not record simulated trials as real human measurements.',409)
            try:payload=summarize_trials([r.model_dump() for r in value.rows],value.evidence_type,value.max_quality_drop)
            except ValueError as e:raise DomainError('INVALID_EFFORT_STUDY',str(e),422)
            return serialize(artifact(db,'EFFORT_STUDY',{**payload,'protocol_evidence':value.protocol_evidence,'measurement_origin':'USER_DECLARED_PAIRED_TRIALS'},id,value.actor))

    @router.post('/motion-preview')
    def motion_preview(value:MotionPreview):
        from backend.app.db.models import ROIVersion,Frame
        with Session() as db:
            cal=get(db,value.calibration_id,'CALIBRATION');media=require(db,Media,cal.payload['media_id']);roi=require(db,ROIVersion,cal.payload['roi_version_id'])
            latest=db.scalar(select(ROIVersion).where(ROIVersion.roi_id==roi.roi_id).order_by(ROIVersion.version.desc()).limit(1))
            if latest.id!=roi.id:raise DomainError('STALE_CALIBRATION','Select calibration for the current ROI.',409)
            if value.start_seconds>=value.end_seconds or value.end_seconds>media.duration_seconds:raise DomainError('INVALID_WINDOW','Preview window outside media.',422)
            for points in value.tracks.values():
                previous=-1
                for p in points:
                    f=require(db,Frame,p.frame_id)
                    if f.media_id!=media.id or f.pts!=p.pts or abs(f.relative_seconds-p.time)>1e-7 or p.time<=previous:raise DomainError('INVALID_TRACK_TIME','Invalid frame/PTS order.',422)
                    previous=p.time
                    if not(0<=p.anchor[0]<=media.width and 0<=p.anchor[1]<=media.height):raise DomainError('INVALID_ANCHOR','Anchor outside image.',422)
            return draft_motion({k:[p.model_dump() for p in ps] for k,ps in value.tracks.items()},cal.payload,roi.polygon_source_px,value.start_seconds,value.end_seconds,value.config.model_dump())

    @router.post('/calibrations',status_code=201)
    def calibration(value:Calibration):
        from backend.app.db.models import ROIVersion
        with Session.begin() as db:
            media=require(db,Media,value.media_id);roi=require(db,ROIVersion,value.roi_version_id)
            if roi.media_id!=media.id:raise DomainError('ROI_MISMATCH','ROI khác video.',422)
            for p in value.points:
                if not(0<=p.pixel[0]<=media.width and 0<=p.pixel[1]<=media.height):raise DomainError('INVALID_CONTROL','Pixel ngoài ảnh.',422)
            try:fit=fit_calibration([p.model_dump() for p in value.points],[p.model_dump() for p in value.lengths],roi.polygon_source_px,value.repeatability_mps)
            except ValueError as e:raise DomainError('INVALID_CALIBRATION',str(e),422)
            payload={**value.model_dump(),**fit,'approved':value.approve and fit['valid'],'evidence_type':media.evidence_type,'source_sha256':media.sha256,'roi_hash':json_hash(serialize(roi))}
            return serialize(artifact(db,'CALIBRATION',payload,media.project_id,value.actor))
    @router.post('/tracks',status_code=202)
    def track(value:Track):
        with Session.begin() as db:
            media=require(db,Media,value.media_id)
            if value.start_seconds>=value.end_seconds or value.end_seconds>media.duration_seconds:raise DomainError('INVALID_SCOPE','Phạm vi tracking ngoài video.',422)
            model=active_model(db,media.evidence_type)
            payload={**value.model_dump(),'profile':model.payload['profile'],'model_id':model.id,'weights_sha256':model.payload['weights_sha256']}
            payload['profile']=copy.deepcopy(payload['profile']);payload['profile']['weights_path']=str(model_path(db,model.payload))
            id=enqueue(db,'AUTO_TRACK',media.project_id,payload)
        launch(id);return {'job_id':id}
    @router.post('/tracks/{id}/revisions',status_code=201)
    def track_revision(id:str,value:TrackReview):
        from backend.app.db.models import Frame
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'));original=get(db,id,'TRACKS');key='tracks:'+id;pointer=db.get(WorkPointer,key);current=pointer.artifact_id if pointer else id
            if current!=value.expected_revision_id:raise DomainError('EDIT_CONFLICT','Track revision đã đổi.',409)
            media=require(db,Media,original.payload['media_id']);verify_source(media,full=True)
            if value.end_seconds>media.duration_seconds or value.start_seconds>=value.end_seconds:raise DomainError('INVALID_WINDOW','Cửa sổ review không hợp lệ.',422)
            seen=set()
            for tid,points in value.tracks.items():
                last=-1
                for p in points:
                    f=require(db,Frame,p.frame_id)
                    if f.media_id!=media.id or f.pts!=p.pts or abs(f.relative_seconds-p.time)>1e-7 or p.time<=last or (tid,p.frame_id) in seen:raise DomainError('INVALID_TRACK_TIME','Frame/PTS/track không khớp hoặc không tăng.',422)
                    if not(0<=p.anchor[0]<=media.width and 0<=p.anchor[1]<=media.height):raise DomainError('INVALID_ANCHOR','Anchor ngoài ảnh.',422)
                    seen.add((tid,p.frame_id));last=p.time
            if value.reviewed and not value.viewed_complete_window:raise DomainError('WINDOW_REVIEW_REQUIRED','Cần xác nhận xem toàn cửa sổ.',422)
            payload={**original.payload,**value.model_dump(mode='json'),'parent_id':current,'root_id':id}
            row=artifact(db,'TRACK_REVISION',payload,media.project_id,value.actor)
            if pointer:pointer.artifact_id=row.id;pointer.version+=1
            else:db.add(WorkPointer(key=key,artifact_id=row.id,version=1))
            return serialize(row)
    @router.post('/speed-snapshots',status_code=201)
    def speed(value:SpeedSnapshot):
        from backend.app.db.models import ROIVersion
        with Session.begin() as db:
            base,media_id,base_hash=gt_snapshot(db,value.gt_snapshot_id);cal=get(db,value.calibration_id,'CALIBRATION');track=get(db,value.track_revision_id,'TRACK_REVISION')
            if cal.payload['media_id']!=media_id or track.payload['media_id']!=media_id:raise DomainError('MEDIA_MISMATCH','Các bằng chứng khác media.',409)
            pointer=db.get(WorkPointer,'tracks:'+track.payload['root_id'])
            if not pointer or pointer.artifact_id!=track.id or not track.payload['reviewed']:raise DomainError('TRACK_NOT_CURRENT_REVIEWED','Cần track revision hiện hành đã review.',409)
            roi=require(db,ROIVersion,cal.payload['roi_version_id']);latest=db.scalar(select(ROIVersion).where(ROIVersion.roi_id==roi.roi_id).order_by(ROIVersion.version.desc()).limit(1))
            if latest.id!=roi.id:raise DomainError('STALE_CALIBRATION','ROI đã đổi; cần calibration mới.',409)
            media=require(db,Media,media_id);verify_source(media,full=True);entries=copy.deepcopy(base['entries'])
            if cal.payload['source_sha256']!=media.sha256 or track.payload['source_sha256']!=media.sha256:raise DomainError('SOURCE_HASH_MISMATCH','Calibration/track source has changed.',409)
            estimator_hash=digest(Path(__file__).with_name('motion.py'))
            for e in entries:
                if e['revision']['roi_version_id']!=roi.id:raise DomainError('ROI_MISMATCH','Count và calibration cần cùng ROI version.',409)
                t=e['frame']['relative_seconds'];obs=e['observation'];population=obs['count']['value'] if obs['count']['status']=='VALID' else None
                valid_scope=track.payload['start_seconds']<=t-value.window_seconds and t<=track.payload['end_seconds']
                velocity=population_speed(track.payload['tracks'] if valid_scope else {},t,population,roi.polygon_source_px,cal.payload,value.model_dump())
                speed_class=classify_speed(velocity['value'],e['revision']['rules']);options=possible_levels(obs['density'],speed_class,e['revision']['rules'])
                bound=value.speed_uncertainty_mps
                velocity['uncertainty']={'bound_mps':bound,'status':'USER_MEASURED_BOUND' if bound is not None else 'NOT_MEASURED','evidence':value.uncertainty_evidence}
                if velocity['value'] is not None and bound is not None:
                    lo=max(0,velocity['value']-bound);hi=velocity['value']+bound
                    classes=possible_classes(Fraction(str(lo)),Fraction(str(hi)),e['revision']['rules']['speed'])
                    speed_class.update(lower=lo,upper=hi,possible_classes=classes,**{'class':classes[0] if len(classes)==1 else None})
                    if len(classes)>1:velocity['status']='UNCERTAIN';velocity['reason']='SPEED_INTERVAL_CROSSES_CLASS_BOUNDARY'
                    options=sorted({e['revision']['rules']['level_matrix'][d][s] for d in obs['density']['possible_classes'] for s in classes})
                obs['speed']={**speed_class,**velocity};obs['level']={**metric(options[0] if len(options)==1 else None,'VALID' if len(options)==1 else 'UNKNOWN'),'possible_levels':options}
                obs['speed_window']=[t-value.window_seconds,t];obs['dependencies_hash']=json_hash({'count_dependencies':obs['dependencies_hash'],'calibration':cal.content_hash,'tracks':track.content_hash,'estimator':value.model_dump(),'estimator_hash':estimator_hash})
            payload={'entries':entries,'media_id':media_id,'base_snapshot_id':value.gt_snapshot_id,'base_hash':base_hash,'calibration_id':cal.id,'track_revision_id':track.id,'evidence_type':media.evidence_type,'estimator':value.model_dump(),'estimator_hash':estimator_hash}
            return serialize(artifact(db,'SPEED_SNAPSHOT',payload,media.project_id,value.actor))
    @router.get('/artifacts/{id}/report.zip')
    def report_zip(id:str):
        from .exports import report_files,zip_bytes
        with Session() as db:row=serialize(get(db,id))
        return Response(zip_bytes(report_files(row)),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="report_{id}.zip"'})
    @router.post('/backup')
    def create_backup(value:BackupInput):
        from .backup import backup
        id=uuid.uuid4().hex
        try:result=backup(root,root/'backups'/f'{id}.zip',value.include_sources)
        except ValueError as e:raise DomainError('BACKUP_BLOCKED',str(e),409)
        return {'id':id,**result,'download_url':f'/api/workflows/backups/{id}'}
    @router.get('/backups/{id}')
    def download_backup(id:str):
        if len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise DomainError('NOT_FOUND','Backup không có.',404)
        path=root/'backups'/f'{id}.zip'
        if not path.is_file():raise DomainError('NOT_FOUND','Backup không có.',404)
        return FileResponse(path,media_type='application/zip',filename=path.name)
    @router.post('/backups/{id}/restore')
    def restore_backup(id:str):
        from .backup import restore
        if len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise DomainError('NOT_FOUND','Backup không có.',404)
        try:return restore(root/'backups'/f'{id}.zip',root/'restores'/uuid.uuid4().hex)
        except (ValueError,OSError) as e:raise DomainError('RESTORE_FAILED',str(e),409)
    app.include_router(router)
