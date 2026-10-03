"""Local person dataset releases. No training or model activation happens here."""
import hashlib
import io
import json
from fractions import Fraction
from pathlib import Path
import uuid
import zipfile
from collections import Counter
from typing import Literal
from PIL import Image
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from backend.app.db.models import (Annotation, AnnotationRevision, ReviewRecord, Frame, Media, Project,
    TrainingReview, MediaPartition, DatasetSnapshot, SplitLock, DatasetNotice, ProposalFrame, AuditEvent)
from backend.app.errors import DomainError
from backend.app.jobs.profiles import json_hash
from backend.app.media.service import frame_image, verify_source, digest
from backend.app.gt.pilot import deltas
from backend.app.annotations.api import seed_objects
from .policy import GUIDE, GUIDE_HASH, scope_rect, eligibility, yolo_labels

EXPORTER={'version':'person_yolo_v1','class_mapping':{'person':0},'image_format':'PNG_RGB','resize':False,
    'label_format':'class cx cy w h normalized','guide_hash':GUIDE_HASH,
    'code_hash':hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('policy.py').read_bytes()).hexdigest()}


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)


class TrainingInput(Input):
    expected_edit_version:int=Field(ge=1)
    reviewer:str=Field(min_length=1,max_length=120)
    task:Literal['person_detection']='person_detection'
    scope:Literal['FULL_FRAME','CROP']='FULL_FRAME'
    crop:tuple[int,int,int,int]|None=None
    decision:Literal['INCLUDE','EXCLUDE']='INCLUDE'
    viewed:bool=False
    all_people_checked:bool=False
    geometry_checked:bool=False
    class_checked:bool=False
    convention_checked:bool=False
    no_unresolved:bool=False
    context_and_truncation_checked:bool=False
    negative_confirmed:bool=False
    guide_hash:str
    sample_reason:str=Field(min_length=1,max_length=1000)
    cohort:Literal['NEW','REPLAY']='NEW'
    notes:str=Field(default='',max_length=4000)


class PartitionInput(Input):
    session_key:str=Field(min_length=1,max_length=160)
    split:Literal['train','validation','locked_test']
    condition:str=Field(min_length=1,max_length=160)
    actor:str=Field(min_length=1,max_length=120)


class Build(Input):
    training_review_ids:list[str]=Field(min_length=1,max_length=200)
    purpose:Literal['DEMO_SMOKE','HELPER_DATASET']='DEMO_SMOKE'
    min_interval_seconds:float=Field(default=1,ge=0,le=3600)
    max_per_group:int=Field(default=50,ge=1,le=200)


class Freeze(Build):
    preview_hash:str
    name:str=Field(min_length=1,max_length=120)
    actor:str=Field(min_length=1,max_length=120)


class Notice(Input):
    actor:str=Field(min_length=1,max_length=120)
    reason:str=Field(min_length=1,max_length=4000)


def sha(data):return hashlib.sha256(data).hexdigest()


def configs(has_test):
    files={'data.yaml':b'train: images/train\nval: images/val\nnames:\n  0: person\nnc: 1\n'}
    if has_test:files['locked_test.yaml']=b'# Evaluation only; no training images.\ntrain: ""\nval: images/locked_test\ntest: images/locked_test\nnames:\n  0: person\nnc: 1\n'
    return files


def register(app,root,Session,require,serialize):
    router=APIRouter(prefix='/api/datasets')

    def latest_review(db,rev):
        return db.scalar(select(ReviewRecord).where(ReviewRecord.revision_id==rev.id).order_by(ReviewRecord.created_at.desc()).limit(1))

    def media_tokens(db,media,partition=None):
        tokens=[];seen=set();m=media
        while m:
            if m.id in seen:raise DomainError('SOURCE_CYCLE','Source lineage có chu kỳ.',409)
            seen.add(m.id);tokens.append('source:'+m.sha256)
            m=db.get(Media,m.parent_media_id) if m.parent_media_id else None
        record=db.get(MediaPartition,media.id)
        p=partition or (record.payload if record else None)
        if p:tokens.append('session:'+p['session_key'])
        return sorted(set(tokens))

    def partition_conflicts(db,media,payload):
        tokens=media_tokens(db,media,payload);conflicts=[]
        for token in tokens:
            lock=db.get(SplitLock,token)
            if lock and lock.split!=payload['split']:conflicts.append(token)
        for existing in db.scalars(select(MediaPartition)):
            if existing.media_id==media.id:continue
            if existing.payload['split']!=payload['split'] and set(tokens)&set(media_tokens(db,db.get(Media,existing.media_id),existing.payload)):
                conflicts.append(existing.media_id)
        return conflicts

    def status(db,row):
        rev=db.get(AnnotationRevision,row.revision_id);ann=db.get(Annotation,rev.annotation_id)
        frame=db.get(Frame,ann.frame_id);media=db.get(Media,frame.media_id);review=latest_review(db,rev)
        rect=row.payload['rect']
        boxes,reasons=eligibility(row.payload,rev.objects,rect,review.scope['kind'] if review else None,bool(review and review.completeness.get('person_boxes')))
        if ann.edit_version!=rev.version:reasons.append('REVISION_SUPERSEDED')
        if not review or review.id!=row.review_id or review.verdict!='APPROVED':reasons.append('REVIEW_SUPERSEDED_OR_NOT_APPROVED')
        latest=db.scalar(select(TrainingReview).where(TrainingReview.revision_id==row.revision_id,TrainingReview.scope_key==row.scope_key).order_by(TrainingReview.created_at.desc()).limit(1))
        if latest.id!=row.id:reasons.append('TRAINING_REVIEW_SUPERSEDED')
        return {'training_review':serialize(row),'annotation_id':ann.id,'revision':serialize(rev),'frame':serialize(frame),'media':serialize(media),
            'review':serialize(review) if review else None,'rect':rect,'boxes':boxes,'eligible':not reasons,'reasons':sorted(set(reasons)),
            'image_url':f'/api/datasets/training-reviews/{row.id}/image'}

    @router.get('/guide')
    def guide():return {'guide':GUIDE,'guide_hash':GUIDE_HASH,'exporter':EXPORTER}

    @router.get('/annotations/{id}/eligibility')
    def annotation_eligibility(id:str):
        with Session() as db:
            require(db,Annotation,id)
            rows=db.scalars(select(TrainingReview).join(AnnotationRevision,TrainingReview.revision_id==AnnotationRevision.id).where(AnnotationRevision.annotation_id==id).order_by(TrainingReview.created_at.desc()))
            return [status(db,row) for row in rows]

    @router.post('/annotations/{id}/training-reviews',status_code=201)
    def training_review(id:str,value:TrainingInput):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            ann=require(db,Annotation,id)
            if ann.edit_version!=value.expected_edit_version:raise DomainError('EDIT_CONFLICT','Cần revision mới nhất.',409)
            rev=db.scalar(select(AnnotationRevision).where(AnnotationRevision.annotation_id==id,AnnotationRevision.version==ann.edit_version))
            review=latest_review(db,rev)
            if not review or review.verdict!='APPROVED':raise DomainError('NOT_REVIEWED','Duyệt nhãn trước khi kiểm eligibility.',409)
            frame=db.get(Frame,ann.frame_id);media=db.get(Media,frame.media_id)
            verify_source(media,full=True)
            try:rect=scope_rect(value.scope,value.crop,media.width,media.height)
            except ValueError as e:raise DomainError('INVALID_CROP',str(e),422)
            if value.guide_hash!=GUIDE_HASH:raise DomainError('GUIDE_CHANGED','Tải lại annotation guide hiện hành.',409)
            proposal=db.scalar(select(ProposalFrame).where(ProposalFrame.run_id==rev.proposal_run_id,ProposalFrame.frame_id==frame.id))
            payload={**value.model_dump(mode='json'),'rect':rect,'guide_version':GUIDE['version'],'guide':GUIDE,
                'correction_types':deltas(seed_objects(proposal.payload),rev.objects),'evidence_type':media.evidence_type}
            row=TrainingReview(id=uuid.uuid4().hex,revision_id=rev.id,review_id=review.id,scope_key=json_hash({'scope':value.scope,'rect':rect}),reviewer=value.reviewer,payload=payload)
            db.add(row);db.flush()
            db.add(AuditEvent(id=uuid.uuid4().hex,actor=value.reviewer,action='TRAINING_REVIEW',entity_id=id,payload={'training_review_id':row.id,'decision':value.decision}))
            return status(db,row)

    @router.get('/training-reviews/{id}/image')
    def scoped_image(id:str):
        with Session() as db:
            item=status(db,require(db,TrainingReview,id));_,image=render(db,item)
            return Response(image,media_type='image/png')

    @router.get('/projects/{id}/candidates')
    def candidates(id:str):
        with Session() as db:
            require(db,Project,id)
            rows=db.scalars(select(TrainingReview).join(AnnotationRevision,TrainingReview.revision_id==AnnotationRevision.id).join(Annotation,AnnotationRevision.annotation_id==Annotation.id).join(Frame,Annotation.frame_id==Frame.id).join(Media,Frame.media_id==Media.id).where(Media.project_id==id).order_by(TrainingReview.created_at.desc()))
            found=set();items=[]
            for row in rows:
                key=(row.revision_id,row.scope_key)
                if key in found:continue
                found.add(key);items.append(status(db,row))
            media=list(db.scalars(select(Media).where(Media.project_id==id)))
            return {'items':items,'media':[{'media':serialize(m),'partition':db.get(MediaPartition,m.id).payload if db.get(MediaPartition,m.id) else None} for m in media]}

    @router.post('/media/{id}/partition')
    def partition(id:str,value:PartitionInput):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            media=require(db,Media,id);payload=value.model_dump();old=db.get(MediaPartition,id)
            if old and old.payload['split']=='locked_test' and (old.payload['split']!=value.split or old.payload['session_key']!=value.session_key):
                raise DomainError('LOCKED_TEST_PROTECTED','Không đổi group đã khai báo locked test sang development/train.',409)
            conflicts=partition_conflicts(db,media,payload)
            if conflicts:raise DomainError('SPLIT_LEAKAGE','Nguồn/session đã thuộc split khác; không chia frame/crop cùng group.',409,conflicts)
            if old:old.payload=payload
            else:db.add(MediaPartition(media_id=id,payload=payload))
            db.add(AuditEvent(id=uuid.uuid4().hex,actor=value.actor,action='SET_DATASET_PARTITION',entity_id=id,payload=payload))
            return payload

    def render(db,item):
        media=db.get(Media,item['media']['id']);frame=db.get(Frame,item['frame']['id'])
        source=frame_image(media,frame,root/'frame_cache',256*1024*1024)
        with Image.open(io.BytesIO(source)) as im:
            rgb=im.convert('RGB');source_hash=sha(str(rgb.size).encode()+rgb.tobytes())
            cropped=rgb.crop(item['rect']);buffer=io.BytesIO();cropped.save(buffer,format='PNG',compress_level=6)
        return source_hash,buffer.getvalue()

    def preview(db,project_id,value):
        require(db,Project,project_id)
        if len(set(value.training_review_ids))!=len(value.training_review_ids):raise DomainError('DUPLICATE_SELECTION','Không chọn trùng training review.',422)
        entries=[];excluded=[];conflicts=[];files={};used_tokens={};seen_images={};times={};counts=Counter();verified=set()
        selected_items=[status(db,require(db,TrainingReview,id)) for id in value.training_review_ids]
        for item in sorted(selected_items,key=lambda r:(r['media']['id'],r['frame']['ordinal'],r['training_review']['id'])):
            id=item['training_review']['id'];media=db.get(Media,item['media']['id']);frame=item['frame']
            if media.project_id!=project_id:raise DomainError('PROJECT_SCOPE_MISMATCH','Mẫu khác project.',422)
            reasons=list(item['reasons']);p=db.get(MediaPartition,media.id)
            if not p:reasons.append('SOURCE_GROUP_REQUIRED')
            if value.purpose=='HELPER_DATASET' and media.evidence_type!='REAL':reasons.append('DEMO_NOT_REAL_TRAINING_DATA')
            if reasons:excluded.append({'id':id,'annotation_id':item['annotation_id'],'frame_id':frame['id'],'reasons':reasons});continue
            p=p.payload;split=p['split'];tokens=media_tokens(db,media,p)
            conflict=partition_conflicts(db,media,p)
            if conflict:conflicts.append({'id':id,'reason':'SOURCE_SESSION_SPLIT_CONFLICT','tokens':conflict})
            if media.id not in verified:verify_source(media,full=True);verified.add(media.id)
            source_hash,image=render(db,item);image_hash=sha(image)
            tokens+=['pixels:'+source_hash,'export_pixels:'+image_hash]
            for token in tokens:
                lock=db.get(SplitLock,token);previous=used_tokens.get(token) or (lock.split if lock else None)
                if previous and previous!=split:conflicts.append({'id':id,'reason':'SPLIT_LEAKAGE','token':token})
                used_tokens[token]=split
            group='source:'+ (db.get(Media,media.parent_media_id).sha256 if media.parent_media_id else media.sha256)
            timestamp=Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])-Fraction(media.start_pts*media.time_base_num,media.time_base_den)+Fraction(media.source_offset_seconds)
            reason=None
            target_signature=json_hash(sorted([o['class_id'],*o['box']] for o in item['boxes']))
            if image_hash in seen_images:
                reason='DUPLICATE_IMAGE'
                if seen_images[image_hash]!=target_signature:conflicts.append({'id':id,'reason':'DUPLICATE_IMAGE_LABEL_CONFLICT','token':image_hash})
            elif any(abs(timestamp-t)<Fraction(str(value.min_interval_seconds)) for t in times.get(group,[])):reason='NEARBY_SOURCE_FRAME'
            elif counts['session:'+p['session_key']]>=value.max_per_group:reason='GROUP_SAMPLE_CAP'
            if reason:excluded.append({'id':id,'annotation_id':item['annotation_id'],'frame_id':frame['id'],'reasons':[reason]});continue
            seen_images[image_hash]=target_signature;times.setdefault(group,[]).append(timestamp);counts['session:'+p['session_key']]+=1
            rect=item['rect'];w,h=rect[2]-rect[0],rect[3]-rect[1]
            name=json_hash({'review':id,'rect':rect})[:32];folder={'validation':'val'}.get(split,split)
            image_path=f'images/{folder}/{name}.png';label_path=f'labels/{folder}/{name}.txt';label=yolo_labels(item['boxes'],w,h)
            files[image_path]=image;files[label_path]=label
            entries.append({'training_review_id':id,'annotation_id':item['annotation_id'],'revision':item['revision'],'review':item['review'],'training_review':item['training_review'],
                'source':{'media_id':media.id,'sha256':media.sha256,'source_path':media.source_path,'parent_media_id':media.parent_media_id,'source_offset_seconds':media.source_offset_seconds,'pts':frame['pts'],'time_base':[frame['time_base_num'],frame['time_base_den']]},'frame':frame,
                'split':split,'group_tokens':tokens,'session_key':p['session_key'],'camera_id':media.camera_id,'condition':p['condition'],
                'cohort':item['training_review']['payload']['cohort'],'sample_reason':item['training_review']['payload']['sample_reason'],
                'evidence_type':media.evidence_type,'rect':rect,'width':w,'height':h,'boxes':item['boxes'],'source_pixel_hash':source_hash,
                'image_path':image_path,'image_hash':image_hash,'label_path':label_path,'label_hash':sha(label),'negative_confirmed':item['training_review']['payload']['negative_confirmed']})
        splits=Counter(e['split'] for e in entries);warnings=[]
        if not splits['train'] or not splits['validation']:warnings.append('NO_INDEPENDENT_TRAIN_VALIDATION_SMOKE_ONLY')
        if not any(e['cohort']=='REPLAY' for e in entries):warnings.append('NO_REPLAY_SAMPLES')
        if not any(not e['boxes'] for e in entries):warnings.append('NO_VERIFIED_NEGATIVES')
        if len({e['camera_id'] for e in entries})<2:warnings.append('SINGLE_CAMERA_NO_CAMERA_HOLDOUT_EVIDENCE')
        files.update(configs(splits['locked_test']))
        summary={'selected':len(value.training_review_ids),'eligible':len(entries),'excluded':len(excluded),'boxes':sum(len(e['boxes']) for e in entries),'splits':dict(splits),
            'cameras':dict(Counter(e['camera_id'] for e in entries)),'conditions':dict(Counter(e['condition'] for e in entries)),
            'cohorts':dict(Counter(e['cohort'] for e in entries)),'groups':dict(Counter(e['session_key'] for e in entries)),
            'sample_reasons':dict(Counter(e['sample_reason'] for e in entries)),
            'exclusion_reasons':dict(Counter(r for e in excluded for r in e['reasons']))}
        result={'schema_version':'person_dataset_v1','task':'person_detection','purpose':value.purpose,'exporter':EXPORTER,'guide':GUIDE,
            'build':value.model_dump(exclude={'preview_hash','name','actor'}),'entries':entries,'excluded':excluded,'conflicts':conflicts,'warnings':warnings,'summary':summary,
            'training_readiness':'DEMO_SMOKE_ONLY' if value.purpose=='DEMO_SMOKE' else 'SPLIT_READY_REQUIRES_T27_T28_EVALUATION' if splits['train'] and splits['validation'] else 'SMOKE_ONLY_NO_INDEPENDENT_VALIDATION',
            'config_files':{path:data.decode('utf-8') for path,data in configs(splits['locked_test']).items()},
            'files':{path:sha(data) for path,data in sorted(files.items())}}
        result['preview_hash']=json_hash(result);result['can_freeze']=bool(entries) and not conflicts
        return result,files

    @router.post('/projects/{id}/preview')
    def build_preview(id:str,value:Build):
        with Session() as db:return preview(db,id,value)[0]

    @router.post('/projects/{id}/snapshots',status_code=201)
    def freeze(id:str,value:Freeze):
        with Session.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'));manifest,files=preview(db,id,value)
            if manifest['preview_hash']!=value.preview_hash:raise DomainError('PREVIEW_CHANGED','Dữ liệu đã đổi; xem preview mới trước khi freeze.',409)
            if not manifest['can_freeze']:raise DomainError('DATASET_BLOCKED','Chưa có mẫu hợp lệ hoặc có split leakage.',409)
            dataset_id=uuid.uuid4().hex;manifest.update(dataset_id=dataset_id,name=value.name,actor=value.actor,version=1)
            folder=root/'datasets'/dataset_id;folder.mkdir(parents=True,exist_ok=False)
            for name,data in files.items():
                target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            manifest_hash=json_hash(manifest)
            (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'manifest.sha256').write_text(manifest_hash+'\n',encoding='ascii')
            row=DatasetSnapshot(id=dataset_id,project_id=id,manifest=manifest,manifest_hash=manifest_hash);db.add(row);db.flush()
            for e in manifest['entries']:
                for token in e['group_tokens']:
                    if not db.get(SplitLock,token):
                        db.add(SplitLock(token=token,split=e['split'],dataset_id=dataset_id));db.flush()
            db.add(AuditEvent(id=uuid.uuid4().hex,actor=value.actor,action='FREEZE_DATASET',entity_id=dataset_id,payload={'manifest_hash':manifest_hash}))
            return serialize(row)

    @router.get('/projects/{id}/snapshots')
    def snapshots(id:str):
        with Session() as db:
            require(db,Project,id)
            return [{'id':r.id,'created_at':r.created_at,'name':r.manifest['name'],'summary':r.manifest['summary'],'purpose':r.manifest['purpose'],
                'not_recommended':bool(db.scalar(select(DatasetNotice.id).where(DatasetNotice.dataset_id==r.id).limit(1)))} for r in db.scalars(select(DatasetSnapshot).where(DatasetSnapshot.project_id==id).order_by(DatasetSnapshot.created_at.desc()))]

    @router.get('/snapshots/{id}')
    def snapshot(id:str):
        with Session() as db:return serialize(require(db,DatasetSnapshot,id))

    def integrity(row,folder):
        errors=[]
        try:
            if json_hash(json.loads((folder/'manifest.json').read_text('utf-8')))!=row.manifest_hash:errors.append('manifest.json')
            if (folder/'manifest.sha256').read_text('ascii').strip()!=row.manifest_hash:errors.append('manifest.sha256')
        except (OSError,ValueError):errors.append('manifest.json')
        for path,expected in row.manifest['files'].items():
            file=folder/path
            if not file.is_file() or digest(file)!=expected:errors.append(path)
        return {'status':'VERIFIED' if not errors else 'CORRUPTED','errors':errors,'manifest_hash':row.manifest_hash}

    @router.post('/snapshots/{id}/verify')
    def verify(id:str):
        with Session() as db:return integrity(require(db,DatasetSnapshot,id),root/'datasets'/id)

    @router.get('/snapshots/{id}/archive')
    def archive(id:str):
        with Session() as db:
            row=require(db,DatasetSnapshot,id);folder=root/'datasets'/id;check=integrity(row,folder)
            if check['errors']:raise DomainError('DATASET_CORRUPTED','File snapshot không khớp hash.',409,check['errors'])
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for name in sorted([*row.manifest['files'],'manifest.json','manifest.sha256']):
                    info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,(folder/name).read_bytes())
            return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="dataset_{id}.zip"'})

    @router.get('/snapshots/{id}/images/{index}')
    def exported_image(id:str,index:int):
        with Session() as db:
            row=require(db,DatasetSnapshot,id)
            if not 0<=index<len(row.manifest['entries']):raise DomainError('NOT_FOUND','Không có ảnh này.',404)
            entry=row.manifest['entries'][index];path=root/'datasets'/id/entry['image_path']
            if not path.is_file() or digest(path)!=entry['image_hash']:raise DomainError('DATASET_CORRUPTED','Ảnh không khớp hash.',409)
            return FileResponse(path,media_type='image/png')

    @router.post('/snapshots/{id}/reproduce')
    def reproduce(id:str):
        with Session() as db:
            row=require(db,DatasetSnapshot,id)
            files={p:s.encode('utf-8') for p,s in row.manifest['config_files'].items()} if 'config_files' in row.manifest else configs('locked_test.yaml' in row.manifest['files'])
            for e in row.manifest['entries']:
                media=require(db,Media,e['source']['media_id']);verify_source(media,full=True)
                _,image=render(db,{'media':{'id':media.id},'frame':e['frame'],'rect':e['rect']})
                files[e['image_path']]=image;files[e['label_path']]=yolo_labels(e['boxes'],e['width'],e['height'])
            if {p:sha(b) for p,b in files.items()}!=row.manifest['files']:raise DomainError('REPRODUCTION_MISMATCH','Ảnh/nhãn tái tạo không khớp bản freeze.',409)
            folder=root/'dataset_reproductions'/f'{id}_{uuid.uuid4().hex}';folder.mkdir(parents=True)
            for path,data in files.items():
                target=folder/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            (folder/'manifest.json').write_text(json.dumps(row.manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'manifest.sha256').write_text(row.manifest_hash+'\n',encoding='ascii')
            return {**integrity(row,folder),'directory':str(folder),'files':len(files)}

    @router.post('/snapshots/{id}/not-recommended',status_code=201)
    def deprecate(id:str,value:Notice):
        with Session.begin() as db:
            require(db,DatasetSnapshot,id)
            row=DatasetNotice(id=uuid.uuid4().hex,dataset_id=id,**value.model_dump());db.add(row);db.flush()
            return serialize(row)

    @router.get('/snapshots/{id}/usage')
    def usage(id:str):
        with Session() as db:
            require(db,DatasetSnapshot,id)
            return {'notices':[serialize(n) for n in db.scalars(select(DatasetNotice).where(DatasetNotice.dataset_id==id))],
                'training_runs':[],'training_runner_status':'T27_NOT_IMPLEMENTED'}

    app.include_router(router)
