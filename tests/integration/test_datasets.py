import copy
import io
import json
import uuid
from pathlib import Path
from PIL import Image
from sqlalchemy import select
from test_review import c,seed,save,approve
from backend.app.db.models import Media, Frame, Annotation, AnnotationRevision, ReviewRecord, TrainingReview
from backend.app.db.session import sessions
from backend.app.media.demo import make_video
from backend.app.media.service import digest
from backend.app.datasets.policy import GUIDE_HASH, targets, yolo_labels, scope_rect


def reviewed(c,index=0,scope='FULL_FRAME'):
    doc=seed(c,index)
    return approve(c,doc,reviewed_scope=scope,boxes_complete=True,count_complete=True).json()


def attest(c,doc,**extra):
    body={'expected_edit_version':doc['annotation']['edit_version'],'reviewer':'DEMO_DATASET_TEST','guide_hash':GUIDE_HASH,
        'viewed':True,'all_people_checked':True,'geometry_checked':True,'class_checked':True,'convention_checked':True,'no_unresolved':True,
        'sample_reason':'DEMO deterministic fixture','negative_confirmed':not doc['revision']['objects'],**extra}
    r=c.client.post(f'/api/datasets/annotations/{doc["annotation"]["id"]}/training-reviews',json=body)
    assert r.status_code==201,r.text
    return r.json()


def partition(c,media=None,split='train',session='session-a'):
    return c.client.post(f'/api/datasets/media/{media or c.media.id}/partition',json={'session_key':session,'split':split,'condition':'DEMO','actor':'DEMO_TEST'})


def preview(c,items,**extra):
    body={'training_review_ids':[x['training_review']['id'] for x in items],'min_interval_seconds':0,**extra}
    r=c.client.post(f'/api/datasets/projects/{c.media.project_id}/preview',json=body)
    assert r.status_code==200,r.text
    return r.json(),body


def freeze(c,items,**extra):
    p,body=preview(c,items,**extra)
    r=c.client.post(f'/api/datasets/projects/{c.media.project_id}/snapshots',json={**body,'preview_hash':p['preview_hash'],'name':'DEMO test','actor':'DEMO_TEST'})
    assert r.status_code==201,r.text
    return r.json()


def test_count_roi_review_does_not_grant_full_frame_training(c):
    doc=reviewed(c,scope='ROI')
    row=attest(c,doc)
    assert not row['eligible'] and 'FULL_FRAME_BOX_REVIEW_REQUIRED' in row['reasons']
    crop=attest(c,doc,scope='CROP',crop=[50,40,120,160],context_and_truncation_checked=True)
    assert crop['eligible']
    assert crop['boxes'][0]['box']==[0,0,50,110] and crop['boxes'][1]['truncated']
    incomplete=attest(c,doc,scope='CROP',crop=[50,40,120,160],context_and_truncation_checked=False)
    assert 'CROP_CONTEXT_REVIEW_REQUIRED' in incomplete['reasons']
    partition(c)
    assert 'TRAINING_REVIEW_SUPERSEDED' in preview(c,[crop])[0]['excluded'][0]['reasons']


def test_negative_requires_explicit_confirmation_and_marker_is_not_box(c):
    empty=reviewed(c,2)
    assert 'EMPTY_NOT_CONFIRMED' in attest(c,empty,negative_confirmed=False)['reasons']
    assert attest(c,empty,negative_confirmed=True)['eligible']
    doc=seed(c);objects=copy.deepcopy(doc['revision']['objects']);objects[0]['box']=None
    doc=save(c,doc,objects);doc=approve(c,doc,reviewed_scope='ROI').json()
    r=attest(c,doc,scope='CROP',crop=[300,200,600,350],context_and_truncation_checked=True,negative_confirmed=True)
    assert 'MARKER_ONLY_EXTENT_UNKNOWN' in r['reasons']


def test_complete_final_labels_crop_pixels_and_reproduction(c):
    doc=seed(c);objects=copy.deepcopy(doc['revision']['objects'])
    objects.pop();objects.append({'id':'manual-person','box':[60,60,110,150],'marker':[80,80],'anchor':None,'anchor_verified':False})
    doc=save(c,doc,objects);doc=approve(c,doc,reviewed_scope='FULL_FRAME',boxes_complete=True).json()
    row=attest(c,doc,scope='CROP',crop=[50,40,120,160],context_and_truncation_checked=True)
    assert row['training_review']['payload']['correction_types']['added']==1
    assert row['training_review']['payload']['correction_types']['removed']==1
    partition(c);frozen=freeze(c,[row]);id=frozen['id'];entry=frozen['manifest']['entries'][0]
    assert len(entry['boxes'])==2 and {o['object_id'] for o in entry['boxes']}=={objects[0]['id'],'manual-person'}
    folder=c.app.state.data_root/'datasets'/id
    label=(folder/entry['label_path']).read_text()
    assert len(label.splitlines())==2 and label.splitlines()[0].startswith('0 0.357142857143')
    exported=Image.open(io.BytesIO(c.client.get(f'/api/datasets/snapshots/{id}/images/0').content))
    source=Image.open(io.BytesIO(c.client.get(f'/api/frames/{doc["frame"]["id"]}/image').content))
    assert exported.size==(70,120) and exported.tobytes()==source.convert('RGB').crop((50,40,120,160)).tobytes()
    changed=save(c,doc,[])
    assert 'REVISION_SUPERSEDED' in preview(c,[row])[0]['excluded'][0]['reasons']
    assert c.client.get(f'/api/datasets/snapshots/{id}').json()==frozen
    assert c.client.post(f'/api/datasets/snapshots/{id}/reproduce').json()['status']=='VERIFIED'
    archive=c.client.get(f'/api/datasets/snapshots/{id}/archive').content
    assert archive==c.client.get(f'/api/datasets/snapshots/{id}/archive').content
    (folder/entry['label_path']).write_text('bad label')
    assert c.client.post(f'/api/datasets/snapshots/{id}/verify').json()['status']=='CORRUPTED'
    assert c.client.get(f'/api/datasets/snapshots/{id}/archive').status_code==409
    notice=c.client.post(f'/api/datasets/snapshots/{id}/not-recommended',json={'actor':'DEMO_TEST','reason':'corrupted test fixture'})
    assert notice.status_code==201
    assert c.client.get(f'/api/datasets/snapshots/{id}/usage').json()['notices'][0]['reason']=='corrupted test fixture'
    assert c.client.get(f'/api/datasets/snapshots/{id}').json()==frozen


def clone_media(c,row,new_file=False,parent=False):
    """A separately imported DEMO source, with copied review fixtures for leakage tests."""
    with sessions(c.app.state.engine).begin() as db:
        original=db.get(Media,c.media.id);data={col.name:getattr(original,col.name) for col in Media.__table__.columns}
        media_id=uuid.uuid4().hex;data['id']=media_id
        if new_file:
            path=make_video(Path(original.source_path).with_name(media_id+'.mkv'),[1000,1100,1370])
            data.update(source_path=str(path),sha256=digest(path),size_bytes=path.stat().st_size,mtime_ns=str(path.stat().st_mtime_ns))
            assert data['sha256']!=original.sha256
        if parent:data['parent_media_id']=original.id
        db.add(Media(**data));db.flush()
        source=db.get(Frame,row['frame']['id']);frame=Frame(id=uuid.uuid4().hex,media_id=media_id,ordinal=source.ordinal,pts=source.pts,time_base_num=source.time_base_num,time_base_den=source.time_base_den,relative_seconds=source.relative_seconds);db.add(frame);db.flush()
        ann=Annotation(id=uuid.uuid4().hex,frame_id=frame.id,roi_id='clone-roi',edit_version=1);db.add(ann);db.flush()
        revdata={k:v for k,v in row['revision'].items() if k not in ('id','annotation_id')};rev=AnnotationRevision(id=uuid.uuid4().hex,annotation_id=ann.id,**revdata);db.add(rev);db.flush()
        reviewdata={k:v for k,v in row['review'].items() if k not in ('id','revision_id')};review=ReviewRecord(id=uuid.uuid4().hex,revision_id=rev.id,**reviewdata);db.add(review);db.flush()
        t=row['training_review'];training=TrainingReview(id=uuid.uuid4().hex,revision_id=rev.id,review_id=review.id,scope_key=t['scope_key'],reviewer=t['reviewer'],payload=t['payload']);db.add(training);db.flush()
        return media_id,{'training_review':{'id':training.id}}


def test_hash_reimport_parent_and_session_cannot_cross_split(c):
    row=attest(c,reviewed(c));assert partition(c).status_code==200
    same,_=clone_media(c,row)
    assert partition(c,same,'validation','different-session').status_code==409
    child,_=clone_media(c,row,new_file=True,parent=True)
    assert partition(c,child,'validation','child-session').status_code==409
    different,_=clone_media(c,row,new_file=True)
    assert partition(c,different,'validation','session-a').status_code==409


def test_exact_pixels_cross_source_split_block_and_persistent_lock(c):
    row=attest(c,reviewed(c));partition(c)
    other,second=clone_media(c,row,new_file=True)
    assert partition(c,other,'validation','different-session').status_code==200
    p,_=preview(c,[row,second]);assert not p['can_freeze'] and any(x['reason']=='SPLIT_LEAKAGE' for x in p['conflicts'])
    frozen=freeze(c,[row])
    p,_=preview(c,[second]);assert not p['can_freeze'] and p['conflicts']
    assert partition(c,split='validation').status_code==409


def test_dedup_nearby_cap_exclusions_and_demo_gate(c):
    rows=[attest(c,reviewed(c,i)) for i in range(3)];partition(c)
    p,_=preview(c,rows,min_interval_seconds=1)
    assert p['summary']['eligible']==1 and p['summary']['exclusion_reasons']['NEARBY_SOURCE_FRAME']==2
    p,_=preview(c,rows,max_per_group=1)
    assert p['summary']['eligible']==1 and p['summary']['exclusion_reasons']['GROUP_SAMPLE_CAP']==2
    p,_=preview(c,rows,purpose='HELPER_DATASET')
    assert not p['can_freeze'] and p['summary']['exclusion_reasons']['DEMO_NOT_REAL_TRAINING_DATA']==3
    other,second=clone_media(c,rows[0]);partition(c,other)
    p,_=preview(c,[rows[0],second]);assert p['summary']['eligible']==1 and p['summary']['exclusion_reasons']['DUPLICATE_IMAGE']==1


def test_locked_test_not_in_helper_config_and_not_reassignable(c):
    row=attest(c,reviewed(c,2));partition(c,split='locked_test')
    assert partition(c,split='train').status_code==409
    frozen=freeze(c,[row]);folder=c.app.state.data_root/'datasets'/frozen['id']
    assert 'locked_test' not in (folder/'data.yaml').read_text()
    assert (folder/'locked_test.yaml').is_file()
    e=frozen['manifest']['entries'][0]
    assert e['negative_confirmed'] and (folder/e['label_path']).read_bytes()==b''


def test_duplicate_image_with_conflicting_final_targets_blocks_release(c):
    row=attest(c,reviewed(c));partition(c)
    other,second=clone_media(c,row);partition(c,other)
    with sessions(c.app.state.engine).begin() as db:
        training=db.get(TrainingReview,second['training_review']['id'])
        revision=db.get(AnnotationRevision,training.revision_id)
        revision.objects=revision.objects[:1]  # conflicting test fixture only
    p,_=preview(c,[row,second])
    assert not p['can_freeze'] and any(x['reason']=='DUPLICATE_IMAGE_LABEL_CONFLICT' for x in p['conflicts'])


def test_preview_conflict_when_review_or_eligibility_changes(c):
    doc=reviewed(c);row=attest(c,doc);partition(c)
    p,body=preview(c,[row])
    attest(c,doc,decision='EXCLUDE',notes='unresolved visibility')
    r=c.client.post(f'/api/datasets/projects/{c.media.project_id}/snapshots',json={**body,'preview_hash':p['preview_hash'],'name':'blocked','actor':'DEMO_TEST'})
    assert r.status_code==409 and r.json()['code']=='PREVIEW_CHANGED'


def test_scope_validation_and_pure_transform():
    import pytest
    with pytest.raises(ValueError):scope_rect('CROP',[0,0,1000,10],640,360)
    with pytest.raises(ValueError):scope_rect('CROP',[0,0,5,10],640,360)
    with pytest.raises(ValueError):scope_rect('FULL_FRAME',[0,0,10,10],640,360)
    boxes,reasons=targets([{'id':'tiny','box':[0,0,.5,3]}],[0,0,10,10]);assert reasons==['BOX_TOO_SMALL']
    boxes,reasons=targets([{'id':'one','box':[0,0,10,10]},{'id':'two','box':[0,0,10,10]}],[0,0,10,10]);assert reasons==['DUPLICATE_TARGET_BOXES']
    with pytest.raises(ValueError):yolo_labels([{'class_id':1,'box':[0,0,10,10]}],10,10)
