import uuid
import json
from pathlib import Path
from sqlalchemy import select
from backend.app.db.models import WorkArtifact, WorkPointer, DatasetSnapshot, DatasetNotice, AuditEvent
from backend.app.jobs.profiles import json_hash, profile
from backend.app.media.service import digest
from backend.app.errors import DomainError

def serialize(row):return {c.name:getattr(row,c.name) for c in row.__table__.columns}

def artifact(db,kind,payload,project_id=None,actor='local_operator'):
    row=WorkArtifact(id=uuid.uuid4().hex,project_id=project_id,kind=kind,payload=payload,content_hash=json_hash(payload))
    db.add(row);db.flush()
    db.add(AuditEvent(id=uuid.uuid4().hex,actor=actor,action='CREATE_'+kind,entity_id=row.id,payload={'content_hash':row.content_hash}))
    return row

def get(db,id,kind=None):
    row=db.get(WorkArtifact,id)
    if not row or kind and row.kind!=kind:raise DomainError('ARTIFACT_NOT_FOUND','Không tìm thấy evidence đúng loại.',404)
    if json_hash(row.payload)!=row.content_hash:raise DomainError('ARTIFACT_CORRUPTED','Evidence không khớp hash.',409)
    return row

def dataset(db,root,id):
    row=db.get(DatasetSnapshot,id)
    if not row:raise DomainError('DATASET_NOT_FOUND','Không có dataset.',404)
    if db.scalar(select(DatasetNotice.id).where(DatasetNotice.dataset_id==id).limit(1)):raise DomainError('DATASET_NOT_RECOMMENDED','Dataset đã được đánh dấu không còn khuyến nghị.',409)
    if json_hash(row.manifest)!=row.manifest_hash:raise DomainError('DATASET_CORRUPTED','Manifest DB sai hash.',409)
    folder=Path(root)/'datasets'/id
    try:
        if json_hash(json.loads((folder/'manifest.json').read_text('utf-8')))!=row.manifest_hash:raise ValueError('hash')
    except (OSError,ValueError):raise DomainError('DATASET_CORRUPTED','Disk manifest does not match frozen DB manifest.',409)
    for name,expected in row.manifest['files'].items():
        path=folder/name
        if not path.is_file() or digest(path)!=expected:raise DomainError('DATASET_CORRUPTED','Dataset file sai hash: '+name,409)
    return row,folder

def model_path(db,payload):
    from .backup import resolve_path
    return resolve_path(Path(db.get_bind().url.database).parent,payload['weights_path'])

def baseline(db):
    p=profile('person_gpu_v1');path=Path(p['weights_path'])
    if not path.is_file():
        for row in db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='MODEL')):
            if row.payload.get('baseline') and model_path(db,row.payload).is_file() and digest(model_path(db,row.payload))==row.payload['weights_sha256']:return row
        raise DomainError('WEIGHTS_MISSING','Thiếu checkpoint baseline.',409)
    checksum=digest(path)
    for row in db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='MODEL')):
        if row.payload.get('baseline') and row.payload['weights_sha256']==checksum:return row
    return artifact(db,'MODEL',{'baseline':True,'weights_path':str(path),'weights_sha256':checksum,'task':'person_detection','class_schema':'person','architecture':'YOLO11n',
        'profile':p,'dataset_id':None,'train_job_id':None,'evidence_type':'BASELINE_UNVALIDATED','license':p['config'].get('weights_license_reference')})

def active_model(db,evidence_type):
    pointer=db.get(WorkPointer,'active_model:'+evidence_type)
    return get(db,pointer.artifact_id,'MODEL') if pointer else baseline(db)

def disposition(db,id):
    for row in db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='MODEL_DECISION').order_by(WorkArtifact.created_at.desc())):
        if row.payload['model_id']==id:return row.payload['state']
    return None
