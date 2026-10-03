import copy
from concurrent.futures import ThreadPoolExecutor
import uuid
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.main import create_app
from backend.app.db.models import Job, Media, Frame, ProposalRun, ProposalFrame, AuditEvent
from backend.app.db.session import sessions
from backend.app.media.demo import make_video
from backend.app.media.importer import run as import_run
from backend.app.models.detector import proposal_objects
from backend.app.annotations import api as review_api
from backend.app.annotations.quality import flags


@pytest.fixture
def c(tmp_path):
    app=create_app(tmp_path/'data'); client=TestClient(app)
    project=client.post('/api/projects',json={'name':'DEMO review state fixtures'}).json()['id']
    source=make_video(tmp_path/'review.mkv',[1000,1100,1370])
    job_id=uuid.uuid4().hex
    with sessions(app.state.engine).begin() as db:
        db.add(Job(id=job_id,project_id=project,kind='MEDIA_IMPORT',state='QUEUED',progress=0,payload={'source_path':str(source),'camera_id':'test','evidence_type':'DEMO','video_stream':0,'source_offset_seconds':0,'parent_media_id':None}))
    import_run(app.state.data_root,job_id)
    with sessions(app.state.engine).begin() as db:
        media=db.scalar(select(Media)); frames=list(db.scalars(select(Frame).order_by(Frame.ordinal)))
        job=Job(id=uuid.uuid4().hex,project_id=project,kind='AUTO_LABEL',state='SUCCEEDED',progress=100,payload={})
        db.add(job);db.flush()
        run=ProposalRun(id=uuid.uuid4().hex,job_id=job.id,media_id=media.id,manifest={'evidence_type':'DEMO_TEST_FIXTURE'},frame_count=3)
        db.add(run);db.flush()
        for f in frames:
            objects=[] if f.ordinal==2 else proposal_objects([[10,20,100,150,.4,0],[20,25,105,155,.9,0]],media.width,media.height)
            for o in objects:o['source_proposal_id']=f'{run.id}:{f.id}:{o["object_id"]}'
            db.add(ProposalFrame(id=uuid.uuid4().hex,run_id=run.id,frame_id=f.id,ordinal=f.ordinal,payload={'objects':objects,'pts':f.pts,'annotation_state':'PROPOSAL','reviewed':False,'ground_truth':False}))
    roi=client.post(f'/api/media/{media.id}/rois',json={'name':'Test','polygon_source_px':[[0,0],[200,0],[200,200],[0,200]],'area_m2':4,'area_evidence':'DEMO'}).json()
    yield SimpleNamespace(app=app,client=client,media=media,frames=frames,run=run,roi=roi)
    client.close();app.state.engine.dispose()


def seed(c,index=0):
    r=c.client.post(f'/api/review/frames/{c.frames[index].id}/draft',json={'proposal_run_id':c.run.id,'roi_version_id':c.roi['id']})
    assert r.status_code==200,r.text
    return r.json()


def body(doc,objects=None,roi=None):
    return {'expected_edit_version':doc['annotation']['edit_version'],'roi_version_id':roi or doc['revision']['roi_version_id'],
        'objects':[{k:o[k] for k in ('id','box','marker','anchor','anchor_verified')} for o in (doc['revision']['objects'] if objects is None else objects)]}


def save(c,doc,objects=None,roi=None):
    r=c.client.post(f'/api/review/annotations/{doc["annotation"]["id"]}/revisions',json=body(doc,objects,roi))
    assert r.status_code==201,r.text
    return r.json()


def approve(c,doc,**extra):
    return c.client.post(f'/api/review/annotations/{doc["annotation"]["id"]}/reviews',json={
        'expected_edit_version':doc['annotation']['edit_version'],'reviewer':'DEMO_TEST_AUTOMATION','verdict':'APPROVED',
        'reviewed_scope':'ROI','viewed':True,**extra})


def snap(c,doc):
    return c.client.post(f'/api/review/media/{c.media.id}/snapshots',json={'revision_ids':[doc['revision']['id']],'actor':'DEMO_TEST_AUTOMATION'})


def test_immutable_revision_snapshot_conflict_and_proposal_lineage(c):
    original=c.client.get(f'/api/proposal-runs/{c.run.id}/frames').json()
    draft=seed(c)
    assert snap(c,draft).status_code==409
    objects=copy.deepcopy(draft['revision']['objects']); objects[0]['marker']=[50,70]
    edited=save(c,draft,objects)
    assert edited['revision']['objects'][0]['origin']=='human_edit'
    assert edited['revision']['objects'][1]==draft['revision']['objects'][1]
    assert c.client.post(f'/api/review/annotations/{draft["annotation"]["id"]}/revisions',json=body(draft)).status_code==409
    assert approve(c,draft).status_code==409
    reviewed=approve(c,edited).json()
    assert reviewed['review']['validity']['count']=='UNKNOWN' and reviewed['review']['validity']['speed']=='UNKNOWN'
    snapshot=snap(c,edited)
    assert snapshot.status_code==201,snapshot.text
    locked=snapshot.json()
    assert approve(c,edited,verdict='INCOMPLETE').status_code==201
    assert snap(c,edited).status_code==409
    assert c.client.get(f'/api/review/snapshots/{locked["id"]}').json()==locked
    next_rev=save(c,edited,[])
    assert next_rev['review'] is None
    assert snap(c,edited).status_code==409 and snap(c,next_rev).status_code==409
    assert c.client.get(f'/api/review/snapshots/{locked["id"]}').json()==locked
    assert c.client.get(f'/api/proposal-runs/{c.run.id}/frames').json()==original
    # Reopening from a later AI run must not reset human revision or resurrect deleted people.
    with sessions(c.app.state.engine).begin() as db:
        job=Job(id=uuid.uuid4().hex,project_id=c.media.project_id,kind='AUTO_LABEL',state='SUCCEEDED',progress=100,payload={})
        db.add(job);db.flush()
        later=ProposalRun(id=uuid.uuid4().hex,job_id=job.id,media_id=c.media.id,manifest={'evidence_type':'DEMO_RERUN'},frame_count=1)
        db.add(later);db.flush()
        db.add(ProposalFrame(id=uuid.uuid4().hex,run_id=later.id,frame_id=c.frames[0].id,ordinal=0,payload={'objects':[]}))
    rerun=c.client.post(f'/api/review/frames/{c.frames[0].id}/draft',json={'proposal_run_id':later.id,'roi_version_id':c.roi['id']}).json()
    assert rerun['revision']['id']==next_rev['revision']['id'] and rerun['revision']['proposal_run_id']==c.run.id
    assert seed(c)['revision']['id']==next_rev['revision']['id']
    restored=save(c,next_rev,edited['revision']['objects'])
    assert restored['revision']['objects'][0]['source_proposal_id']==edited['revision']['objects'][0]['source_proposal_id']
    history=c.client.get(f'/api/review/annotations/{draft["annotation"]["id"]}/history').json()
    assert len(history)==4 and history[1]['reviews'][0]['verdict']=='APPROVED'
    with sessions(c.app.state.engine)() as db: assert len(list(db.scalars(select(AuditEvent))))>=6


def test_completeness_marker_only_empty_and_validity(c):
    doc=seed(c)
    assert approve(c,doc,anchors_complete=True).status_code==422
    objects=copy.deepcopy(doc['revision']['objects'])
    for o in objects:o['anchor_verified']=True
    objects[0]['box']=None
    doc=save(c,doc,objects)
    assert approve(c,doc,boxes_complete=True).status_code==422
    approved=approve(c,doc,count_complete=True,anchors_complete=True).json()
    assert approved['review']['validity']['count']=='VALID'
    assert approved['review']['completeness']['person_boxes'] is False
    assert approved['review']['completeness']['training_eligibility']=='NOT_EVALUATED'
    assert snap(c,doc).status_code==201
    empty=seed(c,2)
    assert empty['revision']['objects']==[] and 'EMPTY_PREDICTION' in empty['flags']
    assert approve(c,empty,count_complete=True,anchors_complete=True,boxes_complete=True).status_code==201
    assert snap(c,empty).json()['manifest']['evidence_type']=='DEMO'


def test_roi_and_rule_changes_invalidate_new_release_not_old_snapshot(c,monkeypatch):
    doc=seed(c);assert approve(c,doc).status_code==201
    locked=snap(c,doc).json()
    changed=c.client.post(f'/api/rois/{c.roi["roi_id"]}/versions',json={k:c.roi[k] for k in ('name','polygon_source_px','area_evidence','evidence_type')}|{'area_m2':6,'base_version_id':c.roi['id']}).json()
    assert approve(c,doc).status_code==409 and snap(c,doc).status_code==409
    updated=save(c,doc,roi=changed['id'])
    assert updated['review'] is None and not updated['stale']
    assert approve(c,updated).status_code==201
    config=review_api.rules();config['version']='changed_for_test'
    monkeypatch.setattr(review_api,'rules',lambda:config)
    assert snap(c,updated).status_code==409
    assert c.client.get(f'/api/review/snapshots/{locked["id"]}').json()==locked
    refreshed=save(c,updated,roi=changed['id'])
    assert approve(c,refreshed).status_code==201 and snap(c,refreshed).status_code==201


def test_concurrent_saves_and_invalid_geometry(c):
    doc=seed(c)
    payload=body(doc)
    url=f'/api/review/annotations/{doc["annotation"]["id"]}/revisions'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:c.client.post(url,json=payload).status_code,range(2)))
    assert sorted(results)==[201,409]
    doc=c.client.get(f'/api/review/annotations/{doc["annotation"]["id"]}').json()
    for bad in ({'box':[10,20,9999,100]}, {'anchor':None,'anchor_verified':True},{'marker':[-1,0]},{'score':1}):
        value=body(doc);value['objects'][0].update(bad)
        assert c.client.post(url,json=value).status_code==422
    assert c.client.post(f'/api/review/annotations/{doc["annotation"]["id"]}/reviews',json={'expected_edit_version':2,'reviewer':'x','verdict':'APPROVED','reviewed_scope':'ROI','viewed':False}).status_code==422
    with sessions(c.app.state.engine)() as db: source_path=db.get(Media,c.media.id).source_path
    from pathlib import Path
    Path(source_path).write_bytes(b'changed DEMO source')
    assert approve(c,doc).json()['code']=='SOURCE_HASH_MISMATCH'


def test_all_quality_flags_preserve_overlap(c):
    doc=seed(c)
    assert {'LOW_CONFIDENCE','POSSIBLE_DUPLICATE','NEAR_DENSITY_THRESHOLD','ANCHOR_UNVERIFIED'}<=set(doc['flags'])
    objects=copy.deepcopy(doc['revision']['objects']);objects[0]['anchor']=[0,150]
    found=flags(objects,SimpleNamespace(polygon_source_px=c.roi['polygon_source_px'],area_m2=4),review_api.rules(),previous_count=20)
    assert 'NEAR_ROI_BOUNDARY' in found and 'COUNT_JUMP' in found and len(objects)==2
    queue=c.client.get(f'/api/review/runs/{c.run.id}/queue?roi_version_id={c.roi["id"]}').json()['items']
    assert len(queue)==3 and queue[-1]['object_count']==0
